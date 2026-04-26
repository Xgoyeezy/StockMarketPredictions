from __future__ import annotations

import json
import math
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from backend.core.config import settings
from backend.services.market_data.base import MarketDataProvider
from backend.services.market_data.types import (
    EventRevisionSnapshot,
    MarketEvent,
    MarketNewsItem,
    MarketStateSnapshot,
    OptionChainSnapshot,
    OptionContractQuote,
    OptionsFlowSnapshot,
    RelativeStrengthSnapshot,
)
from backend.services.market_data.yfinance_adapter import (
    YFinanceMarketDataProvider,
    _extract_symbol_frame_from_downloaded,
    _latest_valid,
    _opening_range_metrics,
    _rolling_percentile,
    _time_of_day_context,
)

_HTTP_USER_AGENT = "StockMarketPredictions/2.6"
_PRIMARY_PRICE_SYMBOLS = ("SPY", "QQQ", "IWM", "DIA")
_SUPPORTED_ALPACA_INTERVALS = {
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "30m": "30Min",
    "60m": "1Hour",
    "1h": "1Hour",
    "1d": "1Day",
}
_PERIOD_PATTERN = re.compile(r"^(?P<count>\d+)(?P<unit>d|wk|mo|y)$", re.IGNORECASE)


def _utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _iso_utc(value: pd.Timestamp | datetime | None = None) -> str:
    ts = pd.Timestamp(value or _utc_now())
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").isoformat()


def _parse_period_to_timedelta(period: str) -> timedelta:
    normalized = str(period or "").strip().lower()
    match = _PERIOD_PATTERN.match(normalized)
    if not match:
        return timedelta(days=60)
    count = max(int(match.group("count")), 1)
    unit = match.group("unit").lower()
    if unit == "d":
        return timedelta(days=count)
    if unit == "wk":
        return timedelta(weeks=count)
    if unit == "mo":
        return timedelta(days=count * 30)
    if unit == "y":
        return timedelta(days=count * 365)
    return timedelta(days=60)


def _normalize_stock_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _is_alpaca_equity_symbol(symbol: str) -> bool:
    normalized = _normalize_stock_symbol(symbol)
    return bool(normalized) and normalized.replace(".", "").replace("-", "").isalnum() and not normalized.startswith("^")


