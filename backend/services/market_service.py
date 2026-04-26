from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from time import perf_counter, time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from backend import stock_direction_model as sdm
from backend.core.config import settings
from backend.schemas import AnalyzeRequest, ChartRequest, CompareRequest, ScanRequest, WatchlistRequest
from backend.services.exceptions import NotFoundError
from backend.services.intraday_momentum_service import build_intraday_momentum_snapshot
from backend.services.market_data import ohlcv_frame_to_chart_frame
from backend.services.ops_service import record_operation, record_route_profile, record_upstream_event
from backend.services.realtime_market_service import get_realtime_capabilities
from backend.services.serialization import serialize_dataframe, serialize_value
from backend.services.session_policy import build_market_session_context, get_session_profile, normalize_session_mode

APP_VERSION = settings.app_version
APP_NAME = settings.app_name
MARKET_TIMEZONE = ZoneInfo("America/New_York")
_INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 60 * 5,
    "15m": 60 * 15,
    "30m": 60 * 30,
    "1h": 60 * 60,
    "4h": 60 * 60 * 4,
    "1d": 60 * 60 * 24,
}

POPULAR_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META",
    "AMZN", "GOOGL", "NFLX", "SMCI", "PLTR", "COIN", "MSTR", "AVGO", "MU", "INTC",
    "BA", "JPM", "XOM", "CVX", "SHOP", "SNOW", "CRM", "UBER", "PYPL", "ADBE",
    "ORCL", "TSM", "ARM", "SOFI", "RIVN", "NIO", "HOOD", "GME", "DIS", "WMT",
]
_BASIC_CHART_INDICATORS = {"ema_9", "ema_21", "ema_50", "ema_200", "sma_20", "sma_50", "sma_200"}
_ADVANCED_SIGNAL_FIELDS = {"setup_grade", "conviction_label", "alignment_label"}
_EXECUTION_SIGNAL_FIELDS = {"entry_low_price", "entry_high_price", "target_price", "stop_loss", "contract_symbol"}
_market_cache_lock = Lock()
_market_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}


def _freeze_cache_key(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze_cache_key(inner)) for key, inner in value.items()))
    if isinstance(value, (list, tuple, set)):
        return tuple(_freeze_cache_key(item) for item in value)
    return value


def clear_market_response_cache() -> None:
    with _market_cache_lock:
        _market_cache.clear()


def _get_cached_market_payload(cache_key: tuple[Any, ...]) -> Any | None:
    ttl_seconds = max(0, int(settings.market_response_cache_ttl_seconds))
    if ttl_seconds <= 0:
        return None
    now = time()
    with _market_cache_lock:
        cached = _market_cache.get(cache_key)
        if not cached:
            return None
        expires_at, payload = cached
        if expires_at <= now:
            _market_cache.pop(cache_key, None)
            return None
        return deepcopy(payload)


def _store_cached_market_payload(cache_key: tuple[Any, ...], payload: Any) -> None:
    ttl_seconds = max(0, int(settings.market_response_cache_ttl_seconds))
    if ttl_seconds <= 0:
        return
    now = time()
    expires_at = now + ttl_seconds
    with _market_cache_lock:
        expired_keys = [key for key, (expiry, _) in _market_cache.items() if expiry <= now]
        for key in expired_keys:
            _market_cache.pop(key, None)
        _market_cache[cache_key] = (expires_at, deepcopy(payload))


