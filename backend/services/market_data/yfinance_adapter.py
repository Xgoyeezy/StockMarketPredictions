from __future__ import annotations

import html
from html.parser import HTMLParser
import math
import re
from typing import Any, Sequence
from urllib import parse, request
from xml.etree import ElementTree

import numpy as np
import pandas as pd
import yfinance as yf

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

_HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
_ARTICLE_FETCH_LIMIT = 1
_GOOGLE_RSS_MAX_ITEMS = 6
_GOOGLE_NEWS_ENDPOINT = "https://news.google.com/rss/search"
_SOURCE_PUBLISHER_FALLBACK = "Google News"
_HTTP_TIMEOUT_SECONDS = 2.5
_MARKET_STATE_SYMBOLS = ("SPY", "QQQ", "IWM", "DIA", "^VIX")
_EARNINGS_UNSUPPORTED_TICKERS = {
    "SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "SLV", "XLE", "XLF", "XLK",
    "XLI", "XLY", "XLP", "XLV", "XLU", "XLB", "XLRE", "SMH", "ARKK", "VTI",
    "VOO", "VEA", "EEM", "UVXY", "SOXL", "SOXS", "TQQQ", "SQQQ",
}


def _extract_symbol_frame_from_downloaded(downloaded: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if not isinstance(downloaded, pd.DataFrame) or downloaded.empty:
        return pd.DataFrame()
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return pd.DataFrame()
    if isinstance(downloaded.columns, pd.MultiIndex):
        target_key = next(
            (
                key for key in downloaded.columns.levels[0]
                if str(key or "").strip().upper() == normalized_symbol
            ),
            None,
        )
        if target_key is None:
            return pd.DataFrame()
        try:
            frame = downloaded[target_key].copy()
        except Exception:
            return pd.DataFrame()
    else:
        frame = downloaded.copy()
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    renamed = frame.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    return renamed


def _latest_valid(series: pd.Series) -> float | None:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float(numeric.iloc[-1])


def _rolling_percentile(series: pd.Series, window: int = 120) -> float:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return 0.5
    sample = numeric.tail(max(window, 20))
    latest = float(sample.iloc[-1])
    if len(sample) <= 1:
        return 0.5
    return float(np.clip((sample <= latest).mean(), 0.0, 1.0))


def _time_of_day_context(timestamp: pd.Timestamp | None) -> tuple[str, float]:
    if timestamp is None or pd.isna(timestamp):
        return "unknown", 0.0
    ts = pd.Timestamp(timestamp)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    ts = ts.tz_convert("America/New_York")
    minutes = (ts.hour * 60) + ts.minute
    session_start = 9 * 60 + 30
    session_end = 16 * 60
    progress = float(np.clip((minutes - session_start) / max(session_end - session_start, 1), 0.0, 1.0))
    if minutes < session_start:
        return "premarket", progress
    if minutes < 10 * 60 + 30:
        return "opening", progress
    if minutes < 12 * 60:
        return "morning", progress
    if minutes < 14 * 60:
        return "midday", progress
    if minutes <= session_end:
        return "closing", progress
    return "afterhours", progress


def _opening_range_metrics(frame: pd.DataFrame, *, opening_bars: int = 6) -> tuple[float, float]:
    if frame.empty or "close" not in frame or "high" not in frame or "low" not in frame:
        return 0.0, 0.0
    local_index = pd.to_datetime(frame.index, errors="coerce")
    if getattr(local_index, "tz", None) is None:
        local_index = local_index.tz_localize("UTC")
    local_index = local_index.tz_convert("America/New_York")
    session_dates = pd.Series(local_index.normalize(), index=frame.index)
    bar_number = session_dates.groupby(session_dates).cumcount()
    opening_high = pd.to_numeric(frame["high"], errors="coerce").where(bar_number < opening_bars).groupby(session_dates).transform("max")
    opening_low = pd.to_numeric(frame["low"], errors="coerce").where(bar_number < opening_bars).groupby(session_dates).transform("min")
    latest_close = _latest_valid(frame["close"])
    latest_high = _latest_valid(opening_high)
    latest_low = _latest_valid(opening_low)
    if latest_close is None or latest_high is None or latest_low is None:
        return 0.0, 0.0
    opening_range = max(latest_high - latest_low, max(abs(latest_close) * 0.001, 1e-6))
    bias = float(np.clip((latest_close - ((latest_high + latest_low) / 2.0)) / opening_range, -2.0, 2.0) / 2.0)
    breakout = 1.0 if latest_close > latest_high else -1.0 if latest_close < latest_low else 0.0
    return bias, breakout


def _safe_history(ticker: str, *, period: str, interval: str, prepost: bool = False) -> pd.DataFrame:
    try:
        return yf.download(
            str(ticker or "").strip().upper(),
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=False,
            prepost=prepost,
        )
    except Exception:
        return pd.DataFrame()


class _ParagraphTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capture_depth = 0
        self._skip_depth = 0
        self._parts: list[str] = []
        self._meta_description = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = str(tag or "").strip().lower()
        if normalized in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if normalized == "p":
            self._capture_depth += 1
        if normalized == "meta":
            attrs_map = {str(key or "").strip().lower(): str(value or "").strip() for key, value in attrs}
            meta_name = attrs_map.get("name", "").lower()
            meta_property = attrs_map.get("property", "").lower()
            if not self._meta_description and (
                meta_name == "description" or meta_property in {"og:description", "twitter:description"}
            ):
                self._meta_description = attrs_map.get("content", "")

    def handle_endtag(self, tag: str) -> None:
        normalized = str(tag or "").strip().lower()
        if normalized in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if normalized == "p" and self._capture_depth:
            self._capture_depth -= 1
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._capture_depth:
            return
        cleaned = " ".join(str(data or "").split())
        if cleaned:
            self._parts.append(cleaned)

    def text(self) -> str:
        body = " ".join(part for part in self._parts if part.strip())
        compact = re.sub(r"\s+", " ", body).strip()
        if compact:
            return compact
        return re.sub(r"\s+", " ", html.unescape(self._meta_description)).strip()


def _http_get_text(url: str) -> str:
    req = request.Request(
        str(url or "").strip(),
        headers={
            "User-Agent": _HTTP_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _extract_article_text_from_html(raw_html: str) -> str:
    extractor = _ParagraphTextExtractor()
    try:
        extractor.feed(raw_html or "")
        extractor.close()
    except Exception:
        return ""
    return extractor.text()[:6000]


def _normalize_news_title(value: object) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or "")).strip()).lower()


