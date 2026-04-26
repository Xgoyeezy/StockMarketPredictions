from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


MARKET_TIMEZONE = ZoneInfo("America/New_York")

DEFAULT_RISK_CONTROL_SETTINGS: dict[str, Any] = {
    "max_gross_leverage": 1.5,
    "max_single_position_pct": 12.0,
    "max_correlated_bucket_pct": 35.0,
    "max_daily_loss_pct": 2.0,
    "max_weekly_loss_pct": 5.0,
    "drawdown_size_cut_pct": 5.0,
    "drawdown_stop_pct": 10.0,
    "drawdown_audit_pct": 15.0,
    "risk_cut_multiplier": 0.5,
    "allow_pyramiding": True,
    "allow_averaging_down": False,
    "min_edge_to_cost_ratio": 2.5,
    "market_slippage_bps": 20.0,
    "limit_slippage_bps": 10.0,
    "max_spread_bps": 25.0,
    "min_average_dollar_volume": 1_000_000.0,
    "max_order_adv_pct": 1.0,
    "max_intraday_volume_pct": 5.0,
    "no_new_entries_first_minutes": 5,
    "no_new_entries_before_close_minutes": 10,
    "require_liquidity_fields": True,
    "require_edge_fields": False,
}

DEFAULT_CORRELATION_BUCKETS: dict[str, set[str]] = {
    "broad_market": {"SPY", "VOO", "IVV", "QQQ", "IWM", "DIA", "TQQQ", "SQQQ", "UPRO", "SPXL"},
    "semiconductors": {"NVDA", "AMD", "AVGO", "INTC", "TSM", "ASML", "MU", "SMH", "SOXX"},
    "mega_cap_tech": {"AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN", "TSLA", "NFLX"},
    "crypto_proxy": {"COIN", "MSTR", "MARA", "RIOT", "CLSK", "IBIT", "FBTC", "BITO"},
    "banks": {"JPM", "BAC", "GS", "MS", "C", "WFC", "XLF"},
    "energy": {"XOM", "CVX", "COP", "OXY", "SLB", "XLE"},
}


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    detail: str
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "detail": self.detail,
            "metrics": self.metrics,
        }