def _is_timeout_exception(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    return "timeout" in name or "timed out" in message or "timeout" in message


def _operation_status_for_exception(exc: Exception) -> str:
    return "timeout" if _is_timeout_exception(exc) else "error"


def _run_upstream_market_call(
    *,
    target: str,
    operation: str,
    context: dict[str, Any] | None,
    func,
):
    started_at = perf_counter()
    try:
        result = func()
    except Exception as exc:
        record_upstream_event(
            target=target,
            operation=operation,
            duration_seconds=perf_counter() - started_at,
            status="timeout" if _is_timeout_exception(exc) else "error",
            error_message=str(exc),
            context=context,
        )
        raise
    record_upstream_event(
        target=target,
        operation=operation,
        duration_seconds=perf_counter() - started_at,
        status="ok",
        context=context,
    )
    return result


def _profile_market_stage(stages: list[dict[str, Any]], name: str, func):
    started_at = perf_counter()
    try:
        result = func()
    except Exception as exc:
        stages.append(
            {
                "name": name,
                "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                "status": "timeout" if _is_timeout_exception(exc) else "error",
            }
        )
        raise
    stages.append(
        {
            "name": name,
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            "status": "ok",
        }
    )
    return result


def _run_market_operation(
    *,
    name: str,
    cache_key: tuple[Any, ...] | None,
    context: dict[str, Any] | None,
    builder,
):
    started_at = perf_counter()
    if cache_key is not None:
        cached_payload = _get_cached_market_payload(cache_key)
        if cached_payload is not None:
            record_operation(
                name=name,
                duration_seconds=perf_counter() - started_at,
                cache_status="hit",
                context=context,
            )
            return cached_payload
    try:
        payload = builder()
    except Exception as exc:
        record_operation(
            name=name,
            duration_seconds=perf_counter() - started_at,
            status=_operation_status_for_exception(exc),
            cache_status="miss" if cache_key is not None else "bypass",
            context=context,
        )
        raise
    if cache_key is not None:
        _store_cached_market_payload(cache_key, payload)
    record_operation(
        name=name,
        duration_seconds=perf_counter() - started_at,
        cache_status="miss" if cache_key is not None else "bypass",
        context=context,
    )
    return payload


def _with_chart_indicators(chart_df: pd.DataFrame) -> pd.DataFrame:
    if chart_df.empty or "close" not in chart_df.columns:
        return chart_df

    enriched = chart_df.copy()
    close = pd.to_numeric(enriched.get("close"), errors="coerce")
    high = pd.to_numeric(enriched.get("high"), errors="coerce")
    low = pd.to_numeric(enriched.get("low"), errors="coerce")
    volume = pd.to_numeric(enriched.get("volume"), errors="coerce")

    if "ema_9" not in enriched.columns:
        enriched["ema_9"] = close.ewm(span=9, adjust=False).mean()
    if "ema_21" not in enriched.columns:
        enriched["ema_21"] = close.ewm(span=21, adjust=False).mean()
    if "ema_50" not in enriched.columns:
        enriched["ema_50"] = close.ewm(span=50, adjust=False).mean()
    if "ema_200" not in enriched.columns:
        enriched["ema_200"] = close.ewm(span=200, adjust=False).mean()

    if "sma_20" not in enriched.columns:
        enriched["sma_20"] = close.rolling(20).mean()
    if "sma_50" not in enriched.columns:
        enriched["sma_50"] = close.rolling(50).mean()
    if "sma_200" not in enriched.columns:
        enriched["sma_200"] = close.rolling(200).mean()

    if "rsi_14" not in enriched.columns:
        enriched["rsi_14"] = sdm.compute_rsi(close, 14)

    if any(column not in enriched.columns for column in ["macd", "macd_signal", "macd_hist"]):
        macd_line, signal_line, macd_hist = sdm.compute_macd(close)
        if "macd" not in enriched.columns:
            enriched["macd"] = macd_line
        if "macd_signal" not in enriched.columns:
            enriched["macd_signal"] = signal_line
        if "macd_hist" not in enriched.columns:
            enriched["macd_hist"] = macd_hist

    if "atr_14" not in enriched.columns and {"high", "low", "close"}.issubset(enriched.columns):
        enriched["atr_14"] = sdm.compute_atr(high, low, close, 14)

    if "volume_ratio" not in enriched.columns and "volume" in enriched.columns:
        baseline_volume = volume.rolling(20).mean().replace(0, pd.NA)
        enriched["volume_ratio"] = volume / baseline_volume

    return enriched


def _normalize_chart_frame(chart_df: pd.DataFrame) -> pd.DataFrame:
    if chart_df.empty:
        return chart_df

    try:
        return ohlcv_frame_to_chart_frame(chart_df)
    except Exception:
        normalized = chart_df.copy()

        if "datetime" in normalized.columns:
            normalized["datetime"] = pd.to_datetime(normalized["datetime"], errors="coerce", utc=True)

        for column in ["open", "high", "low", "close", "volume"]:
            if column in normalized.columns:
                normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

        required_columns = [column for column in ["datetime", "open", "high", "low", "close"] if column in normalized.columns]
        if required_columns:
            normalized = normalized.dropna(subset=required_columns).copy()

        if normalized.empty:
            return normalized

        normalized = normalized.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")

        price_columns = [column for column in ["open", "high", "low", "close"] if column in normalized.columns]
        if price_columns:
            normalized["high"] = normalized[price_columns].max(axis=1)
            normalized["low"] = normalized[price_columns].min(axis=1)

        if "volume" in normalized.columns:
            normalized["volume"] = normalized["volume"].fillna(0)

        return normalized.reset_index(drop=True)


def search_tickers(query: str = "", limit: int = 10) -> dict[str, Any]:
    universe = []
    seen = set()
    for ticker in [*sdm.DEFAULT_SCAN_TICKERS, *POPULAR_TICKERS]:
        cleaned = str(ticker or '').strip().upper()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            universe.append(cleaned)
    needle = str(query or '').strip().upper()
    if needle:
        prefix = [ticker for ticker in universe if ticker.startswith(needle)]
        contains = [ticker for ticker in universe if needle in ticker and ticker not in prefix]
        matches = prefix + contains
    else:
        matches = universe
    matches = matches[:max(1, min(int(limit), 50))]
    return {
        "query": needle,
        "count": len(matches),
        "results": matches,
    }



def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    if isinstance(parsed, pd.Timestamp):
        return parsed.to_pydatetime()
    if isinstance(parsed, datetime):
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _market_interval_seconds(interval: str | None) -> int:
    normalized = str(interval or "").strip().lower()
    return _INTERVAL_SECONDS.get(normalized, 60 * 5)


def _market_interval_minutes(interval: str | None) -> int:
    return max(1, _market_interval_seconds(interval) // 60)


def _coerce_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric


def _format_forecast_horizon_label(interval: str | None, horizon: int | None) -> str:
    steps = max(1, int(horizon or 1))
    total_minutes = _market_interval_minutes(interval) * steps
    duration_label = f"{total_minutes}m"
    if total_minutes >= 1440 and total_minutes % 1440 == 0:
        duration_label = f"{total_minutes // 1440}d"
    elif total_minutes >= 60 and total_minutes % 60 == 0:
        duration_label = f"{total_minutes // 60}h"
    elif total_minutes > 60:
        duration_label = f"{round(total_minutes / 60, 1)}h"
    return f"{steps} bar{'s' if steps != 1 else ''} (~{duration_label})"


def _build_forecast_benchmark_framing(
    *,
    probability_up: float | None,
    forecast: dict[str, Any] | None,
) -> dict[str, Any]:
    source = dict(forecast or {})
    journal_calibration = dict(source.get("journal_calibration") or {})
    calibration_scope = str(journal_calibration.get("calibration_scope") or "").strip().lower()
    resolved_count = int(journal_calibration.get("resolved_count") or 0)
    average_probability_up = _coerce_float(journal_calibration.get("average_probability_up"))
    technical_probability_up = _coerce_float(source.get("technical_probability_up"))

    if probability_up is not None and average_probability_up is not None and resolved_count >= 8:
        scope_label = calibration_scope.replace("_", " ").title() if calibration_scope else "Global"
        return {
            "label": f"{scope_label} baseline",
            "detail": "This forecast is being judged against resolved calibration history instead of a generic directional call.",
            "reference_probability": round(average_probability_up, 4),
        }

    if probability_up is not None and technical_probability_up is not None:
        return {
            "label": "Technical base",
            "detail": "The adjusted forecast is being measured against the raw technical model before calibration and event effects are applied.",
            "reference_probability": round(technical_probability_up, 4),
        }

    if probability_up is not None:
        return {
            "label": "Neutral 50/50",
            "detail": "Without enough resolved calibration history, the forecast should at least beat a neutral up/down baseline.",
            "reference_probability": 0.5,
        }

    return {
        "label": "No benchmark",
        "detail": "There is not enough forecast context yet to define a meaningful benchmark for this setup.",
        "reference_probability": None,
    }


def _build_forecast_framing(
    *,
    interval: str | None,
    horizon: int | None,
    forecast: dict[str, Any] | None,
    probability_up: Any = None,
) -> dict[str, Any]:
    source = dict(forecast or {})
    horizon_bars = max(1, int(horizon or source.get("forecast_horizon_bars") or 1))
    resolved_probability = _coerce_float(probability_up)
    if resolved_probability is None:
        resolved_probability = _coerce_float(source.get("adjusted_probability_up"))
    technical_probability_up = _coerce_float(source.get("technical_probability_up"))
    adjusted_expected_move = _coerce_float(source.get("adjusted_expected_move"))
    has_directional = resolved_probability is not None or technical_probability_up is not None
    has_volatility = adjusted_expected_move is not None

    if has_directional and has_volatility:
        target_family = "direction_plus_volatility"
        label = "Directional move with volatility context"
        short_label = "Direction + vol"
        use_label = "Best for ranking names under one shared horizon instead of making a blanket market call."
        trust_label = "Read the directional probability together with expected move and regime context before acting."
    elif has_directional:
        target_family = "directional_move"
        label = "Directional move"
        short_label = "Direction"
        use_label = "Best for conditional direction over the selected bar window."
        trust_label = "This is a horizon-bound directional read, not a broad market forecast."
    elif has_volatility:
        target_family = "volatility_envelope"
        label = "Volatility envelope"
        short_label = "Volatility"
        use_label = "Best for expected movement and sizing context under the selected horizon."
        trust_label = "Use this as movement context and sizing support rather than as a pure directional claim."
    else:
        target_family = "relative_setup_rank"
        label = "Relative setup rank"
        short_label = "Ranking"
        use_label = "Best for comparing setup quality across names using one common window."
        trust_label = "Treat this as a ranking surface when direct forecast detail is thin."

    benchmark = _build_forecast_benchmark_framing(
        probability_up=resolved_probability,
        forecast=source,
    )
    return {
        "target_family": target_family,
        "label": label,
        "short_label": short_label,
        "use_label": use_label,
        "trust_label": trust_label,
        "interval": str(interval or "").strip().lower(),
        "horizon_bars": horizon_bars,
        "horizon_label": _format_forecast_horizon_label(interval, horizon_bars),
        "benchmark_label": benchmark["label"],
        "benchmark_detail": benchmark["detail"],
        "benchmark_reference_probability": benchmark["reference_probability"],
    }


def _latest_bar_at_from_history_frame(frame: pd.DataFrame | None) -> Any:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None

    if "datetime" in frame.columns:
        datetime_series = pd.to_datetime(frame["datetime"], errors="coerce", utc=True).dropna()
        if not datetime_series.empty:
            return datetime_series.iloc[-1]

    if isinstance(frame.index, pd.DatetimeIndex) and len(frame.index):
        try:
            return pd.to_datetime(frame.index[-1], utc=True)
        except Exception:
            return None

    return None


def _build_execution_context(
    *,
    report: dict[str, Any] | None,
    freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = dict(report or {})
    freshness_snapshot = dict(freshness or {})
    option_plan = dict(source.get("option_plan") or {})
    contract = dict(option_plan.get("recommended_contract") or {})
    event_context = dict(source.get("event_context") or {})
    current_session = _market_session_context()

    option_side = str(option_plan.get("option_side") or "").strip().upper()
    instrument_type = "listed_option" if contract or option_side in {"CALL", "PUT"} else "equity"
    freshness_state = str(freshness_snapshot.get("status") or "unknown").strip().lower() or "unknown"
    session_state = str(freshness_snapshot.get("session") or current_session.get("session") or "unknown").strip().lower()
    session_mode = normalize_session_mode(freshness_snapshot.get("session_mode") or session_state)
    session_label = str(freshness_snapshot.get("session_label") or current_session.get("label") or "Unknown").strip() or "Unknown"
    session_profile = get_session_profile(
        session_mode,
        instrument_type=instrument_type,
        regular_hours_only=str(freshness_snapshot.get("session_policy") or "").strip().lower() == "regular_hours_only",
    )
    trade_posture = str(
        event_context.get("trade_posture")
        or ("defer" if source.get("event_risk") else "quiet")
    ).strip().lower() or "quiet"

    spread_state = "unmapped"
    spread_label = "Spread and book quality are still being confirmed."
    liquidity_state = "unmapped"
    liquidity_label = "Displayed liquidity is still being confirmed."
    summary = "Use price control until spread and liquidity are confirmed."
    route_label = "Prefer a priced limit route over immediacy."
    fill_quality = "price_control"
    fill_label = "Use price control"
    fill_tone = "warning"
    market_order_ok = False
    spread_ratio = None
    size_cap_ratio = 0.5

    if instrument_type == "listed_option":
        spread_ratio = _coerce_float(contract.get("spread_pct"))
        contract_volume = max(0, int(_coerce_float(contract.get("volume")) or 0))
        contract_open_interest = max(0, int(_coerce_float(contract.get("open_interest")) or 0))
        spread_label = (
            f"{round(spread_ratio * 100, 1)}% contract spread"
            if spread_ratio is not None
            else "Contract spread pending"
        )
        liquidity_label = (
            "Vol / OI pending"
            if contract_volume == 0 and contract_open_interest == 0
            else f"Vol {contract_volume} / OI {contract_open_interest}"
        )

        if spread_ratio is None:
            spread_state = "unmapped"
        elif spread_ratio <= 0.08:
            spread_state = "clean"
        elif spread_ratio <= 0.15:
            spread_state = "caution"
        else:
            spread_state = "fragile"

        if contract_volume >= 100 and contract_open_interest >= 500:
            liquidity_state = "supported"
        elif contract_volume >= 25 and contract_open_interest >= 100:
            liquidity_state = "watch"
        elif contract_volume > 0 or contract_open_interest > 0:
            liquidity_state = "thin"
        else:
            liquidity_state = "unmapped"
    else:
        spread_label = "Use the live quote ladder"
        liquidity_label = "Confirm displayed size before routing"

    is_regular_session = session_state == "regular"
    awaiting_regular_session = freshness_state == "awaiting_regular_session"
    is_stale = freshness_state == "stale"
    session_is_extended = session_mode in {"pre_market", "after_hours"}
    session_is_closed = session_mode == "closed"
    posture_is_defer = trade_posture == "defer"
    posture_is_caution = trade_posture == "caution"
    spread_is_fragile = spread_state == "fragile"
    liquidity_is_thin = liquidity_state == "thin"

    if awaiting_regular_session:
        fill_quality = "waiting"
        fill_label = "Await regular session"
        fill_tone = "warning"
        route_label = "Desk is intentionally waiting for core-session liquidity."
        summary = (
            "Regular-hours mode is active, so off-session bars are treated as planning context instead of a stale-feed failure."
        )
        size_cap_ratio = 0.0
    elif instrument_type == "listed_option" and not is_regular_session:
        fill_quality = "waiting"
        fill_label = "Options regular only"
        fill_tone = "warning"
        route_label = "Listed options stay locked to the regular session."
        summary = "Equity extended-hours routing is active, but listed-option orders remain regular-session only."
        size_cap_ratio = 0.0
    elif session_is_closed:
        fill_quality = "planning"
        fill_label = "Planning mode"
        fill_tone = "neutral"
        route_label = "Keep scans, reconciliation, and watchlists live; do not open new positions."
        summary = "Closed-session mode keeps the desk alive for data, planning, and replay while blocking fresh entries."
        size_cap_ratio = 0.0
    elif posture_is_defer or is_stale or spread_is_fragile or liquidity_is_thin:
        fill_quality = "fragile"
        fill_label = "Fragile fills"
        fill_tone = "negative"
        route_label = "Stand down or keep tight price control on the route."
        summary = (
            "Execution is fragile right now because session, freshness, event posture, or contract liquidity is not supporting a clean fill."
        )
        size_cap_ratio = 0.0 if posture_is_defer else 0.25
    elif instrument_type == "listed_option":
        if spread_state == "clean" and liquidity_state == "supported" and freshness_state == "fresh" and trade_posture == "quiet" and is_regular_session:
            fill_quality = "clean"
            fill_label = "Execution clean"
            fill_tone = "positive"
            route_label = "Marketable routing can work if urgency is real."
            summary = "Spread, liquidity, and session posture all support a controlled options fill."
            market_order_ok = True
            size_cap_ratio = 1.0
        else:
            fill_quality = "price_control"
            fill_label = "Use price control"
            fill_tone = "warning"
            route_label = "Prefer a priced limit route while the options book is only partly confirmed."
            summary = "Execution is workable, but spread, liquidity, or session context still calls for tighter price control."
            size_cap_ratio = 0.5 if posture_is_caution or freshness_state == "warning" else 0.75
    else:
        if session_is_extended:
            fill_quality = "price_control"
            fill_label = "Extended-hours price control"
            fill_tone = "warning"
            route_label = f"Use limit {session_profile.time_in_force.upper()} only and keep size capped for {session_profile.label.lower()}."
            summary = session_profile.detail
            size_cap_ratio = session_profile.size_cap_ratio
        elif freshness_state == "fresh" and trade_posture == "quiet" and is_regular_session:
            fill_quality = "price_control"
            fill_label = "Use price control"
            fill_tone = "warning"
            route_label = "Prefer a priced equity route until the live book confirms urgency."
            summary = "The equity setup is tradable, but the desk should still use limit-style price control instead of assuming a frictionless fill."
            size_cap_ratio = 0.75
        else:
            fill_quality = "fragile"
            fill_label = "Fragile fills"
            fill_tone = "negative"
            route_label = "Keep the route defensive and wait for cleaner session conditions."
            summary = "Session or catalyst posture is too unstable to treat the equity fill as routine."
            size_cap_ratio = 0.25 if not posture_is_defer else 0.0

    if size_cap_ratio >= 1:
        size_cap_label = "Full planned size is available."
    elif size_cap_ratio >= 0.75:
        size_cap_label = "Start under full size until the book proves out."
    elif size_cap_ratio >= 0.5:
        size_cap_label = "Half size until fills improve."
    elif size_cap_ratio > 0:
        size_cap_label = "Quarter size or less until the route stabilizes."
    else:
        size_cap_label = "Stand down until the context resets."

    return {
        "instrument_type": instrument_type,
        "fill_quality": fill_quality,
        "fill_label": fill_label,
        "fill_tone": fill_tone,
        "summary": summary,
        "route_label": route_label,
        "preferred_order_type": "limit",
        "market_order_ok": market_order_ok,
        "spread_state": spread_state,
        "spread_label": spread_label,
        "spread_ratio": serialize_value(spread_ratio),
        "liquidity_state": liquidity_state,
        "liquidity_label": liquidity_label,
        "session_state": session_state,
        "session_mode": session_mode,
        "session_label": session_label,
        "freshness_state": freshness_state,
        "freshness_label": str(freshness_snapshot.get("message") or "").strip(),
        "trade_posture": trade_posture,
        "size_cap_ratio": serialize_value(size_cap_ratio),
        "size_cap_label": size_cap_label,
        "session_profile": session_profile.to_record(),
    }


def _build_forecast_timestamps(
    latest_bar_at: Any,
    *,
    interval: str,
    horizon: int,
) -> list[str]:
    latest_dt = _coerce_utc_datetime(latest_bar_at)
    if latest_dt is None:
        return []
    interval_seconds = _market_interval_seconds(interval)
    if interval_seconds <= 0:
        return []
    points: list[str] = []
    for step in range(1, max(1, int(horizon)) + 1):
        points.append((latest_dt + pd.Timedelta(seconds=interval_seconds * step)).isoformat())
    return points


def _build_chart_forecast_payload(
    *,
    forecast: dict[str, Any] | None,
    latest_bar_at: Any,
    interval: str,
    latest_close: float | None,
) -> dict[str, Any]:
    source = dict(forecast or {})
    horizon = max(1, int(source.get("forecast_horizon_bars") or 0))
    expected_price = serialize_value(source.get("expected_price"))
    upper_price = serialize_value(source.get("upper_price"))
    lower_price = serialize_value(source.get("lower_price"))
    timestamps = _build_forecast_timestamps(latest_bar_at, interval=interval, horizon=horizon)
    anchor_price = serialize_value(latest_close)
    points = []
    for index, timestamp in enumerate(timestamps):
        progress = (index + 1) / max(1, horizon)
        eased_progress = progress ** 0.85
        expected_value = None
        upper_value = None
        lower_value = None
        if isinstance(anchor_price, (int, float)) and isinstance(expected_price, (int, float)):
            expected_value = round(float(anchor_price) + (float(expected_price) - float(anchor_price)) * eased_progress, 4)
        if isinstance(anchor_price, (int, float)) and isinstance(upper_price, (int, float)):
            upper_value = round(float(anchor_price) + (float(upper_price) - float(anchor_price)) * (progress ** 0.7), 4)
        if isinstance(anchor_price, (int, float)) and isinstance(lower_price, (int, float)):
            lower_value = round(float(anchor_price) + (float(lower_price) - float(anchor_price)) * (progress ** 0.7), 4)
        points.append(
            {
                "datetime": timestamp,
                "expected_price": expected_value,
                "upper_price": upper_value,
                "lower_price": lower_value,
            }
        )

    return {
        **{key: serialize_value(value) for key, value in source.items()},
        "points": points,
    }


def _is_insufficient_training_data_error(error: Exception) -> bool:
    message = str(error or "").lower()
    return "too little clean training data" in message or "insufficient training data" in message


def _build_lightweight_chart_analysis_snapshot(
    *,
    ticker: str,
    interval: str,
    latest_close: float | None,
    error: Exception,
) -> dict[str, Any]:
    note = str(error or "").strip() or "Insufficient historical data for chart-side analysis."
    return {
        "ticker": str(ticker or "").upper(),
        "interval": str(interval or "").lower(),
        "close": serialize_value(latest_close),
        "market_regime": "unknown",
        "forecast": {
            "forecast_horizon_bars": 5,
            "market_regime": "unknown",
            "label": "Unavailable",
            "confidence_score": 0.0,
        },
        "news_sentiment": {},
        "notes": [note],
    }


def _market_session_context(now_utc: datetime | None = None) -> dict[str, Any]:
    return build_market_session_context(now_utc)


def _clamp_ranking_score(value: Any, default: float = 50.0) -> float:
    numeric = _coerce_float(value)
    if numeric is None:
        return float(default)
    return max(0.0, min(100.0, float(numeric)))


def _build_ranking_context(
    *,
    ticker: str,
    report: dict[str, Any] | None,
    execution_context: dict[str, Any] | None,
) -> dict[str, Any]:
    source = dict(report or {})
    forecast = dict(source.get("forecast") or {})
    event_context = dict(source.get("event_context") or {})
    execution = dict(execution_context or {})
    calibration = dict(forecast.get("journal_calibration") or {})

    setup_score = _coerce_float(source.get("setup_score"))
    probability_up = _coerce_float(source.get("probability_up"))
    alpha_score = _coerce_float(source.get("alpha_score"))
    model_execution_score = _coerce_float(source.get("execution_score"))
    base_portfolio_score = _coerce_float(source.get("portfolio_score"))
    edge_to_cost_ratio = _coerce_float(source.get("edge_to_cost_ratio"))
    trend_score = _clamp_ranking_score(
        alpha_score if alpha_score is not None else (setup_score if setup_score is not None else ((probability_up or 0.5) * 100.0))
    )

    trade_posture = str(event_context.get("trade_posture") or "").strip().lower() or (
        "defer" if source.get("event_risk") else "clear"
    )
    event_severity = str(event_context.get("event_severity") or "").strip().lower()
    event_base = {
        "clear": 88.0,
        "quiet": 88.0,
        "watch": 68.0,
        "caution": 56.0,
        "defer": 22.0,
    }.get(trade_posture, 62.0)
    severity_multiplier = {
        "critical": 0.4,
        "high": 0.6,
        "medium": 0.8,
        "low": 0.95,
    }.get(event_severity, 1.0)
    event_score = _clamp_ranking_score(event_base * severity_multiplier)

    regime_strength_score = _coerce_float(forecast.get("regime_strength_score"))
    market_regime = str(forecast.get("market_regime") or source.get("market_regime") or "").strip().lower()
    volatility_score = _clamp_ranking_score(
        58.0 if regime_strength_score is None else regime_strength_score * 100.0,
        default=58.0,
    )
    if any(token in market_regime for token in ("unstable", "volatile", "shock", "risk_off", "choppy")):
        volatility_score = min(volatility_score, 42.0)
    elif any(token in market_regime for token in ("calm", "stable", "trend", "constructive")):
        volatility_score = max(volatility_score, 72.0)

    fill_tone = str(execution.get("fill_tone") or "").strip().lower()
    execution_score = model_execution_score if model_execution_score is not None else {
        "positive": 86.0,
        "warning": 58.0,
        "negative": 24.0,
    }.get(fill_tone, 52.0)
    size_cap_ratio = _coerce_float(execution.get("size_cap_ratio"))
    if size_cap_ratio is not None:
        execution_score = execution_score * 0.7 + (_clamp_ranking_score(size_cap_ratio * 100.0) * 0.3)
    execution_score = _clamp_ranking_score(execution_score)

    resolved_count = max(0, int(_coerce_float(calibration.get("resolved_count")) or 0))
    average_error = _coerce_float(calibration.get("average_error"))
    empirical_hit_rate = _coerce_float(calibration.get("empirical_hit_rate"))
    average_probability_up = _coerce_float(calibration.get("average_probability_up"))
    calibration_score = 42.0
    if resolved_count >= 20:
        calibration_score += 20.0
    elif resolved_count >= 8:
        calibration_score += 12.0
    elif resolved_count > 0:
        calibration_score += 5.0
    if average_error is not None:
        if average_error <= 0.12:
            calibration_score += 22.0
        elif average_error <= 0.2:
            calibration_score += 10.0
        elif average_error > 0.3:
            calibration_score -= 16.0
        elif average_error > 0.24:
            calibration_score -= 8.0
    if empirical_hit_rate is not None and average_probability_up is not None:
        edge = empirical_hit_rate - average_probability_up
        if edge >= 0.04:
            calibration_score += 12.0
        elif edge >= 0:
            calibration_score += 6.0
        elif edge < -0.04:
            calibration_score -= 12.0
        else:
            calibration_score -= 6.0
    calibration_score = _clamp_ranking_score(calibration_score)

    portfolio_score = _clamp_ranking_score(
        base_portfolio_score
        if base_portfolio_score is not None
        else ((trend_score * 0.55) + (execution_score * 0.45))
    )
    if edge_to_cost_ratio is not None:
        portfolio_score = _clamp_ranking_score(
            (portfolio_score * 0.88) + (_clamp_ranking_score(min(edge_to_cost_ratio, 4.0) / 4.0 * 100.0) * 0.12)
        )

    total_score = round(
        (portfolio_score * 0.45)
        + (trend_score * 0.15)
        + (event_score * 0.15)
        + (volatility_score * 0.1)
        + (execution_score * 0.1)
        + (calibration_score * 0.05),
        1,
    )

    trade_decision = str(source.get("trade_decision") or "").strip().upper()
    auto_entry_eligible = bool(source.get("auto_entry_eligible"))
    if total_score >= 78.0 and trade_decision == "VALID TRADE" and auto_entry_eligible and trade_posture != "defer" and fill_tone != "negative":
        tier = "promote"
        tone = "positive"
        label = "Promote first"
    elif total_score >= 58.0 and fill_tone != "negative":
        tier = "review"
        tone = "warning"
        label = "Reviewable"
    else:
        tier = "stand_down"
        tone = "negative"
        label = "Stand down"

    component_rows = [
        ("trend", trend_score),
        ("event", event_score),
        ("volatility", volatility_score),
        ("execution", execution_score),
        ("calibration", calibration_score),
    ]
    strongest_component = max(component_rows, key=lambda item: item[1])[0]
    weakest_component = min(component_rows, key=lambda item: item[1])[0]
    component_summary = " / ".join(f"{name[:4].upper()} {round(score)}" for name, score in component_rows)
    summary = (
        f"{strongest_component.title()} is carrying the setup while {weakest_component.title()} remains the main drag."
        if strongest_component != weakest_component
        else f"{strongest_component.title()} is setting the tone for this setup."
    )
    controlled_universe = ticker in set(sdm.get_controlled_liquid_universe())
    return {
        "board_name": "Controlled liquid ranking board",
        "board_short_name": "Liquid board",
        "controlled_universe": controlled_universe,
        "tier": tier,
        "tone": tone,
        "label": label,
        "score": total_score,
        "summary": summary,
        "component_summary": component_summary,
        "component_leader": strongest_component,
        "component_drag": weakest_component,
        "trend_score": round(trend_score, 1),
        "event_score": round(event_score, 1),
        "volatility_score": round(volatility_score, 1),
        "execution_score": round(execution_score, 1),
        "calibration_score": round(calibration_score, 1),
        "alpha_score": round(trend_score, 1),
        "portfolio_score": round(portfolio_score, 1),
        "edge_to_cost_ratio": edge_to_cost_ratio,
        "proxy_correlation_bucket": str(source.get("proxy_correlation_bucket") or ""),
        "auto_entry_eligible": auto_entry_eligible,
    }


def _sort_and_annotate_ranking_rows(
    rows: list[dict[str, Any]],
    *,
    sort_by: str = "ranking_score",
    descending: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not rows:
        return [], {
            "board_name": "Controlled liquid ranking board",
            "sort_by": sort_by,
            "count": 0,
            "promote_count": 0,
            "review_count": 0,
            "stand_down_count": 0,
            "controlled_universe_count": 0,
            "coverage_ratio": 0.0,
            "leader": None,
        }

    df = pd.DataFrame(rows)
    effective_sort_by = str(sort_by or "").strip() if str(sort_by or "").strip() in df.columns else ""
    if not effective_sort_by:
        effective_sort_by = "ranking_score" if "ranking_score" in df.columns else "setup_score"
    secondary_fields = [
        field for field in ["ranking_score", "setup_score", "probability_up"] if field in df.columns and field != effective_sort_by
    ]
    sort_fields = [effective_sort_by, *secondary_fields]
    ascending = [not descending, *([False] * len(secondary_fields))]
    df = df.sort_values(by=sort_fields, ascending=ascending, na_position="last")

    records = df.to_dict("records")
    leader_score = _coerce_float(records[0].get("ranking_score"))
    promote_count = 0
    review_count = 0
    stand_down_count = 0
    controlled_universe_count = 0
    for index, row in enumerate(records, start=1):
        ranking_score = _coerce_float(row.get("ranking_score"))
        ranking_gap = None if leader_score is None or ranking_score is None else round(max(0.0, leader_score - ranking_score), 1)
        ranking_context = dict(row.get("ranking_context") or {})
        tier = str(ranking_context.get("tier") or row.get("ranking_tier") or "review").strip().lower()
        if tier == "promote":
            promote_count += 1
        elif tier == "stand_down":
            stand_down_count += 1
        else:
            review_count += 1
        if bool(ranking_context.get("controlled_universe")):
            controlled_universe_count += 1
        ranking_context.update(
            {
                "board_rank": index,
                "board_gap": ranking_gap,
                "leader": index == 1,
            }
        )
        row["board_rank"] = index
        row["ranking_gap"] = ranking_gap
        row["ranking_context"] = ranking_context
        row["ranking_tier"] = ranking_context.get("tier")
        row["ranking_label"] = ranking_context.get("label")

    coverage_ratio = round(controlled_universe_count / len(records), 2) if records else 0.0
    leader_row = records[0] if records else None
    return records, {
        "board_name": "Controlled liquid ranking board",
        "sort_by": effective_sort_by,
        "count": len(records),
        "promote_count": promote_count,
        "review_count": review_count,
        "stand_down_count": stand_down_count,
        "controlled_universe_count": controlled_universe_count,
        "coverage_ratio": coverage_ratio,
        "leader": (
            {
                "ticker": leader_row.get("ticker"),
                "ranking_score": leader_row.get("ranking_score"),
                "ranking_label": leader_row.get("ranking_label"),
            }
            if leader_row
            else None
        ),
    }


def _build_candidate_board_validation_artifact(
    *,
    source: str,
    board_name: str,
    interval: str,
    horizon: int,
    rows: list[dict[str, Any]],
    ranking_board: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_rows = list(rows or [])
    board_meta = dict(ranking_board or {})
    ranking_values = [
        _coerce_float(row.get("ranking_score") or (row.get("ranking_context") or {}).get("score"))
        for row in normalized_rows
        if _coerce_float(row.get("ranking_score") or (row.get("ranking_context") or {}).get("score")) is not None
    ]
    event_window_count = 0
    fragile_execution_count = 0
    review_gate_count = 0
    for row in normalized_rows:
        event_context = dict(row.get("event_context") or {})
        trade_posture = str(event_context.get("trade_posture") or row.get("trade_posture") or "").strip().lower()
        if bool(row.get("event_risk")) or trade_posture in {"caution", "defer"}:
            event_window_count += 1
        execution_context = dict(row.get("execution_context") or {})
        fill_tone = str(execution_context.get("fill_tone") or row.get("execution_fill_tone") or "").strip().lower()
        if fill_tone in {"warning", "negative"}:
            fragile_execution_count += 1
        ranking_tier = str(row.get("ranking_tier") or (row.get("ranking_context") or {}).get("tier") or "").strip().lower()
        if ranking_tier == "review":
            review_gate_count += 1

    leader_row = normalized_rows[0] if normalized_rows else None
    artifact_rows: list[dict[str, Any]] = []
    for row in normalized_rows[:6]:
        ranking_context = dict(row.get("ranking_context") or {})
        event_context = dict(row.get("event_context") or {})
        execution_context = dict(row.get("execution_context") or {})
        artifact_rows.append(
            {
                "ticker": row.get("ticker"),
                "board_rank": row.get("board_rank") or ranking_context.get("board_rank"),
                "ranking_score": serialize_value(row.get("ranking_score") or ranking_context.get("score")),
                "ranking_label": row.get("ranking_label") or ranking_context.get("label"),
                "ranking_tier": row.get("ranking_tier") or ranking_context.get("tier"),
                "ranking_summary": row.get("ranking_summary") or ranking_context.get("summary"),
                "trade_decision": row.get("trade_decision"),
                "event_label": row.get("event_label") or event_context.get("primary_event_label"),
                "event_risk": bool(row.get("event_risk") or event_context.get("event_risk")),
                "next_event_name": row.get("next_event_name") or event_context.get("next_event_name"),
                "next_event_date": row.get("next_event_date") or event_context.get("next_event_date"),
                "next_event_days": serialize_value(row.get("next_event_days") or event_context.get("next_event_days")),
                "execution_label": execution_context.get("fill_label") or row.get("execution_fill_label"),
                "execution_tone": execution_context.get("fill_tone") or row.get("execution_fill_tone"),
            }
        )

    return {
        "artifact_type": "candidate_board_snapshot",
        "source": str(source or "board").strip().lower() or "board",
        "board_name": str(board_name or board_meta.get("board_name") or "Controlled liquid ranking board").strip(),
        "captured_at": utc_now_iso(),
        "interval": str(interval or "").strip().lower(),
        "horizon": int(horizon or 0),
        "leader": serialize_value(
            {
                "ticker": leader_row.get("ticker"),
                "ranking_score": leader_row.get("ranking_score"),
                "ranking_label": leader_row.get("ranking_label"),
                "ranking_tier": leader_row.get("ranking_tier"),
            }
            if leader_row
            else None
        ),
        "summary": {
            "candidate_count": len(normalized_rows),
            "promote_count": int(board_meta.get("promote_count", 0) or 0),
            "review_count": int(board_meta.get("review_count", 0) or 0),
            "stand_down_count": int(board_meta.get("stand_down_count", 0) or 0),
            "event_window_count": event_window_count,
            "fragile_execution_count": fragile_execution_count,
            "review_gate_count": review_gate_count,
            "average_ranking_score": round(sum(ranking_values) / len(ranking_values), 2) if ranking_values else None,
        },
        "rows": serialize_value(artifact_rows),
    }


def _build_market_data_freshness_snapshot(
    *,
    ticker: str,
    interval: str,
    latest_bar_at: Any,
    point_count: int,
    regular_hours_only: bool = False,
    source: str = "history",
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    checked_at_utc = checked_at or datetime.now(timezone.utc)
    latest_dt = _coerce_utc_datetime(latest_bar_at)
    session = _market_session_context(checked_at_utc)
    session_profile = get_session_profile(
        session.get("session_mode") or session.get("session"),
        instrument_type="equity",
        regular_hours_only=bool(regular_hours_only),
    )
    interval_seconds = _market_interval_seconds(interval)
    warning_threshold_seconds = interval_seconds * max(1, int(settings.market_freshness_warning_multiplier))
    stale_threshold_seconds = interval_seconds * max(
        int(settings.market_freshness_stale_multiplier),
        int(settings.market_freshness_warning_multiplier) + 1,
    )
    latest_age_seconds = None
    latest_age_minutes = None
    if latest_dt is not None:
        latest_age_seconds = max(0, int((checked_at_utc - latest_dt).total_seconds()))
        latest_age_minutes = round(latest_age_seconds / 60, 1)

    awaiting_regular_session = bool(regular_hours_only) and session["session"] in {
        "premarket",
        "after_hours",
        "weekend",
        "closed",
    }

    if latest_dt is None:
        if awaiting_regular_session:
            status = "awaiting_regular_session"
            message = (
                f"No recent {interval} bars were returned for {ticker}; the desk is regular-hours only and is waiting for the next core session."
            )
        else:
            status = "stale" if session["feed_expected"] else "idle"
            message = (
                f"No recent {interval} bars were returned for {ticker} while the market feed should be active."
                if session["feed_expected"]
                else f"No recent {interval} bars were returned for {ticker}, but the market feed is currently outside the active session."
            )
    elif awaiting_regular_session:
        status = "awaiting_regular_session"
        message = (
            f"Latest {interval} bar for {ticker} is {latest_age_minutes} minutes old; the desk is regular-hours only and is waiting for core-session bars."
        )
    elif not session["feed_expected"]:
        status = "idle"
        message = f"Latest {interval} bar for {ticker} is {latest_age_minutes} minutes old; feed is currently outside the active session."
    elif latest_age_seconds is not None and latest_age_seconds > stale_threshold_seconds:
        status = "stale"
        message = f"Latest {interval} bar for {ticker} is {latest_age_minutes} minutes old, which exceeds the stale threshold."
    elif latest_age_seconds is not None and latest_age_seconds > warning_threshold_seconds:
        status = "warning"
        message = f"Latest {interval} bar for {ticker} is {latest_age_minutes} minutes old and may indicate a lagging feed."
    else:
        status = "fresh"
        message = f"Latest {interval} bar for {ticker} is within the expected freshness window."

    return {
        "ticker": str(ticker or "").upper(),
        "interval": str(interval or "").lower(),
        "status": status,
        "warning": status == "warning",
        "stale": status == "stale",
        "feed_expected": bool(session["feed_expected"]) and not awaiting_regular_session,
        "session": session["session"],
        "session_mode": session.get("session_mode") or normalize_session_mode(session["session"]),
        "session_label": session["label"],
        "session_policy": "regular_hours_only" if regular_hours_only else "session_flex",
        "session_profile": session_profile.to_record(),
        "latest_bar_at": latest_dt.isoformat() if latest_dt is not None else None,
        "latest_bar_age_seconds": latest_age_seconds,
        "latest_bar_age_minutes": latest_age_minutes,
        "warning_threshold_seconds": warning_threshold_seconds,
        "stale_threshold_seconds": stale_threshold_seconds,
        "point_count": int(point_count or 0),
        "source": source,
        "checked_at": checked_at_utc.isoformat(),
        "checked_at_et": session["checked_at_et"],
        "message": message,
    }


def make_settings(ticker: str, horizon: int, interval: str) -> sdm.ModelConfig:
    period = sdm.get_period_for_interval(interval)
    threshold_up, threshold_down = sdm.get_thresholds_for_interval(interval)

    min_rows = 180
    if interval == "1m":
        min_rows = 120
    elif interval == "4h":
        min_rows = 140

    return sdm.ModelConfig(
        ticker=ticker,
        horizon=horizon,
        interval=interval,
        period=period,
        threshold_up=threshold_up,
        threshold_down=threshold_down,
        min_rows=min_rows,
    )


def get_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": APP_NAME,
        "version": APP_VERSION,
        "timestamp": utc_now_iso(),
    }


def get_defaults() -> dict[str, Any]:
    return {
        "default_scan_tickers": sdm.DEFAULT_SCAN_TICKERS,
        "controlled_liquid_universe": sdm.get_controlled_liquid_universe(),
        "supported_intervals": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
        "default_interval": "5m",
        "default_horizon": 5,
        "live_update_seconds": int(sdm.LIVE_PRICE_CACHE_TTL_SECONDS),
        "realtime": get_realtime_capabilities(),
    }


def get_history(ticker: str, interval: str) -> dict[str, Any]:
    settings_obj = make_settings(ticker=ticker, horizon=5, interval=interval)
    frame = _run_upstream_market_call(
        target="market-data",
        operation="download_ohlcv",
        context={"ticker": str(ticker or "").upper(), "interval": settings_obj.interval, "period": settings_obj.period},
        func=lambda: sdm.download_ohlcv(ticker, settings_obj.period, settings_obj.interval),
    )
    if frame.empty:
        raise NotFoundError(f"No history returned for {ticker}.")
    return {
        "ticker": ticker,
        "interval": interval,
        "period": settings_obj.period,
        "rows": len(frame),
        "history": serialize_dataframe(frame.reset_index()),
    }


def _build_regime_aware_report(
    settings_obj: Any,
    history: pd.DataFrame,
    *,
    ticker: str,
    interval: str,
    include_contract_lookup: bool,
    include_event_lookup: bool,
    include_alignment: bool,
    fast_mode: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved_ticker = str(getattr(settings_obj, "ticker", "") or ticker or "").upper()
    resolved_interval = str(getattr(settings_obj, "interval", "") or interval or "").lower()
    has_required_model_fields = all(
        hasattr(settings_obj, field)
        for field in (
            "ticker",
            "period",
            "interval",
            "horizon",
            "threshold_up",
            "threshold_down",
            "min_rows",
            "chart_days",
        )
    )
    if has_required_model_fields:
        report_settings = settings_obj
    else:
        period = str(getattr(settings_obj, "period", "") or sdm.get_period_for_interval(resolved_interval))
        threshold_up, threshold_down = sdm.get_thresholds_for_interval(resolved_interval)
        horizon = int(getattr(settings_obj, "horizon", 5) or 5)
        min_rows = int(getattr(settings_obj, "min_rows", 180) or 180)
        if resolved_interval == "1m":
            min_rows = int(getattr(settings_obj, "min_rows", 120) or 120)
        elif resolved_interval == "4h":
            min_rows = int(getattr(settings_obj, "min_rows", 140) or 140)
        report_settings = sdm.ModelConfig(
            ticker=resolved_ticker,
            period=period,
            interval=resolved_interval,
            horizon=horizon,
            threshold_up=float(getattr(settings_obj, "threshold_up", threshold_up) or threshold_up),
            threshold_down=float(getattr(settings_obj, "threshold_down", threshold_down) or threshold_down),
            min_rows=min_rows,
            chart_days=int(getattr(settings_obj, "chart_days", sdm.ModelConfig.chart_days) or sdm.ModelConfig.chart_days),
            scan_top_n=int(getattr(settings_obj, "scan_top_n", sdm.ModelConfig.scan_top_n) or sdm.ModelConfig.scan_top_n),
            scan_output_file=str(
                getattr(settings_obj, "scan_output_file", sdm.ModelConfig.scan_output_file)
                or sdm.ModelConfig.scan_output_file
            ),
        )
    broad_calibration = sdm.journal_probability_calibration_summary(
        resolved_ticker,
        resolved_interval,
    )
    report = sdm.analyze_ticker(
        report_settings,
        preloaded_price_frame=history,
        include_contract_lookup=include_contract_lookup,
        include_event_lookup=include_event_lookup,
        include_alignment=include_alignment,
        fast_mode=fast_mode,
        journal_calibration=broad_calibration,
    )
    market_regime = str(
        report.get("market_regime") or report.get("forecast", {}).get("market_regime") or ""
    ).strip().lower()
    if not market_regime:
        return report, broad_calibration

    regime_calibration = sdm.journal_probability_calibration_summary(
        resolved_ticker,
        resolved_interval,
        market_regime=market_regime,
    )
    if regime_calibration.get("calibration_scope") == "regime":
        report = sdm.analyze_ticker(
            report_settings,
            preloaded_price_frame=history,
            include_contract_lookup=include_contract_lookup,
            include_event_lookup=include_event_lookup,
            include_alignment=include_alignment,
            fast_mode=fast_mode,
            journal_calibration=regime_calibration,
        )
        return report, regime_calibration
    return report, broad_calibration


def get_chart_payload(request: ChartRequest) -> dict[str, Any]:
    cache_key = (
        "chart",
        str(request.ticker or "").upper(),
        str(request.interval or "").lower(),
        int(request.points_limit or 0),
        bool(request.regular_hours_only),
    )
    context = {
        "ticker": str(request.ticker or "").upper(),
        "interval": str(request.interval or "").lower(),
        "points_limit": int(request.points_limit or 0),
        "regular_hours_only": bool(request.regular_hours_only),
    }

    def _build_payload() -> dict[str, Any]:
        settings_obj = make_settings(request.ticker, horizon=5, interval=request.interval)
        frame = _run_upstream_market_call(
            target="market-data",
            operation="download_ohlcv",
            context={"ticker": str(request.ticker or "").upper(), "interval": settings_obj.interval, "period": settings_obj.period},
            func=lambda: sdm.download_ohlcv(request.ticker, settings_obj.period, settings_obj.interval),
        )
        if frame.empty:
            raise NotFoundError(f"No chart data returned for {request.ticker}.")

        chart_df = _normalize_chart_frame(frame)
        chart_df = _with_chart_indicators(chart_df)
        strategy_snapshot = build_intraday_momentum_snapshot(
            request.ticker,
            chart_df=chart_df,
        )
        latest_close = chart_df["close"].iloc[-1] if not chart_df.empty and "close" in chart_df.columns else None
        try:
            analysis_result = _run_upstream_market_call(
                target="market-analysis",
                operation="analyze_ticker",
                context={"ticker": str(request.ticker or "").upper(), "interval": settings_obj.interval},
                func=lambda: _build_regime_aware_report(
                    settings_obj,
                    frame,
                    ticker=request.ticker,
                    interval=request.interval,
                    include_contract_lookup=False,
                    include_event_lookup=False,
                    include_alignment=False,
                    fast_mode=True,
                ),
            )
            analysis_snapshot = analysis_result[0] if isinstance(analysis_result, tuple) else analysis_result
            if not isinstance(analysis_snapshot, dict):
                raise ValueError("Chart analysis did not return a payload.")
        except Exception as exc:
            analysis_snapshot = _build_lightweight_chart_analysis_snapshot(
                ticker=request.ticker,
                interval=request.interval,
                latest_close=latest_close,
                error=exc,
            )
        indicators = []
        preferred = [
            "ema_9",
            "ema_21",
            "ema_50",
            "ema_200",
            "sma_20",
            "sma_50",
            "sma_200",
            "rsi_14",
            "macd",
            "macd_signal",
            "macd_hist",
            "atr_14",
            "volume_ratio",
        ]
        for column in preferred:
            if column in chart_df.columns:
                indicators.append(column)
        selected_cols = [col for col in ["datetime", "open", "high", "low", "close", "volume", *indicators] if col in chart_df.columns]
        chart_df = chart_df[selected_cols]
        if request.points_limit and len(chart_df) > request.points_limit:
            chart_df = chart_df.tail(request.points_limit).reset_index(drop=True)

        overlays = {}
        for column in indicators:
            overlays[column] = [serialize_value(v) for v in chart_df[column].tolist()]
        for name, series in (strategy_snapshot.get("overlays") or {}).items():
            overlays[name] = [serialize_value(value) for value in series]
            if name not in indicators:
                indicators.append(name)

        candles = []
        for row in chart_df.to_dict(orient="records"):
            candles.append({
                "datetime": serialize_value(row.get("datetime")),
                "open": serialize_value(row.get("open")),
                "high": serialize_value(row.get("high")),
                "low": serialize_value(row.get("low")),
                "close": serialize_value(row.get("close")),
                "volume": serialize_value(row.get("volume")),
            })
        freshness = _build_market_data_freshness_snapshot(
            ticker=request.ticker,
            interval=request.interval,
            latest_bar_at=chart_df["datetime"].iloc[-1] if not chart_df.empty and "datetime" in chart_df.columns else None,
            point_count=len(candles),
            regular_hours_only=bool(request.regular_hours_only),
            source="chart",
        )
        latest_bar_at = chart_df["datetime"].iloc[-1] if not chart_df.empty and "datetime" in chart_df.columns else None
        forecast = _build_chart_forecast_payload(
            forecast=analysis_snapshot.get("forecast") if isinstance(analysis_snapshot, dict) else None,
            latest_bar_at=latest_bar_at,
            interval=request.interval,
            latest_close=serialize_value(latest_close),
        )
        forecast_framing = _build_forecast_framing(
            interval=request.interval,
            horizon=forecast.get("forecast_horizon_bars") if isinstance(forecast, dict) else 5,
            forecast=forecast if isinstance(forecast, dict) else None,
            probability_up=analysis_snapshot.get("probability_up") if isinstance(analysis_snapshot, dict) else None,
        )
        execution_context = _build_execution_context(
            report=analysis_snapshot if isinstance(analysis_snapshot, dict) else None,
            freshness=freshness,
        )

        return {
            "ticker": request.ticker,
            "interval": request.interval,
            "period": settings_obj.period,
            "extended_hours": request.interval != "1d",
            "regular_hours_only": bool(request.regular_hours_only),
            "point_count": len(candles),
            "candles": candles,
            "overlays": overlays,
            "available_indicators": indicators,
            "strategy": serialize_value(strategy_snapshot),
            "forecast": forecast,
            "forecast_framing": forecast_framing,
            "execution_context": serialize_value(execution_context),
            "event_context": serialize_value(analysis_snapshot.get("event_context") if isinstance(analysis_snapshot, dict) else {}),
            "news_sentiment": serialize_value(analysis_snapshot.get("news_sentiment") if isinstance(analysis_snapshot, dict) else {}),
            "freshness": freshness,
        }

    return _run_market_operation(
        name="market.chart_payload",
        cache_key=cache_key,
        context=context,
        builder=_build_payload,
    )


def get_market_data_freshness_snapshot(
    ticker: str | None = None,
    interval: str | None = None,
    regular_hours_only: bool = False,
) -> dict[str, Any]:
    resolved_ticker = str(ticker or settings.market_freshness_probe_ticker or "SPY").strip().upper() or "SPY"
    resolved_interval = str(interval or settings.market_freshness_probe_interval or "5m").strip().lower() or "5m"
    cache_key = ("market_freshness", resolved_ticker, resolved_interval, bool(regular_hours_only))
    context = {
        "ticker": resolved_ticker,
        "interval": resolved_interval,
        "regular_hours_only": bool(regular_hours_only),
    }

    def _build_payload() -> dict[str, Any]:
        settings_obj = make_settings(resolved_ticker, horizon=5, interval=resolved_interval)
        frame = _run_upstream_market_call(
            target="market-data",
            operation="download_ohlcv",
            context={"ticker": resolved_ticker, "interval": settings_obj.interval, "period": settings_obj.period, "probe": True},
            func=lambda: sdm.download_ohlcv(resolved_ticker, settings_obj.period, settings_obj.interval),
        )
        if frame.empty:
            return _build_market_data_freshness_snapshot(
                ticker=resolved_ticker,
                interval=resolved_interval,
                latest_bar_at=None,
                point_count=0,
                regular_hours_only=bool(regular_hours_only),
                source="probe",
            )
        chart_df = _normalize_chart_frame(frame)
        latest_bar_at = chart_df["datetime"].iloc[-1] if not chart_df.empty and "datetime" in chart_df.columns else None
        return _build_market_data_freshness_snapshot(
            ticker=resolved_ticker,
            interval=resolved_interval,
            latest_bar_at=latest_bar_at,
            point_count=len(chart_df),
            regular_hours_only=bool(regular_hours_only),
            source="probe",
        )

    return _run_market_operation(
        name="market.freshness",
        cache_key=cache_key,
        context=context,
        builder=_build_payload,
    )


def apply_chart_entitlements(payload: dict[str, Any], *, advanced_indicators_enabled: bool) -> dict[str, Any]:
    next_payload = dict(payload)
    if advanced_indicators_enabled:
        next_payload["capabilities"] = {"advanced_indicators": True}
        return next_payload

    overlays = {
        key: value
        for key, value in (payload.get("overlays") or {}).items()
        if key in _BASIC_CHART_INDICATORS
    }
    indicators = [
        key
        for key in (payload.get("available_indicators") or [])
        if key in _BASIC_CHART_INDICATORS
    ]
    strategy = payload.get("strategy") if isinstance(payload.get("strategy"), dict) else {}
    next_payload["overlays"] = overlays
    next_payload["available_indicators"] = indicators
    next_payload["strategy"] = {
        **strategy,
        "available": False,
        "restricted": True,
        "message": "Upgrade this tenant to Pro or above to unlock lower panes, strategy overlays, and advanced studies.",
        "overlays": {},
    }
    next_payload["capabilities"] = {"advanced_indicators": False}
    return next_payload


def _entitlement_messages(*, advanced_indicators_enabled: bool, broker_execution_enabled: bool) -> list[str]:
    messages: list[str] = []
    if not advanced_indicators_enabled:
        messages.append("Upgrade this tenant to Pro or above to unlock advanced studies, lower panes, and strategy overlays.")
    if not broker_execution_enabled:
        messages.append("Upgrade this tenant to Team or above to unlock execution planning, contract selection, and live trade rails.")
    return messages


def _sanitize_signal_row(
    row: dict[str, Any],
    *,
    advanced_indicators_enabled: bool,
    broker_execution_enabled: bool,
) -> dict[str, Any]:
    next_row = dict(row)
    if not advanced_indicators_enabled:
        for key in _ADVANCED_SIGNAL_FIELDS:
            if key in next_row:
                next_row[key] = None
    if not broker_execution_enabled:
        for key in _EXECUTION_SIGNAL_FIELDS:
            if key in next_row:
                next_row[key] = None
        if "execution_action" in next_row:
            next_row["execution_action"] = None
        if "trade_status" in next_row:
            next_row["trade_status"] = None
    return next_row


def apply_analysis_entitlements(
    payload: dict[str, Any],
    *,
    advanced_indicators_enabled: bool,
    broker_execution_enabled: bool,
) -> dict[str, Any]:
    next_payload = dict(payload)
    report = dict(next_payload.get("report") or {})
    if not advanced_indicators_enabled:
        for key in _ADVANCED_SIGNAL_FIELDS:
            if key in report:
                report[key] = None
        report["advanced_restricted"] = True
    if not broker_execution_enabled:
        option_plan = dict(report.get("option_plan") or {})
        for key in ("entry_low_price", "entry_high_price", "expected_underlying_target", "stop_loss", "contracts", "contract_lookup"):
            if key in option_plan:
                option_plan[key] = None
        if "recommended_contract" in option_plan:
            option_plan["recommended_contract"] = {}
        option_plan["restricted"] = True
        option_plan["message"] = "Upgrade this tenant to Team or above to unlock execution planning and contract selection."
        report["option_plan"] = option_plan
        next_payload["trade_status"] = None
        next_payload["execution_decision"] = None
        next_payload["alerts"] = []
    next_payload["report"] = report
    next_payload["capabilities"] = {
        "advanced_indicators": advanced_indicators_enabled,
        "broker_execution": broker_execution_enabled,
    }
    next_payload["messages"] = _entitlement_messages(
        advanced_indicators_enabled=advanced_indicators_enabled,
        broker_execution_enabled=broker_execution_enabled,
    )
    return next_payload


def apply_scan_entitlements(
    payload: dict[str, Any],
    *,
    advanced_indicators_enabled: bool,
    broker_execution_enabled: bool,
) -> dict[str, Any]:
    next_payload = dict(payload)
    rows = [
        _sanitize_signal_row(
            dict(row),
            advanced_indicators_enabled=advanced_indicators_enabled,
            broker_execution_enabled=broker_execution_enabled,
        )
        for row in list(payload.get("results") or [])
    ]
    next_payload["results"] = rows
    next_payload["capabilities"] = {
        "advanced_indicators": advanced_indicators_enabled,
        "broker_execution": broker_execution_enabled,
    }
    next_payload["messages"] = _entitlement_messages(
        advanced_indicators_enabled=advanced_indicators_enabled,
        broker_execution_enabled=broker_execution_enabled,
    )
    return next_payload


def apply_watchlist_entitlements(
    payload: dict[str, Any],
    *,
    advanced_indicators_enabled: bool,
    broker_execution_enabled: bool,
) -> dict[str, Any]:
    next_payload = dict(payload)
    rows = [
        _sanitize_signal_row(
            dict(row),
            advanced_indicators_enabled=advanced_indicators_enabled,
            broker_execution_enabled=broker_execution_enabled,
        )
        for row in list(payload.get("rows") or [])
    ]
    next_payload["rows"] = rows
    next_payload["results"] = rows
    summary = dict(payload.get("summary") or {})
    if not advanced_indicators_enabled:
        summary["high_conviction"] = 0
    if not broker_execution_enabled:
        summary["entry_now"] = 0
    next_payload["summary"] = summary
    next_payload["capabilities"] = {
        "advanced_indicators": advanced_indicators_enabled,
        "broker_execution": broker_execution_enabled,
    }
    next_payload["messages"] = _entitlement_messages(
        advanced_indicators_enabled=advanced_indicators_enabled,
        broker_execution_enabled=broker_execution_enabled,
    )
    return next_payload


def apply_compare_entitlements(
    payload: dict[str, Any],
    *,
    advanced_indicators_enabled: bool,
    broker_execution_enabled: bool,
) -> dict[str, Any]:
    next_payload = dict(payload)
    rows = [
        _sanitize_signal_row(
            dict(row),
            advanced_indicators_enabled=advanced_indicators_enabled,
            broker_execution_enabled=broker_execution_enabled,
        )
        for row in list(payload.get("rows") or [])
    ]
    charts = {
        ticker: apply_chart_entitlements(
            dict(chart),
            advanced_indicators_enabled=advanced_indicators_enabled,
        )
        for ticker, chart in dict(payload.get("charts") or {}).items()
    }
    next_payload["rows"] = rows
    next_payload["charts"] = charts
    next_payload["leader"] = rows[0] if rows else None
    summary = dict(payload.get("summary") or {})
    summary["leader"] = next_payload["leader"]
    next_payload["summary"] = summary
    next_payload["capabilities"] = {
        "advanced_indicators": advanced_indicators_enabled,
        "broker_execution": broker_execution_enabled,
    }
    next_payload["messages"] = _entitlement_messages(
        advanced_indicators_enabled=advanced_indicators_enabled,
        broker_execution_enabled=broker_execution_enabled,
    )
    return next_payload


def apply_dashboard_entitlements(
    payload: dict[str, Any],
    *,
    advanced_indicators_enabled: bool,
    broker_execution_enabled: bool,
) -> dict[str, Any]:
    next_payload = dict(payload)
    next_payload["scan"] = apply_scan_entitlements(
        dict(payload.get("scan") or {}),
        advanced_indicators_enabled=advanced_indicators_enabled,
        broker_execution_enabled=broker_execution_enabled,
    )
    next_payload["watchlist"] = apply_watchlist_entitlements(
        dict(payload.get("watchlist") or {}),
        advanced_indicators_enabled=advanced_indicators_enabled,
        broker_execution_enabled=broker_execution_enabled,
    )
    next_payload["capabilities"] = {
        "advanced_indicators": advanced_indicators_enabled,
        "broker_execution": broker_execution_enabled,
    }
    return next_payload


def analyze_market(request: AnalyzeRequest, *, current_user: Any | None = None) -> dict[str, Any]:
    cache_key = (
        "analyze",
        str(request.ticker or "").upper(),
        int(request.horizon or 0),
        str(request.interval or "").lower(),
        bool(request.include_history),
        bool(request.include_contract_lookup),
        bool(request.include_event_lookup),
        bool(request.include_alignment),
        bool(request.use_fast_model),
        bool(request.regular_hours_only),
    )
    context = {
        "ticker": str(request.ticker or "").upper(),
        "interval": str(request.interval or "").lower(),
        "horizon": int(request.horizon or 0),
        "include_history": bool(request.include_history),
        "regular_hours_only": bool(request.regular_hours_only),
    }
    stages: list[dict[str, Any]] = []
    tenant_id = str(getattr(current_user, "tenant_id", "") or "").strip()
    tenant_slug = str(getattr(current_user, "tenant_slug", "") or "").strip().lower()

    def _build_payload() -> dict[str, Any]:
        settings_obj = _profile_market_stage(
            stages,
            "settings",
            lambda: make_settings(request.ticker, request.horizon, request.interval),
        )
        history = _profile_market_stage(
            stages,
            "download_history",
            lambda: _run_upstream_market_call(
                target="market-data",
                operation="download_ohlcv",
                context={"ticker": str(request.ticker or "").upper(), "interval": settings_obj.interval, "period": settings_obj.period},
                func=lambda: sdm.download_ohlcv(request.ticker, settings_obj.period, settings_obj.interval),
            ),
        )
        if history.empty:
            raise NotFoundError(f"No market data returned for {request.ticker}.")

        _profile_market_stage(
            stages,
            "resolve_forecast_journal",
            lambda: sdm.resolve_forecast_journal_entries(
                request.ticker,
                request.interval,
                history,
            ),
        )
        report = _profile_market_stage(
            stages,
            "analyze_ticker",
            lambda: _build_regime_aware_report(
                settings_obj,
                history,
                ticker=request.ticker,
                interval=request.interval,
                include_contract_lookup=request.include_contract_lookup,
                include_event_lookup=request.include_event_lookup,
                include_alignment=request.include_alignment,
                fast_mode=request.use_fast_model,
            ),
        )
        if isinstance(report, tuple):
            report, journal_calibration = report
        else:
            journal_calibration = {}
        _profile_market_stage(
            stages,
            "append_forecast_journal",
            lambda: sdm.append_forecast_journal_records(
                sdm.build_paired_forecast_journal_records(
                    report,
                    settings_obj,
                    history,
                    tenant_id=tenant_id or None,
                    tenant_slug=tenant_slug or None,
                )
            ),
        )
        history_freshness = _profile_market_stage(
            stages,
            "history_freshness",
            lambda: _build_market_data_freshness_snapshot(
                ticker=request.ticker,
                interval=request.interval,
                latest_bar_at=_latest_bar_at_from_history_frame(history),
                point_count=len(history),
                regular_hours_only=bool(request.regular_hours_only),
                source="analyze",
            ),
        )
        execution_context = _profile_market_stage(
            stages,
            "execution_context",
            lambda: _build_execution_context(
                report=report if isinstance(report, dict) else None,
                freshness=history_freshness,
            ),
        )
        payload: dict[str, Any] = _profile_market_stage(
            stages,
            "serialize_payload",
            lambda: (
                lambda serialized_report, forecast_framing, serialized_execution_context: {
                    "report": (
                        {
                            **serialized_report,
                            "forecast_framing": forecast_framing,
                            "execution_context": serialized_execution_context,
                        }
                        if isinstance(serialized_report, dict)
                        else serialized_report
                    ),
                    "settings": serialize_value(settings_obj.__dict__),
                    "journal_calibration": serialize_value(journal_calibration),
                    "forecast_framing": forecast_framing,
                    "execution_context": serialized_execution_context,
                    "freshness": history_freshness,
                }
            )(
                serialize_value(report),
                _build_forecast_framing(
                    interval=settings_obj.interval,
                    horizon=settings_obj.horizon,
                    forecast=report.get("forecast") if isinstance(report, dict) else None,
                    probability_up=report.get("probability_up") if isinstance(report, dict) else None,
                ),
                serialize_value(execution_context),
            ),
        )
        if request.include_history:
            payload["history"] = _profile_market_stage(
                stages,
                "serialize_history",
                lambda: serialize_dataframe(history.reset_index()),
            )
        return payload

    started_at = perf_counter()
    payload = _run_market_operation(
        name="market.analyze",
        cache_key=cache_key,
        context=context,
        builder=_build_payload,
    )
    if request.include_live_price:
        report = payload.get("report") or {}
        live_price = _profile_market_stage(
            stages,
            "get_live_price",
            lambda: _run_upstream_market_call(
                target="market-data",
                operation="get_live_price",
                context={"ticker": str(request.ticker or "").upper()},
                func=lambda: sdm.get_live_price(request.ticker),
            ),
        )
        payload["live_price"] = live_price
        payload["trade_status"] = _profile_market_stage(stages, "trade_status", lambda: sdm.evaluate_trade_status(report, live_price))
        payload["execution_decision"] = _profile_market_stage(stages, "execution_decision", lambda: sdm.get_execution_decision(report, live_price))
        payload["alerts"] = _profile_market_stage(stages, "alerts", lambda: sdm.evaluate_trade_alerts(report, live_price))
    record_route_profile(
        route_key="market.analyze",
        total_duration_seconds=perf_counter() - started_at,
        stages=stages,
        context={**context, "cache_key": "analyze"},
    )
    return payload


def _build_scan_settings(interval: str, horizon: int) -> tuple[str, float, float]:
    period = sdm.get_period_for_interval(interval)
    threshold_up, threshold_down = sdm.get_thresholds_for_interval(interval)
    return period, threshold_up, threshold_down


def run_scan(request: ScanRequest) -> dict[str, Any]:
    tickers = tuple(request.tickers or sdm.DEFAULT_SCAN_TICKERS)
    cache_key = (
        "scan",
        _freeze_cache_key(tickers),
        str(request.interval or "").lower(),
        int(request.horizon or 0),
        int(request.top_n or 0),
        bool(request.include_errors),
        bool(request.include_contract_lookup),
        bool(request.include_event_lookup),
        bool(request.include_alignment),
        bool(request.use_fast_model),
        bool(request.regular_hours_only),
    )
    context = {
        "interval": str(request.interval or "").lower(),
        "horizon": int(request.horizon or 0),
        "ticker_count": len(tickers),
        "top_n": int(request.top_n or 0),
        "regular_hours_only": bool(request.regular_hours_only),
    }

    def _build_payload() -> dict[str, Any]:
        period, threshold_up, threshold_down = _build_scan_settings(request.interval, request.horizon)
        price_data = _run_upstream_market_call(
            target="market-data",
            operation="batch_download_ohlcv",
            context={"ticker_count": len(tickers), "interval": request.interval, "period": period},
            func=lambda: sdm.batch_download_ohlcv(tickers, period, request.interval),
        )

        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        preloaded_frames_map: dict[str, dict[str, pd.DataFrame]] = {}
        if request.include_alignment:
            alignment_intervals = ["1m", "5m", "15m", "1h", "4h"]
            for alignment_interval in alignment_intervals:
                if alignment_interval == request.interval:
                    continue
                alignment_period = sdm.get_period_for_interval(alignment_interval)
                preloaded_frames_map[alignment_interval] = _run_upstream_market_call(
                    target="market-data",
                    operation="batch_download_ohlcv",
                    context={"ticker_count": len(tickers), "interval": alignment_interval, "period": alignment_period, "alignment": True},
                    func=lambda intrvl=alignment_interval, intrvl_period=alignment_period: sdm.batch_download_ohlcv(tickers, intrvl_period, intrvl),
                )

        for ticker in tickers:
            frame = price_data.get(ticker)
            if frame is None or frame.empty:
                errors.append({"ticker": ticker, "error": "No price data returned."})
                continue
            try:
                settings_obj = sdm.ModelConfig(
                    ticker=ticker,
                    period=period,
                    interval=request.interval,
                    horizon=request.horizon,
                    threshold_up=threshold_up,
                    threshold_down=threshold_down,
                )
                ticker_preloaded_frames = {
                    intrvl: frames[ticker]
                    for intrvl, frames in preloaded_frames_map.items()
                    if ticker in frames and not frames[ticker].empty
                }
                report = sdm.analyze_ticker(
                    settings=settings_obj,
                    make_chart=False,
                    preloaded_price_frame=frame,
                    preloaded_frames=ticker_preloaded_frames,
                    include_contract_lookup=request.include_contract_lookup,
                    include_event_lookup=request.include_event_lookup,
                    include_alignment=request.include_alignment,
                    fast_mode=request.use_fast_model,
                )
                option_plan = report["option_plan"]
                execution_context = _build_execution_context(
                    report=report,
                    freshness=_build_market_data_freshness_snapshot(
                        ticker=ticker,
                        interval=request.interval,
                        latest_bar_at=_latest_bar_at_from_history_frame(frame),
                        point_count=len(frame),
                        regular_hours_only=bool(request.regular_hours_only),
                        source="scan",
                    ),
                )
                ranking_context = _build_ranking_context(
                    ticker=ticker,
                    report=report,
                    execution_context=execution_context,
                )
                results.append(
                    {
                        "ticker": ticker,
                        "verdict": report["verdict"],
                        "probability_up": report["probability_up"],
                        "base_probability_up": report.get("base_probability_up"),
                        "state_adjusted_probability_up": report.get("state_adjusted_probability_up"),
                        "setup_score": report["setup_score"],
                        "uncertainty_score": report.get("uncertainty_score"),
                        "prediction_data_quality": report.get("prediction_data_quality"),
                        "degraded_prediction": report.get("degraded_prediction"),
                        "state_source_map": serialize_value(report.get("state_source_map") or {}),
                        "state_freshness": serialize_value(report.get("state_freshness") or {}),
                        "driver_scores": serialize_value(report.get("driver_scores") or {}),
                        "driver_agreement_score": report.get("driver_agreement_score"),
                        "volatility_regime": report.get("volatility_regime"),
                        "relative_strength_score": report.get("relative_strength_score"),
                        "iv_context": serialize_value(report.get("iv_context") or {}),
                        "market_state": serialize_value(report.get("market_state") or {}),
                        "relative_strength": serialize_value(report.get("relative_strength") or {}),
                        "options_flow": serialize_value(report.get("options_flow") or {}),
                        "event_revision": serialize_value(report.get("event_revision") or {}),
                        "ensemble_summary": serialize_value(report.get("ensemble_summary") or {}),
                        "alpha_score": report.get("alpha_score"),
                        "execution_score": report.get("execution_score"),
                        "portfolio_score": report.get("portfolio_score"),
                        "edge_to_cost_ratio": report.get("edge_to_cost_ratio"),
                        "expected_edge_bps": report.get("expected_edge_bps"),
                        "estimated_cost_bps": report.get("estimated_cost_bps"),
                        "spread_bps": report.get("spread_bps"),
                        "average_dollar_volume": report.get("average_dollar_volume"),
                        "average_1m_dollar_volume": report.get("average_1m_dollar_volume"),
                        "quote_age_seconds": report.get("quote_age_seconds"),
                        "proxy_correlation_bucket": report.get("proxy_correlation_bucket"),
                        "portfolio_rank": report.get("portfolio_rank"),
                        "auto_entry_eligible": report.get("auto_entry_eligible"),
                        "setup_grade": report["setup_grade"],
                        "conviction_label": report["conviction_label"],
                        "alignment_label": report["alignment_label"],
                        "trade_decision": report["trade_decision"],
                        "reject_reason": report["reject_reason"],
                        "vehicle_recommendation": report.get("vehicle_recommendation"),
                        "vehicle_reason": report.get("vehicle_reason"),
                        "close": report["close"],
                        "entry_low_price": option_plan["entry_low_price"],
                        "entry_high_price": option_plan["entry_high_price"],
                        "target_price": option_plan["expected_underlying_target"],
                        "stop_loss": option_plan["stop_loss"],
                        "contract_symbol": (option_plan["recommended_contract"] or {}).get("contract_symbol", ""),
                        "event_risk": report.get("event_risk"),
                        "event_label": report.get("event_label"),
                        "event_reason": report.get("event_reason"),
                        "next_event_name": report.get("next_event_name"),
                        "next_event_date": report.get("next_event_date"),
                        "event_context": report.get("event_context"),
                        "event_window_label": (report.get("event_context") or {}).get("event_window_label", ""),
                        "event_severity": (report.get("event_context") or {}).get("event_severity", ""),
                        "event_session_label": (report.get("event_context") or {}).get("session_label", ""),
                        "trade_posture": (report.get("event_context") or {}).get("trade_posture", ""),
                        "execution_context": serialize_value(execution_context),
                        "execution_fill_label": execution_context.get("fill_label"),
                        "execution_fill_tone": execution_context.get("fill_tone"),
                        "preferred_order_type": execution_context.get("preferred_order_type"),
                        "market_order_ok": execution_context.get("market_order_ok"),
                        "size_cap_ratio": execution_context.get("size_cap_ratio"),
                        "size_cap_label": execution_context.get("size_cap_label"),
                        "ranking_context": serialize_value(ranking_context),
                        "ranking_score": ranking_context.get("score"),
                        "ranking_label": ranking_context.get("label"),
                        "ranking_tier": ranking_context.get("tier"),
                        "ranking_summary": ranking_context.get("summary"),
                        "contract_spread_bps": report.get("spread_bps"),
                        "contract_quote_age_seconds": report.get("quote_age_seconds"),
                        "market_regime": report.get("market_regime"),
                        "regime_strength_score": (report.get("forecast") or {}).get("regime_strength_score"),
                        "news_sentiment": serialize_value(report.get("news_sentiment") or {}),
                        "institutional_flow": serialize_value(report.get("institutional_flow") or {}),
                        "institutional_flow_score": (report.get("institutional_flow") or {}).get("score"),
                        "institutional_flow_label": (report.get("institutional_flow") or {}).get("label", ""),
                        "option_execution_profile": serialize_value(report.get("option_execution_profile") or {}),
                        "option_execution_score": (report.get("option_execution_profile") or {}).get("execution_score"),
                        "contract_quality_tier": (report.get("option_execution_profile") or {}).get("contract_quality_tier"),
                    }
                )
            except Exception as exc:
                errors.append({"ticker": ticker, "error": str(exc)})

        ranked_rows, ranking_board = _sort_and_annotate_ranking_rows(results, sort_by="ranking_score", descending=True)
        visible_rows = ranked_rows[: request.top_n]

        payload: dict[str, Any] = {
            "interval": request.interval,
            "horizon": request.horizon,
            "tickers_requested": list(tickers),
            "result_count": int(len(visible_rows)),
            "results": serialize_value(visible_rows),
            "ranking_board": {
                **ranking_board,
                "visible_count": len(visible_rows),
            },
        }
        if request.include_errors:
            payload["errors"] = errors
        return payload

    return _run_market_operation(
        name="market.scan",
        cache_key=cache_key,
        context=context,
        builder=_build_payload,
    )


def build_watchlist_from_scan_payload(
    scan_payload: dict[str, Any],
    request: WatchlistRequest,
) -> dict[str, Any]:
    rows = [dict(row) for row in list(scan_payload.get("results") or [])]
    live_tickers = [
        str(row.get("ticker", "")).strip().upper()
        for row in rows
        if str(row.get("ticker", "")).strip()
    ]
    live_prices = _run_upstream_market_call(
        target="market-data",
        operation="batch_get_live_prices",
        context={"ticker_count": len(live_tickers)},
        func=lambda: sdm.batch_get_live_prices(live_tickers),
    ) if live_tickers else {}
    for row in rows:
        ticker = str(row.get("ticker", "")).strip().upper()
        row["live_price"] = live_prices.get(ticker)

    ranked_rows, ranking_board = _sort_and_annotate_ranking_rows(
        rows,
        sort_by=request.sort_by,
        descending=request.descending,
    )
    visible_rows = ranked_rows[: request.limit]
    watchlist_df = pd.DataFrame(visible_rows)
    summary = {
        "valid_trades": int((watchlist_df.get("trade_decision") == "VALID TRADE").sum()) if "trade_decision" in watchlist_df else 0,
        "high_conviction": int((watchlist_df.get("conviction_label") == "HIGH CONVICTION").sum()) if "conviction_label" in watchlist_df else 0,
        "entry_now": int(
            watchlist_df.apply(
                lambda row: (
                    row.get("entry_low_price") is not None
                    and row.get("entry_high_price") is not None
                    and row.get("live_price") is not None
                    and float(row["entry_low_price"]) <= float(row["live_price"]) <= float(row["entry_high_price"])
                ),
                axis=1,
            ).sum()
        ) if not watchlist_df.empty else 0,
        "ranking_board": {
            **ranking_board,
            "visible_count": len(visible_rows),
        },
    }
    validation_artifact = _build_candidate_board_validation_artifact(
        source="watchlist",
        board_name=str(summary.get("ranking_board", {}).get("board_name") or "Controlled liquid ranking board"),
        interval=request.interval,
        horizon=request.horizon,
        rows=visible_rows,
        ranking_board=summary.get("ranking_board"),
    )
    serialized_rows = serialize_value(visible_rows)
    return {
        "summary": summary,
        "rows": serialized_rows,
        "results": serialized_rows,
        "count": int(len(serialized_rows)),
        "validation_artifact": serialize_value(validation_artifact),
        "errors": scan_payload.get("errors", []),
    }


def build_watchlist(request: WatchlistRequest) -> dict[str, Any]:
    started_at = perf_counter()
    tickers = request.tickers or sdm.DEFAULT_SCAN_TICKERS
    try:
        scan_payload = run_scan(
            ScanRequest(
                tickers=tickers,
                interval=request.interval,
                horizon=request.horizon,
                regular_hours_only=bool(request.regular_hours_only),
                top_n=max(request.limit, len(tickers)),
                include_errors=True,
                include_contract_lookup=request.include_contract_lookup,
                include_event_lookup=request.include_event_lookup,
                include_alignment=request.include_alignment,
                use_fast_model=request.use_fast_model,
            )
        )
        payload = build_watchlist_from_scan_payload(scan_payload, request)
    except Exception:
        record_operation(
            name="market.watchlist",
            duration_seconds=perf_counter() - started_at,
            status="error",
            cache_status="bypass",
            context={"interval": request.interval, "limit": int(request.limit or 0), "ticker_count": len(tickers)},
        )
        raise
    record_operation(
        name="market.watchlist",
        duration_seconds=perf_counter() - started_at,
        cache_status="bypass",
        context={"interval": request.interval, "limit": int(request.limit or 0), "ticker_count": len(tickers)},
    )
    return payload


def get_live_price_snapshot(ticker: str) -> dict[str, Any]:
    price = _run_upstream_market_call(
        target="market-data",
        operation="get_live_price",
        context={"ticker": str(ticker or "").upper()},
        func=lambda: sdm.get_live_price(ticker),
    )
    return {
        "ticker": ticker,
        "live_price": price,
        "timestamp": utc_now_iso(),
    }


def get_live_prices_snapshot(tickers: list[str]) -> dict[str, Any]:
    prices = _run_upstream_market_call(
        target="market-data",
        operation="batch_get_live_prices",
        context={"ticker_count": len(tickers)},
        func=lambda: sdm.batch_get_live_prices(tickers),
    ) if tickers else {}
    timestamp = utc_now_iso()
    rows = []
    price_map: dict[str, Any] = {}
    for ticker in tickers:
        live_price = serialize_value(prices.get(ticker))
        rows.append({
            "ticker": ticker,
            "live_price": live_price,
            "timestamp": timestamp,
        })
        price_map[ticker] = live_price
    return {
        "rows": rows,
        "prices": price_map,
        "count": len(rows),
        "timestamp": timestamp,
    }


def compare_tickers(request: CompareRequest) -> dict[str, Any]:
    tickers = request.tickers[:12]
    cache_key = (
        "compare",
        _freeze_cache_key(tickers),
        str(request.interval or "").lower(),
        int(request.horizon or 0),
        int(request.points_limit or 0),
        bool(request.regular_hours_only),
    )
    context = {
        "interval": str(request.interval or "").lower(),
        "horizon": int(request.horizon or 0),
        "ticker_count": len(tickers),
        "regular_hours_only": bool(request.regular_hours_only),
    }
    stages: list[dict[str, Any]] = []

    def _build_payload() -> dict[str, Any]:
        period, threshold_up, threshold_down = _profile_market_stage(
            stages,
            "scan_settings",
            lambda: _build_scan_settings(request.interval, request.horizon),
        )
        frames = _profile_market_stage(
            stages,
            "batch_download_history",
            lambda: _run_upstream_market_call(
                target="market-data",
                operation="batch_download_ohlcv",
                context={"ticker_count": len(tickers), "interval": request.interval, "period": period},
                func=lambda: sdm.batch_download_ohlcv(tickers, period, request.interval),
            ),
        )
        live_prices = _profile_market_stage(
            stages,
            "batch_live_prices",
            lambda: _run_upstream_market_call(
                target="market-data",
                operation="batch_get_live_prices",
                context={"ticker_count": len(tickers)},
                func=lambda: sdm.batch_get_live_prices(tickers),
            ),
        )

        rows: list[dict[str, Any]] = []
        charts: dict[str, Any] = {}
        errors: list[dict[str, str]] = []

        def _build_rows_and_charts():
            for ticker in tickers:
                frame = frames.get(ticker)
                if frame is None or frame.empty:
                    errors.append({"ticker": ticker, "error": "No price data returned."})
                    continue
                try:
                    settings_obj = sdm.ModelConfig(
                        ticker=ticker,
                        period=period,
                        interval=request.interval,
                        horizon=request.horizon,
                        threshold_up=threshold_up,
                        threshold_down=threshold_down,
                    )
                    report = sdm.analyze_ticker(settings=settings_obj, make_chart=False, preloaded_price_frame=frame)
                    live_price_raw = live_prices.get(ticker)
                    option_plan = report.get("option_plan", {}) or {}
                    execution = sdm.get_execution_decision(report, live_price_raw) if live_price_raw is not None else None
                    status = sdm.evaluate_trade_status(report, live_price_raw) if live_price_raw is not None else None
                    execution_context = _build_execution_context(
                        report=report,
                        freshness=_build_market_data_freshness_snapshot(
                            ticker=ticker,
                            interval=request.interval,
                            latest_bar_at=_latest_bar_at_from_history_frame(frame),
                            point_count=len(frame),
                            regular_hours_only=bool(request.regular_hours_only),
                            source="compare",
                        ),
                    )
                    ranking_context = _build_ranking_context(
                        ticker=ticker,
                        report=report,
                        execution_context=execution_context,
                    )
                    option_side = str(option_plan.get("option_side") or "").upper()
                    if option_side not in {"CALL", "PUT", "NONE"}:
                        option_side = "CALL" if str(report.get("verdict", "")).upper() == "BULLISH" else "PUT" if str(report.get("verdict", "")).upper() == "BEARISH" else "NONE"
                    rows.append({
                        "ticker": ticker,
                        "verdict": report.get("verdict"),
                        "direction": option_side,
                        "probability_up": serialize_value(report.get("probability_up")),
                        "base_probability_up": serialize_value(report.get("base_probability_up")),
                        "state_adjusted_probability_up": serialize_value(report.get("state_adjusted_probability_up")),
                        "setup_score": serialize_value(report.get("setup_score")),
                        "uncertainty_score": serialize_value(report.get("uncertainty_score")),
                        "prediction_data_quality": report.get("prediction_data_quality"),
                        "degraded_prediction": serialize_value(report.get("degraded_prediction")),
                        "state_source_map": serialize_value(report.get("state_source_map") or {}),
                        "state_freshness": serialize_value(report.get("state_freshness") or {}),
                        "driver_scores": serialize_value(report.get("driver_scores") or {}),
                        "driver_agreement_score": serialize_value(report.get("driver_agreement_score")),
                        "volatility_regime": report.get("volatility_regime"),
                        "relative_strength_score": serialize_value(report.get("relative_strength_score")),
                        "iv_context": serialize_value(report.get("iv_context") or {}),
                        "market_state": serialize_value(report.get("market_state") or {}),
                        "relative_strength": serialize_value(report.get("relative_strength") or {}),
                        "options_flow": serialize_value(report.get("options_flow") or {}),
                        "event_revision": serialize_value(report.get("event_revision") or {}),
                        "ensemble_summary": serialize_value(report.get("ensemble_summary") or {}),
                        "setup_grade": report.get("setup_grade"),
                        "conviction_label": report.get("conviction_label"),
                        "alignment_label": report.get("alignment_label"),
                        "trade_decision": report.get("trade_decision"),
                        "reject_reason": report.get("reject_reason"),
                        "vehicle_recommendation": report.get("vehicle_recommendation"),
                        "vehicle_reason": report.get("vehicle_reason"),
                        "live_price": serialize_value(live_price_raw),
                        "close": serialize_value(report.get("close")),
                        "entry_low_price": serialize_value(option_plan.get("entry_low_price")),
                        "entry_high_price": serialize_value(option_plan.get("entry_high_price")),
                        "target_price": serialize_value(option_plan.get("expected_underlying_target")),
                        "stop_loss": serialize_value(option_plan.get("stop_loss")),
                        "contract_symbol": (option_plan.get("recommended_contract") or {}).get("contract_symbol", ""),
                        "execution_action": serialize_value(execution),
                        "trade_status": serialize_value(status),
                        "event_risk": serialize_value(report.get("event_risk")),
                        "event_label": report.get("event_label"),
                        "event_reason": report.get("event_reason"),
                        "next_event_name": report.get("next_event_name"),
                        "next_event_date": report.get("next_event_date"),
                        "event_context": serialize_value(report.get("event_context") or {}),
                        "execution_context": serialize_value(execution_context),
                        "ranking_context": serialize_value(ranking_context),
                        "ranking_score": ranking_context.get("score"),
                        "ranking_label": ranking_context.get("label"),
                        "ranking_tier": ranking_context.get("tier"),
                        "ranking_summary": ranking_context.get("summary"),
                        "news_sentiment": serialize_value(report.get("news_sentiment") or {}),
                        "institutional_flow": serialize_value(report.get("institutional_flow") or {}),
                        "institutional_flow_score": (report.get("institutional_flow") or {}).get("score"),
                        "institutional_flow_label": (report.get("institutional_flow") or {}).get("label", ""),
                        "option_execution_profile": serialize_value(report.get("option_execution_profile") or {}),
                        "option_execution_score": (report.get("option_execution_profile") or {}).get("execution_score"),
                        "contract_quality_tier": (report.get("option_execution_profile") or {}).get("contract_quality_tier"),
                        "forecast_framing": _build_forecast_framing(
                            interval=request.interval,
                            horizon=request.horizon,
                            forecast=report.get("forecast") if isinstance(report, dict) else None,
                            probability_up=report.get("probability_up") if isinstance(report, dict) else None,
                        ),
                    })
                    charts[ticker] = get_chart_payload(ChartRequest(ticker=ticker, interval=request.interval, points_limit=request.points_limit))
                except Exception as exc:
                    errors.append({"ticker": ticker, "error": str(exc)})

        _profile_market_stage(stages, "per_ticker_compare", _build_rows_and_charts)

        ranked_rows, ranking_board = _profile_market_stage(
            stages,
            "ranking_board",
            lambda: _sort_and_annotate_ranking_rows(rows, sort_by="ranking_score", descending=True),
        )
        serialized_rows = _profile_market_stage(stages, "serialize_rows", lambda: serialize_value(ranked_rows))
        leader = serialized_rows[0] if serialized_rows else None
        ranking_scores = [
            _coerce_float(row.get("ranking_score"))
            for row in ranked_rows
            if _coerce_float(row.get("ranking_score")) is not None
        ]
        payload = _profile_market_stage(
            stages,
            "assemble_payload",
            lambda: {
                "interval": request.interval,
                "horizon": request.horizon,
                "tickers": tickers,
                "rows": serialized_rows,
                "charts": charts,
                "leader": leader,
                "summary": {
                    "count": len(serialized_rows),
                    "valid_trades": sum(1 for row in serialized_rows if row.get("trade_decision") == "VALID TRADE"),
                    "bullish_count": sum(1 for row in serialized_rows if str(row.get("verdict", "")).upper() == "BULLISH"),
                    "bearish_count": sum(1 for row in serialized_rows if str(row.get("verdict", "")).upper() == "BEARISH"),
                    "average_setup_score": round(sum(ranking_scores) / len(ranking_scores), 2) if ranking_scores else None,
                    "ranking_board": ranking_board,
                    "leader": leader,
                },
                "validation_artifact": _build_candidate_board_validation_artifact(
                    source="compare",
                    board_name=str(ranking_board.get("board_name") or "Controlled liquid ranking board"),
                    interval=request.interval,
                    horizon=request.horizon,
                    rows=ranked_rows,
                    ranking_board=ranking_board,
                ),
                "errors": errors,
            },
        )
        return payload
    started_at = perf_counter()
    payload = _run_market_operation(
        name="market.compare",
        cache_key=cache_key,
        context=context,
        builder=lambda: _profile_market_stage(stages, "compare_builder", _build_payload),
    )
    record_route_profile(
        route_key="market.compare",
        total_duration_seconds=perf_counter() - started_at,
        stages=stages,
        context={**context, "cache_key": "compare"},
    )
    return payload