def _normalize_company_terms(symbol: str, company_name: str | None) -> tuple[str, ...]:
    values = {str(symbol or "").strip().upper(), f"${str(symbol or '').strip().upper()}"}
    cleaned_name = re.sub(r"[^A-Za-z0-9&.\- ]+", " ", str(company_name or "")).strip()
    if cleaned_name:
        values.add(cleaned_name)
        for part in cleaned_name.split():
            if len(part) >= 4:
                values.add(part)
    return tuple(value for value in values if value)


def _mentioned_terms(text: str, terms: Sequence[str]) -> tuple[str, ...]:
    normalized_text = str(text or "").lower()
    hits = []
    for term in terms:
        cleaned = str(term or "").strip()
        if cleaned and cleaned.lower() in normalized_text:
            hits.append(cleaned.upper())
    return tuple(dict.fromkeys(hits))


def _compute_relevance_score(*, symbol: str, title: str, summary: str, article_text: str, terms: Sequence[str]) -> float:
    base = 0.18
    combined = " ".join(part for part in [title, summary, article_text] if part).lower()
    if not combined:
        return base
    title_hits = sum(1 for term in terms if str(term or "").strip() and str(term).lower() in title.lower())
    summary_hits = sum(1 for term in terms if str(term or "").strip() and str(term).lower() in summary.lower())
    article_hits = sum(1 for term in terms if str(term or "").strip() and str(term).lower() in article_text.lower())
    base += min(0.42, title_hits * 0.22)
    base += min(0.24, summary_hits * 0.08)
    base += min(0.16, article_hits * 0.04)
    if str(symbol or "").strip().upper() in combined:
        base += 0.12
    return float(max(0.0, min(1.0, base)))


def _coerce_timestamp(value: object) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        timestamp = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(timestamp):
        return None
    return timestamp.tz_localize(None) if timestamp.tzinfo is not None else timestamp


def _latest_close_from_frame(frame: pd.DataFrame) -> float:
    if frame.empty or "Close" not in frame.columns:
        return float("nan")
    close_series = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if close_series.empty:
        return float("nan")
    return float(close_series.iloc[-1])