def _to_chart_like_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    normalized = frame.copy()
    if "timestamp" in normalized.columns and normalized.index.name != "timestamp":
        normalized = normalized.set_index("timestamp")
    normalized.index = pd.to_datetime(normalized.index, errors="coerce", utc=True)
    normalized = normalized[normalized.index.notna()]
    rename_map = {
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    normalized = normalized.rename(columns=rename_map)
    columns = [column for column in ("open", "high", "low", "close", "volume") if column in normalized.columns]
    if not columns:
        return pd.DataFrame()
    normalized = normalized.loc[:, columns].sort_index()
    return normalized


def _concat_symbol_frames(frames: dict[str, pd.DataFrame], *, group_by: str | None) -> pd.DataFrame:
    valid_frames = {
        str(symbol or "").strip().upper(): _to_chart_like_frame(frame)
        for symbol, frame in dict(frames or {}).items()
        if isinstance(frame, pd.DataFrame) and not frame.empty
    }
    valid_frames = {symbol: frame for symbol, frame in valid_frames.items() if not frame.empty}
    if not valid_frames:
        return pd.DataFrame()
    if len(valid_frames) == 1 and str(group_by or "").strip().lower() != "ticker":
        return next(iter(valid_frames.values())).copy()
    pieces = []
    for symbol, frame in valid_frames.items():
        symbol_frame = frame.copy()
        symbol_frame.columns = pd.MultiIndex.from_product([[symbol], list(symbol_frame.columns)])
        pieces.append(symbol_frame)
    return pd.concat(pieces, axis=1).sort_index()


def _average_close_from_frame(frame: pd.DataFrame) -> float:
    close = pd.to_numeric(frame.get("close"), errors="coerce").dropna()
    if close.empty:
        return float("nan")
    return float(close.iloc[-1])


def _snapshot_age_seconds(fetched_at: str | None) -> float | None:
    if not fetched_at:
        return None
    parsed = pd.to_datetime(fetched_at, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return float(max((_utc_now() - parsed).total_seconds(), 0.0))


class AlpacaMarketDataClient:
    def __init__(
        self,
        *,
        api_key_id: str,
        api_secret_key: str,
        base_url: str,
        feed: str,
        timeout_seconds: int,
    ) -> None:
        self._api_key_id = str(api_key_id or "").strip()
        self._api_secret_key = str(api_secret_key or "").strip()
        self._base_url = str(base_url or "").rstrip("/")
        self._feed = str(feed or "iex").strip().lower() or "iex"
        self._timeout_seconds = max(int(timeout_seconds or 10), 1)

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key_id and self._api_secret_key)

    def _request_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured:
            return {}
        query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
        url = f"{self._base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = Request(
            url,
            headers={
                "APCA-API-KEY-ID": self._api_key_id,
                "APCA-API-SECRET-KEY": self._api_secret_key,
                "Accept": "application/json",
                "User-Agent": _HTTP_USER_AGENT,
            },
            method="GET",
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_stock_bars(
        self,
        symbols: Sequence[str],
        *,
        interval: str,
        period: str,
        prepost: bool,
    ) -> dict[str, pd.DataFrame]:
        if not self.is_configured:
            return {}
        timeframe = _SUPPORTED_ALPACA_INTERVALS.get(str(interval or "").strip().lower())
        normalized_symbols = [_normalize_stock_symbol(symbol) for symbol in symbols if _is_alpaca_equity_symbol(symbol)]
        if not timeframe or not normalized_symbols:
            return {}

        end_at = datetime.now(timezone.utc)
        start_at = end_at - _parse_period_to_timedelta(period)
        page_token: str | None = None
        collected: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in normalized_symbols}
        while True:
            payload = self._request_json(
                "/v2/stocks/bars",
                {
                    "symbols": ",".join(normalized_symbols),
                    "timeframe": timeframe,
                    "start": start_at.isoformat().replace("+00:00", "Z"),
                    "end": end_at.isoformat().replace("+00:00", "Z"),
                    "adjustment": "all",
                    "feed": self._feed,
                    "sort": "asc",
                    "limit": 10000,
                    "page_token": page_token,
                    "asof": None,
                },
            )
            bars_by_symbol = payload.get("bars") if isinstance(payload, dict) else {}
            if isinstance(bars_by_symbol, dict):
                for symbol, items in bars_by_symbol.items():
                    if isinstance(items, list):
                        collected.setdefault(symbol, []).extend(items)
            page_token = payload.get("next_page_token") if isinstance(payload, dict) else None
            if not page_token:
                break

        results: dict[str, pd.DataFrame] = {}
        for symbol, items in collected.items():
            if not items:
                continue
            frame = pd.DataFrame(
                {
                    "timestamp": [item.get("t") for item in items],
                    "open": [item.get("o") for item in items],
                    "high": [item.get("h") for item in items],
                    "low": [item.get("l") for item in items],
                    "close": [item.get("c") for item in items],
                    "volume": [item.get("v") for item in items],
                }
            )
            results[symbol] = _to_chart_like_frame(frame)
        return results

    def get_latest_prices(self, symbols: Sequence[str]) -> dict[str, float]:
        if not self.is_configured:
            return {}
        normalized_symbols = [_normalize_stock_symbol(symbol) for symbol in symbols if _is_alpaca_equity_symbol(symbol)]
        if not normalized_symbols:
            return {}
        payload = self._request_json(
            "/v2/stocks/trades/latest",
            {
                "symbols": ",".join(normalized_symbols),
                "feed": self._feed,
            },
        )
        trades = payload.get("trades") if isinstance(payload, dict) else {}
        results: dict[str, float] = {}
        if isinstance(trades, dict):
            for symbol, item in trades.items():
                price = item.get("p") if isinstance(item, dict) else None
                if isinstance(price, (int, float)) and not math.isnan(float(price)):
                    results[str(symbol).strip().upper()] = float(price)
        return results

    def get_option_chain_snapshots(
        self,
        ticker: str,
        *,
        feed: str,
        expiration: str | None = None,
        limit: int = 500,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured:
            return {}
        return self._request_json(
            f"/v1beta1/options/snapshots/{_normalize_stock_symbol(ticker)}",
            {
                "feed": str(feed or "opra").strip().lower() or "opra",
                "expiration_date": expiration,
                "limit": max(int(limit or 500), 1),
                "page_token": page_token,
            },
        )


class PolygonReferenceClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: int,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self._base_url = str(base_url or "").rstrip("/")
        self._timeout_seconds = max(int(timeout_seconds or 10), 1)

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _request_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured:
            return {}
        query = urlencode(
            {
                **{key: value for key, value in params.items() if value not in (None, "")},
                "apiKey": self._api_key,
            }
        )
        request = Request(
            f"{self._base_url}{path}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": _HTTP_USER_AGENT,
            },
            method="GET",
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def list_option_contracts(self, ticker: str, *, expiration: str | None = None, limit: int = 250) -> list[dict[str, Any]]:
        payload = self._request_json(
            "/v3/reference/options/contracts",
            {
                "underlying_ticker": _normalize_stock_symbol(ticker),
                "expiration_date": expiration,
                "limit": max(int(limit), 1),
            },
        )
        results = payload.get("results") if isinstance(payload, dict) else []
        return list(results or []) if isinstance(results, list) else []

    def get_options_chain_snapshot(self, ticker: str, *, expiration: str | None = None, limit: int = 250) -> list[dict[str, Any]]:
        payload = self._request_json(
            f"/v3/snapshot/options/{_normalize_stock_symbol(ticker)}",
            {
                "expiration_date": expiration,
                "limit": max(int(limit), 1),
            },
        )
        results = payload.get("results") if isinstance(payload, dict) else []
        return list(results or []) if isinstance(results, list) else []

    def get_ticker_details(self, ticker: str) -> dict[str, Any]:
        payload = self._request_json(f"/v3/reference/tickers/{_normalize_stock_symbol(ticker)}", {})
        result = payload.get("results") if isinstance(payload, dict) else {}
        return dict(result or {}) if isinstance(result, dict) else {}

    def list_ticker_news(self, ticker: str, *, limit: int = 10) -> list[dict[str, Any]]:
        payload = self._request_json(
            "/v2/reference/news",
            {
                "ticker": _normalize_stock_symbol(ticker),
                "limit": max(int(limit), 1),
                "order": "desc",
                "sort": "published_utc",
            },
        )
        results = payload.get("results") if isinstance(payload, dict) else []
        return list(results or []) if isinstance(results, list) else []


class HybridMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        *,
        alpaca_client: AlpacaMarketDataClient | None = None,
        polygon_client: PolygonReferenceClient | None = None,
        fallback_provider: MarketDataProvider | None = None,
    ) -> None:
        alpaca_base_url = "https://data.sandbox.alpaca.markets" if settings.alpaca_use_sandbox else "https://data.alpaca.markets"
        self._alpaca = alpaca_client or AlpacaMarketDataClient(
            api_key_id=settings.alpaca_api_key_id,
            api_secret_key=settings.alpaca_api_secret_key,
            base_url=alpaca_base_url,
            feed=settings.alpaca_stock_feed,
            timeout_seconds=settings.alpaca_market_data_request_timeout_seconds,
        )
        self._polygon = polygon_client or PolygonReferenceClient(
            api_key=settings.polygon_api_key,
            base_url=settings.polygon_api_base_url,
            timeout_seconds=settings.polygon_request_timeout_seconds,
        )
        self._fallback = fallback_provider or YFinanceMarketDataProvider()
        self._snapshot_cache: dict[tuple[str, tuple[Any, ...]], tuple[float, Any]] = {}

    @property
    def provider_name(self) -> str:
        return "hybrid"

    def _mark_snapshot(self, snapshot: Any, *, source: str, source_detail: str, ttl_seconds: int, cache_status: str, degraded: bool) -> Any:
        fetched_at = getattr(snapshot, "fetched_at", None) or _iso_utc()
        freshness_seconds = _snapshot_age_seconds(fetched_at)
        freshness_status = "fresh"
        if degraded:
            freshness_status = "degraded"
        if freshness_seconds is not None and freshness_seconds > max(int(ttl_seconds or 0), 0):
            freshness_status = "stale"
            degraded = True
        return replace(
            snapshot,
            source=source,
            source_detail=source_detail,
            fetched_at=fetched_at,
            freshness_seconds=None if freshness_seconds is None else round(float(freshness_seconds), 4),
            freshness_ttl_seconds=int(ttl_seconds or 0),
            freshness_status=freshness_status,
            cache_status=cache_status,
            degraded=bool(degraded),
        )

    def _resolve_snapshot_with_fallback(
        self,
        *,
        cache_namespace: str,
        cache_key: tuple[Any, ...],
        ttl_seconds: int,
        primary_loader: callable,
        fallback_loader: callable,
    ) -> Any:
        full_key = (cache_namespace, tuple(cache_key))
        cached = self._snapshot_cache.get(full_key)
        if cached is not None:
            cached_at, cached_snapshot = cached
            if (time.monotonic() - cached_at) <= max(int(ttl_seconds or 0), 0):
                return self._mark_snapshot(
                    cached_snapshot,
                    source=str(getattr(cached_snapshot, "source", "fallback") or "fallback"),
                    source_detail=str(getattr(cached_snapshot, "source_detail", getattr(cached_snapshot, "source", "fallback")) or "fallback"),
                    ttl_seconds=ttl_seconds,
                    cache_status="cached",
                    degraded=bool(getattr(cached_snapshot, "degraded", False)),
                )
        try:
            primary_snapshot = primary_loader()
        except Exception:
            primary_snapshot = None
        if primary_snapshot is not None:
            live_snapshot = self._mark_snapshot(
                primary_snapshot,
                source=str(getattr(primary_snapshot, "source", "fallback") or "fallback"),
                source_detail=str(getattr(primary_snapshot, "source_detail", getattr(primary_snapshot, "source", "fallback")) or "fallback"),
                ttl_seconds=ttl_seconds,
                cache_status="live",
                degraded=bool(getattr(primary_snapshot, "degraded", False)),
            )
            self._snapshot_cache[full_key] = (time.monotonic(), live_snapshot)
            return live_snapshot
        fallback_snapshot = fallback_loader()
        degraded_snapshot = self._mark_snapshot(
            fallback_snapshot,
            source=str(getattr(fallback_snapshot, "source", "fallback") or "fallback"),
            source_detail=str(getattr(fallback_snapshot, "source_detail", "yfinance_fallback") or "yfinance_fallback"),
            ttl_seconds=ttl_seconds,
            cache_status="fallback",
            degraded=True,
        )
        self._snapshot_cache[full_key] = (time.monotonic(), degraded_snapshot)
        return degraded_snapshot

    def download_bars(
        self,
        tickers: str | Sequence[str],
        *,
        period: str,
        interval: str,
        prepost: bool,
        group_by: str | None = None,
    ) -> pd.DataFrame:
        normalized_symbols = (
            [_normalize_stock_symbol(tickers)]
            if isinstance(tickers, str)
            else [_normalize_stock_symbol(symbol) for symbol in list(tickers or []) if _normalize_stock_symbol(symbol)]
        )
        primary_frames: dict[str, pd.DataFrame] = {}
        if normalized_symbols and str(interval or "").strip().lower() in _SUPPORTED_ALPACA_INTERVALS:
            try:
                primary_frames = self._alpaca.get_stock_bars(
                    normalized_symbols,
                    interval=interval,
                    period=period,
                    prepost=prepost,
                )
            except Exception:
                primary_frames = {}
        if len(primary_frames) == len(normalized_symbols) and primary_frames:
            return _concat_symbol_frames(primary_frames, group_by=group_by)

        fallback_downloaded = self._fallback.download_bars(
            tickers,
            period=period,
            interval=interval,
            prepost=prepost,
            group_by=group_by,
        )
        if not primary_frames:
            return fallback_downloaded
        merged_frames = dict(primary_frames)
        for symbol in normalized_symbols:
            if symbol in merged_frames and not merged_frames[symbol].empty:
                continue
            fallback_frame = _extract_symbol_frame_from_downloaded(fallback_downloaded, symbol)
            if isinstance(fallback_frame, pd.DataFrame) and not fallback_frame.empty:
                merged_frames[symbol] = _to_chart_like_frame(fallback_frame)
        return _concat_symbol_frames(merged_frames, group_by=group_by)

    def get_contract_history(self, contract_symbol: str, *, period: str, interval: str) -> pd.DataFrame:
        return self._fallback.get_contract_history(contract_symbol, period=period, interval=interval)

    def get_latest_prices(self, symbols: Sequence[str], *, prepost: bool) -> dict[str, float]:
        normalized_symbols = [_normalize_stock_symbol(symbol) for symbol in symbols if _normalize_stock_symbol(symbol)]
        equity_symbols = [symbol for symbol in normalized_symbols if _is_alpaca_equity_symbol(symbol)]
        results: dict[str, float] = {}
        if equity_symbols:
            try:
                results.update(self._alpaca.get_latest_prices(equity_symbols))
            except Exception:
                results = {}
        missing = [symbol for symbol in normalized_symbols if symbol not in results]
        if missing:
            fallback_quotes = self._fallback.get_latest_prices(missing, prepost=prepost)
            for symbol, value in fallback_quotes.items():
                if isinstance(value, (int, float)) and not math.isnan(float(value)):
                    results[str(symbol).strip().upper()] = float(value)
        return results

    def get_calendar_events(self, ticker: str) -> list[MarketEvent]:
        return self._fallback.get_calendar_events(ticker)

    def get_earnings_events(self, ticker: str, *, limit: int) -> list[MarketEvent]:
        return self._fallback.get_earnings_events(ticker, limit=limit)

    def get_news_items(self, ticker: str) -> list[MarketNewsItem]:
        return self._fallback.get_news_items(ticker)

    def get_option_expirations(self, ticker: str) -> list[str]:
        if self._polygon.is_configured:
            try:
                contracts = self._polygon.list_option_contracts(ticker, limit=500)
            except Exception:
                contracts = []
            expirations = sorted(
                {
                    str(
                        (contract.get("expiration_date") if isinstance(contract, dict) else None)
                        or ((contract.get("details") or {}).get("expiration_date") if isinstance(contract, dict) else None)
                        or ""
                    ).strip()
                    for contract in contracts
                }
            )
            expirations = [value for value in expirations if value]
            if expirations:
                return expirations
        return self._fallback.get_option_expirations(ticker)

    def get_option_chain(self, ticker: str, expiration: str) -> OptionChainSnapshot:
        if self._polygon.is_configured:
            try:
                snapshot_rows = self._polygon.get_options_chain_snapshot(ticker, expiration=expiration, limit=500)
            except Exception:
                snapshot_rows = []
            if snapshot_rows:
                calls: list[OptionContractQuote] = []
                puts: list[OptionContractQuote] = []
                for item in snapshot_rows:
                    details = dict(item.get("details") or {}) if isinstance(item, dict) else {}
                    last_quote = dict(item.get("last_quote") or {}) if isinstance(item, dict) else {}
                    last_trade = dict(item.get("last_trade") or {}) if isinstance(item, dict) else {}
                    contract_type = str(details.get("contract_type") or item.get("contract_type") or "").strip().lower()
                    option = OptionContractQuote(
                        contract_symbol=str(
                            details.get("ticker")
                            or item.get("ticker")
                            or item.get("contract_symbol")
                            or ""
                        ).strip(),
                        strike=float(details.get("strike_price")) if isinstance(details.get("strike_price"), (int, float)) else None,
                        bid=float(last_quote.get("bid_price")) if isinstance(last_quote.get("bid_price"), (int, float)) else None,
                        ask=float(last_quote.get("ask_price")) if isinstance(last_quote.get("ask_price"), (int, float)) else None,
                        last_price=float(last_trade.get("price")) if isinstance(last_trade.get("price"), (int, float)) else None,
                        implied_volatility=float(item.get("implied_volatility")) if isinstance(item.get("implied_volatility"), (int, float)) else None,
                        volume=int(float((item.get("day") or {}).get("volume") or 0)),
                        open_interest=int(float(item.get("open_interest") or 0)),
                        in_the_money=bool(item.get("in_the_money")),
                        quote_timestamp=pd.to_datetime(last_quote.get("last_updated"), errors="coerce", utc=True),
                    )
                    if contract_type == "call":
                        calls.append(option)
                    elif contract_type == "put":
                        puts.append(option)
                if calls or puts:
                    return OptionChainSnapshot(expiration=str(expiration), calls=calls, puts=puts)
        return self._fallback.get_option_chain(ticker, expiration)

    def get_market_state_snapshot(self, ticker: str, *, interval: str) -> MarketStateSnapshot:
        resolved_interval = interval if str(interval or "").strip().lower() in _SUPPORTED_ALPACA_INTERVALS else "1d"
        period = "60d" if resolved_interval != "1d" else "1y"

        def _primary_loader() -> MarketStateSnapshot | None:
            if not self._alpaca.is_configured:
                return None
            frames = self._alpaca.get_stock_bars(
                list(_PRIMARY_PRICE_SYMBOLS),
                interval=resolved_interval,
                period=period,
                prepost=False,
            )
            if not frames:
                return None
            benchmark_returns: list[float] = []
            benchmark_trends: list[float] = []
            for symbol in _PRIMARY_PRICE_SYMBOLS:
                frame = _to_chart_like_frame(frames.get(symbol, pd.DataFrame()))
                close = pd.to_numeric(frame.get("close"), errors="coerce").dropna()
                if len(close) >= 6:
                    benchmark_returns.append(float(close.pct_change().iloc[-1]))
                    sma20 = close.rolling(20).mean()
                    trend = ((close / sma20) - 1.0).dropna()
                    if not trend.empty:
                        benchmark_trends.append(float(trend.iloc[-1]))
            spy_frame = _to_chart_like_frame(frames.get("SPY", pd.DataFrame()))
            if spy_frame.empty:
                return None
            spy_close = pd.to_numeric(spy_frame.get("close"), errors="coerce").dropna()
            spy_returns = spy_close.pct_change().dropna()
            realized_volatility = float(spy_returns.tail(20).std()) if len(spy_returns) >= 5 else 0.0
            realized_volatility_percentile = _rolling_percentile(spy_returns.rolling(20).std().dropna()) if len(spy_returns) >= 20 else 0.5
            opening_range_bias, opening_range_breakout = _opening_range_metrics(spy_frame)
            latest_ts = pd.to_datetime(spy_frame.index[-1], errors="coerce") if not spy_frame.empty else None
            time_of_day_bucket, time_of_day_progress = _time_of_day_context(latest_ts)
            breadth_score = float(np.clip(np.mean([1.0 if item > 0 else -1.0 if item < 0 else 0.0 for item in benchmark_returns]), -1.0, 1.0)) if benchmark_returns else 0.0
            breadth_momentum = float(np.clip(np.mean(benchmark_returns) * 200.0, -1.0, 1.0)) if benchmark_returns else 0.0
            market_trend_score = float(np.clip(0.5 + (np.mean(benchmark_trends) * 12.0), 0.0, 1.0)) if benchmark_trends else 0.5
            dispersion_score = float(np.clip(np.std(benchmark_returns) * 200.0, 0.0, 1.0)) if benchmark_returns else 0.0
            return MarketStateSnapshot(
                breadth_score=round(breadth_score, 4),
                breadth_momentum=round(breadth_momentum, 4),
                market_trend_score=round(market_trend_score, 4),
                dispersion_score=round(dispersion_score, 4),
                vix_level=None,
                vix_change_pct=0.0,
                vix_term_structure=0.0,
                realized_volatility=round(realized_volatility, 6),
                realized_volatility_percentile=round(realized_volatility_percentile, 4),
                opening_range_bias=round(opening_range_bias, 4),
                opening_range_breakout=round(opening_range_breakout, 4),
                time_of_day_bucket=time_of_day_bucket,
                time_of_day_progress=round(time_of_day_progress, 4),
                source="alpaca",
                source_detail="alpaca_market_state",
                fetched_at=_iso_utc(),
                freshness_status="fresh",
                cache_status="live",
                degraded=False,
            )

        def _fallback_loader() -> MarketStateSnapshot:
            fallback_snapshot = self._fallback.get_market_state_snapshot(ticker, interval=interval)
            return replace(
                fallback_snapshot,
                source_detail=str(getattr(fallback_snapshot, "source_detail", "yfinance_proxy") or "yfinance_proxy"),
                degraded=True,
            )

        return self._resolve_snapshot_with_fallback(
            cache_namespace="market_state",
            cache_key=(str(ticker or "").strip().upper(), resolved_interval),
            ttl_seconds=settings.hybrid_market_state_ttl_seconds,
            primary_loader=_primary_loader,
            fallback_loader=_fallback_loader,
        )

    def get_relative_strength_snapshot(
        self,
        ticker: str,
        *,
        interval: str,
        benchmark_symbol: str,
        sector_symbol: str | None,
        peer_symbols: Sequence[str],
    ) -> RelativeStrengthSnapshot:
        normalized_ticker = _normalize_stock_symbol(ticker)
        requested_symbols = [normalized_ticker, _normalize_stock_symbol(benchmark_symbol or "SPY")]
        if sector_symbol:
            requested_symbols.append(_normalize_stock_symbol(sector_symbol))
        requested_symbols.extend(_normalize_stock_symbol(item) for item in peer_symbols if _normalize_stock_symbol(item))
        requested_symbols = list(dict.fromkeys(symbol for symbol in requested_symbols if symbol))
        resolved_interval = interval if str(interval or "").strip().lower() in _SUPPORTED_ALPACA_INTERVALS else "1d"
        period = "60d" if resolved_interval != "1d" else "1y"

        def _primary_loader() -> RelativeStrengthSnapshot | None:
            if not self._alpaca.is_configured:
                return None
            frames = self._alpaca.get_stock_bars(
                requested_symbols,
                interval=resolved_interval,
                period=period,
                prepost=False,
            )
            if not frames or normalized_ticker not in frames:
                return None

            def _ret(symbol: str, bars: int = 5) -> float:
                frame = _to_chart_like_frame(frames.get(symbol, pd.DataFrame()))
                close = pd.to_numeric(frame.get("close"), errors="coerce").dropna()
                if len(close) <= bars:
                    return 0.0
                value = close.pct_change(bars).iloc[-1]
                return float(value) if not pd.isna(value) else 0.0

            benchmark_symbol_resolved = _normalize_stock_symbol(benchmark_symbol or "SPY") or "SPY"
            sector_symbol_resolved = _normalize_stock_symbol(sector_symbol or "")
            ticker_ret = _ret(normalized_ticker)
            benchmark_ret = _ret(benchmark_symbol_resolved)
            sector_ret = _ret(sector_symbol_resolved) if sector_symbol_resolved else 0.0
            benchmark_relative = ticker_ret - benchmark_ret
            sector_relative = ticker_ret - sector_ret if sector_symbol_resolved else benchmark_relative
            residual_return = ticker_ret - ((benchmark_ret * 0.65) + (sector_ret * 0.35 if sector_symbol_resolved else 0.0))
            peer_returns = [(_normalize_stock_symbol(symbol), _ret(symbol)) for symbol in requested_symbols if _normalize_stock_symbol(symbol) != normalized_ticker]
            peer_rank = 0.5
            if peer_returns:
                ordered = sorted([value for _, value in peer_returns] + [ticker_ret])
                try:
                    peer_rank = float(ordered.index(ticker_ret) / max(len(ordered) - 1, 1))
                except ValueError:
                    peer_rank = 0.5
            relative_strength_score = float(
                np.clip(
                    0.5
                    + (benchmark_relative * 10.0)
                    + (sector_relative * 8.0)
                    + ((peer_rank - 0.5) * 0.4)
                    + (residual_return * 8.0),
                    0.0,
                    1.0,
                )
            )
            return RelativeStrengthSnapshot(
                benchmark_symbol=benchmark_symbol_resolved,
                sector_symbol=sector_symbol_resolved,
                benchmark_relative_return=round(benchmark_relative, 6),
                sector_relative_return=round(sector_relative, 6),
                residual_return=round(residual_return, 6),
                peer_momentum_rank=round(peer_rank, 4),
                peer_count=max(len(peer_returns), 0),
                relative_strength_score=round(relative_strength_score, 4),
                source="alpaca",
                source_detail="alpaca_relative_strength",
                fetched_at=_iso_utc(),
                freshness_status="fresh",
                cache_status="live",
                degraded=False,
            )

        def _fallback_loader() -> RelativeStrengthSnapshot:
            fallback_snapshot = self._fallback.get_relative_strength_snapshot(
                ticker,
                interval=interval,
                benchmark_symbol=benchmark_symbol,
                sector_symbol=sector_symbol,
                peer_symbols=peer_symbols,
            )
            return replace(
                fallback_snapshot,
                source_detail=str(getattr(fallback_snapshot, "source_detail", "yfinance_proxy") or "yfinance_proxy"),
                degraded=True,
            )

        return self._resolve_snapshot_with_fallback(
            cache_namespace="relative_strength",
            cache_key=(normalized_ticker, resolved_interval, _normalize_stock_symbol(benchmark_symbol or "SPY"), _normalize_stock_symbol(sector_symbol or ""), tuple(requested_symbols[2:])),
            ttl_seconds=settings.hybrid_relative_strength_ttl_seconds,
            primary_loader=_primary_loader,
            fallback_loader=_fallback_loader,
        )

    def get_options_flow_snapshot(
        self,
        ticker: str,
        *,
        underlying_price: float | None = None,
        expiration: str | None = None,
    ) -> OptionsFlowSnapshot:
        normalized_ticker = _normalize_stock_symbol(ticker)

        def _primary_loader() -> OptionsFlowSnapshot | None:
            if not self._polygon.is_configured:
                return None
            rows = self._polygon.get_options_chain_snapshot(normalized_ticker, expiration=expiration, limit=500)
            if not rows:
                return None
            calls_rows = []
            puts_rows = []
            for item in rows:
                details = dict(item.get("details") or {}) if isinstance(item, dict) else {}
                contract_type = str(details.get("contract_type") or item.get("contract_type") or "").strip().lower()
                quote = dict(item.get("last_quote") or {}) if isinstance(item, dict) else {}
                day = dict(item.get("day") or {}) if isinstance(item, dict) else {}
                row = {
                    "strike": details.get("strike_price"),
                    "impliedVolatility": item.get("implied_volatility"),
                    "volume": day.get("volume"),
                    "openInterest": item.get("open_interest"),
                    "bid": quote.get("bid_price"),
                    "ask": quote.get("ask_price"),
                }
                if contract_type == "call":
                    calls_rows.append(row)
                elif contract_type == "put":
                    puts_rows.append(row)
            calls = pd.DataFrame(calls_rows)
            puts = pd.DataFrame(puts_rows)
            if calls.empty and puts.empty:
                return None
            spot = float(underlying_price or 0.0)
            if spot <= 0:
                latest_map = self.get_latest_prices([normalized_ticker], prepost=True)
                spot = float(latest_map.get(normalized_ticker) or 0.0)
            for frame in (calls, puts):
                for column in ("strike", "impliedVolatility", "volume", "openInterest"):
                    if column in frame.columns:
                        frame[column] = pd.to_numeric(frame[column], errors="coerce")
            if spot > 0:
                calls_near = calls[(calls["strike"] - spot).abs() / max(spot, 1e-6) <= 0.05] if not calls.empty else calls
                puts_near = puts[(puts["strike"] - spot).abs() / max(spot, 1e-6) <= 0.05] if not puts.empty else puts
            else:
                calls_near = calls
                puts_near = puts
            call_iv = float(pd.to_numeric(calls_near.get("impliedVolatility"), errors="coerce").dropna().mean()) if not calls_near.empty else float("nan")
            put_iv = float(pd.to_numeric(puts_near.get("impliedVolatility"), errors="coerce").dropna().mean()) if not puts_near.empty else float("nan")
            implied_volatility = float(np.nanmean([call_iv, put_iv])) if not math.isnan(np.nanmean([call_iv, put_iv])) else 0.0
            call_volume = float(pd.to_numeric(calls_near.get("volume"), errors="coerce").fillna(0).sum()) if not calls_near.empty else 0.0
            put_volume = float(pd.to_numeric(puts_near.get("volume"), errors="coerce").fillna(0).sum()) if not puts_near.empty else 0.0
            call_oi = float(pd.to_numeric(calls_near.get("openInterest"), errors="coerce").fillna(0).sum()) if not calls_near.empty else 0.0
            put_oi = float(pd.to_numeric(puts_near.get("openInterest"), errors="coerce").fillna(0).sum()) if not puts_near.empty else 0.0
            total_volume = max(call_volume + put_volume, 1.0)
            total_oi = max(call_oi + put_oi, 1.0)
            put_call_oi_ratio = float(np.clip(put_oi / max(call_oi, 1.0), 0.0, 10.0))
            call_volume_pressure = float(np.clip(call_volume / total_volume, 0.0, 1.0))
            put_volume_pressure = float(np.clip(put_volume / total_volume, 0.0, 1.0))
            net_flow_pressure = float(np.clip((call_volume - put_volume) / total_volume, -1.0, 1.0))
            unusual_volume_score = float(np.clip((call_volume + put_volume) / total_oi, 0.0, 3.0) / 3.0)
            skew_score = float(np.clip((put_iv - call_iv) if not (math.isnan(put_iv) or math.isnan(call_iv)) else 0.0, -1.0, 1.0))
            latest_price_map = self.get_latest_prices([normalized_ticker], prepost=True)
            current_price = float(latest_price_map.get(normalized_ticker) or spot or 0.0)
            realized_vol_frame = self.download_bars(normalized_ticker, period="60d", interval="1d", prepost=False)
            realized_close = pd.to_numeric(_extract_symbol_frame_from_downloaded(realized_vol_frame, normalized_ticker).get("close"), errors="coerce").pct_change().dropna() if isinstance(realized_vol_frame, pd.DataFrame) and not realized_vol_frame.empty else pd.Series(dtype=float)
            realized_vol = float(realized_close.tail(20).std() * math.sqrt(252)) if len(realized_close) >= 5 else 0.0
            iv_realized_vol_spread = float(implied_volatility - realized_vol)
            iv_percentile = float(np.clip(0.5 + (iv_realized_vol_spread * 1.5), 0.0, 1.0))
            return OptionsFlowSnapshot(
                implied_volatility=round(implied_volatility, 6),
                iv_rank=round(iv_percentile, 4),
                iv_percentile=round(iv_percentile, 4),
                iv_realized_vol_spread=round(iv_realized_vol_spread, 6),
                put_call_open_interest_ratio=round(put_call_oi_ratio, 4),
                call_volume_pressure=round(call_volume_pressure, 4),
                put_volume_pressure=round(put_volume_pressure, 4),
                net_flow_pressure=round(net_flow_pressure, 4),
                skew_score=round(skew_score, 4),
                unusual_volume_score=round(unusual_volume_score, 4),
                source="polygon",
                source_detail="polygon_options_chain",
                fetched_at=_iso_utc(),
                freshness_status="fresh",
                cache_status="live",
                degraded=False,
            )

        def _fallback_loader() -> OptionsFlowSnapshot:
            fallback_snapshot = self._fallback.get_options_flow_snapshot(
                ticker,
                underlying_price=underlying_price,
                expiration=expiration,
            )
            return replace(
                fallback_snapshot,
                source_detail=str(getattr(fallback_snapshot, "source_detail", "yfinance_proxy") or "yfinance_proxy"),
                degraded=True,
            )

        return self._resolve_snapshot_with_fallback(
            cache_namespace="options_flow",
            cache_key=(normalized_ticker, round(float(underlying_price or 0.0), 4), str(expiration or "")),
            ttl_seconds=settings.hybrid_options_flow_ttl_seconds,
            primary_loader=_primary_loader,
            fallback_loader=_fallback_loader,
        )

    def get_event_revision_snapshot(self, ticker: str) -> EventRevisionSnapshot:
        normalized_ticker = _normalize_stock_symbol(ticker)

        def _primary_loader() -> EventRevisionSnapshot | None:
            if not self._polygon.is_configured:
                return None
            details = self._polygon.get_ticker_details(normalized_ticker)
            news_items = self._polygon.list_ticker_news(normalized_ticker, limit=6)
            if not details and not news_items:
                return None
            now = pd.Timestamp.now(tz="UTC").normalize()
            earnings_date_raw = (
                details.get("earnings_date")
                or details.get("earnings_release_date")
                or details.get("next_earnings_date")
            )
            earnings_date = pd.to_datetime(earnings_date_raw, errors="coerce", utc=True)
            days_to_earnings = None if pd.isna(earnings_date) else int((earnings_date.normalize() - now).days)
            beta = details.get("beta")
            macro_sensitivity = float(np.clip(abs(float(beta)), 0.0, 2.0) / 2.0) if isinstance(beta, (int, float)) else 0.0
            analyst_revision_score = 0.0
            recommendation_mean = details.get("recommendation_mean") or details.get("analyst_recommendation_mean")
            if isinstance(recommendation_mean, (int, float)):
                analyst_revision_score = float(np.clip((3.0 - float(recommendation_mean)) / 2.0, -1.0, 1.0))
            target_revision_score = 0.0
            target_price = details.get("target_price") or details.get("analyst_target_price")
            current_price = details.get("price") or details.get("market_cap") or 0.0
            if isinstance(target_price, (int, float)) and isinstance(current_price, (int, float)) and float(current_price) > 0:
                target_revision_score = float(np.clip((float(target_price) / float(current_price)) - 1.0, -1.0, 1.0))
            estimate_revision_score = 0.0
            earnings_growth = details.get("earnings_growth") or details.get("earnings_quarterly_growth")
            if isinstance(earnings_growth, (int, float)) and not math.isnan(float(earnings_growth)):
                estimate_revision_score = float(np.clip(float(earnings_growth), -1.0, 1.0))
            recent_news_count = len(news_items)
            event_pressure = 0.0
            if days_to_earnings is not None:
                event_pressure = float(np.clip((5 - max(days_to_earnings, 0)) / 5.0, 0.0, 1.0))
            revision_confidence = float(
                np.clip(
                    (0.45 if details else 0.0)
                    + (0.25 if recommendation_mean not in (None, "") else 0.0)
                    + (0.15 if target_price not in (None, "") else 0.0)
                    + min(recent_news_count, 3) * 0.05,
                    0.0,
                    1.0,
                )
            )
            return EventRevisionSnapshot(
                days_to_earnings=days_to_earnings,
                event_pressure=round(event_pressure, 4),
                analyst_revision_score=round(analyst_revision_score, 4),
                target_revision_score=round(target_revision_score, 4),
                estimate_revision_score=round(estimate_revision_score, 4),
                macro_sensitivity=round(macro_sensitivity, 4),
                revision_confidence=round(revision_confidence, 4),
                source="polygon",
                source_detail="polygon_reference",
                fetched_at=_iso_utc(),
                freshness_status="fresh",
                cache_status="live",
                degraded=False,
            )

        def _fallback_loader() -> EventRevisionSnapshot:
            fallback_snapshot = self._fallback.get_event_revision_snapshot(ticker)
            return replace(
                fallback_snapshot,
                source_detail=str(getattr(fallback_snapshot, "source_detail", "yfinance_proxy") or "yfinance_proxy"),
                degraded=True,
            )

        return self._resolve_snapshot_with_fallback(
            cache_namespace="event_revision",
            cache_key=(normalized_ticker,),
            ttl_seconds=settings.hybrid_event_revision_ttl_seconds,
            primary_loader=_primary_loader,
            fallback_loader=_fallback_loader,
        )
