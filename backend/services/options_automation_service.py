from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.saas import DomainEventLog, OptionAutomationScanRun, OrderEventRecord
from backend.schemas import (
    AnalyzeRequest,
    CloseTradeRequest,
    OpenTradeRequest,
    OptionsAutomationCloseRequest,
    OptionsAutomationExecuteRequest,
    OptionsAutomationRefreshRequest,
    OptionsAutomationScanRequest,
)
from backend.services.exceptions import NotFoundError, ValidationError
from backend.services.execution.alpaca_client import build_alpaca_paper_client
from backend.services.execution.tradier_client import (
    TradierApiError,
    build_tradier_live_market_data_client,
    build_tradier_paper_client,
    normalize_tradier_balances,
    normalize_tradier_option_contract,
)
from backend.services.market_data.hybrid_adapter import AlpacaMarketDataClient
from backend.services.market_service import analyze_market
from backend.services.options_validation_service import export_options_validation
from backend.services.serialization import serialize_value
from backend.services.strategy_engine.events import record_domain_event
from backend.services.tenant_service import _resolve_tenant_for_current_user
from backend.services.trade_automation_service import _refresh_option_quote_for_close, get_tenant_trade_automation_snapshot
from backend.services.trade_service import (
    _scoped_closed_trades,
    _scoped_open_trades,
    _scoped_pending_orders,
    close_trade_from_request,
    open_trade_from_request,
    resolve_trade_identifier,
    sync_pending_orders_from_broker,
)

DEFAULT_OPTIONS_TICKERS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]
SUPPORTED_OPTION_RIGHTS = {"call", "put"}
OCC_SYMBOL_PATTERN = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<date>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")
ENTRY_EVENT_TYPES = {"options.paper_entry_submitted", "options.paper_entry_blocked"}
REFRESH_EVENT_TYPES = {"options.position_quote_refreshed", "options.position_quote_refresh_blocked"}
EXIT_EVENT_TYPES = {"options.paper_exit_submitted", "options.paper_exit_blocked"}
SCAN_EVENT_TYPES = {"options.scan_completed", "options.scan_blocked"}
SYNC_EVENT_TYPES = {"options.lifecycle_synced"}
LIFECYCLE_EVENT_TYPES = ENTRY_EVENT_TYPES | REFRESH_EVENT_TYPES | EXIT_EVENT_TYPES | SCAN_EVENT_TYPES | SYNC_EVENT_TYPES
REQUIRED_CLEAN_SCHEDULED_OPTION_CYCLES = 5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | pd.Timestamp | None) -> str | None:
    if value is None:
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").isoformat()