def _coerce_float(value: Any, default: float) -> float:
    if value in (None, "", "nan"):
        return float(default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if pd.isna(parsed):
        return float(default)
    return float(parsed)


def _coerce_int(value: Any, default: int) -> int:
    if value in (None, "", "nan"):
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on"}:
            return True
        if cleaned in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def clamp_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    parsed = _coerce_float(value, default)
    return float(max(minimum, min(maximum, parsed)))


def clamp_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    parsed = _coerce_int(value, default)
    return int(max(minimum, min(maximum, parsed)))


def normalize_risk_control_settings(settings_state: dict[str, Any]) -> dict[str, Any]:
    state = dict(settings_state or {})
    defaults = DEFAULT_RISK_CONTROL_SETTINGS
    return {
        "max_gross_leverage": clamp_float(state.get("max_gross_leverage"), defaults["max_gross_leverage"], minimum=0.10, maximum=10.0),
        "max_single_position_pct": clamp_float(state.get("max_single_position_pct"), defaults["max_single_position_pct"], minimum=1.0, maximum=100.0),
        "max_correlated_bucket_pct": clamp_float(state.get("max_correlated_bucket_pct"), defaults["max_correlated_bucket_pct"], minimum=1.0, maximum=100.0),
        "max_daily_loss_pct": clamp_float(state.get("max_daily_loss_pct"), defaults["max_daily_loss_pct"], minimum=0.10, maximum=50.0),
        "max_weekly_loss_pct": clamp_float(state.get("max_weekly_loss_pct"), defaults["max_weekly_loss_pct"], minimum=0.10, maximum=50.0),
        "drawdown_size_cut_pct": clamp_float(state.get("drawdown_size_cut_pct"), defaults["drawdown_size_cut_pct"], minimum=0.10, maximum=90.0),
        "drawdown_stop_pct": clamp_float(state.get("drawdown_stop_pct"), defaults["drawdown_stop_pct"], minimum=0.10, maximum=95.0),
        "drawdown_audit_pct": clamp_float(state.get("drawdown_audit_pct"), defaults["drawdown_audit_pct"], minimum=0.10, maximum=99.0),
        "risk_cut_multiplier": clamp_float(state.get("risk_cut_multiplier"), defaults["risk_cut_multiplier"], minimum=0.05, maximum=1.0),
        "allow_pyramiding": _coerce_bool(state.get("allow_pyramiding"), defaults["allow_pyramiding"]),
        "allow_averaging_down": _coerce_bool(state.get("allow_averaging_down"), defaults["allow_averaging_down"]),
        "min_edge_to_cost_ratio": clamp_float(state.get("min_edge_to_cost_ratio"), defaults["min_edge_to_cost_ratio"], minimum=0.0, maximum=25.0),
        "market_slippage_bps": clamp_float(state.get("market_slippage_bps"), defaults["market_slippage_bps"], minimum=0.0, maximum=500.0),
        "limit_slippage_bps": clamp_float(state.get("limit_slippage_bps"), defaults["limit_slippage_bps"], minimum=0.0, maximum=500.0),
        "max_spread_bps": clamp_float(state.get("max_spread_bps"), defaults["max_spread_bps"], minimum=0.0, maximum=1000.0),
        "min_average_dollar_volume": clamp_float(state.get("min_average_dollar_volume"), defaults["min_average_dollar_volume"], minimum=0.0, maximum=1_000_000_000_000.0),
        "max_order_adv_pct": clamp_float(state.get("max_order_adv_pct"), defaults["max_order_adv_pct"], minimum=0.001, maximum=100.0),
        "max_intraday_volume_pct": clamp_float(state.get("max_intraday_volume_pct"), defaults["max_intraday_volume_pct"], minimum=0.001, maximum=100.0),
        "no_new_entries_first_minutes": clamp_int(state.get("no_new_entries_first_minutes"), defaults["no_new_entries_first_minutes"], minimum=0, maximum=120),
        "no_new_entries_before_close_minutes": clamp_int(state.get("no_new_entries_before_close_minutes"), defaults["no_new_entries_before_close_minutes"], minimum=0, maximum=240),
        "require_liquidity_fields": _coerce_bool(state.get("require_liquidity_fields"), defaults["require_liquidity_fields"]),
        "require_edge_fields": _coerce_bool(state.get("require_edge_fields"), defaults["require_edge_fields"]),
    }


def effective_total_notional_cap(settings_state: dict[str, Any], *, equity: float | None = None) -> float:
    account_size = max(_coerce_float(settings_state.get("account_size"), 100000.0), 1.0)
    equity_base = max(_coerce_float(equity, account_size), 1.0)
    configured_cap = _coerce_float(settings_state.get("max_total_open_notional"), account_size)
    leverage_cap = _coerce_float(settings_state.get("max_gross_leverage"), DEFAULT_RISK_CONTROL_SETTINGS["max_gross_leverage"])
    return float(min(configured_cap, equity_base * leverage_cap))


def estimate_trade_notional(row: dict[str, Any] | pd.Series | None) -> float:
    if row is None:
        return 0.0
    getter = row.get if hasattr(row, "get") else dict(row).get
    for key in ("total_position_cost", "projected_position_cost", "position_cost", "broker_notional", "notional", "position_notional"):
        value = _coerce_float(getter(key), 0.0)
        if value > 0:
            return float(value)

    units = 0.0
    for key in ("suggested_contracts", "filled_quantity", "filled_contracts", "broker_qty", "qty", "quantity"):
        value = _coerce_float(getter(key), 0.0)
        if value > 0:
            units = value
            break

    price = 0.0
    for key in ("live_price", "live_price_at_open", "current_underlying_price", "entry_price", "limit_price", "close"):
        value = _coerce_float(getter(key), 0.0)
        if value > 0:
            price = value
            break
    return float(units * price) if units > 0 and price > 0 else 0.0


def sum_frame_notional(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    return float(sum(estimate_trade_notional(row) for row in frame.to_dict(orient="records")))


def _ticker_series(frame: pd.DataFrame) -> pd.Series:
    if frame.empty or "ticker" not in frame.columns:
        return pd.Series(dtype=str)
    return frame["ticker"].astype(str).str.strip().str.upper()


def bucket_for_symbol(symbol: str, settings_state: dict[str, Any] | None = None) -> str:
    normalized = str(symbol or "").strip().upper()
    custom = dict((settings_state or {}).get("correlation_buckets") or {})
    for bucket, members in custom.items():
        member_set = {str(item or "").strip().upper() for item in list(members or [])}
        if normalized in member_set:
            return str(bucket or "custom").strip().lower() or "custom"
    for bucket, members in DEFAULT_CORRELATION_BUCKETS.items():
        if normalized in members:
            return bucket
    return f"symbol:{normalized or 'UNKNOWN'}"


def build_bucket_exposure(
    frames: list[pd.DataFrame],
    *,
    settings_state: dict[str, Any],
) -> dict[str, float]:
    exposure: dict[str, float] = {}
    for frame in frames:
        if frame.empty:
            continue
        for row in frame.to_dict(orient="records"):
            ticker = str(row.get("ticker") or "").strip().upper()
            bucket = bucket_for_symbol(ticker, settings_state)
            exposure[bucket] = float(exposure.get(bucket, 0.0) + estimate_trade_notional(row))
    return {key: round(value, 4) for key, value in exposure.items() if value > 0}


def estimate_unrealized_pnl(open_frame: pd.DataFrame, monitored_frame: pd.DataFrame | None = None) -> float:
    if open_frame.empty:
        return 0.0

    monitored_lookup: dict[str, dict[str, Any]] = {}
    if monitored_frame is not None and not monitored_frame.empty:
        for row in monitored_frame.to_dict(orient="records"):
            trade_id = str(row.get("trade_id") or row.get("order_id") or "").strip()
            if trade_id:
                monitored_lookup[trade_id] = row

    total = 0.0
    for row in open_frame.to_dict(orient="records"):
        trade_id = str(row.get("trade_id") or row.get("order_id") or "").strip()
        monitor_row = monitored_lookup.get(trade_id, {})
        for key in ("unrealized_pnl", "current_unrealized_pnl", "open_pnl", "floating_pnl"):
            value = _coerce_float(monitor_row.get(key), float("nan"))
            if not pd.isna(value):
                total += value
                break
        else:
            quantity = _coerce_float(row.get("suggested_contracts") or row.get("filled_contracts") or row.get("broker_qty"), 0.0)
            entry_price = _coerce_float(row.get("actual_fill_price") or row.get("broker_filled_avg_price") or row.get("live_price_at_open"), 0.0)
            current_price = _coerce_float(
                monitor_row.get("current_underlying")
                or monitor_row.get("current_underlying_price")
                or row.get("current_underlying_price")
                or row.get("live_price_at_open"),
                0.0,
            )
            instrument_type = str(row.get("instrument_type") or "equity").strip().lower()
            multiplier = 1.0 if instrument_type == "equity" else 100.0
            if quantity > 0 and entry_price > 0 and current_price > 0:
                total += (current_price - entry_price) * quantity * multiplier
    return float(total)


def _closed_pnl_series(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    if frame.empty:
        return pd.Series(dtype="datetime64[ns, UTC]"), pd.Series(dtype=float)
    timestamps = pd.to_datetime(frame.get("closed_at", pd.Series(dtype=str)), errors="coerce", utc=True)
    pnl = pd.to_numeric(frame.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    return timestamps, pnl


def compute_realized_loss_windows(
    closed_frame: pd.DataFrame,
    *,
    now: datetime | None = None,
    timezone_name: str = "America/New_York",
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    market_tz = ZoneInfo(timezone_name)
    now_local = now.astimezone(market_tz)
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_local = day_start_local + timedelta(days=1)
    week_start_local = day_start_local - timedelta(days=day_start_local.weekday())
    week_end_local = week_start_local + timedelta(days=7)

    timestamps, pnl = _closed_pnl_series(closed_frame)
    if closed_frame.empty or timestamps.empty:
        return {
            "session_day": day_start_local.strftime("%Y-%m-%d"),
            "session_week": week_start_local.strftime("%G-W%V"),
            "today_realized_pnl": 0.0,
            "weekly_realized_pnl": 0.0,
            "today_closed_count": 0,
            "weekly_closed_count": 0,
            "total_realized_pnl": 0.0,
        }

    local_ts = timestamps.dt.tz_convert(market_tz)
    day_mask = (local_ts >= day_start_local) & (local_ts < day_end_local)
    week_mask = (local_ts >= week_start_local) & (local_ts < week_end_local)
    return {
        "session_day": day_start_local.strftime("%Y-%m-%d"),
        "session_week": week_start_local.strftime("%G-W%V"),
        "today_realized_pnl": float(pnl.loc[day_mask.fillna(False)].sum()),
        "weekly_realized_pnl": float(pnl.loc[week_mask.fillna(False)].sum()),
        "today_closed_count": int(day_mask.fillna(False).sum()),
        "weekly_closed_count": int(week_mask.fillna(False).sum()),
        "total_realized_pnl": float(pnl.sum()),
    }


def compute_current_equity(
    *,
    account_size: float,
    closed_frame: pd.DataFrame,
    open_frame: pd.DataFrame,
    monitored_frame: pd.DataFrame | None = None,
) -> dict[str, float]:
    windows = compute_realized_loss_windows(closed_frame)
    unrealized_pnl = estimate_unrealized_pnl(open_frame, monitored_frame)
    realized_pnl = float(windows["total_realized_pnl"])
    current_equity = float(account_size) + realized_pnl + unrealized_pnl
    return {
        "starting_equity": float(account_size),
        "realized_pnl_total": round(realized_pnl, 4),
        "unrealized_pnl_estimate": round(unrealized_pnl, 4),
        "current_equity_estimate": round(current_equity, 4),
    }


def update_high_water_runtime(runtime_state: dict[str, Any], current_equity: float, starting_equity: float) -> dict[str, Any]:
    prior = _coerce_float(runtime_state.get("equity_high_water_mark"), starting_equity)
    high_water = max(float(starting_equity), prior, float(current_equity))
    runtime_state["equity_high_water_mark"] = round(high_water, 4)
    drawdown_pct = 0.0 if high_water <= 0 else max(0.0, (high_water - float(current_equity)) / high_water * 100.0)
    return {
        "equity_high_water_mark": round(high_water, 4),
        "current_equity_estimate": round(float(current_equity), 4),
        "drawdown_pct": round(drawdown_pct, 4),
    }


def effective_risk_percent(settings_state: dict[str, Any], drawdown_pct: float) -> float:
    base = _coerce_float(settings_state.get("risk_percent"), 0.25)
    cut_at = _coerce_float(settings_state.get("drawdown_size_cut_pct"), DEFAULT_RISK_CONTROL_SETTINGS["drawdown_size_cut_pct"])
    multiplier = _coerce_float(settings_state.get("risk_cut_multiplier"), DEFAULT_RISK_CONTROL_SETTINGS["risk_cut_multiplier"])
    if drawdown_pct >= cut_at:
        return round(max(base * multiplier, 0.01), 6)
    return round(base, 6)


def evaluate_session_entry_window(settings_state: dict[str, Any], session: dict[str, Any]) -> RiskDecision:
    phase = str(session.get("phase") or "").strip().lower()
    minutes_to_close = session.get("minutes_to_close")
    first_minutes = int(settings_state.get("no_new_entries_first_minutes") or 0)
    before_close = int(settings_state.get("no_new_entries_before_close_minutes") or 0)

    if first_minutes > 0 and phase == "opening_range":
        return RiskDecision(
            False,
            "opening_window_lock",
            f"New entries are blocked during the first {first_minutes} minute(s) of the regular session.",
            {"phase": phase, "first_minutes": first_minutes},
        )
    if before_close > 0 and minutes_to_close is not None and int(minutes_to_close) <= before_close:
        return RiskDecision(
            False,
            "closing_window_lock",
            f"New entries are blocked inside {before_close} minute(s) of the close.",
            {"phase": phase, "minutes_to_close": int(minutes_to_close), "before_close": before_close},
        )
    return RiskDecision(True, "session_ok", "Session timing is inside entry limits.", {"phase": phase, "minutes_to_close": minutes_to_close})


def _first_numeric(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _coerce_float(row.get(key), float("nan"))
        if not pd.isna(value) and value > 0:
            return float(value)
    return None


def _expected_edge_bps(candidate: dict[str, Any]) -> float | None:
    value = _first_numeric(candidate, ("expected_edge_bps", "edge_bps", "forecast_edge_bps", "expected_move_bps"))
    if value is not None:
        return value
    raw_move = _first_numeric(candidate, ("expected_move", "technical_expected_move"))
    if raw_move is None:
        return None
    return abs(raw_move * 10000.0 if abs(raw_move) < 1.0 else raw_move)


def _average_dollar_volume(candidate: dict[str, Any]) -> float | None:
    direct = _first_numeric(candidate, ("average_dollar_volume", "avg_dollar_volume", "average_daily_dollar_volume", "dollar_volume"))
    if direct is not None:
        return direct
    volume = _first_numeric(candidate, ("average_volume", "avg_volume", "volume"))
    price = _first_numeric(candidate, ("live_price", "close", "current_underlying_price"))
    if volume is not None and price is not None:
        return volume * price
    return None


def _intraday_dollar_volume(candidate: dict[str, Any]) -> float | None:
    direct = _first_numeric(
        candidate,
        (
            "intraday_dollar_volume",
            "average_one_minute_dollar_volume",
            "avg_one_minute_dollar_volume",
            "average_1m_dollar_volume",
            "avg_1m_dollar_volume",
            "one_minute_dollar_volume",
        ),
    )
    if direct is not None:
        return direct
    volume = _first_numeric(candidate, ("average_one_minute_volume", "avg_one_minute_volume", "average_1m_volume", "avg_1m_volume", "one_minute_volume"))
    price = _first_numeric(candidate, ("live_price", "close", "current_underlying_price"))
    if volume is not None and price is not None:
        return volume * price
    return None


def evaluate_candidate_microstructure(candidate: dict[str, Any], settings_state: dict[str, Any]) -> RiskDecision:
    order_type = str(settings_state.get("order_type") or "market").strip().lower()
    spread_bps = _first_numeric(candidate, ("spread_bps", "bid_ask_spread_bps", "quote_spread_bps", "live_spread_bps"))
    if spread_bps is None:
        spread_pct = _first_numeric(candidate, ("spread_pct", "contract_spread_pct"))
        if spread_pct is not None:
            spread_bps = spread_pct * 10000.0
    adv = _average_dollar_volume(candidate)
    edge_bps = _expected_edge_bps(candidate)
    direct_edge_ratio = _first_numeric(candidate, ("edge_to_cost_ratio",))
    max_spread_bps = _coerce_float(settings_state.get("max_spread_bps"), DEFAULT_RISK_CONTROL_SETTINGS["max_spread_bps"])
    min_adv = _coerce_float(settings_state.get("min_average_dollar_volume"), DEFAULT_RISK_CONTROL_SETTINGS["min_average_dollar_volume"])
    require_liquidity_fields = _coerce_bool(settings_state.get("require_liquidity_fields"), False)
    require_edge_fields = _coerce_bool(settings_state.get("require_edge_fields"), False)

    if spread_bps is None and require_liquidity_fields:
        return RiskDecision(False, "missing_spread", "Candidate has no spread field and liquidity fields are required.", {})
    if adv is None and require_liquidity_fields:
        return RiskDecision(False, "missing_average_dollar_volume", "Candidate has no average dollar volume field and liquidity fields are required.", {})
    if edge_bps is None and require_edge_fields:
        return RiskDecision(False, "missing_edge", "Candidate has no expected edge field and edge validation is required.", {})

    if spread_bps is not None and spread_bps > max_spread_bps:
        return RiskDecision(
            False,
            "spread_too_wide",
            f"Spread is {spread_bps:.2f} bps, above the {max_spread_bps:.2f} bps limit.",
            {"spread_bps": spread_bps, "max_spread_bps": max_spread_bps},
        )
    if adv is not None and adv < min_adv:
        return RiskDecision(
            False,
            "liquidity_too_low",
            f"Average dollar volume is ${adv:,.0f}, below the ${min_adv:,.0f} floor.",
            {"average_dollar_volume": adv, "min_average_dollar_volume": min_adv},
        )

    base_cost_bps = _coerce_float(
        settings_state.get("market_slippage_bps" if order_type == "market" else "limit_slippage_bps"),
        DEFAULT_RISK_CONTROL_SETTINGS["market_slippage_bps" if order_type == "market" else "limit_slippage_bps"],
    )
    half_spread = (spread_bps or 0.0) / 2.0
    estimated_cost_bps = base_cost_bps + half_spread
    ratio_min = _coerce_float(settings_state.get("min_edge_to_cost_ratio"), DEFAULT_RISK_CONTROL_SETTINGS["min_edge_to_cost_ratio"])
    edge_to_cost_ratio = direct_edge_ratio
    if edge_to_cost_ratio is None and edge_bps is not None and estimated_cost_bps > 0:
        edge_to_cost_ratio = edge_bps / estimated_cost_bps
    if edge_to_cost_ratio is not None and edge_to_cost_ratio < ratio_min:
        return RiskDecision(
            False,
            "edge_cost_ratio_too_low",
            f"Expected edge to cost ratio is {edge_to_cost_ratio:.2f}x, below the {ratio_min:.2f}x floor.",
            {
                "edge_bps": edge_bps,
                "estimated_cost_bps": estimated_cost_bps,
                "edge_to_cost_ratio": edge_to_cost_ratio,
                "min_edge_to_cost_ratio": ratio_min,
            },
        )

    return RiskDecision(
        True,
        "microstructure_ok",
        "Candidate spread, liquidity, and estimated cost checks are inside limits or not required by current settings.",
        {
            "spread_bps": spread_bps,
            "average_dollar_volume": adv,
            "edge_bps": edge_bps,
            "estimated_cost_bps": estimated_cost_bps,
            "edge_to_cost_ratio": edge_to_cost_ratio,
        },
    )


def evaluate_candidate_risk_controls(
    candidate: dict[str, Any],
    *,
    settings_state: dict[str, Any],
    owned_open: pd.DataFrame,
    owned_pending: pd.DataFrame,
    session: dict[str, Any],
    current_equity: float,
) -> RiskDecision:
    session_decision = evaluate_session_entry_window(settings_state, session)
    if not session_decision.allowed:
        return session_decision

    microstructure_decision = evaluate_candidate_microstructure(candidate, settings_state)
    if not microstructure_decision.allowed:
        return microstructure_decision

    ticker = str(candidate.get("ticker") or "").strip().upper()
    projected_notional = estimate_trade_notional(candidate)
    if projected_notional <= 0:
        projected_notional = _coerce_float(settings_state.get("max_notional_per_trade"), max(float(current_equity), 1.0))
    projected_notional = min(projected_notional, _coerce_float(settings_state.get("max_notional_per_trade"), projected_notional))

    adv = _average_dollar_volume(candidate)
    if adv is not None:
        max_order_adv_pct = _coerce_float(settings_state.get("max_order_adv_pct"), DEFAULT_RISK_CONTROL_SETTINGS["max_order_adv_pct"])
        order_adv_cap = adv * max_order_adv_pct / 100.0
        if projected_notional > order_adv_cap:
            return RiskDecision(
                False,
                "order_adv_cap",
                f"Projected order notional is ${projected_notional:,.2f}, above the {max_order_adv_pct:.3f}% ADV cap of ${order_adv_cap:,.2f}.",
                {
                    "ticker": ticker,
                    "projected_notional": projected_notional,
                    "average_dollar_volume": adv,
                    "max_order_adv_pct": max_order_adv_pct,
                    "order_adv_cap": order_adv_cap,
                },
            )

    intraday_volume = _intraday_dollar_volume(candidate)
    if intraday_volume is not None:
        max_intraday_volume_pct = _coerce_float(settings_state.get("max_intraday_volume_pct"), DEFAULT_RISK_CONTROL_SETTINGS["max_intraday_volume_pct"])
        intraday_cap = intraday_volume * max_intraday_volume_pct / 100.0
        if projected_notional > intraday_cap:
            return RiskDecision(
                False,
                "intraday_volume_cap",
                f"Projected order notional is ${projected_notional:,.2f}, above the {max_intraday_volume_pct:.3f}% intraday-volume cap of ${intraday_cap:,.2f}.",
                {
                    "ticker": ticker,
                    "projected_notional": projected_notional,
                    "intraday_dollar_volume": intraday_volume,
                    "max_intraday_volume_pct": max_intraday_volume_pct,
                    "intraday_volume_cap": intraday_cap,
                },
            )

    current_open_notional = sum_frame_notional(owned_open) + sum_frame_notional(owned_pending)
    total_cap = effective_total_notional_cap(settings_state, equity=current_equity)
    single_cap = max(float(current_equity), 1.0) * _coerce_float(settings_state.get("max_single_position_pct"), 10.0) / 100.0
    if projected_notional > single_cap:
        return RiskDecision(
            False,
            "single_position_cap",
            f"Projected {ticker} notional is ${projected_notional:,.2f}, above the ${single_cap:,.2f} single-position cap.",
            {"ticker": ticker, "projected_notional": projected_notional, "single_position_cap": single_cap},
        )
    if current_open_notional + projected_notional > total_cap:
        return RiskDecision(
            False,
            "gross_exposure_cap",
            f"Projected gross notional would be ${current_open_notional + projected_notional:,.2f}, above the ${total_cap:,.2f} cap.",
            {"current_open_notional": current_open_notional, "projected_notional": projected_notional, "total_notional_cap": total_cap},
        )

    bucket = bucket_for_symbol(ticker, settings_state)
    bucket_exposure = build_bucket_exposure([owned_open, owned_pending], settings_state=settings_state)
    bucket_before = float(bucket_exposure.get(bucket, 0.0))
    bucket_cap = max(float(current_equity), 1.0) * _coerce_float(settings_state.get("max_correlated_bucket_pct"), 30.0) / 100.0
    if bucket_before + projected_notional > bucket_cap:
        return RiskDecision(
            False,
            "correlated_bucket_cap",
            f"Projected {bucket} bucket notional would be ${bucket_before + projected_notional:,.2f}, above the ${bucket_cap:,.2f} cap.",
            {"ticker": ticker, "bucket": bucket, "bucket_before": bucket_before, "projected_notional": projected_notional, "bucket_cap": bucket_cap},
        )

    return RiskDecision(
        True,
        "risk_controls_ok",
        "Candidate clears gross leverage, single-position, bucket, session, spread, liquidity, and edge-cost checks.",
        {
            "ticker": ticker,
            "projected_notional": projected_notional,
            "current_open_notional": current_open_notional,
            "total_notional_cap": total_cap,
            "single_position_cap": single_cap,
            "bucket": bucket,
            "bucket_before": bucket_before,
            "bucket_cap": bucket_cap,
            "microstructure": microstructure_decision.metrics,
            "average_dollar_volume": adv,
            "intraday_dollar_volume": intraday_volume,
        },
    )