def _extract_symbol_close_from_downloaded(downloaded: pd.DataFrame, symbol: str) -> float:
    if downloaded.empty:
        return float("nan")

    symbol_upper = str(symbol or "").strip().upper()
    if isinstance(downloaded.columns, pd.MultiIndex):
        available_symbols = {str(level) for level in downloaded.columns.get_level_values(0)}
        if symbol_upper in available_symbols:
            subset = downloaded.loc[:, symbol_upper]
            if isinstance(subset, pd.Series):
                subset = subset.to_frame()
            return _latest_close_from_frame(pd.DataFrame(subset))

        if "Close" in downloaded.columns.get_level_values(0):
            close_frame = downloaded.loc[:, "Close"]
            if isinstance(close_frame, pd.DataFrame) and symbol_upper in close_frame.columns:
                close_series = pd.to_numeric(close_frame[symbol_upper], errors="coerce").dropna()
                if not close_series.empty:
                    return float(close_series.iloc[-1])

    return _latest_close_from_frame(downloaded)


def _normalize_calendar_events(calendar_obj: object) -> list[MarketEvent]:
    events: list[MarketEvent] = []
    if isinstance(calendar_obj, pd.DataFrame):
        for index_value, row in calendar_obj.iterrows():
            event_name = str(index_value or "").strip() or "Earnings"
            values = row.tolist() if isinstance(row, pd.Series) else [row]
            for value in values:
                timestamp = _coerce_timestamp(value)
                if timestamp is not None:
                    events.append(MarketEvent(event_name=event_name, event_date=timestamp.normalize()))
                    break
    elif isinstance(calendar_obj, pd.Series):
        for index_value, value in calendar_obj.items():
            timestamp = _coerce_timestamp(value)
            if timestamp is not None:
                event_name = str(index_value or "").strip() or "Earnings"
                events.append(MarketEvent(event_name=event_name, event_date=timestamp.normalize()))
    elif isinstance(calendar_obj, dict):
        for key, value in calendar_obj.items():
            timestamp = _coerce_timestamp(value)
            if timestamp is not None:
                event_name = str(key or "").strip() or "Earnings"
                events.append(MarketEvent(event_name=event_name, event_date=timestamp.normalize()))
    return events


def _normalize_news_items(raw_news: object) -> list[MarketNewsItem]:
    normalized: list[MarketNewsItem] = []
    for raw_item in list(raw_news or []):
        if not isinstance(raw_item, dict):
            continue
        published_value = raw_item.get("providerPublishTime")
        content = raw_item.get("content")
        if published_value is None and isinstance(content, dict):
            published_value = content.get("pubDate") or content.get("displayTime")
        published_at = _coerce_timestamp(published_value)
        if published_at is None:
            continue
        title = str(raw_item.get("title") or "").strip()
        if not title:
            continue
        publisher = str(raw_item.get("publisher") or "").strip()
        url = str(raw_item.get("link") or raw_item.get("canonicalUrl", {}).get("url") or "").strip()
        summary = ""
        if isinstance(content, dict):
            summary = str(content.get("summary") or content.get("description") or "").strip()
            if not publisher:
                provider = content.get("provider")
                if isinstance(provider, dict):
                    publisher = str(provider.get("displayName") or "").strip()
            if not url:
                url = str(content.get("canonicalUrl", {}).get("url") or "").strip()
        normalized.append(
            MarketNewsItem(
                title=title,
                publisher=publisher,
                url=url,
                summary=summary,
                published_at=published_at,
                source_type="headline",
            )
        )
    return normalized


def _company_display_name(ticker: str) -> str:
    return str(ticker or "").strip().upper()