def _coerce_float(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _coerce_int(value: Any) -> int:
    numeric = _coerce_float(value)
    return int(numeric or 0)


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_right(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"call", "c"}:
        return "call"
    if normalized in {"put", "p"}:
        return "put"
    return None


def _parse_occ_contract_symbol(contract_symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(contract_symbol)
    match = OCC_SYMBOL_PATTERN.match(normalized)
    if not match:
        return {}
    date_token = match.group("date")
    expiration = f"20{date_token[:2]}-{date_token[2:4]}-{date_token[4:6]}"
    right = "call" if match.group("right") == "C" else "put"
    strike = int(match.group("strike")) / 1000.0
    return {
        "underlying": match.group("root"),
        "expiration": expiration,
        "right": right,
        "strike": strike,
    }


def _parse_timestamp(value: Any) -> pd.Timestamp | None:
    if value in (None, "", 0):
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed


def _extract_option_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    snapshots = payload.get("snapshots")
    if isinstance(snapshots, dict):
        rows: list[dict[str, Any]] = []
        for symbol, snapshot in snapshots.items():
            if isinstance(snapshot, dict):
                rows.append({"symbol": symbol, **snapshot})
        return rows
    results = payload.get("results")
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    if isinstance(results, dict):
        return [{"symbol": symbol, **item} for symbol, item in results.items() if isinstance(item, dict)]
    return []


def _nested_dict(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = row.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _extract_contract_candidate(
    *,
    ticker: str,
    row: dict[str, Any],
    underlying_price: float | None,
    required_right: str | None,
    feed: str,
    scan_started_at: datetime,
) -> dict[str, Any] | None:
    details = _nested_dict(row, "details", "contract", "option_contract")
    latest_quote = _nested_dict(row, "latestQuote", "latest_quote", "quote", "latest_quote_data")
    latest_trade = _nested_dict(row, "latestTrade", "latest_trade", "trade", "latest_trade_data")
    daily_bar = _nested_dict(row, "dailyBar", "daily_bar", "day", "day_bar")
    greeks = _nested_dict(row, "greeks")

    contract_symbol = _normalize_symbol(
        row.get("symbol")
        or row.get("contract_symbol")
        or row.get("contractSymbol")
        or details.get("symbol")
        or details.get("ticker")
    )
    if not contract_symbol:
        return None
    parsed = _parse_occ_contract_symbol(contract_symbol)
    right = _normalize_right(
        details.get("type")
        or details.get("contract_type")
        or row.get("type")
        or row.get("contract_type")
        or parsed.get("right")
    )
    if right not in SUPPORTED_OPTION_RIGHTS:
        return None
    expiration = str(
        details.get("expiration_date")
        or details.get("expiration")
        or row.get("expiration_date")
        or row.get("expiration")
        or parsed.get("expiration")
        or ""
    ).strip()
    strike = _coerce_float(
        details.get("strike_price")
        or details.get("strike")
        or row.get("strike_price")
        or row.get("strike")
        or parsed.get("strike")
    )
    bid = _coerce_float(latest_quote.get("bp") or latest_quote.get("bid_price") or latest_quote.get("bid"))
    ask = _coerce_float(latest_quote.get("ap") or latest_quote.get("ask_price") or latest_quote.get("ask"))
    last_price = _coerce_float(
        latest_trade.get("p")
        or latest_trade.get("price")
        or row.get("lastPrice")
        or row.get("last_price")
        or daily_bar.get("c")
    )
    mid = ((bid + ask) / 2.0) if bid is not None and ask is not None and (bid + ask) > 0 else last_price
    if mid is None or mid <= 0:
        return None
    spread_pct = ((ask - bid) / mid) if bid is not None and ask is not None and mid > 0 else None
    quote_ts = _parse_timestamp(
        latest_quote.get("t")
        or latest_quote.get("timestamp")
        or latest_quote.get("last_updated")
        or latest_quote.get("updated_at")
        or row.get("quote_timestamp")
    )
    quote_age_seconds = None
    if quote_ts is not None:
        quote_age_seconds = max(0.0, round(float((pd.Timestamp(scan_started_at) - quote_ts).total_seconds()), 3))
    volume = _coerce_int(daily_bar.get("v") or daily_bar.get("volume") or row.get("volume"))
    open_interest = _coerce_int(row.get("open_interest") or row.get("openInterest") or details.get("open_interest"))
    implied_volatility = _coerce_float(
        row.get("impliedVolatility")
        or row.get("implied_volatility")
        or greeks.get("iv")
        or greeks.get("implied_volatility")
    )

    dte_days = None
    if expiration:
        expiration_ts = pd.to_datetime(expiration, errors="coerce", utc=True)
        if not pd.isna(expiration_ts):
            dte_days = int((expiration_ts.normalize() - pd.Timestamp(scan_started_at).normalize()).days)
    distance_pct = None
    if underlying_price and strike:
        distance_pct = abs(float(strike) - float(underlying_price)) / max(float(underlying_price), 0.01)

    rejection_reasons: list[str] = []
    if required_right and right != required_right:
        rejection_reasons.append(f"Signal requires {required_right}.")
    normalized_feed = str(feed or "").strip().lower()
    if normalized_feed not in {"opra", "tradier_realtime"}:
        rejection_reasons.append("Real-time options quotes are required for automation.")
    if quote_age_seconds is None:
        rejection_reasons.append("Missing option quote timestamp.")
    elif quote_age_seconds > settings.options_quote_max_age_seconds:
        rejection_reasons.append("Option quote is stale.")
    if spread_pct is None:
        rejection_reasons.append("Missing bid/ask spread.")
    elif spread_pct > settings.options_max_spread_pct:
        rejection_reasons.append("Option spread is too wide.")
    if volume < settings.options_min_volume:
        rejection_reasons.append("Option volume is too low.")
    if open_interest < settings.options_min_open_interest:
        rejection_reasons.append("Option open interest is too low.")
    if dte_days is None:
        rejection_reasons.append("Missing option expiration.")
    elif dte_days < settings.options_min_dte_days or dte_days > settings.options_max_dte_days:
        rejection_reasons.append("Option expiration is outside the automation DTE window.")

    premium_notional = float((ask if ask is not None and ask > 0 else mid) * 100.0)
    selection_score = (
        float(distance_pct if distance_pct is not None else 1.0) * 1.5
        + float(spread_pct if spread_pct is not None else 1.0)
        + (float(quote_age_seconds or 9999.0) / max(float(settings.options_quote_max_age_seconds), 1.0)) * 0.15
        - min(float(volume + open_interest) / 100000.0, 0.25)
    )
    entry_limit_price = min(float(ask), round(float(mid), 2)) if ask is not None and ask > 0 else round(float(mid), 2)
    exit_reference_price = float(bid) if bid is not None and bid > 0 else round(float(mid), 2)

    return {
        "underlying": _normalize_symbol(ticker),
        "contract_symbol": contract_symbol,
        "right": right,
        "expiration": expiration or None,
        "strike": strike,
        "dte_days": dte_days,
        "bid": bid,
        "ask": ask,
        "mid": float(mid),
        "last_price": last_price,
        "entry_limit_price": round(float(entry_limit_price), 2),
        "exit_reference_price": round(float(exit_reference_price), 2),
        "spread_pct": spread_pct,
        "volume": volume,
        "open_interest": open_interest,
        "implied_volatility": implied_volatility,
        "quote_timestamp": _iso(quote_ts),
        "quote_age_seconds": quote_age_seconds,
        "underlying_price": underlying_price,
        "distance_pct": distance_pct,
        "premium_notional": round(premium_notional, 2),
        "feed": feed,
        "source": "tradier_options_chain" if normalized_feed == "tradier_realtime" else "alpaca_options_chain",
        "ready_to_execute": not rejection_reasons,
        "rejection_reasons": rejection_reasons,
        "selection_score": round(float(selection_score), 6),
    }


def _build_alpaca_options_client() -> AlpacaMarketDataClient:
    base_url = "https://data.sandbox.alpaca.markets" if settings.alpaca_use_sandbox else "https://data.alpaca.markets"
    return AlpacaMarketDataClient(
        api_key_id=settings.alpaca_api_key_id,
        api_secret_key=settings.alpaca_api_secret_key,
        base_url=base_url,
        feed=settings.alpaca_stock_feed,
        timeout_seconds=settings.alpaca_market_data_request_timeout_seconds,
    )


def _options_data_provider() -> str:
    provider = str(getattr(settings, "options_data_provider", "free_delayed") or "free_delayed").strip().lower()
    return provider if provider in {"free_delayed", "alpaca", "tradier"} else "free_delayed"


def _options_broker_provider() -> str:
    provider = str(getattr(settings, "options_broker_provider", "internal") or "internal").strip().lower()
    if provider in {"internal", "internal_paper", "internal_simulator"}:
        return "internal"
    return provider if provider in {"alpaca", "tradier"} else "internal"


def _options_feed_label() -> str:
    data_provider = _options_data_provider()
    if data_provider == "free_delayed":
        return "free_delayed"
    return "tradier_realtime" if data_provider == "tradier" else str(settings.alpaca_options_feed or "opra").strip().lower() or "opra"


def _options_data_source_label() -> str:
    data_provider = _options_data_provider()
    if data_provider == "free_delayed":
        return "free_delayed_options_research"
    return "tradier_options_chain" if data_provider == "tradier" else "alpaca_options_chain"


def _options_feed_required_label() -> str:
    data_provider = _options_data_provider()
    if data_provider == "tradier":
        return "tradier_realtime"
    if data_provider == "alpaca":
        return "opra"
    return "licensed_realtime_options_feed"


def _account_summary() -> dict[str, Any]:
    if _options_broker_provider() == "internal":
        return {
            "effective_funds": 100_000.0,
            "funds_source": "internal_simulated_balance",
            "equity": 100_000.0,
            "cash": 100_000.0,
            "buying_power": 100_000.0,
            "option_buying_power": 100_000.0,
            "provider": "internal_paper",
        }
    if _options_broker_provider() == "tradier":
        client = build_tradier_paper_client()
        balances = normalize_tradier_balances(client.get_account_balances())
        funds = balances.get("option_buying_power") or balances.get("buying_power") or balances.get("cash") or balances.get("equity")
        return {
            "effective_funds": float(funds or 0.0),
            "funds_source": "tradier_option_buying_power" if balances.get("option_buying_power") is not None else "tradier_paper_balance",
            "equity": balances.get("equity"),
            "cash": balances.get("cash"),
            "buying_power": balances.get("buying_power"),
            "option_buying_power": balances.get("option_buying_power"),
            "provider": "tradier",
        }
    client = build_alpaca_paper_client()
    account = client.get_account()
    funds = None
    funds_source = None
    for key in ("equity", "portfolio_value", "cash", "buying_power"):
        funds = _coerce_float(account.get(key))
        if funds is not None and funds > 0:
            funds_source = key
            break
    return {
        "effective_funds": funds or 0.0,
        "funds_source": funds_source or "unavailable",
        "equity": _coerce_float(account.get("equity")),
        "portfolio_value": _coerce_float(account.get("portfolio_value")),
        "cash": _coerce_float(account.get("cash")),
        "buying_power": _coerce_float(account.get("buying_power")),
        "provider": "alpaca",
    }


def _automation_settings(db: Session, current_user: Any) -> dict[str, Any]:
    try:
        snapshot = get_tenant_trade_automation_snapshot(db, current_user=current_user, scope="personal_paper")
    except Exception:
        snapshot = {}
    return dict(snapshot.get("settings") or {})


def _resolve_scan_tickers(db: Session, current_user: Any, request: OptionsAutomationScanRequest | None) -> list[str]:
    if request and request.tickers:
        tickers = request.tickers
    else:
        settings_state = _automation_settings(db, current_user)
        tickers = list(settings_state.get("tickers") or DEFAULT_OPTIONS_TICKERS)
    normalized: list[str] = []
    seen: set[str] = set()
    for ticker in tickers:
        symbol = _normalize_symbol(ticker)
        if symbol and symbol not in seen:
            normalized.append(symbol)
            seen.add(symbol)
    return normalized or list(DEFAULT_OPTIONS_TICKERS)


def _resolve_scan_interval(db: Session, current_user: Any) -> int:
    configured = max(int(settings.options_scan_interval_seconds or 30), 5)
    settings_state = _automation_settings(db, current_user)
    trading_engine_interval = int(settings_state.get("cycle_interval_seconds") or 60)
    return min(configured, max(trading_engine_interval, 5))


def _signal_right_for_ticker(ticker: str, current_user: Any) -> tuple[str | None, dict[str, Any]]:
    analysis = analyze_market(
        AnalyzeRequest(
            ticker=ticker,
            interval="5m",
            horizon=5,
            include_history=False,
            include_live_price=True,
            include_contract_lookup=False,
            include_event_lookup=True,
            include_alignment=True,
            use_fast_model=False,
        ),
        current_user=current_user,
    )
    report = dict(analysis.get("report") or {})
    verdict = str(report.get("verdict") or "").strip().upper()
    if verdict == "BULLISH":
        return "call", analysis
    if verdict == "BEARISH":
        return "put", analysis
    return None, analysis


def _fetch_candidates_for_ticker(
    *,
    client: AlpacaMarketDataClient,
    ticker: str,
    current_user: Any,
    feed: str,
    scan_started_at: datetime,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    blockers: list[str] = []
    required_right: str | None = None
    analysis: dict[str, Any] = {}
    try:
        required_right, analysis = _signal_right_for_ticker(ticker, current_user)
    except Exception as exc:
        blockers.append(f"Signal unavailable: {exc}")
    underlying_price = _coerce_float(analysis.get("live_price")) or _coerce_float((analysis.get("report") or {}).get("close"))
    if underlying_price is None:
        try:
            underlying_price = client.get_latest_prices([ticker]).get(ticker)
        except Exception:
            underlying_price = None

    payloads: list[dict[str, Any]] = []
    next_page_token = None
    while True:
        payload = client.get_option_chain_snapshots(
            ticker,
            feed=feed,
            limit=500,
            page_token=next_page_token,
        )
        payloads.append(payload)
        next_page_token = str(payload.get("next_page_token") or "").strip() if isinstance(payload, dict) else ""
        if not next_page_token:
            break

    candidates: list[dict[str, Any]] = []
    for payload in payloads:
        for row in _extract_option_rows(payload):
            candidate = _extract_contract_candidate(
                ticker=ticker,
                row=row,
                underlying_price=underlying_price,
                required_right=required_right,
                feed=feed,
                scan_started_at=scan_started_at,
            )
            if candidate is not None:
                candidate["signal_right"] = required_right
                candidates.append(candidate)
    return candidates, blockers, {"signal_right": required_right, "underlying_price": underlying_price}


def _expiration_in_dte_window(expiration: str, scan_started_at: datetime) -> bool:
    expiration_ts = pd.to_datetime(expiration, errors="coerce", utc=True)
    if pd.isna(expiration_ts):
        return False
    dte_days = int((expiration_ts.normalize() - pd.Timestamp(scan_started_at).normalize()).days)
    return int(settings.options_min_dte_days or 0) <= dte_days <= int(settings.options_max_dte_days or 0)


def _fetch_tradier_candidates_for_ticker(
    *,
    client: Any,
    ticker: str,
    current_user: Any,
    feed: str,
    scan_started_at: datetime,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    blockers: list[str] = []
    required_right: str | None = None
    analysis: dict[str, Any] = {}
    try:
        required_right, analysis = _signal_right_for_ticker(ticker, current_user)
    except Exception as exc:
        blockers.append(f"Signal unavailable: {exc}")

    underlying_price = _coerce_float(analysis.get("live_price")) or _coerce_float((analysis.get("report") or {}).get("close"))
    if underlying_price is None:
        try:
            quotes = client.get_quotes([ticker])
            if quotes:
                underlying_price = _coerce_float(quotes[0].get("last") or quotes[0].get("bid") or quotes[0].get("ask"))
        except Exception:
            underlying_price = None

    expirations = [
        item
        for item in client.get_option_expirations(ticker)
        if _expiration_in_dte_window(item, scan_started_at)
    ][:6]
    if not expirations:
        blockers.append("No Tradier option expirations are inside the configured DTE window.")

    candidates: list[dict[str, Any]] = []
    for expiration in expirations:
        for row in client.get_option_chain(ticker, expiration, greeks=True):
            candidate = _extract_contract_candidate(
                ticker=ticker,
                row=normalize_tradier_option_contract(row),
                underlying_price=underlying_price,
                required_right=required_right,
                feed=feed,
                scan_started_at=scan_started_at,
            )
            if candidate is not None:
                candidate["signal_right"] = required_right
                candidates.append(candidate)
    return candidates, blockers, {"signal_right": required_right, "underlying_price": underlying_price, "expirations": expirations}


def _serialize_scan_run(row: OptionAutomationScanRun | None, *, include_candidates: bool = True) -> dict[str, Any]:
    if row is None:
        return {
            "latest_scan_run_id": None,
            "status": "idle",
            "feed": _options_feed_label(),
            "scan_interval_seconds": int(settings.options_scan_interval_seconds or 30),
            "ticker_count": 0,
            "candidate_count": 0,
            "ready_candidate_count": 0,
            "blocked_reason": None,
            "summary": {},
            "candidates": [],
            "created_at": None,
            "updated_at": None,
        }
    candidates = list((row.candidates_json or {}).get("items") or []) if include_candidates else []
    payload = {
        "latest_scan_run_id": row.id,
        "status": row.status,
        "feed": row.feed,
        "scan_interval_seconds": int(row.scan_interval_seconds or 0),
        "ticker_count": int(row.ticker_count or 0),
        "candidate_count": int(row.candidate_count or 0),
        "ready_candidate_count": int(row.ready_candidate_count or 0),
        "blocked_reason": row.blocked_reason,
        "requested_tickers": list((row.requested_tickers_json or {}).get("tickers") or []),
        "summary": dict(row.summary_json or {}),
        "candidates": candidates,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if not include_candidates:
        payload["candidates_omitted"] = True
    return payload


def _scan_run_matches_active_provider(row: OptionAutomationScanRun | None) -> bool:
    if row is None:
        return False
    active_feed = _options_feed_label()
    if str(row.feed or "").strip().lower() != active_feed:
        return False
    summary = dict(row.summary_json or {})
    data_provider = str(summary.get("options_data_provider") or "").strip().lower()
    if data_provider and data_provider != _options_data_provider():
        return False
    data_source = str(summary.get("data_source") or "").strip().lower()
    if data_source and data_source != _options_data_source_label():
        return False
    return True


def _active_scan_summary_defaults() -> dict[str, Any]:
    data_provider = _options_data_provider()
    return {
        "scope": "personal_paper",
        "execution_intent": "internal_paper",
        "data_source": _options_data_source_label(),
        "feed": _options_feed_label(),
        "feed_required": _options_feed_required_label(),
        "options_data_provider": data_provider,
        "options_broker_provider": _options_broker_provider(),
        "broker_mode": "internal_paper" if _options_broker_provider() == "internal" else "broker_paper",
        "licensed_realtime_options_data": bool(getattr(settings, "licensed_realtime_options_data", False)),
        "options_automation_ready": False,
    }


def _options_data_blocked_reason(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "licensed_realtime_options_feed_required":
        return "Licensed real-time options data is required before options auto-entry, auto-exit, or current bid/ask liquidity gates can be marked ready."
    if normalized == "credentials_missing":
        if _options_data_provider() == "tradier":
            return "Tradier live options data credentials are missing. Real-time options data is required for paper options automation."
        return "Options data credentials are missing. Real-time options data is required for paper options automation."
    if normalized == "broker_credentials_missing":
        return "Tradier paper execution credentials are missing. Paper options lifecycle evidence requires sandbox order access."
    if normalized == "stale_quotes":
        return "Options quotes are stale. Fresh real-time options quotes are required for paper options automation."
    if normalized == "blocked_wrong_feed":
        return "The configured options feed is not valid for paper options automation."
    if normalized == "not_entitled":
        return "The configured options data account is not entitled for real-time options data."
    return "Real-time options data is not ready for paper options automation."


def _build_policy_snapshot() -> dict[str, Any]:
    return {
        "scope": "personal_paper",
        "execution_intent": "internal_paper" if _options_broker_provider() == "internal" else "broker_paper",
        "instrument_type": "listed_option",
        "option_strategy": "long_option",
        "supported_rights": sorted(SUPPORTED_OPTION_RIGHTS),
        "limit_orders_only": True,
        "live_routing_enabled": False,
        "brokerage_linked_routing_enabled": False if _options_broker_provider() == "internal" else _options_broker_provider() == "tradier",
        "broker_provider": _options_broker_provider(),
        "broker_mode": "internal_paper" if _options_broker_provider() == "internal" else "broker_paper",
        "data_provider": _options_data_provider(),
        "feed_required": _options_feed_required_label(),
        "licensed_realtime_options_data": bool(getattr(settings, "licensed_realtime_options_data", False)),
        "short_premium_enabled": False,
        "spreads_enabled": False,
        "quote_max_age_seconds": int(settings.options_quote_max_age_seconds or 30),
        "max_spread_pct": float(settings.options_max_spread_pct or 0.15),
        "min_volume": int(settings.options_min_volume or 25),
        "min_open_interest": int(settings.options_min_open_interest or 100),
        "min_dte_days": int(settings.options_min_dte_days or 7),
        "max_dte_days": int(settings.options_max_dte_days or 45),
    }


def _serialize_domain_event_row(row: DomainEventLog | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "event_type": row.event_type,
        "aggregate_type": row.aggregate_type,
        "aggregate_id": row.aggregate_id,
        "status": row.status,
        "payload": serialize_value(dict(row.payload_json or {})),
        "metadata": serialize_value(dict(row.metadata_json or {})),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _serialize_order_event_row(row: OrderEventRecord | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "trade_id": row.trade_id,
        "ticker": row.ticker,
        "event_key": row.event_key,
        "status": row.status,
        "detail": row.detail,
        "payload": serialize_value(dict(row.payload_json or {})),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _event_payload(row: DomainEventLog | None) -> dict[str, Any]:
    return dict(getattr(row, "payload_json", None) or {})


def _event_metadata(row: DomainEventLog | None) -> dict[str, Any]:
    return dict(getattr(row, "metadata_json", None) or {})


def _event_trigger(row: DomainEventLog | None) -> str:
    payload = _event_payload(row)
    metadata = _event_metadata(row)
    return str(payload.get("automation_trigger") or metadata.get("automation_trigger") or "manual").strip().lower() or "manual"


def _cycle_key_from_payload(payload: dict[str, Any], row: DomainEventLog | None = None) -> tuple[str | None, str | None, str | None]:
    trade_id = str(payload.get("trade_id") or getattr(row, "aggregate_id", None) or "").strip() or None
    contract_symbol = _normalize_symbol(payload.get("contract_symbol")) or None
    if trade_id:
        return f"trade:{trade_id}", trade_id, contract_symbol
    if contract_symbol:
        return f"contract:{contract_symbol}", None, contract_symbol
    return None, trade_id, contract_symbol


def _event_created_at(row: DomainEventLog | None) -> pd.Timestamp | None:
    return _parse_timestamp(getattr(row, "created_at", None))


def _max_timestamp(*values: Any) -> pd.Timestamp | None:
    latest: pd.Timestamp | None = None
    for value in values:
        parsed = _parse_timestamp(value)
        if parsed is None:
            continue
        latest = parsed if latest is None else max(latest, parsed)
    return latest


def _load_lifecycle_events(db: Session, tenant_id: str, *, limit: int = 250) -> list[DomainEventLog]:
    return list(
        db.execute(
            select(DomainEventLog)
            .where(DomainEventLog.tenant_id == tenant_id, DomainEventLog.event_type.in_(sorted(LIFECYCLE_EVENT_TYPES)))
            .order_by(DomainEventLog.created_at.desc())
            .limit(limit)
        ).scalars()
    )


def _load_option_order_events(db: Session, tenant_id: str, *, limit: int = 500) -> list[OrderEventRecord]:
    return list(
        db.execute(
            select(OrderEventRecord)
            .where(OrderEventRecord.tenant_id == tenant_id)
            .order_by(OrderEventRecord.created_at.desc())
            .limit(limit)
        ).scalars()
    )


def _latest_event_snapshot(
    events: list[DomainEventLog],
    allowed_types: set[str],
    *,
    automation_trigger: str | None = None,
) -> dict[str, Any] | None:
    for row in events:
        if row.event_type in allowed_types and (automation_trigger is None or _event_trigger(row) == automation_trigger):
            return _serialize_domain_event_row(row)
    return None


def _option_position_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    instrument_series = frame.get("instrument_type", pd.Series("", index=frame.index)).astype(str).str.lower()
    return frame.loc[instrument_series.eq("listed_option")]


def _option_open_positions(current_user: Any) -> pd.DataFrame:
    return _option_position_frame(_scoped_open_trades(current_user))


def _option_pending_orders(current_user: Any) -> pd.DataFrame:
    return _option_position_frame(_scoped_pending_orders(current_user))


def _option_closed_positions(current_user: Any) -> pd.DataFrame:
    return _option_position_frame(_scoped_closed_trades(current_user))


def _frame_lookup(frame: pd.DataFrame, *, column: str) -> dict[str, list[dict[str, Any]]]:
    if frame.empty or column not in frame.columns:
        return {}
    lookup: dict[str, list[dict[str, Any]]] = {}
    for row in frame.to_dict(orient="records"):
        key = str(row.get(column) or "").strip()
        if not key:
            continue
        lookup.setdefault(key, []).append(row)
    return lookup


def _order_event_lookup(order_events: list[OrderEventRecord], *, key: str) -> dict[str, list[OrderEventRecord]]:
    lookup: dict[str, list[OrderEventRecord]] = {}
    for row in order_events:
        raw_value = getattr(row, key, None)
        value = str(raw_value or "").strip()
        if not value and key == "order_id":
            payload = dict(row.payload_json or {})
            trade = dict(payload.get("trade") or {})
            synced_order = dict(payload.get("synced_order") or {})
            value = str(
                payload.get("order_id")
                or payload.get("broker_order_id")
                or trade.get("order_id")
                or trade.get("broker_order_id")
                or synced_order.get("order_id")
                or synced_order.get("broker_order_id")
                or ""
            ).strip()
        if not value:
            continue
        lookup.setdefault(value, []).append(row)
    return lookup


def _latest_payloads_by_key(events: list[DomainEventLog], allowed_types: set[str]) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.event_type not in allowed_types:
            continue
        payload = _event_payload(event)
        cycle_key, trade_id, contract_symbol = _cycle_key_from_payload(payload, event)
        for key in (
            cycle_key or "",
            trade_id or "",
            contract_symbol or "",
        ):
            if key and key not in payloads:
                payloads[key] = payload
    return payloads


def _is_terminal_order_status(value: Any) -> bool:
    return str(value or "").strip().lower() in {"filled", "closed", "canceled", "cancelled", "rejected", "expired"}


def _has_quote_quality_block(payload: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            str(payload.get("reason") or ""),
            str(payload.get("detail") or ""),
            str(payload.get("sell_block_reason") or ""),
            str(payload.get("sell_block_detail") or ""),
        ]
    ).strip().lower()
    return any(token in haystack for token in ("stale", "wide", "missing", "quote"))


def _readiness_label(readiness_state: str) -> str:
    mapping = {
        "blocked": "blocked",
        "collecting_lifecycle_evidence": "collecting lifecycle evidence",
        "ready": "ready",
    }
    return mapping.get(str(readiness_state or "").strip().lower(), "collecting lifecycle evidence")


def _build_options_validation_artifact(
    *,
    snapshot: dict[str, Any],
    events: list[DomainEventLog],
    order_events: list[OrderEventRecord],
    open_positions: list[dict[str, Any]],
    open_frame: pd.DataFrame,
    pending_frame: pd.DataFrame,
    closed_frame: pd.DataFrame,
    opra_status: str,
    automation_runtime: dict[str, Any],
) -> dict[str, Any]:
    scheduled_events = [row for row in events if _event_trigger(row) == "scheduled"]
    scheduled_entries = [row for row in scheduled_events if row.event_type == "options.paper_entry_submitted"]
    scheduled_entry_blocks = [row for row in scheduled_events if row.event_type == "options.paper_entry_blocked"]
    scheduled_refreshes = [row for row in scheduled_events if row.event_type == "options.position_quote_refreshed"]
    scheduled_refresh_blocks = [row for row in scheduled_events if row.event_type == "options.position_quote_refresh_blocked"]
    scheduled_exits = [row for row in scheduled_events if row.event_type == "options.paper_exit_submitted"]
    scheduled_exit_blocks = [row for row in scheduled_events if row.event_type == "options.paper_exit_blocked"]

    open_by_trade = _frame_lookup(open_frame, column="trade_id")
    pending_by_trade = _frame_lookup(pending_frame, column="trade_id")
    closed_by_trade = _frame_lookup(closed_frame, column="trade_id")
    open_by_contract = _frame_lookup(open_frame, column="contract_symbol")
    pending_by_contract = _frame_lookup(pending_frame, column="contract_symbol")
    closed_by_contract = _frame_lookup(closed_frame, column="contract_symbol")
    order_events_by_trade = _order_event_lookup(order_events, key="trade_id")
    order_events_by_order = _order_event_lookup(order_events, key="order_id")
    refresh_payloads = _latest_payloads_by_key(scheduled_refreshes, REFRESH_EVENT_TYPES)

    entry_records: dict[str, dict[str, Any]] = {}
    clean_entry_keys: set[str] = set()
    clean_cycles: list[dict[str, Any]] = []
    stale_quote_block_count = 0
    broker_linked_item_count = 0
    orphan_events: list[dict[str, Any]] = []
    last_clean_lifecycle_at: str | None = None
    last_broker_linked_entry: dict[str, Any] | None = None
    last_broker_linked_exit: dict[str, Any] | None = None

    for row in scheduled_entry_blocks + scheduled_refresh_blocks + scheduled_exit_blocks:
        if _has_quote_quality_block(_event_payload(row)):
            stale_quote_block_count += 1

    for row in scheduled_entries:
        payload = _event_payload(row)
        cycle_key, trade_id, contract_symbol = _cycle_key_from_payload(payload, row)
        order_id = str(payload.get("order_id") or payload.get("broker_order_id") or "").strip()
        order_match = bool(order_events_by_trade.get(trade_id) or order_events_by_order.get(order_id))
        position_match = bool(
            (trade_id and (open_by_trade.get(trade_id) or pending_by_trade.get(trade_id) or closed_by_trade.get(trade_id)))
            or (contract_symbol and (open_by_contract.get(contract_symbol) or pending_by_contract.get(contract_symbol) or closed_by_contract.get(contract_symbol)))
        )
        broker_linked = bool(order_match or position_match or payload.get("broker_order_id"))
        created_at = _event_created_at(row)
        if cycle_key:
            current = entry_records.get(cycle_key)
            if current is None or (created_at and created_at >= (current.get("created_at") or pd.Timestamp(0, tz="UTC"))):
                entry_records[cycle_key] = {
                    "trade_id": trade_id,
                    "contract_symbol": contract_symbol,
                    "payload": payload,
                    "created_at": created_at,
                    "broker_linked": broker_linked,
                }
        if broker_linked and cycle_key:
            clean_entry_keys.add(cycle_key)
            broker_linked_item_count += 1
            if last_broker_linked_entry is None:
                last_broker_linked_entry = _serialize_domain_event_row(row)
        elif not broker_linked:
            orphan_events.append(
                {
                    "event_type": row.event_type,
                    "trade_id": trade_id,
                    "contract_symbol": contract_symbol,
                    "detail": "Scheduled option entry did not link back to broker/order or local position state.",
                    "created_at": _iso(row.created_at),
                }
            )

    for row in scheduled_exits:
        payload = _event_payload(row)
        cycle_key, trade_id, contract_symbol = _cycle_key_from_payload(payload, row)
        order_id = str(
            payload.get("order_id")
            or payload.get("broker_order_id")
            or payload.get("broker_close_order_id")
            or ""
        ).strip()
        refresh_payload = refresh_payloads.get(cycle_key or "") or refresh_payloads.get(trade_id or "") or refresh_payloads.get(contract_symbol or "") or {}
        has_refresh_evidence = bool(refresh_payload) and bool(refresh_payload.get("sell_ready"))
        terminal_close = bool((trade_id and closed_by_trade.get(trade_id)) or (contract_symbol and closed_by_contract.get(contract_symbol)))
        linked_order_rows = list(order_events_by_trade.get(trade_id) or []) + list(order_events_by_order.get(order_id) or [])
        terminal_order_match = any(_is_terminal_order_status(item.status) for item in linked_order_rows)
        broker_linked = bool(linked_order_rows or order_id or trade_id or payload.get("broker_close_order_id") or payload.get("broker_order_id"))
        entry_record = entry_records.get(cycle_key or "")
        entry_broker_linked = bool(entry_record and entry_record.get("broker_linked"))
        if broker_linked and last_broker_linked_exit is None:
            last_broker_linked_exit = _serialize_domain_event_row(row)
        if entry_broker_linked and has_refresh_evidence and terminal_close and (terminal_order_match or broker_linked):
            broker_linked_item_count += 1
            entry_created_at = entry_record.get("created_at") if isinstance(entry_record, dict) else None
            refresh_created_at = _parse_timestamp(refresh_payload.get("refreshed_at") or refresh_payload.get("created_at"))
            exit_created_at = _event_created_at(row)
            clean_cycle = {
                "trade_id": trade_id,
                "contract_symbol": contract_symbol,
                "ticker": str(payload.get("ticker") or refresh_payload.get("ticker") or "").strip().upper() or None,
                "entry_at": _iso(entry_created_at),
                "refresh_at": _iso(refresh_created_at),
                "exit_at": _iso(exit_created_at),
            }
            clean_cycles.append(clean_cycle)
            latest_clean_ts = _max_timestamp(last_clean_lifecycle_at, exit_created_at)
            last_clean_lifecycle_at = _iso(latest_clean_ts)
        elif not broker_linked and not terminal_close:
            orphan_events.append(
                {
                    "event_type": row.event_type,
                    "trade_id": trade_id,
                    "contract_symbol": contract_symbol,
                    "detail": "Scheduled option exit did not link back to broker/order or closed position state.",
                    "created_at": _iso(row.created_at),
                }
            )

    clean_cycles.sort(key=lambda item: str(item.get("exit_at") or ""), reverse=True)
    recent_clean_cycles = clean_cycles[:10]
    clean_cycle_count = len(clean_cycles)
    clean_entry_count = len(clean_entry_keys)
    clean_exit_count = clean_cycle_count
    working_order_count = int(len(pending_frame.index))
    latest_sync = _latest_event_snapshot(events, SYNC_EVENT_TYPES)
    latest_sync_at = latest_sync.get("created_at") if latest_sync else None
    latest_scheduled_lifecycle_ts = _max_timestamp(
        *[_event_created_at(row) for row in scheduled_entries + scheduled_refreshes + scheduled_refresh_blocks + scheduled_exits + scheduled_exit_blocks]
    )
    latest_sync_ts = _parse_timestamp(latest_sync_at)
    broker_sync_stale = bool(open_positions or working_order_count) and (
        latest_sync_ts is None or (latest_scheduled_lifecycle_ts is not None and latest_sync_ts < latest_scheduled_lifecycle_ts)
    )

    blockers: list[str] = []
    if opra_status != "ready":
        if opra_status == "licensed_realtime_options_feed_required":
            blockers.append(_options_data_blocked_reason(opra_status))
        else:
            blockers.append("Real-time options data is not ready for personal-paper automation.")
    sell_blockers = []
    for item in open_positions:
        if bool(item.get("sell_ready")):
            continue
        detail = str(item.get("sell_block_detail") or item.get("sell_block_reason") or "").strip()
        if detail and detail not in sell_blockers:
            sell_blockers.append(detail)
    blockers.extend(sell_blockers)
    if broker_sync_stale:
        blockers.append("Broker sync is stale while option orders or positions are still open.")
    if orphan_events:
        blockers.append("One or more scheduled option lifecycle events could not be matched back to broker/order state.")

    readiness_state = "collecting_lifecycle_evidence"
    if blockers:
        readiness_state = "blocked"
    elif (
        clean_cycle_count >= REQUIRED_CLEAN_SCHEDULED_OPTION_CYCLES
        and not open_positions
        and working_order_count == 0
    ):
        readiness_state = "ready"

    if readiness_state == "blocked":
        if opra_status != "ready":
            next_step = (
                "Add a licensed real-time options feed before enabling options auto-entry or auto-exit."
                if opra_status == "licensed_realtime_options_feed_required"
                else "Resolve the real-time options data blocker before relying on scheduled paper options."
            )
        elif broker_sync_stale:
            next_step = "Run the option lifecycle sync and verify broker/order linkage before widening scope."
        elif sell_blockers:
            next_step = "Keep collecting unchanged until every open scheduled option position has fresh sell-side quote evidence."
        else:
            next_step = "Inspect the unmatched scheduled option lifecycle events and fix the blocker before widening scope."
    elif readiness_state == "ready":
        next_step = "Keep collecting unchanged. The personal-paper options lane has met the 5-cycle readiness bar."
    else:
        next_step = (
            f"Keep collecting unchanged until {REQUIRED_CLEAN_SCHEDULED_OPTION_CYCLES} clean scheduled paper-option lifecycles are recorded."
        )

    latest_runtime_blocker = str(automation_runtime.get("last_options_blocker") or "").strip() or None
    return {
        "validation_scope": "personal_paper",
        "options_data_status": opra_status,
        "options_data_provider": _options_data_provider(),
        "options_broker_provider": _options_broker_provider(),
        "broker_mode": "internal_paper" if _options_broker_provider() == "internal" else "broker_paper",
        "real_money_execution_enabled": False,
        "licensed_realtime_options_data": bool(getattr(settings, "licensed_realtime_options_data", False)),
        "options_automation_ready": readiness_state == "ready" and opra_status == "ready",
        "readiness_state": readiness_state,
        "readiness_label": _readiness_label(readiness_state),
        "required_clean_cycles": REQUIRED_CLEAN_SCHEDULED_OPTION_CYCLES,
        "clean_cycle_count": clean_cycle_count,
        "clean_entry_count": clean_entry_count,
        "clean_exit_count": clean_exit_count,
        "blocked_entry_count": len(scheduled_entry_blocks),
        "blocked_exit_count": len(scheduled_exit_blocks),
        "stale_quote_block_count": stale_quote_block_count,
        "open_position_count": int(len(open_positions)),
        "working_order_count": working_order_count,
        "last_broker_sync_at": latest_sync_at,
        "last_clean_lifecycle_at": last_clean_lifecycle_at,
        "blockers": serialize_value(blockers),
        "next_step": next_step,
        "orphan_event_count": len(orphan_events),
        "orphan_events": serialize_value(orphan_events[:10]),
        "recent_clean_cycles": serialize_value(recent_clean_cycles),
        "last_broker_linked_entry": serialize_value(last_broker_linked_entry),
        "last_broker_linked_exit": serialize_value(last_broker_linked_exit),
        "last_scheduled_blocker": latest_runtime_blocker or None,
        "latest_scheduled_entry": _latest_event_snapshot(events, ENTRY_EVENT_TYPES, automation_trigger="scheduled"),
        "latest_scheduled_refresh": _latest_event_snapshot(events, REFRESH_EVENT_TYPES, automation_trigger="scheduled"),
        "latest_scheduled_exit": _latest_event_snapshot(events, EXIT_EVENT_TYPES, automation_trigger="scheduled"),
    }


def _position_contract_count(row: dict[str, Any]) -> float:
    for key in ("filled_contracts", "suggested_contracts", "broker_filled_qty", "broker_qty"):
        quantity = _coerce_float(row.get(key))
        if quantity is not None and quantity > 0:
            return float(quantity)
    return 0.0


def _current_underlying_price(client: AlpacaMarketDataClient | None, ticker: str, *, fallback: float | None = None) -> float | None:
    normalized = _normalize_symbol(ticker)
    if normalized and client is not None and getattr(client, "is_configured", True):
        try:
            latest_prices = client.get_latest_prices([normalized])
            price = _coerce_float((latest_prices or {}).get(normalized))
            if price is not None and price > 0:
                return float(price)
        except Exception:
            pass
    return fallback


def _build_position_refresh_snapshot(
    row: dict[str, Any],
    *,
    refresh: dict[str, Any],
    underlying_price: float | None,
    refreshed_at: datetime,
) -> dict[str, Any]:
    diagnostics = dict(refresh.get("diagnostics") or {})
    contract = dict(refresh.get("contract") or {})
    quantity = _position_contract_count(row)
    entry_contract_mid = _coerce_float(row.get("contract_mid_at_open"))
    entry_value = _coerce_float(row.get("position_cost"))
    if entry_value is None and entry_contract_mid is not None and quantity > 0:
        entry_value = round(float(entry_contract_mid * quantity * 100.0), 2)
    bid = _coerce_float(contract.get("bid")) or _coerce_float(diagnostics.get("bid"))
    ask = _coerce_float(contract.get("ask")) or _coerce_float(diagnostics.get("ask"))
    mid = _coerce_float(contract.get("mid")) or _coerce_float(diagnostics.get("mid")) or _coerce_float(refresh.get("close_contract_mid"))
    quote_timestamp = _iso(_parse_timestamp(contract.get("quote_timestamp") or contract.get("timestamp")))
    quote_age_seconds = _coerce_float(diagnostics.get("option_quote_age_seconds"))
    spread_pct = _coerce_float(contract.get("spread_pct")) or _coerce_float(diagnostics.get("option_spread_pct"))
    volume = _coerce_int(contract.get("volume") or diagnostics.get("volume"))
    open_interest = _coerce_int(contract.get("open_interest") or diagnostics.get("open_interest"))
    sell_limit_price = bid if bid is not None and bid > 0 else mid
    liquidation_mark = bid if bid is not None and bid > 0 else mid
    current_value = round(float(liquidation_mark * quantity * 100.0), 2) if liquidation_mark is not None and quantity > 0 else None
    unrealized_pnl = round(float(current_value - entry_value), 2) if current_value is not None and entry_value is not None else None
    trade_id = resolve_trade_identifier(row)
    return {
        "trade_id": trade_id,
        "ticker": _normalize_symbol(row.get("ticker")),
        "contract_symbol": _normalize_symbol(row.get("contract_symbol")),
        "option_right": _normalize_right(row.get("option_right") or row.get("direction")),
        "expiration": str(row.get("contract_expiration") or "").strip() or None,
        "strike": _coerce_float(row.get("contract_strike")),
        "quantity": quantity,
        "entry_contract_mid": entry_contract_mid,
        "entry_value": entry_value,
        "current_underlying_price": underlying_price,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "sell_limit_price": sell_limit_price,
        "quote_timestamp": quote_timestamp,
        "quote_age_seconds": quote_age_seconds,
        "spread_pct": spread_pct,
        "volume": volume,
        "open_interest": open_interest,
        "current_value": current_value,
        "unrealized_pnl": unrealized_pnl,
        "sell_ready": bool(refresh.get("allowed")),
        "sell_block_reason": refresh.get("reason"),
        "sell_block_detail": refresh.get("detail"),
        "broker_name": str(row.get("broker_name") or "").strip() or None,
        "execution_intent": str(row.get("automation_execution_intent") or "broker_paper").strip() or "broker_paper",
        "opened_at": _iso(_parse_timestamp(row.get("opened_at"))),
        "refreshed_at": _iso(refreshed_at),
        "quote_source": str(diagnostics.get("quote_source") or "exact_chain").strip() or "exact_chain",
    }


def _latest_refresh_payloads(events: list[DomainEventLog]) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.event_type not in REFRESH_EVENT_TYPES:
            continue
        payload = dict(event.payload_json or {})
        trade_id = str(payload.get("trade_id") or event.aggregate_id or "").strip()
        contract_symbol = _normalize_symbol(payload.get("contract_symbol"))
        if trade_id and trade_id not in payloads:
            payloads[trade_id] = payload
        if contract_symbol and contract_symbol not in payloads:
            payloads[contract_symbol] = payload
    return payloads


def _position_snapshot_from_payload(row: dict[str, Any], payload: dict[str, Any] | None) -> dict[str, Any]:
    trade_id = resolve_trade_identifier(row)
    payload = dict(payload or {})
    quantity = _position_contract_count(row)
    entry_contract_mid = _coerce_float(row.get("contract_mid_at_open"))
    entry_value = _coerce_float(row.get("position_cost"))
    if entry_value is None and entry_contract_mid is not None and quantity > 0:
        entry_value = round(float(entry_contract_mid * quantity * 100.0), 2)
    current_value = _coerce_float(payload.get("current_value"))
    unrealized_pnl = _coerce_float(payload.get("unrealized_pnl"))
    if unrealized_pnl is None and current_value is not None and entry_value is not None:
        unrealized_pnl = round(float(current_value - entry_value), 2)
    return {
        "trade_id": trade_id,
        "ticker": _normalize_symbol(row.get("ticker")),
        "contract_symbol": _normalize_symbol(row.get("contract_symbol")),
        "option_right": _normalize_right(row.get("option_right") or row.get("direction")),
        "expiration": str(row.get("contract_expiration") or "").strip() or None,
        "strike": _coerce_float(row.get("contract_strike")),
        "quantity": quantity,
        "entry_contract_mid": entry_contract_mid,
        "entry_value": entry_value,
        "current_underlying_price": _coerce_float(payload.get("current_underlying_price")),
        "bid": _coerce_float(payload.get("bid")),
        "ask": _coerce_float(payload.get("ask")),
        "mid": _coerce_float(payload.get("mid")),
        "sell_limit_price": _coerce_float(payload.get("sell_limit_price")),
        "quote_timestamp": str(payload.get("quote_timestamp") or "").strip() or None,
        "quote_age_seconds": _coerce_float(payload.get("quote_age_seconds")),
        "spread_pct": _coerce_float(payload.get("spread_pct")),
        "volume": _coerce_int(payload.get("volume")),
        "open_interest": _coerce_int(payload.get("open_interest")),
        "current_value": current_value,
        "unrealized_pnl": unrealized_pnl,
        "sell_ready": bool(payload.get("sell_ready")),
        "sell_block_reason": str(payload.get("sell_block_reason") or "").strip() or None,
        "sell_block_detail": str(payload.get("sell_block_detail") or "").strip() or None,
        "broker_name": str(row.get("broker_name") or "").strip() or None,
        "execution_intent": str(row.get("automation_execution_intent") or "broker_paper").strip() or "broker_paper",
        "opened_at": _iso(_parse_timestamp(row.get("opened_at"))),
        "refreshed_at": str(payload.get("refreshed_at") or "").strip() or None,
        "quote_source": str(payload.get("quote_source") or "").strip() or None,
    }


def _extract_http_error_message(exc: HTTPError) -> str | None:
    try:
        raw = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return None
    finally:
        response = getattr(exc, "fp", None)
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        try:
            exc.close()
        except Exception:
            pass
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return raw
    if isinstance(payload, dict):
        for key in ("message", "detail", "description", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = next(
                    (
                        str(value.get(nested_key)).strip()
                        for nested_key in ("message", "detail", "description", "code")
                        if str(value.get(nested_key) or "").strip()
                    ),
                    "",
                )
                if nested:
                    return nested
    return raw


def _summarize_options_data_http_error(exc: HTTPError, *, feed: str) -> tuple[str, str]:
    message = str(_extract_http_error_message(exc) or "").strip()
    normalized_message = message.lower()
    normalized_feed = str(feed or "").strip().lower()
    if exc.code == 403 and "subscription does not permit querying opra data" in normalized_message:
        return (
            "Alpaca subscription does not permit querying OPRA data. Real-time OPRA is required for paper options automation.",
            f"Alpaca HTTP 403: {message}",
        )
    if exc.code == 401:
        return (
            "Alpaca options data credentials were rejected. Check the configured paper market-data keys.",
            f"Alpaca HTTP 401: {message or 'credentials rejected.'}",
        )
    if exc.code == 403 and normalized_feed != "opra":
        return (
            "The configured Alpaca options feed is not valid for paper options automation. OPRA is required.",
            f"Alpaca HTTP 403: {message or 'forbidden feed selection.'}",
        )
    if exc.code == 403:
        return (
            "Alpaca options data access was forbidden. Check the account's market-data plan and option-data permissions.",
            f"Alpaca HTTP 403: {message or 'forbidden.'}",
        )
    return (
        "Alpaca options data is not reachable.",
        f"Alpaca HTTP {exc.code}: {message or 'request failed.'}",
    )


def _derive_opra_entitlement_status(*, feed: str, blocked_reason: str | None) -> str:
    normalized_feed = str(feed or "").strip().lower()
    reason = str(blocked_reason or "").strip().lower()
    if _options_data_provider() == "free_delayed":
        return "licensed_realtime_options_feed_required"
    if _options_data_provider() == "tradier":
        if not settings.tradier_live_token or not settings.tradier_live_account_id:
            return "credentials_missing"
        if _options_broker_provider() == "tradier" and (not settings.tradier_paper_token or not settings.tradier_paper_account_id):
            return "broker_credentials_missing"
        if "stale" in reason:
            return "stale_quotes"
        if "tradier" in reason and reason:
            return "not_ready"
        return "ready"
    if normalized_feed == "tradier_realtime":
        if "credentials" in reason:
            return "credentials_missing"
        if "stale" in reason:
            return "stale_quotes"
        if "tradier" in reason and reason:
            return "not_ready"
        return "ready"
    if normalized_feed != "opra":
        return "blocked_wrong_feed"
    if not reason:
        return "ready"
    if "not entitled" in reason or "http 403" in reason or "does not permit querying opra data" in reason:
        return "not_entitled"
    if "credentials" in reason:
        return "credentials_missing"
    return "ready"


def _readiness_state(snapshot: dict[str, Any], open_positions: list[dict[str, Any]], *, opra_status: str) -> str:
    if opra_status not in {"ready"}:
        return "blocked"
    if any(bool(item.get("sell_ready")) for item in open_positions):
        return "ready"
    if int(snapshot.get("ready_candidate_count") or 0) > 0:
        return "ready"
    return "collecting_lifecycle_evidence"


def get_options_automation_snapshot(db: Session, *, current_user: Any) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    row = db.execute(
        select(OptionAutomationScanRun)
        .where(OptionAutomationScanRun.tenant_id == tenant.id)
        .order_by(OptionAutomationScanRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    previous_scan: dict[str, Any] | None = None
    if row is not None and not _scan_run_matches_active_provider(row):
        previous_scan = _serialize_scan_run(row, include_candidates=False)
        row = None
    snapshot = _serialize_scan_run(row)
    if previous_scan is not None:
        snapshot["previous_scan"] = previous_scan
    summary = dict(snapshot.get("summary") or {})
    for key, value in _active_scan_summary_defaults().items():
        summary.setdefault(key, value)
    snapshot["summary"] = summary
    snapshot["policy"] = _build_policy_snapshot()
    snapshot["options_data_provider"] = _options_data_provider()
    snapshot["options_broker_provider"] = _options_broker_provider()
    snapshot["broker_mode"] = "internal_paper" if _options_broker_provider() == "internal" else "broker_paper"
    snapshot["real_money_execution_enabled"] = False
    snapshot["licensed_realtime_options_data"] = bool(getattr(settings, "licensed_realtime_options_data", False))
    snapshot["data_source"] = _options_data_source_label()
    automation_snapshot = get_tenant_trade_automation_snapshot(db, current_user=current_user, scope="personal_paper")
    automation_runtime = dict(automation_snapshot.get("runtime") or {})
    automation_settings = dict(automation_snapshot.get("settings") or {})
    events = _load_lifecycle_events(db, tenant.id)
    order_events = _load_option_order_events(db, tenant.id)
    refresh_payloads = _latest_refresh_payloads(events)
    open_frame = _option_open_positions(current_user)
    pending_frame = _option_pending_orders(current_user)
    closed_frame = _option_closed_positions(current_user)
    open_positions = []
    for row_dict in open_frame.to_dict(orient="records"):
        payload = refresh_payloads.get(resolve_trade_identifier(row_dict)) or refresh_payloads.get(_normalize_symbol(row_dict.get("contract_symbol")))
        open_positions.append(_position_snapshot_from_payload(row_dict, payload))
    latest_scan = {
        "scan_run_id": snapshot.get("latest_scan_run_id"),
        "status": snapshot.get("status"),
        "blocked_reason": snapshot.get("blocked_reason"),
        "candidate_count": int(snapshot.get("candidate_count") or 0),
        "ready_candidate_count": int(snapshot.get("ready_candidate_count") or 0),
        "created_at": snapshot.get("created_at"),
    }
    latest_paper_execution = _latest_event_snapshot(events, ENTRY_EVENT_TYPES)
    latest_quote_refresh = _latest_event_snapshot(events, REFRESH_EVENT_TYPES)
    latest_paper_exit = _latest_event_snapshot(events, EXIT_EVENT_TYPES)
    latest_broker_sync = _latest_event_snapshot(events, SYNC_EVENT_TYPES)
    opra_status = _derive_opra_entitlement_status(feed=str(snapshot.get("feed") or ""), blocked_reason=snapshot.get("blocked_reason"))
    if opra_status != "ready" and str(snapshot.get("status") or "").strip().lower() == "idle":
        snapshot["status"] = "blocked"
        snapshot["blocked_reason"] = _options_data_blocked_reason(opra_status)
    validation_artifact = _build_options_validation_artifact(
        snapshot=snapshot,
        events=events,
        order_events=order_events,
        open_positions=open_positions,
        open_frame=open_frame,
        pending_frame=pending_frame,
        closed_frame=closed_frame,
        opra_status=opra_status,
        automation_runtime=automation_runtime,
    )
    blockers: list[str] = []
    if snapshot.get("blocked_reason"):
        blockers.append(str(snapshot["blocked_reason"]))
    blockers.extend(
        sorted(
            {
                str(item.get("sell_block_reason") or "").strip()
                for item in open_positions
                if str(item.get("sell_block_reason") or "").strip()
            }
        )
    )
    blockers.extend(
        item
        for item in list(validation_artifact.get("blockers") or [])
        if str(item or "").strip() and str(item or "").strip() not in blockers
    )
    snapshot["lifecycle"] = {
        "opra_entitlement_status": opra_status,
        "options_data_status": opra_status,
        "options_data_provider": _options_data_provider(),
        "options_broker_provider": _options_broker_provider(),
        "broker_mode": snapshot["broker_mode"],
        "real_money_execution_enabled": False,
        "licensed_realtime_options_data": bool(getattr(settings, "licensed_realtime_options_data", False)),
        "options_automation_ready": validation_artifact["readiness_state"] == "ready" and opra_status == "ready",
        "latest_scan": latest_scan,
        "latest_paper_execution": latest_paper_execution,
        "latest_quote_refresh": latest_quote_refresh,
        "latest_paper_exit": latest_paper_exit,
        "latest_broker_sync": latest_broker_sync,
        "automation_profile_key": "personal_paper",
        "automation_enabled": bool(automation_settings.get("enabled")),
        "automation_armed": bool(automation_settings.get("armed")),
        "last_scheduled_cycle_at": automation_runtime.get("last_options_cycle_at"),
        "last_scheduled_entry_at": dict(automation_runtime.get("last_option_entry") or {}).get("at"),
        "last_scheduled_exit_at": dict(automation_runtime.get("last_option_exit") or {}).get("at"),
        "last_scheduled_blocker": automation_runtime.get("last_options_blocker"),
        "open_position_count": len(open_positions),
        "sell_ready_count": sum(1 for item in open_positions if bool(item.get("sell_ready"))),
        "blocked_position_count": sum(1 for item in open_positions if not bool(item.get("sell_ready"))),
        "blockers": blockers,
        "validation_artifact": serialize_value(validation_artifact),
    }
    snapshot["latest_paper_execution"] = latest_paper_execution
    snapshot["latest_quote_refresh"] = latest_quote_refresh
    snapshot["latest_paper_exit"] = latest_paper_exit
    snapshot["latest_broker_sync"] = latest_broker_sync
    snapshot["open_positions"] = open_positions
    snapshot["blockers"] = blockers
    snapshot["validation_artifact"] = serialize_value(validation_artifact)
    snapshot["readiness_state"] = validation_artifact["readiness_state"]
    snapshot["readiness_label"] = validation_artifact["readiness_label"]
    snapshot["options_automation_ready"] = bool(validation_artifact["readiness_state"] == "ready" and opra_status == "ready")
    snapshot["clean_entry_count"] = int(validation_artifact.get("clean_entry_count") or 0)
    snapshot["clean_exit_count"] = int(validation_artifact.get("clean_exit_count") or 0)
    snapshot["required_clean_cycles"] = int(validation_artifact.get("required_clean_cycles") or REQUIRED_CLEAN_SCHEDULED_OPTION_CYCLES)
    snapshot["clean_cycle_count"] = int(validation_artifact.get("clean_cycle_count") or 0)
    snapshot["blocked_entry_count"] = int(validation_artifact.get("blocked_entry_count") or 0)
    snapshot["blocked_exit_count"] = int(validation_artifact.get("blocked_exit_count") or 0)
    snapshot["stale_quote_block_count"] = int(validation_artifact.get("stale_quote_block_count") or 0)
    snapshot["open_position_count"] = int(validation_artifact.get("open_position_count") or 0)
    snapshot["working_order_count"] = int(validation_artifact.get("working_order_count") or 0)
    snapshot["last_broker_sync_at"] = validation_artifact.get("last_broker_sync_at")
    snapshot["last_clean_lifecycle_at"] = validation_artifact.get("last_clean_lifecycle_at")
    snapshot["next_step"] = validation_artifact.get("next_step")
    snapshot["recent_clean_cycles"] = list(validation_artifact.get("recent_clean_cycles") or [])
    snapshot["automation_profile_key"] = "personal_paper"
    snapshot["automation_enabled"] = bool(automation_settings.get("enabled"))
    snapshot["automation_armed"] = bool(automation_settings.get("armed"))
    snapshot["last_scheduled_cycle_at"] = automation_runtime.get("last_options_cycle_at")
    snapshot["last_scheduled_entry_at"] = dict(automation_runtime.get("last_option_entry") or {}).get("at")
    snapshot["last_scheduled_exit_at"] = dict(automation_runtime.get("last_option_exit") or {}).get("at")
    snapshot["last_scheduled_blocker"] = automation_runtime.get("last_options_blocker")
    export_options_validation(snapshot)
    return snapshot


def sync_options_automation(db: Session, *, current_user: Any) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    sync_started_at = _utc_now()
    pending_frame = _option_pending_orders(current_user)
    synced_items: list[dict[str, Any]] = []
    unique_order_ids: set[str] = set()
    for row in pending_frame.to_dict(orient="records"):
        order_id = str(row.get("order_id") or "").strip()
        if not order_id or order_id in unique_order_ids:
            continue
        unique_order_ids.add(order_id)
        result = sync_pending_orders_from_broker(
            db,
            current_user=current_user,
            order_id=order_id,
        )
        synced_items.extend(list(result.get("items") or []))
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="options.lifecycle_synced",
        aggregate_type="options_automation",
        aggregate_id=None,
        payload={
            "automation_trigger": "manual",
            "pending_option_order_count": int(len(unique_order_ids)),
            "synced_item_count": int(len(synced_items)),
            "synced_items": serialize_value(synced_items[:20]),
            "synced_at": _iso(sync_started_at),
        },
        metadata={"automation_trigger": "manual"},
    )
    db.commit()
    return get_options_automation_snapshot(db, current_user=current_user)


def run_options_automation_scan(
    db: Session,
    *,
    current_user: Any,
    request: OptionsAutomationScanRequest | None = None,
) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    request = request or OptionsAutomationScanRequest()
    automation_trigger = str(request.automation_trigger or "manual").strip().lower() or "manual"
    tickers = _resolve_scan_tickers(db, current_user, request)
    scan_started_at = _utc_now()
    data_provider = _options_data_provider()
    broker_provider = _options_broker_provider()
    feed = _options_feed_label()
    data_source = _options_data_source_label()
    scan_interval = _resolve_scan_interval(db, current_user)
    account = {"effective_funds": 0.0, "funds_source": "unavailable"}
    try:
        account = _account_summary()
    except Exception as exc:
        account = {"effective_funds": 0.0, "funds_source": "unavailable", "error": str(exc)}

    client = None
    candidates: list[dict[str, Any]] = []
    blockers_by_ticker: dict[str, list[str]] = {}
    metadata_by_ticker: dict[str, Any] = {}
    status = "completed"
    blocked_reason = None
    if data_provider == "free_delayed":
        status = "blocked"
        blocked_reason = _options_data_blocked_reason("licensed_realtime_options_feed_required")
        blockers_by_ticker = {ticker: [blocked_reason] for ticker in tickers}
        metadata_by_ticker = {
            ticker: {
                "data_source": data_source,
                "feed": feed,
                "automation_ready": False,
                "reason": "free_delayed_data_cannot_satisfy_current_options_bid_ask_gates",
            }
            for ticker in tickers
        }
    elif broker_provider == "tradier" and (not settings.tradier_paper_token or not settings.tradier_paper_account_id):
        status = "blocked"
        blocked_reason = "Tradier paper execution credentials are required for the paper options lifecycle."
    else:
        client = build_tradier_live_market_data_client() if data_provider == "tradier" else _build_alpaca_options_client()
    if status != "blocked" and client is not None and not client.is_configured:
        status = "blocked"
        if data_provider == "tradier":
            blocked_reason = "Tradier live market-data credentials are required for real-time options automation."
        else:
            blocked_reason = "Alpaca paper data credentials are not configured."
    elif status != "blocked" and client is not None:
        for ticker in tickers:
            try:
                if data_provider == "tradier":
                    ticker_candidates, ticker_blockers, ticker_metadata = _fetch_tradier_candidates_for_ticker(
                        client=client,
                        ticker=ticker,
                        current_user=current_user,
                        feed=feed,
                        scan_started_at=scan_started_at,
                    )
                else:
                    ticker_candidates, ticker_blockers, ticker_metadata = _fetch_candidates_for_ticker(
                        client=client,
                        ticker=ticker,
                        current_user=current_user,
                        feed=feed,
                        scan_started_at=scan_started_at,
                    )
                candidates.extend(ticker_candidates)
                if ticker_blockers:
                    blockers_by_ticker[ticker] = ticker_blockers
                metadata_by_ticker[ticker] = ticker_metadata
            except HTTPError as exc:
                status = "blocked"
                blocked_reason, blocker_detail = _summarize_options_data_http_error(exc, feed=feed)
                blockers_by_ticker[ticker] = [blocker_detail]
            except TradierApiError as exc:
                status = "blocked"
                blocked_reason = f"Tradier options data request failed: {exc}"
                blockers_by_ticker[ticker] = [blocked_reason]
            except Exception as exc:
                blockers_by_ticker[ticker] = [str(exc)]

    max_premium_risk = float(account.get("effective_funds") or 0.0) * (float(settings.options_max_premium_risk_pct or 1.0) / 100.0)
    for candidate in candidates:
        reasons = list(candidate.get("rejection_reasons") or [])
        if max_premium_risk > 0 and float(candidate.get("premium_notional") or 0.0) > max_premium_risk:
            reasons.append("Premium risk exceeds the account option risk cap.")
        candidate["max_premium_risk"] = round(max_premium_risk, 2)
        candidate["ready_to_execute"] = not reasons and status != "blocked"
        candidate["rejection_reasons"] = reasons
    candidates.sort(key=lambda item: (not bool(item.get("ready_to_execute")), float(item.get("selection_score") or 999.0)))
    total_candidate_count = len(candidates)
    ready_count = sum(1 for item in candidates if item.get("ready_to_execute"))
    displayed_candidates = candidates[: max(1, int(request.limit or settings.options_scan_candidate_limit or 30))]
    if status != "blocked" and ready_count == 0:
        status = "blocked"
        blocked_reason = "No option contracts passed the current price, liquidity, entitlement, and risk gates."

    summary = {
        "scope": "personal_paper",
        "execution_intent": "internal_paper" if broker_provider == "internal" else "broker_paper",
        "data_source": data_source,
        "feed": feed,
        "feed_required": _options_feed_required_label(),
        "options_paper_ready": ready_count > 0 and feed in {"opra", "tradier_realtime"},
        "options_automation_ready": ready_count > 0 and feed in {"opra", "tradier_realtime"},
        "licensed_realtime_options_data": bool(getattr(settings, "licensed_realtime_options_data", False)),
        "broker_mode": "internal_paper" if broker_provider == "internal" else "broker_paper",
        "options_data_provider": data_provider,
        "options_broker_provider": broker_provider,
        "scan_started_at": _iso(scan_started_at),
        "scan_interval_seconds": scan_interval,
        "scanned_contract_count": total_candidate_count,
        "trading_engine_interval_seconds": int((_automation_settings(db, current_user).get("cycle_interval_seconds") or 60)),
        "account_summary": account,
        "blockers_by_ticker": blockers_by_ticker,
        "metadata_by_ticker": metadata_by_ticker,
    }
    row = OptionAutomationScanRun(
        tenant_id=tenant.id,
        status=status,
        feed=feed,
        scan_interval_seconds=scan_interval,
        ticker_count=len(tickers),
        candidate_count=total_candidate_count,
        ready_candidate_count=ready_count,
        blocked_reason=blocked_reason,
        requested_tickers_json={"tickers": tickers},
        candidates_json={"items": serialize_value(displayed_candidates)},
        summary_json=serialize_value(summary),
    )
    db.add(row)
    db.flush()
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="options.scan_completed",
        aggregate_type="option_automation_scan",
        aggregate_id=row.id,
        payload={
            "status": status,
            "candidate_count": len(candidates),
            "ready_candidate_count": ready_count,
            "automation_trigger": automation_trigger,
        },
        metadata={"automation_trigger": automation_trigger},
    )
    if status == "blocked":
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="options.scan_blocked",
            aggregate_type="option_automation_scan",
            aggregate_id=row.id,
            payload={
                "blocked_reason": blocked_reason,
                "candidate_count": len(candidates),
                "ready_candidate_count": ready_count,
                "automation_trigger": automation_trigger,
            },
            metadata={"automation_trigger": automation_trigger},
        )
    db.commit()
    db.refresh(row)
    return get_options_automation_snapshot(db, current_user=current_user)


def _get_scan_run(db: Session, tenant_id: str, scan_run_id: str | None) -> OptionAutomationScanRun:
    statement = select(OptionAutomationScanRun).where(OptionAutomationScanRun.tenant_id == tenant_id)
    if scan_run_id:
        statement = statement.where(OptionAutomationScanRun.id == scan_run_id)
    statement = statement.order_by(OptionAutomationScanRun.created_at.desc()).limit(1)
    row = db.execute(statement).scalar_one_or_none()
    if row is None:
        raise NotFoundError("No options automation scan is available. Run a scan first.")
    return row


def execute_options_paper(
    db: Session,
    *,
    current_user: Any,
    request: OptionsAutomationExecuteRequest | None = None,
) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    request = request or OptionsAutomationExecuteRequest()
    automation_trigger = str(request.automation_trigger or "manual").strip().lower() or "manual"
    row = _get_scan_run(db, tenant.id, request.scan_run_id)
    candidates = list((row.candidates_json or {}).get("items") or [])
    normalized_contract = _normalize_symbol(request.contract_symbol)
    ready_candidates = [
        item
        for item in candidates
        if bool(item.get("ready_to_execute"))
        and (not normalized_contract or _normalize_symbol(item.get("contract_symbol")) == normalized_contract)
    ]
    if not ready_candidates:
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="options.paper_entry_blocked",
            aggregate_type="option_automation_scan",
            aggregate_id=row.id,
            payload={
                "scan_run_id": row.id,
                "reason": "no_ready_candidates",
                "requested_contract_symbol": normalized_contract or None,
                "automation_trigger": automation_trigger,
            },
            metadata={"automation_trigger": automation_trigger},
        )
        db.commit()
        raise ValidationError("No ready long-option paper candidate is available for execution.")
    account = _account_summary()
    execution_intent = "internal_paper" if _options_broker_provider() == "internal" else "broker_paper"
    results: list[dict[str, Any]] = []
    for candidate in ready_candidates[: max(1, int(request.max_candidates or 1))]:
        open_request = OpenTradeRequest(
            ticker=str(candidate["underlying"]),
            account_target_type="personal",
            execution_mode="automated_entry",
            execution_intent=execution_intent,
            instrument_type="listed_option",
            option_strategy="long_option",
            option_right=str(candidate["right"]),
            broker_side="buy",
            contract_symbol=str(candidate["contract_symbol"]),
            contract_expiration=str(candidate.get("expiration") or ""),
            contract_strike=float(candidate["strike"]),
            contract_bid=candidate.get("bid"),
            contract_ask=candidate.get("ask"),
            contract_mid=float(candidate["mid"]),
            contract_spread_pct=candidate.get("spread_pct"),
            contract_volume=int(candidate.get("volume") or 0),
            contract_open_interest=int(candidate.get("open_interest") or 0),
            contract_quote_timestamp=str(candidate.get("quote_timestamp") or ""),
            order_type="limit",
            time_in_force="day",
            limit_price=float(candidate["entry_limit_price"]),
            extended_hours=False,
            account_size=float(account.get("effective_funds") or 0.0),
            risk_percent=float(settings.options_max_premium_risk_pct or 1.0),
            max_open_positions=int(settings.options_max_open_positions or 4),
            source="options_automation",
            automation_entry_reason="options_price_gated_scan",
        )
        if request.dry_run:
            results.append({"contract_symbol": candidate["contract_symbol"], "dry_run": True, "request": open_request.model_dump()})
            continue
        execution = open_trade_from_request(open_request, db=db, current_user=current_user)
        record = serialize_value(execution.get("record") or execution.get("pending_order") or {})
        aggregate_id = str(record.get("trade_id") or record.get("order_id") or candidate["contract_symbol"]).strip() or candidate["contract_symbol"]
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="options.paper_entry_submitted",
            aggregate_type="option_position",
            aggregate_id=aggregate_id,
            payload={
                "scan_run_id": row.id,
                "ticker": candidate["underlying"],
                "contract_symbol": candidate["contract_symbol"],
                "trade_id": record.get("trade_id"),
                "order_id": record.get("order_id"),
                "execution_intent": execution_intent,
                "scope": "personal_paper",
                "entry_limit_price": candidate.get("entry_limit_price"),
                "mid": candidate.get("mid"),
                "bid": candidate.get("bid"),
                "ask": candidate.get("ask"),
                "quote_timestamp": candidate.get("quote_timestamp"),
                "automation_trigger": automation_trigger,
            },
            metadata={"automation_trigger": automation_trigger},
        )
        results.append(
            {
                "contract_symbol": candidate["contract_symbol"],
                "dry_run": False,
                "execution": serialize_value(execution.get("execution") or {}),
                "record": record,
            }
        )
    db.commit()
    get_options_automation_snapshot(db, current_user=current_user)
    return {
        "scan_run_id": row.id,
        "status": "dry_run" if request.dry_run else "submitted",
        "execution_intent": execution_intent,
        "scope": "personal_paper",
        "items": results,
    }


def refresh_options_positions(
    db: Session,
    *,
    current_user: Any,
    request: OptionsAutomationRefreshRequest | None = None,
) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    request = request or OptionsAutomationRefreshRequest()
    automation_trigger = str(request.automation_trigger or "manual").strip().lower() or "manual"
    execution_intent = "internal_paper" if _options_broker_provider() == "internal" else "broker_paper"
    refresh_started_at = _utc_now()
    open_positions = _option_open_positions(current_user)
    if request.trade_id:
        trade_id = str(request.trade_id or "").strip()
        open_positions = open_positions.loc[
            open_positions.apply(lambda row: resolve_trade_identifier(row.to_dict()) == trade_id, axis=1)
        ]
    if request.contract_symbol:
        contract_symbol = _normalize_symbol(request.contract_symbol)
        contract_series = open_positions.get("contract_symbol", pd.Series("", index=open_positions.index)).astype(str).str.upper()
        open_positions = open_positions.loc[contract_series.eq(contract_symbol)]
    if open_positions.empty:
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="options.position_quote_refresh_blocked",
            aggregate_type="options_automation",
            aggregate_id=None,
            payload={
                "reason": "no_open_positions",
                "trade_id": request.trade_id,
                "contract_symbol": request.contract_symbol,
                "automation_trigger": automation_trigger,
            },
            metadata={"automation_trigger": automation_trigger},
        )
        db.commit()
        get_options_automation_snapshot(db, current_user=current_user)
        return {
            "status": "blocked",
            "scope": "personal_paper",
            "execution_intent": execution_intent,
            "refreshed_at": _iso(refresh_started_at),
            "refreshed_count": 0,
            "sell_ready_count": 0,
            "blocked_count": 0,
            "items": [],
            "reason": "No open long option paper positions are available for quote refresh.",
        }

    if _options_data_provider() == "free_delayed":
        reason = _options_data_blocked_reason("licensed_realtime_options_feed_required")
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="options.position_quote_refresh_blocked",
            aggregate_type="options_automation",
            aggregate_id=None,
            payload={"reason": "licensed_realtime_options_feed_required", "automation_trigger": automation_trigger},
            metadata={"automation_trigger": automation_trigger},
        )
        db.commit()
        get_options_automation_snapshot(db, current_user=current_user)
        return {
            "status": "blocked",
            "scope": "personal_paper",
            "execution_intent": execution_intent,
            "refreshed_at": _iso(refresh_started_at),
            "refreshed_count": 0,
            "sell_ready_count": 0,
            "blocked_count": int(len(open_positions)),
            "items": [],
            "reason": reason,
        }

    client = _build_alpaca_options_client()
    items: list[dict[str, Any]] = []
    sell_ready_count = 0
    for row in open_positions.to_dict(orient="records"):
        refresh = _refresh_option_quote_for_close(row, now=refresh_started_at)
        underlying_price = _current_underlying_price(
            client,
            str(row.get("ticker") or ""),
            fallback=_coerce_float(row.get("live_price_at_open")),
        )
        item = _build_position_refresh_snapshot(
            row,
            refresh=refresh,
            underlying_price=underlying_price,
            refreshed_at=refresh_started_at,
        )
        event_type = "options.position_quote_refreshed" if item["sell_ready"] else "options.position_quote_refresh_blocked"
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type=event_type,
            aggregate_type="option_position",
            aggregate_id=item["trade_id"],
            payload={**serialize_value(item), "automation_trigger": automation_trigger},
            metadata={
                "scan_interval_seconds": _resolve_scan_interval(db, current_user),
                "automation_trigger": automation_trigger,
            },
        )
        items.append(item)
        if item["sell_ready"]:
            sell_ready_count += 1
    db.commit()
    get_options_automation_snapshot(db, current_user=current_user)
    return {
        "status": "completed" if sell_ready_count else "blocked",
        "scope": "personal_paper",
        "execution_intent": execution_intent,
        "refreshed_at": _iso(refresh_started_at),
        "refreshed_count": len(items),
        "sell_ready_count": sell_ready_count,
        "blocked_count": len(items) - sell_ready_count,
        "items": items,
    }


def close_options_paper(
    db: Session,
    *,
    current_user: Any,
    request: OptionsAutomationCloseRequest | None = None,
) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    request = request or OptionsAutomationCloseRequest()
    automation_trigger = str(request.automation_trigger or "manual").strip().lower() or "manual"
    execution_intent = "internal_paper" if _options_broker_provider() == "internal" else "broker_paper"
    all_open_trades = _scoped_open_trades(current_user)
    option_positions = _option_open_positions(current_user)
    if option_positions.empty:
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="options.paper_exit_blocked",
            aggregate_type="options_automation",
            aggregate_id=None,
            payload={"reason": "no_open_positions", "automation_trigger": automation_trigger},
            metadata={"automation_trigger": automation_trigger},
        )
        db.commit()
        raise ValidationError("There are no open long option paper positions to close.")

    if request.trade_id:
        trade_id = str(request.trade_id or "").strip()
        matches = option_positions.loc[
            option_positions.apply(lambda row: resolve_trade_identifier(row.to_dict()) == trade_id, axis=1)
        ]
    elif request.contract_symbol:
        contract_symbol = _normalize_symbol(request.contract_symbol)
        contract_series = option_positions.get("contract_symbol", pd.Series("", index=option_positions.index)).astype(str).str.upper()
        matches = option_positions.loc[contract_series.eq(contract_symbol)]
    elif len(option_positions) == 1:
        matches = option_positions
    else:
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="options.paper_exit_blocked",
            aggregate_type="options_automation",
            aggregate_id=None,
            payload={
                "reason": "ambiguous_position_selection",
                "open_position_count": len(option_positions),
                "automation_trigger": automation_trigger,
            },
            metadata={"automation_trigger": automation_trigger},
        )
        db.commit()
        raise ValidationError("Choose a specific open option position before submitting a paper sell-to-close order.")

    if matches.empty:
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="options.paper_exit_blocked",
            aggregate_type="options_automation",
            aggregate_id=None,
            payload={
                "reason": "position_not_found",
                "trade_id": request.trade_id,
                "contract_symbol": request.contract_symbol,
                "automation_trigger": automation_trigger,
            },
            metadata={"automation_trigger": automation_trigger},
        )
        db.commit()
        raise ValidationError("The requested open option paper position was not found.")
    if len(matches) > 1:
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="options.paper_exit_blocked",
            aggregate_type="options_automation",
            aggregate_id=None,
            payload={
                "reason": "multiple_matching_positions",
                "trade_id": request.trade_id,
                "contract_symbol": request.contract_symbol,
                "automation_trigger": automation_trigger,
            },
            metadata={"automation_trigger": automation_trigger},
        )
        db.commit()
        raise ValidationError("Multiple open option positions match that request. Use a specific trade id.")

    target_index = matches.index[0]
    target_row = matches.loc[target_index].to_dict()
    now = _utc_now()
    if _options_data_provider() == "free_delayed":
        reason = _options_data_blocked_reason("licensed_realtime_options_feed_required")
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="options.paper_exit_blocked",
            aggregate_type="option_position",
            aggregate_id=resolve_trade_identifier(target_row),
            payload={"reason": "licensed_realtime_options_feed_required", "automation_trigger": automation_trigger},
            metadata={"automation_trigger": automation_trigger},
        )
        db.commit()
        raise ValidationError(reason)
    refresh = _refresh_option_quote_for_close(target_row, now=now)
    client = _build_alpaca_options_client()
    underlying_price = _current_underlying_price(
        client,
        str(target_row.get("ticker") or ""),
        fallback=_coerce_float(target_row.get("live_price_at_open")),
    )
    position_snapshot = _build_position_refresh_snapshot(
        target_row,
        refresh=refresh,
        underlying_price=underlying_price,
        refreshed_at=now,
    )
    if not position_snapshot["sell_ready"]:
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="options.paper_exit_blocked",
            aggregate_type="option_position",
            aggregate_id=position_snapshot["trade_id"],
            payload={**serialize_value(position_snapshot), "automation_trigger": automation_trigger},
            metadata={"automation_trigger": automation_trigger},
        )
        db.commit()
        raise ValidationError(str(position_snapshot.get("sell_block_detail") or "Current option quote is not sell-ready."))

    sell_limit_price = _coerce_float(position_snapshot.get("sell_limit_price"))
    close_contract_mid = _coerce_float(position_snapshot.get("mid")) or sell_limit_price
    if sell_limit_price is None or sell_limit_price <= 0 or close_contract_mid is None or close_contract_mid <= 0:
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="options.paper_exit_blocked",
            aggregate_type="option_position",
            aggregate_id=position_snapshot["trade_id"],
            payload={**serialize_value(position_snapshot), "reason": "missing_close_price", "automation_trigger": automation_trigger},
            metadata={"automation_trigger": automation_trigger},
        )
        db.commit()
        raise ValidationError("The current option quote does not provide a valid sell-to-close price.")

    trade_index = int(all_open_trades.index.get_loc(target_index))
    close_result = close_trade_from_request(
        CloseTradeRequest(
            trade_index=trade_index,
            close_underlying_price=float(position_snapshot.get("current_underlying_price") or target_row.get("live_price_at_open") or 0.01),
            close_contract_mid=float(close_contract_mid),
            close_limit_price=float(sell_limit_price),
            close_fraction=float(request.close_fraction or 1.0),
        ),
        db=db,
        current_user=current_user,
    )
    close_record = serialize_value(
        close_result.get("record") or close_result.get("pending_order") or close_result.get("closed_trade") or {}
    )
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="options.paper_exit_submitted",
        aggregate_type="option_position",
        aggregate_id=position_snapshot["trade_id"],
        payload={
            **serialize_value(position_snapshot),
            "close_limit_price": float(sell_limit_price),
            "close_contract_mid": float(close_contract_mid),
            "close_fraction": float(request.close_fraction or 1.0),
            "trade_id": position_snapshot["trade_id"],
            "order_id": close_record.get("order_id"),
            "broker_order_id": close_record.get("broker_order_id"),
            "broker_close_order_id": close_record.get("broker_close_order_id"),
            "broker_status": close_record.get("broker_status") or close_record.get("broker_close_status"),
            "automation_trigger": automation_trigger,
            "close_result": serialize_value(close_result),
        },
        metadata={"automation_trigger": automation_trigger},
    )
    db.commit()
    get_options_automation_snapshot(db, current_user=current_user)
    return {
        "status": "submitted",
        "scope": "personal_paper",
        "execution_intent": execution_intent,
        "trade_id": position_snapshot["trade_id"],
        "contract_symbol": position_snapshot["contract_symbol"],
        "close_limit_price": float(sell_limit_price),
        "close_contract_mid": float(close_contract_mid),
        "position": position_snapshot,
        "close_result": serialize_value(close_result),
    }