def _parse_google_news_items(*, ticker: str, company_name: str) -> list[MarketNewsItem]:
    query = " OR ".join(
        part
        for part in [
            f'"{str(ticker or "").strip().upper()}"',
            f'"{str(company_name or "").strip()}"' if company_name else "",
            "stock",
        ]
        if part
    )
    url = (
        f"{_GOOGLE_NEWS_ENDPOINT}?{parse.urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})}"
    )
    try:
        raw_xml = _http_get_text(url)
        root = ElementTree.fromstring(raw_xml)
    except Exception:
        return []

    company_terms = _normalize_company_terms(ticker, company_name)
    results: list[MarketNewsItem] = []
    for item in root.findall("./channel/item"):
        title = html.unescape(str(item.findtext("title") or "").strip())
        if not title:
            continue
        link = str(item.findtext("link") or "").strip()
        pub_date = _coerce_timestamp(item.findtext("pubDate"))
        if pub_date is None:
            continue
        source_node = item.find("source")
        publisher = html.unescape(str(source_node.text or "").strip()) if source_node is not None else _SOURCE_PUBLISHER_FALLBACK
        summary = ""
        article_text = ""
        try:
            if link and len(results) < _ARTICLE_FETCH_LIMIT:
                article_html = _http_get_text(link)
                article_text = _extract_article_text_from_html(article_html)
        except Exception:
            article_text = ""

        if article_text:
            summary = article_text[:280].rsplit(" ", 1)[0].strip()
        mentioned_tickers = _mentioned_terms(" ".join([title, summary, article_text]), company_terms)
        relevance_score = _compute_relevance_score(
            symbol=ticker,
            title=title,
            summary=summary,
            article_text=article_text,
            terms=company_terms,
        )
        results.append(
            MarketNewsItem(
                title=title,
                publisher=publisher,
                url=link,
                summary=summary,
                published_at=pub_date,
                article_text=article_text,
                source_type="article" if article_text else "headline",
                relevance_score=relevance_score,
                mentioned_tickers=mentioned_tickers,
            )
        )
        if len(results) >= _GOOGLE_RSS_MAX_ITEMS:
            break
    return results


def _dedupe_news_items(items: Sequence[MarketNewsItem]) -> list[MarketNewsItem]:
    deduped: list[MarketNewsItem] = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda news_item: news_item.published_at, reverse=True):
        key = _normalize_news_title(item.title) or str(item.url or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _safe_float(value: object) -> float | None:
    try:
        numeric = float(value)
    except Exception:
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _safe_int(value: object) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _normalize_option_rows(frame: pd.DataFrame) -> list[OptionContractQuote]:
    if frame.empty:
        return []
    contracts = frame.copy()
    fetched_at = pd.Timestamp.now(tz="UTC")
    required_columns = {
        "contractSymbol": "",
        "strike": np.nan,
        "bid": np.nan,
        "ask": np.nan,
        "lastPrice": np.nan,
        "impliedVolatility": np.nan,
        "volume": 0,
        "openInterest": 0,
        "inTheMoney": False,
    }
    for column_name, default_value in required_columns.items():
        if column_name not in contracts.columns:
            contracts[column_name] = default_value

    normalized: list[OptionContractQuote] = []
    for _, row in contracts.iterrows():
        contract_symbol = str(row.get("contractSymbol") or "").strip()
        if not contract_symbol:
            continue
        normalized.append(
            OptionContractQuote(
                contract_symbol=contract_symbol,
                strike=_safe_float(row.get("strike")),
                bid=_safe_float(row.get("bid")),
                ask=_safe_float(row.get("ask")),
                last_price=_safe_float(row.get("lastPrice")),
                implied_volatility=_safe_float(row.get("impliedVolatility")),
                volume=_safe_int(row.get("volume")),
                open_interest=_safe_int(row.get("openInterest")),
                in_the_money=bool(row.get("inTheMoney")),
                quote_timestamp=fetched_at,
            )
        )
    return normalized


class YFinanceMarketDataProvider(MarketDataProvider):
    @property
    def provider_name(self) -> str:
        return "yfinance"

    def download_bars(
        self,
        tickers: str | Sequence[str],
        *,
        period: str,
        interval: str,
        prepost: bool,
        group_by: str | None = None,
    ) -> pd.DataFrame:
        download_kwargs: dict[str, Any] = {
            "period": period,
            "interval": interval,
            "auto_adjust": True,
            "progress": False,
            "threads": False,
            "prepost": prepost,
        }
        if group_by:
            download_kwargs["group_by"] = group_by
        return yf.download(tickers, **download_kwargs)

    def get_contract_history(self, contract_symbol: str, *, period: str, interval: str) -> pd.DataFrame:
        return yf.Ticker(contract_symbol).history(period=period, interval=interval)

    def get_latest_prices(self, symbols: Sequence[str], *, prepost: bool) -> dict[str, float]:
        normalized_symbols = [str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()]
        if not normalized_symbols:
            return {}
        try:
            downloaded = yf.download(
                normalized_symbols,
                period="1d",
                interval="1m",
                auto_adjust=True,
                progress=False,
                threads=False,
                group_by="ticker",
                prepost=prepost,
            )
        except Exception:
            return {}
        if not isinstance(downloaded, pd.DataFrame) or downloaded.empty:
            return {}
        results: dict[str, float] = {}
        for symbol in normalized_symbols:
            value = _extract_symbol_close_from_downloaded(downloaded, symbol)
            if not math.isnan(value):
                results[symbol] = value
        return results

    def get_calendar_events(self, ticker: str) -> list[MarketEvent]:
        normalized_ticker = str(ticker or "").strip().upper()
        if normalized_ticker in _EARNINGS_UNSUPPORTED_TICKERS:
            return []
        calendar_obj = getattr(yf.Ticker(normalized_ticker), "calendar", None)
        return _normalize_calendar_events(calendar_obj)

    def get_earnings_events(self, ticker: str, *, limit: int) -> list[MarketEvent]:
        normalized_ticker = str(ticker or "").strip().upper()
        if normalized_ticker in _EARNINGS_UNSUPPORTED_TICKERS:
            return []
        try:
            earnings_df = yf.Ticker(normalized_ticker).get_earnings_dates(limit=limit)
        except Exception:
            earnings_df = None
        if not isinstance(earnings_df, pd.DataFrame) or earnings_df.empty:
            return []
        events: list[MarketEvent] = []
        for index_value in earnings_df.index.tolist():
            timestamp = _coerce_timestamp(index_value)
            if timestamp is not None:
                events.append(MarketEvent(event_name="Earnings", event_date=timestamp.normalize()))
        return events

    def get_news_items(self, ticker: str) -> list[MarketNewsItem]:
        normalized_ticker = str(ticker or "").strip().upper()
        raw_news = yf.Ticker(normalized_ticker).news or []
        yahoo_items = _normalize_news_items(raw_news)
        company_name = _company_display_name(normalized_ticker)
        google_items = _parse_google_news_items(ticker=normalized_ticker, company_name=company_name)
        company_terms = _normalize_company_terms(normalized_ticker, company_name)

        enriched_yahoo_items: list[MarketNewsItem] = []
        for item in yahoo_items:
            mentioned_tickers = _mentioned_terms(" ".join([item.title, item.summary]), company_terms)
            relevance_score = _compute_relevance_score(
                symbol=normalized_ticker,
                title=item.title,
                summary=item.summary,
                article_text="",
                terms=company_terms,
            )
            enriched_yahoo_items.append(
                MarketNewsItem(
                    title=item.title,
                    publisher=item.publisher,
                    url=item.url,
                    summary=item.summary,
                    published_at=item.published_at,
                    article_text=item.article_text,
                    source_type=item.source_type,
                    relevance_score=relevance_score,
                    mentioned_tickers=mentioned_tickers,
                )
            )
        return _dedupe_news_items([*google_items, *enriched_yahoo_items])

    def get_option_expirations(self, ticker: str) -> list[str]:
        return list(yf.Ticker(ticker).options or [])

    def get_option_chain(self, ticker: str, expiration: str) -> OptionChainSnapshot:
        chain = yf.Ticker(ticker).option_chain(expiration)
        calls = getattr(chain, "calls", pd.DataFrame())
        puts = getattr(chain, "puts", pd.DataFrame())
        return OptionChainSnapshot(
            expiration=str(expiration),
            calls=_normalize_option_rows(pd.DataFrame(calls).copy()),
            puts=_normalize_option_rows(pd.DataFrame(puts).copy()),
        )

    def get_market_state_snapshot(self, ticker: str, *, interval: str) -> MarketStateSnapshot:
        fetched_at = pd.Timestamp.now(tz="UTC").isoformat()
        resolved_interval = interval if str(interval or "").strip().lower() in {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"} else "1d"
        period = "60d" if resolved_interval != "1d" else "1y"
        try:
            downloaded = self.download_bars(
                list(_MARKET_STATE_SYMBOLS),
                period=period,
                interval=resolved_interval,
                prepost=False,
                group_by="ticker",
            )
        except Exception:
            downloaded = pd.DataFrame()
        frames = {
            symbol: _extract_symbol_frame_from_downloaded(downloaded, symbol)
            for symbol in _MARKET_STATE_SYMBOLS
        }
        benchmark_returns: list[float] = []
        benchmark_trends: list[float] = []
        for symbol in ("SPY", "QQQ", "IWM", "DIA"):
            frame = frames.get(symbol, pd.DataFrame())
            close = pd.to_numeric(frame.get("close"), errors="coerce").dropna()
            if len(close) >= 6:
                benchmark_returns.append(float(close.pct_change().iloc[-1]))
                sma20 = close.rolling(20).mean()
                trend = ((close / sma20) - 1.0).dropna()
                if not trend.empty:
                    benchmark_trends.append(float(trend.iloc[-1]))
        breadth_score = float(np.clip(np.mean([1.0 if item > 0 else -1.0 if item < 0 else 0.0 for item in benchmark_returns]), -1.0, 1.0)) if benchmark_returns else 0.0
        breadth_momentum = float(np.clip(np.mean(benchmark_returns) * 200.0, -1.0, 1.0)) if benchmark_returns else 0.0
        market_trend_score = float(np.clip(0.5 + (np.mean(benchmark_trends) * 12.0), 0.0, 1.0)) if benchmark_trends else 0.5
        dispersion_score = float(np.clip(np.std(benchmark_returns) * 200.0, 0.0, 1.0)) if benchmark_returns else 0.0

        spy_frame = frames.get("SPY", pd.DataFrame())
        spy_close = pd.to_numeric(spy_frame.get("close"), errors="coerce").dropna()
        spy_returns = spy_close.pct_change().dropna()
        realized_volatility = float(spy_returns.tail(20).std()) if len(spy_returns) >= 5 else 0.0
        realized_volatility_percentile = _rolling_percentile(spy_returns.rolling(20).std().dropna()) if len(spy_returns) >= 20 else 0.5
        opening_range_bias, opening_range_breakout = _opening_range_metrics(spy_frame)
        latest_ts = pd.to_datetime(spy_frame.index[-1], errors="coerce") if not spy_frame.empty else None
        time_of_day_bucket, time_of_day_progress = _time_of_day_context(latest_ts)

        vix_frame = frames.get("^VIX", pd.DataFrame())
        vix_close = pd.to_numeric(vix_frame.get("close"), errors="coerce").dropna()
        vix_level = float(vix_close.iloc[-1]) if not vix_close.empty else None
        vix_change_pct = float(vix_close.pct_change(5).iloc[-1]) if len(vix_close) >= 6 and not pd.isna(vix_close.pct_change(5).iloc[-1]) else 0.0
        vix_term_structure = 0.0
        if vix_level is not None and realized_volatility > 0:
            vix_term_structure = float(np.clip((vix_level / max(realized_volatility * 100.0, 1e-6)) - 1.0, -2.0, 2.0))

        return MarketStateSnapshot(
            breadth_score=round(breadth_score, 4),
            breadth_momentum=round(breadth_momentum, 4),
            market_trend_score=round(market_trend_score, 4),
            dispersion_score=round(dispersion_score, 4),
            vix_level=round(vix_level, 4) if vix_level is not None else None,
            vix_change_pct=round(vix_change_pct, 4),
            vix_term_structure=round(vix_term_structure, 4),
            realized_volatility=round(realized_volatility, 6),
            realized_volatility_percentile=round(realized_volatility_percentile, 4),
            opening_range_bias=round(opening_range_bias, 4),
            opening_range_breakout=round(opening_range_breakout, 4),
            time_of_day_bucket=time_of_day_bucket,
            time_of_day_progress=round(time_of_day_progress, 4),
            source="yfinance",
            source_detail="yfinance_proxy",
            fetched_at=fetched_at,
            freshness_status="degraded",
            cache_status="live",
            degraded=True,
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
        fetched_at = pd.Timestamp.now(tz="UTC").isoformat()
        normalized_ticker = str(ticker or "").strip().upper()
        requested_symbols = [normalized_ticker, str(benchmark_symbol or "SPY").strip().upper()]
        if sector_symbol:
            requested_symbols.append(str(sector_symbol).strip().upper())
        requested_symbols.extend(str(item or "").strip().upper() for item in peer_symbols if str(item or "").strip())
        requested_symbols = list(dict.fromkeys(symbol for symbol in requested_symbols if symbol))
        resolved_interval = interval if str(interval or "").strip().lower() in {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"} else "1d"
        period = "60d" if resolved_interval != "1d" else "1y"
        try:
            downloaded = self.download_bars(
                requested_symbols,
                period=period,
                interval=resolved_interval,
                prepost=False,
                group_by="ticker",
            )
        except Exception:
            downloaded = pd.DataFrame()

        def _ret(symbol: str, bars: int = 5) -> float:
            frame = _extract_symbol_frame_from_downloaded(downloaded, symbol)
            close = pd.to_numeric(frame.get("close"), errors="coerce").dropna()
            if len(close) <= bars:
                return 0.0
            value = close.pct_change(bars).iloc[-1]
            return float(value) if not pd.isna(value) else 0.0

        benchmark_symbol = str(benchmark_symbol or "SPY").strip().upper() or "SPY"
        sector_symbol = str(sector_symbol or "").strip().upper()
        ticker_ret = _ret(normalized_ticker)
        benchmark_ret = _ret(benchmark_symbol)
        sector_ret = _ret(sector_symbol) if sector_symbol else 0.0
        benchmark_relative = ticker_ret - benchmark_ret
        sector_relative = ticker_ret - sector_ret if sector_symbol else benchmark_relative
        residual_return = ticker_ret - ((benchmark_ret * 0.65) + (sector_ret * 0.35 if sector_symbol else 0.0))

        peer_returns = []
        for peer in requested_symbols:
            if peer == normalized_ticker:
                continue
            peer_returns.append((peer, _ret(peer)))
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
            benchmark_symbol=benchmark_symbol,
            sector_symbol=sector_symbol,
            benchmark_relative_return=round(benchmark_relative, 6),
            sector_relative_return=round(sector_relative, 6),
            residual_return=round(residual_return, 6),
            peer_momentum_rank=round(peer_rank, 4),
            peer_count=max(len(peer_returns), 0),
            relative_strength_score=round(relative_strength_score, 4),
            source="yfinance",
            source_detail="yfinance_proxy",
            fetched_at=fetched_at,
            freshness_status="degraded",
            cache_status="live",
            degraded=True,
        )

    def get_options_flow_snapshot(
        self,
        ticker: str,
        *,
        underlying_price: float | None = None,
        expiration: str | None = None,
    ) -> OptionsFlowSnapshot:
        fetched_at = pd.Timestamp.now(tz="UTC").isoformat()
        normalized_ticker = str(ticker or "").strip().upper()
        try:
            ticker_obj = yf.Ticker(normalized_ticker)
            expirations = list(ticker_obj.options or [])
        except Exception:
            expirations = []
            ticker_obj = yf.Ticker(normalized_ticker)
        if not expirations:
            return OptionsFlowSnapshot(
                source="yfinance",
                source_detail="yfinance_proxy",
                fetched_at=fetched_at,
                freshness_status="degraded",
                cache_status="live",
                degraded=True,
            )
        resolved_expiration = str(expiration or expirations[0])
        try:
            chain = ticker_obj.option_chain(resolved_expiration)
        except Exception:
            return OptionsFlowSnapshot(
                source="yfinance",
                source_detail="yfinance_proxy",
                fetched_at=fetched_at,
                freshness_status="degraded",
                cache_status="live",
                degraded=True,
            )
        calls = pd.DataFrame(getattr(chain, "calls", pd.DataFrame())).copy()
        puts = pd.DataFrame(getattr(chain, "puts", pd.DataFrame())).copy()
        if calls.empty and puts.empty:
            return OptionsFlowSnapshot(
                source="yfinance",
                source_detail="yfinance_proxy",
                fetched_at=fetched_at,
                freshness_status="degraded",
                cache_status="live",
                degraded=True,
            )
        spot = float(underlying_price or 0.0)
        if spot <= 0:
            spot_frame = _safe_history(normalized_ticker, period="10d", interval="5m")
            spot = _latest_close_from_frame(spot_frame)
        for frame in (calls, puts):
            for column in ("strike", "impliedVolatility", "volume", "openInterest"):
                frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
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

        underlying_frame = _safe_history(normalized_ticker, period="60d", interval="1d")
        realized_returns = pd.to_numeric(underlying_frame.get("Close"), errors="coerce").pct_change().dropna() if not underlying_frame.empty else pd.Series(dtype=float)
        realized_vol = float(realized_returns.tail(20).std() * math.sqrt(252)) if len(realized_returns) >= 5 else 0.0
        iv_realized_vol_spread = float(implied_volatility - realized_vol)
        iv_percentile = float(np.clip(0.5 + (iv_realized_vol_spread * 1.5), 0.0, 1.0))
        iv_rank = iv_percentile
        return OptionsFlowSnapshot(
            implied_volatility=round(implied_volatility, 6),
            iv_rank=round(iv_rank, 4),
            iv_percentile=round(iv_percentile, 4),
            iv_realized_vol_spread=round(iv_realized_vol_spread, 6),
            put_call_open_interest_ratio=round(put_call_oi_ratio, 4),
            call_volume_pressure=round(call_volume_pressure, 4),
            put_volume_pressure=round(put_volume_pressure, 4),
            net_flow_pressure=round(net_flow_pressure, 4),
            skew_score=round(skew_score, 4),
            unusual_volume_score=round(unusual_volume_score, 4),
            source="yfinance",
            source_detail="yfinance_proxy",
            fetched_at=fetched_at,
            freshness_status="degraded",
            cache_status="live",
            degraded=True,
        )

    def get_event_revision_snapshot(self, ticker: str) -> EventRevisionSnapshot:
        fetched_at = pd.Timestamp.now(tz="UTC").isoformat()
        normalized_ticker = str(ticker or "").strip().upper()
        if normalized_ticker in _EARNINGS_UNSUPPORTED_TICKERS:
            return EventRevisionSnapshot(
                days_to_earnings=None,
                event_pressure=0.0,
                analyst_revision_score=0.0,
                target_revision_score=0.0,
                estimate_revision_score=0.0,
                macro_sensitivity=0.0,
                revision_confidence=0.0,
                source="yfinance",
                source_detail="unsupported_index_or_etf",
                fetched_at=fetched_at,
            )
        days_to_earnings = None
        try:
            earnings_events = self.get_earnings_events(normalized_ticker, limit=4)
        except Exception:
            earnings_events = []
        if earnings_events:
            now = pd.Timestamp.now(tz="UTC").normalize()
            future = [item for item in earnings_events if item.event_date.normalize() >= now]
            if future:
                days_to_earnings = int((future[0].event_date.normalize() - now).days)
        try:
            info = yf.Ticker(normalized_ticker).info or {}
        except Exception:
            info = {}
        recommendation_mean = float(info.get("recommendationMean") or 0.0) if info.get("recommendationMean") is not None else 0.0
        target_mean = float(info.get("targetMeanPrice") or 0.0) if info.get("targetMeanPrice") is not None else 0.0
        current_price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0.0) if info else 0.0
        analyst_revision_score = 0.0
        if recommendation_mean > 0:
            analyst_revision_score = float(np.clip((3.0 - recommendation_mean) / 2.0, -1.0, 1.0))
        target_revision_score = 0.0
        if current_price > 0 and target_mean > 0:
            target_revision_score = float(np.clip((target_mean / current_price) - 1.0, -1.0, 1.0))
        estimate_revision_score = 0.0
        earnings_growth = info.get("earningsQuarterlyGrowth")
        if isinstance(earnings_growth, (int, float)) and not math.isnan(float(earnings_growth)):
            estimate_revision_score = float(np.clip(float(earnings_growth), -1.0, 1.0))
        beta = info.get("beta")
        macro_sensitivity = float(np.clip(abs(float(beta)), 0.0, 2.0) / 2.0) if isinstance(beta, (int, float)) else 0.0
        event_pressure = 0.0
        if days_to_earnings is not None:
            event_pressure = float(np.clip((5 - max(days_to_earnings, 0)) / 5.0, 0.0, 1.0))
        revision_confidence = float(
            np.clip(
                (0.4 if recommendation_mean > 0 else 0.0)
                + (0.3 if target_mean > 0 and current_price > 0 else 0.0)
                + (0.3 if isinstance(earnings_growth, (int, float)) else 0.0),
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
            source="yfinance",
            source_detail="yfinance_proxy",
            fetched_at=fetched_at,
            freshness_status="degraded",
            cache_status="live",
            degraded=True,
        )
