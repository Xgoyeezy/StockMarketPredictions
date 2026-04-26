from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend import stock_direction_model as sdm
from backend.core.config import settings
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.schemas import (
    AnalyzeRequest,
    CloseTradeRequest,
    OpenTradeRequest,
    OptionsAutomationRefreshRequest,
    OptionsAutomationScanRequest,
    OrganizationTradeAutomationActionRequest,
    OrganizationTradeAutomationUpdateRequest,
    WatchlistRequest,
)
from backend.services.audit_service import record_audit_event
from backend.services.brokerage_account_service import (
    build_linked_account_execution_client,
    build_linked_client_automation_summary,
    get_linked_account_automation_profile,
)
from backend.services.equity_snapshot_service import (
    get_latest_trade_automation_equity_snapshot,
    record_trade_automation_equity_snapshot,
)
from backend.services.execution.alpaca_client import AlpacaApiError, build_alpaca_live_client, build_alpaca_paper_client
from backend.services.exceptions import ServiceError, ValidationError
from backend.services.execution import get_execution_adapter_for
from backend.services.market_service import analyze_market, build_watchlist
from backend.services.permissions import require_current_user_permission
from backend.services import risk_control_service
from backend.services import (
    automation_accuracy_calibration_service,
    automation_ai_review_service,
    automation_daily_objective_service,
    automation_exit_execution_watchdog_service,
    automation_loss_containment_service,
    automation_live_pilot_canary_service,
    automation_live_pilot_expansion_canary_service,
    automation_live_pilot_expansion_service,
    automation_live_pilot_promotion_report_service,
    automation_live_pilot_readiness_service,
    automation_live_pilot_soak_service,
    automation_live_pilot_window_canary_service,
    automation_live_pilot_window_service,
    automation_limited_live_cap_expansion_canary_service,
    automation_limited_live_cap_expansion_gate_service,
    automation_limited_live_cap_expansion_report_service,
    automation_limited_live_next_tier_cap_gate_service,
    automation_limited_live_next_tier_cap_report_service,
    automation_limited_live_rollout_canary_service,
    automation_limited_live_rollout_gate_service,
    automation_limited_live_safety_ladder_service,
    automation_paper_broker_reconciliation_service,
    automation_paper_canary_service,
    automation_paper_order_lifecycle_canary_service,
    automation_paper_order_lifecycle_soak_service,
    automation_state_control_service,
)
from backend.services import automation_state_control_shadow_service
from backend.services import strategy_validation_service
from backend.services.serialization import serialize_value
from backend.services.session_policy import (
    EASTERN_MARKET_TIMEZONE,
    build_market_session_context,
    get_session_profile,
    normalize_session_mode,
)
from backend.services.strategy_engine.events import record_domain_event
from backend.services.tenant_service import _resolve_tenant_for_current_user, _resolve_user_for_current_user
from backend.services.trade_service import (
    _record_order_event,
    get_trade_summary,
    open_trade_from_request,
    resolve_trade_identifier,
    sync_pending_orders_from_broker,
)
from backend.services.workspace_service import delete_workspace, list_workspaces, save_workspace

_TRADE_AUTOMATION_KEY = "trade_automation"
_TRADE_AUTOMATION_TEMPLATE_KEY = "trade_automation_template"
_TRADE_AUTOMATION_PROFILES_KEY = "trade_automation_profiles"
_TRADE_AUTOMATION_HISTORY_LIMIT = 20
_COLLECTION_AUDIT_HISTORY_LIMIT = 20
_AUTOMATION_MARKER = "trade_automation"
_AUTOMATION_BOARD_TAG = "automation-board"
_AUTOMATION_BOARD_HISTORY_LIMIT = 12
_MARKET_TIMEZONE = EASTERN_MARKET_TIMEZONE
_AUTOMATION_INTERVAL_CHOICES = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
_AUTOMATION_ROUTE_CHOICES = {"desk", "broker_paper", "broker_live"}
_AUTOMATION_ORDER_CHOICES = {"market", "limit"}
_AUTOMATION_TIF_CHOICES = {"day", "day_ext", "gtc_90d"}
_AUTOMATION_INSTRUMENT_CHOICES = {"equity", "listed_option"}
_AUTOMATION_OPTION_MAX_SPREAD_PCT = 0.15
_AUTOMATION_OPTION_MAX_QUOTE_AGE_SECONDS = 180.0
_AUTOMATION_OPTION_MIN_VOLUME = 25
_AUTOMATION_OPTION_MIN_OPEN_INTEREST = 100
_COLLECTION_PHASE_ROUTE = "broker_paper"
_COLLECTION_PHASE_LABEL_COLLECTING = "Collecting sample"
_COLLECTION_PHASE_LABEL_RERUNNING = "Rerunning validation"
_COLLECTION_PHASE_LABEL_BLOCKED = "Validation still blocked"
_COLLECTION_PHASE_LABEL_PERSISTENCE = "Local ledger persistence issue"
_COLLECTION_PHASE_LABEL_READY = "Ready for rollout review"
_VALIDATION_PROFILE_CONFIG_PATH = Path("config/ten_x_stand_test_profile.json")
_VALIDATION_EXPORT_DEFAULTS = {
    "starting_capital": 100000.0,
    "known_peak_equity": 1900000.0,
    "known_drawdown_peak": 1800000.0,
    "known_drawdown_trough": 1300000.0,
    "known_max_drawdown_pct": 27.8,
}
_AUTOMATION_PERSONAL_PAPER_PROFILE = "personal_paper"
_AUTOMATION_PERSONAL_LIVE_PROFILE = "personal_live"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: Any) -> datetime | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _max_serialized_datetime(*values: Any) -> str | None:
    latest: datetime | None = None
    for value in values:
        parsed = _parse_iso_datetime(value)
        if parsed is None:
            continue
        latest = parsed if latest is None else max(latest, parsed)
    return _serialize_datetime(latest)


def _normalize_tickers(values: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        cleaned = str(value or "").strip().upper()
        if not cleaned or cleaned in seen:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)
    return normalized


def _normalize_bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    return bool(value)


def _normalize_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = int(default)
    return max(minimum, min(maximum, normalized))


def _normalize_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        normalized = float(default)
    return max(minimum, min(maximum, normalized))


def _normalize_automation_instrument(value: Any, default: str = "equity") -> str:
    normalized = str(value or default).strip().lower() or default
    return normalized if normalized in _AUTOMATION_INSTRUMENT_CHOICES else default


def _build_automation_target_key(ticker: Any, instrument_type: Any = None) -> str:
    normalized_ticker = str(ticker or "").strip().upper()
    normalized_instrument = _normalize_automation_instrument(instrument_type, default="")
    if not normalized_ticker:
        return ""
    if normalized_instrument:
        return f"{normalized_ticker}::{normalized_instrument}"
    return normalized_ticker


def _resolve_enabled_automation_instruments(settings_state: dict[str, Any]) -> list[str]:
    enabled: list[str] = []
    if bool(settings_state.get("auto_trade_equities", True)):
        enabled.append("equity")
    if bool(settings_state.get("auto_trade_listed_options", True)):
        enabled.append("listed_option")
    return enabled or ["equity"]


def _build_broker_route_snapshot(*, execution_intent: str) -> dict[str, Any]:
    has_trading_credentials = bool(settings.alpaca_api_key_id and settings.alpaca_api_secret_key)
    live_enabled = bool(settings.alpaca_live_trading_enabled)
    execution_intent = str(execution_intent or "").strip().lower() or "broker_paper"

    def _route_payload(*, key: str, label: str, connected: bool, enabled: bool, active: bool, detail: str) -> dict[str, Any]:
        if active:
            tone = "positive"
            status = "active"
            value = "Active"
        elif connected and enabled:
            tone = "warning"
            status = "standby"
            value = "Standby"
        elif connected:
            tone = "neutral"
            status = "configured"
            value = "Configured"
        else:
            tone = "negative"
            status = "unavailable"
            value = "Unavailable"
        return {
            "key": key,
            "label": label,
            "status": status,
            "tone": tone,
            "value": value,
            "connected": connected,
            "enabled": enabled,
            "active": active,
            "detail": detail,
        }

    desk_active = execution_intent == "desk"
    paper_active = execution_intent == "broker_paper"
    live_active = execution_intent == "broker_live"

    return {
        "desk": _route_payload(
            key="desk",
            label="Desk route",
            connected=True,
            enabled=True,
            active=desk_active,
            detail="Internal desk routing only. No broker order leaves the platform.",
        ),
        "broker_paper": _route_payload(
            key="broker_paper",
            label="Broker paper",
            connected=has_trading_credentials,
            enabled=has_trading_credentials,
            active=paper_active,
            detail=(
                "Alpaca paper route is connected and can accept unattended paper orders."
                if has_trading_credentials
                else "Paper broker credentials are missing."
            ),
        ),
        "broker_live": _route_payload(
            key="broker_live",
            label="Broker live",
            connected=has_trading_credentials,
            enabled=live_enabled,
            active=live_active,
            detail=(
                "Live broker credentials are present, but live trading is not enabled. This route is visible and inactive."
                if has_trading_credentials and not live_enabled
                else "Live broker is enabled, but rollout gating still controls whether it can be used."
                if has_trading_credentials and live_enabled
                else "Live broker credentials are not configured."
            ),
        ),
    }


def _normalize_trade_automation_profile_state(stored: dict[str, Any] | None) -> dict[str, Any]:
    stored = stored or {}
    if not isinstance(stored, dict):
        stored = {}

    settings_state = stored.get("settings") or {}
    runtime_state = stored.get("runtime") or {}
    history = stored.get("history") or []
    if not isinstance(settings_state, dict):
        settings_state = {}
    if not isinstance(runtime_state, dict):
        runtime_state = {}
    if not isinstance(history, list):
        history = []

    tickers = _normalize_tickers(settings_state.get("tickers"))
    if not tickers:
        tickers = ["SPY", "QQQ", "AAPL", "MSFT"]

    execution_intent = str(settings_state.get("execution_intent") or "broker_paper").strip().lower()
    if execution_intent not in _AUTOMATION_ROUTE_CHOICES:
        execution_intent = "broker_paper"

    interval = str(settings_state.get("interval") or "5m").strip().lower()
    if interval not in _AUTOMATION_INTERVAL_CHOICES:
        interval = "5m"

    instrument_type = _normalize_automation_instrument(settings_state.get("instrument_type"), default="equity")
    default_time_in_force = "day" if instrument_type == "listed_option" else "day_ext"
    default_regular_hours_only = instrument_type == "listed_option"

    order_type = str(settings_state.get("order_type") or "limit").strip().lower()
    if order_type not in _AUTOMATION_ORDER_CHOICES:
        order_type = "limit"

    time_in_force = str(settings_state.get("time_in_force") or default_time_in_force).strip().lower()
    if time_in_force not in _AUTOMATION_TIF_CHOICES:
        time_in_force = default_time_in_force
    regular_hours_only = _normalize_bool(settings_state.get("regular_hours_only"), default_regular_hours_only)
    if instrument_type == "listed_option":
        time_in_force = "day"
        regular_hours_only = True
    auto_trade_equities = _normalize_bool(
        settings_state.get("auto_trade_equities"),
        instrument_type != "listed_option",
    )
    auto_trade_listed_options = _normalize_bool(
        settings_state.get("auto_trade_listed_options"),
        instrument_type == "listed_option",
    )

    ticker_cooldowns = {}
    for key, value in dict(runtime_state.get("ticker_cooldowns") or {}).items():
        cleaned_key = str(key or "").strip()
        parsed_value = _parse_iso_datetime(value)
        if cleaned_key and parsed_value is not None:
            ticker_cooldowns[cleaned_key] = _serialize_datetime(parsed_value)

    normalized_history = []
    for item in history[-_TRADE_AUTOMATION_HISTORY_LIMIT:]:
        if isinstance(item, dict):
            normalized_history.append({str(key): serialize_value(value) for key, value in item.items()})

    collection_audit_history = []
    for item in list(runtime_state.get("collection_audit_history") or [])[-_COLLECTION_AUDIT_HISTORY_LIMIT:]:
        if isinstance(item, dict):
            collection_audit_history.append({str(key): serialize_value(value) for key, value in item.items()})

    account_size = _normalize_float(settings_state.get("account_size"), 10000.0, minimum=100.0, maximum=5_000_000.0)
    effective_funds_multiplier = _normalize_float(
        settings_state.get("effective_funds_multiplier"),
        1.0,
        minimum=1.0,
        maximum=10.0,
    )
    max_gross_leverage = _normalize_float(
        settings_state.get("max_gross_leverage"),
        1.5,
        minimum=0.1,
        maximum=10.0,
    )
    max_single_position_pct = _normalize_float(
        settings_state.get("max_single_position_pct"),
        12.0,
        minimum=1.0,
        maximum=100.0,
    )
    max_notional_per_trade_default = max(account_size * (max_single_position_pct / 100.0), 100.0)
    max_total_open_notional_default = max(account_size * max_gross_leverage, 100.0)
    risk_settings = risk_control_service.normalize_risk_control_settings(settings_state)
    ai_review_settings = automation_ai_review_service.normalize_ai_review_settings(settings_state)
    ai_review_runtime = automation_ai_review_service.normalize_ai_review_runtime(runtime_state)
    accuracy_calibration_settings = (
        automation_accuracy_calibration_service.normalize_accuracy_calibration_settings(settings_state)
    )
    accuracy_calibration_runtime = (
        automation_accuracy_calibration_service.normalize_accuracy_calibration_runtime(runtime_state)
    )
    daily_objective_settings = automation_daily_objective_service.normalize_daily_objective_settings(settings_state)
    daily_objective_runtime = automation_daily_objective_service.normalize_daily_objective_runtime(runtime_state)
    loss_containment_settings = (
        automation_loss_containment_service.normalize_loss_containment_settings(settings_state)
    )
    loss_containment_runtime = (
        automation_loss_containment_service.normalize_loss_containment_runtime(runtime_state)
    )
    exit_watchdog_settings = automation_exit_execution_watchdog_service.normalize_exit_watchdog_settings(
        settings_state
    )
    exit_watchdog_runtime = automation_exit_execution_watchdog_service.normalize_exit_watchdog_runtime(
        runtime_state
    )
    state_control_settings = automation_state_control_service.normalize_state_control_settings(settings_state)
    state_control_runtime = automation_state_control_service.normalize_state_control_runtime(runtime_state)
    state_control_shadow_runtime = automation_state_control_shadow_service.normalize_shadow_validation_runtime(runtime_state)
    paper_broker_reconciliation_runtime = (
        automation_paper_broker_reconciliation_service.normalize_paper_broker_reconciliation_runtime(runtime_state)
    )
    paper_order_lifecycle_soak_runtime = (
        automation_paper_order_lifecycle_soak_service.normalize_paper_order_lifecycle_soak_runtime(runtime_state)
    )
    paper_order_lifecycle_canary_settings = (
        automation_paper_order_lifecycle_canary_service.normalize_paper_order_lifecycle_canary_settings(settings_state)
    )
    paper_order_lifecycle_canary_runtime = (
        automation_paper_order_lifecycle_canary_service.normalize_paper_order_lifecycle_canary_runtime(runtime_state)
    )
    paper_canary_settings = automation_paper_canary_service.normalize_paper_canary_settings(settings_state)
    paper_canary_runtime = automation_paper_canary_service.normalize_paper_canary_runtime(runtime_state)
    live_pilot_readiness_runtime = (
        automation_live_pilot_readiness_service.normalize_live_pilot_readiness_runtime(runtime_state)
    )
    live_pilot_soak_settings = automation_live_pilot_soak_service.normalize_live_pilot_soak_settings(settings_state)
    live_pilot_soak_runtime = automation_live_pilot_soak_service.normalize_live_pilot_soak_runtime(runtime_state)
    live_pilot_canary_settings = automation_live_pilot_canary_service.normalize_live_pilot_canary_settings(
        settings_state
    )
    live_pilot_canary_runtime = automation_live_pilot_canary_service.normalize_live_pilot_canary_runtime(runtime_state)
    live_pilot_expansion_settings = (
        automation_live_pilot_expansion_service.normalize_live_pilot_expansion_settings(settings_state)
    )
    live_pilot_expansion_runtime = (
        automation_live_pilot_expansion_service.normalize_live_pilot_expansion_runtime(runtime_state)
    )
    live_pilot_expansion_canary_settings = (
        automation_live_pilot_expansion_canary_service.normalize_live_pilot_expansion_canary_settings(settings_state)
    )
    live_pilot_expansion_canary_runtime = (
        automation_live_pilot_expansion_canary_service.normalize_live_pilot_expansion_canary_runtime(runtime_state)
    )
    live_pilot_window_settings = (
        automation_live_pilot_window_service.normalize_live_pilot_window_settings(settings_state)
    )
    live_pilot_window_runtime = (
        automation_live_pilot_window_service.normalize_live_pilot_window_runtime(runtime_state)
    )
    live_pilot_window_canary_settings = (
        automation_live_pilot_window_canary_service.normalize_live_pilot_window_canary_settings(settings_state)
    )
    live_pilot_window_canary_runtime = (
        automation_live_pilot_window_canary_service.normalize_live_pilot_window_canary_runtime(runtime_state)
    )
    live_pilot_promotion_settings = (
        automation_live_pilot_promotion_report_service.normalize_live_pilot_promotion_settings(settings_state)
    )
    live_pilot_promotion_runtime = (
        automation_live_pilot_promotion_report_service.normalize_live_pilot_promotion_runtime(runtime_state)
    )
    limited_live_rollout_settings = (
        automation_limited_live_rollout_gate_service.normalize_limited_live_rollout_settings(settings_state)
    )
    limited_live_rollout_runtime = (
        automation_limited_live_rollout_gate_service.normalize_limited_live_rollout_runtime(runtime_state)
    )
    limited_live_rollout_canary_settings = (
        automation_limited_live_rollout_canary_service.normalize_limited_live_rollout_canary_settings(settings_state)
    )
    limited_live_rollout_canary_runtime = (
        automation_limited_live_rollout_canary_service.normalize_limited_live_rollout_canary_runtime(runtime_state)
    )
    limited_live_cap_expansion_settings = (
        automation_limited_live_cap_expansion_report_service.normalize_limited_live_cap_expansion_settings(settings_state)
    )
    limited_live_cap_expansion_runtime = (
        automation_limited_live_cap_expansion_report_service.normalize_limited_live_cap_expansion_runtime(runtime_state)
    )
    limited_live_cap_expansion_gate_settings = (
        automation_limited_live_cap_expansion_gate_service.normalize_limited_live_cap_expansion_gate_settings(settings_state)
    )
    limited_live_cap_expansion_gate_runtime = (
        automation_limited_live_cap_expansion_gate_service.normalize_limited_live_cap_expansion_gate_runtime(runtime_state)
    )
    limited_live_cap_expansion_canary_settings = (
        automation_limited_live_cap_expansion_canary_service.normalize_limited_live_cap_expansion_canary_settings(
            settings_state
        )
    )
    limited_live_cap_expansion_canary_runtime = (
        automation_limited_live_cap_expansion_canary_service.normalize_limited_live_cap_expansion_canary_runtime(
            runtime_state
        )
    )
    limited_live_next_tier_cap_settings = (
        automation_limited_live_next_tier_cap_report_service.normalize_limited_live_next_tier_cap_settings(
            settings_state
        )
    )
    limited_live_next_tier_cap_runtime = (
        automation_limited_live_next_tier_cap_report_service.normalize_limited_live_next_tier_cap_runtime(
            runtime_state
        )
    )
    limited_live_next_tier_cap_gate_settings = (
        automation_limited_live_next_tier_cap_gate_service.normalize_limited_live_next_tier_cap_gate_settings(
            settings_state
        )
    )
    limited_live_next_tier_cap_gate_runtime = (
        automation_limited_live_next_tier_cap_gate_service.normalize_limited_live_next_tier_cap_gate_runtime(
            runtime_state
        )
    )
    limited_live_ladder_settings = (
        automation_limited_live_safety_ladder_service.normalize_limited_live_ladder_settings(settings_state)
    )
    limited_live_ladder_runtime = (
        automation_limited_live_safety_ladder_service.normalize_limited_live_ladder_runtime(runtime_state)
    )

    return {
        "settings": {
            "enabled": _normalize_bool(settings_state.get("enabled"), False),
            "armed": _normalize_bool(settings_state.get("armed"), False),
            "kill_switch": _normalize_bool(settings_state.get("kill_switch"), False),
            "execution_intent": execution_intent,
            "allow_review_candidates": _normalize_bool(settings_state.get("allow_review_candidates"), False),
            "tickers": tickers,
            "interval": interval,
            "horizon": _normalize_int(settings_state.get("horizon"), 5, minimum=1, maximum=50),
            "cycle_interval_seconds": _normalize_int(settings_state.get("cycle_interval_seconds"), 60, minimum=15, maximum=3600),
            "cooldown_minutes": _normalize_int(settings_state.get("cooldown_minutes"), 20, minimum=0, maximum=1440),
            "account_size": account_size,
            "effective_funds_multiplier": effective_funds_multiplier,
            "risk_percent": _normalize_float(settings_state.get("risk_percent"), 0.50, minimum=0.05, maximum=5.0),
            "instrument_type": instrument_type,
            "auto_trade_equities": auto_trade_equities,
            "auto_trade_listed_options": auto_trade_listed_options,
            "order_type": order_type,
            "time_in_force": time_in_force,
            "regular_hours_only": regular_hours_only,
            "auto_sync_orders": _normalize_bool(settings_state.get("auto_sync_orders"), True),
            "auto_manage_positions": _normalize_bool(settings_state.get("auto_manage_positions"), True),
            "auto_flatten_before_close": _normalize_bool(settings_state.get("auto_flatten_before_close"), True),
            "flatten_before_close_minutes": _normalize_int(settings_state.get("flatten_before_close_minutes"), 15, minimum=1, maximum=90),
            "max_open_positions": _normalize_int(settings_state.get("max_open_positions"), 6, minimum=1, maximum=25),
            "max_notional_per_trade": _normalize_float(settings_state.get("max_notional_per_trade"), max_notional_per_trade_default, minimum=100.0, maximum=5_000_000.0),
            "max_total_open_notional": _normalize_float(settings_state.get("max_total_open_notional"), max_total_open_notional_default, minimum=100.0, maximum=5_000_000.0),
            "max_gross_leverage": max_gross_leverage,
            "max_single_position_pct": max_single_position_pct,
            "max_correlated_bucket_pct": _normalize_float(settings_state.get("max_correlated_bucket_pct"), 35.0, minimum=1.0, maximum=100.0),
            "max_daily_loss_pct": risk_settings["max_daily_loss_pct"],
            "max_weekly_loss_pct": risk_settings["max_weekly_loss_pct"],
            "drawdown_size_cut_pct": risk_settings["drawdown_size_cut_pct"],
            "drawdown_stop_pct": risk_settings["drawdown_stop_pct"],
            "drawdown_audit_pct": risk_settings["drawdown_audit_pct"],
            "risk_cut_multiplier": risk_settings["risk_cut_multiplier"],
            "min_edge_to_cost_ratio": _normalize_float(settings_state.get("min_edge_to_cost_ratio"), 2.5, minimum=0.0, maximum=25.0),
            "allow_pyramiding": _normalize_bool(settings_state.get("allow_pyramiding"), True),
            "allow_averaging_down": risk_settings["allow_averaging_down"],
            "require_liquidity_fields": _normalize_bool(settings_state.get("require_liquidity_fields"), True),
            "require_edge_fields": risk_settings["require_edge_fields"],
            "market_slippage_bps": risk_settings["market_slippage_bps"],
            "limit_slippage_bps": risk_settings["limit_slippage_bps"],
            "max_spread_bps": risk_settings["max_spread_bps"],
            "min_average_dollar_volume": risk_settings["min_average_dollar_volume"],
            "max_order_adv_pct": risk_settings["max_order_adv_pct"],
            "max_intraday_volume_pct": risk_settings["max_intraday_volume_pct"],
            "no_new_entries_first_minutes": risk_settings["no_new_entries_first_minutes"],
            "no_new_entries_before_close_minutes": risk_settings["no_new_entries_before_close_minutes"],
            "cycle_entry_rank_limit": _normalize_int(settings_state.get("cycle_entry_rank_limit"), 2, minimum=1, maximum=10),
            "max_daily_loss_r": _normalize_float(settings_state.get("max_daily_loss_r"), 2.0, minimum=0.25, maximum=25.0),
            "max_consecutive_losses": _normalize_int(settings_state.get("max_consecutive_losses"), 3, minimum=1, maximum=25),
            "max_daily_entries": _normalize_int(settings_state.get("max_daily_entries"), 3, minimum=1, maximum=100),
            "max_daily_entries_per_symbol": _normalize_int(settings_state.get("max_daily_entries_per_symbol"), 1, minimum=1, maximum=25),
            "max_error_streak": _normalize_int(settings_state.get("max_error_streak"), 3, minimum=1, maximum=25),
            "long_only": _normalize_bool(settings_state.get("long_only"), True),
            "equities_only": _normalize_bool(settings_state.get("equities_only"), True),
            "fractional_shares_only": _normalize_bool(settings_state.get("fractional_shares_only"), True),
            "use_fast_model": _normalize_bool(settings_state.get("use_fast_model"), True),
            "ai_daily_review_enabled": ai_review_settings["ai_daily_review_enabled"],
            "ai_auto_adjust_enabled": ai_review_settings["ai_auto_adjust_enabled"],
            "ai_adjust_live_enabled": ai_review_settings["ai_adjust_live_enabled"],
            "ai_review_min_trades": ai_review_settings["ai_review_min_trades"],
            "ai_max_daily_setting_changes": ai_review_settings["ai_max_daily_setting_changes"],
            "ai_max_step_pct": ai_review_settings["ai_max_step_pct"],
            "accuracy_calibration_enabled": accuracy_calibration_settings["accuracy_calibration_enabled"],
            "accuracy_calibration_apply_to_live": accuracy_calibration_settings["accuracy_calibration_apply_to_live"],
            "accuracy_calibration_min_samples": accuracy_calibration_settings["accuracy_calibration_min_samples"],
            "accuracy_calibration_stale_after_sessions": accuracy_calibration_settings[
                "accuracy_calibration_stale_after_sessions"
            ],
            "accuracy_calibration_max_candidate_penalty": accuracy_calibration_settings[
                "accuracy_calibration_max_candidate_penalty"
            ],
            "daily_objective_enabled": daily_objective_settings["daily_objective_enabled"],
            "daily_profit_target_pct": daily_objective_settings["daily_profit_target_pct"],
            "daily_profit_target_dollars": daily_objective_settings["daily_profit_target_dollars"],
            "daily_loss_budget_pct": daily_objective_settings["daily_loss_budget_pct"],
            "daily_objective_apply_to_live": daily_objective_settings["daily_objective_apply_to_live"],
            "loss_containment_enabled": loss_containment_settings["loss_containment_enabled"],
            "loss_containment_apply_to_live": loss_containment_settings["loss_containment_apply_to_live"],
            "loss_containment_auto_close_paper": loss_containment_settings["loss_containment_auto_close_paper"],
            "loss_containment_auto_close_live": loss_containment_settings["loss_containment_auto_close_live"],
            "loss_containment_max_open_heat_pct": loss_containment_settings["loss_containment_max_open_heat_pct"],
            "loss_containment_max_position_loss_r": loss_containment_settings[
                "loss_containment_max_position_loss_r"
            ],
            "loss_containment_max_position_mae_pct": loss_containment_settings[
                "loss_containment_max_position_mae_pct"
            ],
            "loss_containment_profit_protect_trigger_r": loss_containment_settings[
                "loss_containment_profit_protect_trigger_r"
            ],
            "loss_containment_profit_protect_floor_r": loss_containment_settings[
                "loss_containment_profit_protect_floor_r"
            ],
            "loss_containment_time_stop_minutes": loss_containment_settings["loss_containment_time_stop_minutes"],
            "loss_containment_stale_quote_seconds": loss_containment_settings[
                "loss_containment_stale_quote_seconds"
            ],
            "exit_watchdog_enabled": exit_watchdog_settings["exit_watchdog_enabled"],
            "exit_watchdog_apply_to_live": exit_watchdog_settings["exit_watchdog_apply_to_live"],
            "exit_watchdog_max_confirmation_seconds": exit_watchdog_settings[
                "exit_watchdog_max_confirmation_seconds"
            ],
            "exit_watchdog_max_partial_minutes": exit_watchdog_settings["exit_watchdog_max_partial_minutes"],
            "exit_watchdog_block_entries_on_unconfirmed_exit": exit_watchdog_settings[
                "exit_watchdog_block_entries_on_unconfirmed_exit"
            ],
            "state_control_enabled": state_control_settings["state_control_enabled"],
            "state_control_auto_throttle_enabled": state_control_settings["state_control_auto_throttle_enabled"],
            "state_control_auto_halt_enabled": state_control_settings["state_control_auto_halt_enabled"],
            "state_control_watch_score": state_control_settings["state_control_watch_score"],
            "state_control_derisk_score": state_control_settings["state_control_derisk_score"],
            "state_control_halt_score": state_control_settings["state_control_halt_score"],
            "state_control_recovery_cycles": state_control_settings["state_control_recovery_cycles"],
            "paper_canary_enabled": paper_canary_settings["paper_canary_enabled"],
            "paper_canary_auto_review_enabled": paper_canary_settings["paper_canary_auto_review_enabled"],
            "paper_canary_window_sessions": paper_canary_settings["paper_canary_window_sessions"],
            "paper_canary_required_clean_sessions": paper_canary_settings["paper_canary_required_clean_sessions"],
            "paper_order_lifecycle_canary_enabled": paper_order_lifecycle_canary_settings[
                "paper_order_lifecycle_canary_enabled"
            ],
            "paper_order_lifecycle_auto_submit_enabled": paper_order_lifecycle_canary_settings[
                "paper_order_lifecycle_auto_submit_enabled"
            ],
            "paper_order_lifecycle_window_sessions": paper_order_lifecycle_canary_settings[
                "paper_order_lifecycle_window_sessions"
            ],
            "paper_order_lifecycle_required_clean_sessions": paper_order_lifecycle_canary_settings[
                "paper_order_lifecycle_required_clean_sessions"
            ],
            "live_pilot_soak_enabled": live_pilot_soak_settings["live_pilot_soak_enabled"],
            "live_pilot_max_notional": live_pilot_soak_settings["live_pilot_max_notional"],
            "live_pilot_symbol": live_pilot_soak_settings["live_pilot_symbol"],
            "live_pilot_approval_ttl_minutes": live_pilot_soak_settings[
                "live_pilot_approval_ttl_minutes"
            ],
            "live_pilot_cancel_timeout_seconds": live_pilot_soak_settings[
                "live_pilot_cancel_timeout_seconds"
            ],
            "live_pilot_canary_enabled": live_pilot_canary_settings["live_pilot_canary_enabled"],
            "live_pilot_canary_auto_review_enabled": live_pilot_canary_settings[
                "live_pilot_canary_auto_review_enabled"
            ],
            "live_pilot_canary_window_sessions": live_pilot_canary_settings[
                "live_pilot_canary_window_sessions"
            ],
            "live_pilot_canary_required_clean_sessions": live_pilot_canary_settings[
                "live_pilot_canary_required_clean_sessions"
            ],
            "live_pilot_expansion_enabled": live_pilot_expansion_settings[
                "live_pilot_expansion_enabled"
            ],
            "live_pilot_expansion_max_notional": live_pilot_expansion_settings[
                "live_pilot_expansion_max_notional"
            ],
            "live_pilot_expansion_max_daily_orders": live_pilot_expansion_settings[
                "live_pilot_expansion_max_daily_orders"
            ],
            "live_pilot_expansion_approval_ttl_minutes": live_pilot_expansion_settings[
                "live_pilot_expansion_approval_ttl_minutes"
            ],
            "live_pilot_expansion_require_limit": live_pilot_expansion_settings[
                "live_pilot_expansion_require_limit"
            ],
            "live_pilot_expansion_allow_autonomous_entries": live_pilot_expansion_settings[
                "live_pilot_expansion_allow_autonomous_entries"
            ],
            "live_pilot_expansion_canary_enabled": live_pilot_expansion_canary_settings[
                "live_pilot_expansion_canary_enabled"
            ],
            "live_pilot_expansion_canary_auto_review_enabled": live_pilot_expansion_canary_settings[
                "live_pilot_expansion_canary_auto_review_enabled"
            ],
            "live_pilot_expansion_canary_window_sessions": live_pilot_expansion_canary_settings[
                "live_pilot_expansion_canary_window_sessions"
            ],
            "live_pilot_expansion_canary_required_clean_sessions": live_pilot_expansion_canary_settings[
                "live_pilot_expansion_canary_required_clean_sessions"
            ],
            "live_pilot_window_enabled": live_pilot_window_settings["live_pilot_window_enabled"],
            "live_pilot_window_max_notional": live_pilot_window_settings["live_pilot_window_max_notional"],
            "live_pilot_window_max_session_orders": live_pilot_window_settings[
                "live_pilot_window_max_session_orders"
            ],
            "live_pilot_window_approval_ttl_minutes": live_pilot_window_settings[
                "live_pilot_window_approval_ttl_minutes"
            ],
            "live_pilot_window_duration_minutes": live_pilot_window_settings[
                "live_pilot_window_duration_minutes"
            ],
            "live_pilot_window_require_limit": live_pilot_window_settings["live_pilot_window_require_limit"],
            "live_pilot_window_canary_enabled": live_pilot_window_canary_settings[
                "live_pilot_window_canary_enabled"
            ],
            "live_pilot_window_canary_auto_review_enabled": live_pilot_window_canary_settings[
                "live_pilot_window_canary_auto_review_enabled"
            ],
            "live_pilot_window_canary_window_sessions": live_pilot_window_canary_settings[
                "live_pilot_window_canary_window_sessions"
            ],
            "live_pilot_window_canary_required_clean_sessions": live_pilot_window_canary_settings[
                "live_pilot_window_canary_required_clean_sessions"
            ],
            "live_pilot_promotion_report_enabled": live_pilot_promotion_settings[
                "live_pilot_promotion_report_enabled"
            ],
            "live_pilot_promotion_report_auto_review_enabled": live_pilot_promotion_settings[
                "live_pilot_promotion_report_auto_review_enabled"
            ],
            "live_pilot_promotion_required_window_clean_sessions": live_pilot_promotion_settings[
                "live_pilot_promotion_required_window_clean_sessions"
            ],
            "live_pilot_promotion_stale_after_days": live_pilot_promotion_settings[
                "live_pilot_promotion_stale_after_days"
            ],
            "limited_live_rollout_enabled": limited_live_rollout_settings["limited_live_rollout_enabled"],
            "limited_live_rollout_max_notional": limited_live_rollout_settings["limited_live_rollout_max_notional"],
            "limited_live_rollout_max_session_orders": limited_live_rollout_settings[
                "limited_live_rollout_max_session_orders"
            ],
            "limited_live_rollout_duration_minutes": limited_live_rollout_settings[
                "limited_live_rollout_duration_minutes"
            ],
            "limited_live_rollout_require_limit": limited_live_rollout_settings["limited_live_rollout_require_limit"],
            "limited_live_rollout_approval_ttl_minutes": limited_live_rollout_settings[
                "limited_live_rollout_approval_ttl_minutes"
            ],
            "limited_live_rollout_auto_expand_enabled": limited_live_rollout_settings[
                "limited_live_rollout_auto_expand_enabled"
            ],
            "limited_live_rollout_canary_enabled": limited_live_rollout_canary_settings[
                "limited_live_rollout_canary_enabled"
            ],
            "limited_live_rollout_canary_auto_review_enabled": limited_live_rollout_canary_settings[
                "limited_live_rollout_canary_auto_review_enabled"
            ],
            "limited_live_rollout_canary_window_sessions": limited_live_rollout_canary_settings[
                "limited_live_rollout_canary_window_sessions"
            ],
            "limited_live_rollout_canary_required_clean_sessions": limited_live_rollout_canary_settings[
                "limited_live_rollout_canary_required_clean_sessions"
            ],
            "limited_live_rollout_canary_stale_after_days": limited_live_rollout_canary_settings[
                "limited_live_rollout_canary_stale_after_days"
            ],
            "limited_live_cap_expansion_report_enabled": limited_live_cap_expansion_settings[
                "limited_live_cap_expansion_report_enabled"
            ],
            "limited_live_cap_expansion_report_auto_review_enabled": limited_live_cap_expansion_settings[
                "limited_live_cap_expansion_report_auto_review_enabled"
            ],
            "limited_live_cap_expansion_required_clean_sessions": limited_live_cap_expansion_settings[
                "limited_live_cap_expansion_required_clean_sessions"
            ],
            "limited_live_cap_expansion_stale_after_days": limited_live_cap_expansion_settings[
                "limited_live_cap_expansion_stale_after_days"
            ],
            "limited_live_cap_expansion_target_max_notional": limited_live_cap_expansion_settings[
                "limited_live_cap_expansion_target_max_notional"
            ],
            "limited_live_cap_expansion_enabled": limited_live_cap_expansion_gate_settings[
                "limited_live_cap_expansion_enabled"
            ],
            "limited_live_cap_expansion_max_notional": limited_live_cap_expansion_gate_settings[
                "limited_live_cap_expansion_max_notional"
            ],
            "limited_live_cap_expansion_duration_minutes": limited_live_cap_expansion_gate_settings[
                "limited_live_cap_expansion_duration_minutes"
            ],
            "limited_live_cap_expansion_approval_ttl_minutes": limited_live_cap_expansion_gate_settings[
                "limited_live_cap_expansion_approval_ttl_minutes"
            ],
            "limited_live_cap_expansion_max_session_orders": limited_live_cap_expansion_gate_settings[
                "limited_live_cap_expansion_max_session_orders"
            ],
            "limited_live_cap_expansion_require_limit": limited_live_cap_expansion_gate_settings[
                "limited_live_cap_expansion_require_limit"
            ],
            "limited_live_cap_expansion_auto_expand_enabled": limited_live_cap_expansion_gate_settings[
                "limited_live_cap_expansion_auto_expand_enabled"
            ],
            "limited_live_cap_expansion_canary_enabled": limited_live_cap_expansion_canary_settings[
                "limited_live_cap_expansion_canary_enabled"
            ],
            "limited_live_cap_expansion_canary_auto_review_enabled": limited_live_cap_expansion_canary_settings[
                "limited_live_cap_expansion_canary_auto_review_enabled"
            ],
            "limited_live_cap_expansion_canary_window_sessions": limited_live_cap_expansion_canary_settings[
                "limited_live_cap_expansion_canary_window_sessions"
            ],
            "limited_live_cap_expansion_canary_required_clean_sessions": limited_live_cap_expansion_canary_settings[
                "limited_live_cap_expansion_canary_required_clean_sessions"
            ],
            "limited_live_cap_expansion_canary_stale_after_days": limited_live_cap_expansion_canary_settings[
                "limited_live_cap_expansion_canary_stale_after_days"
            ],
            "limited_live_next_tier_cap_report_enabled": limited_live_next_tier_cap_settings[
                "limited_live_next_tier_cap_report_enabled"
            ],
            "limited_live_next_tier_cap_report_auto_review_enabled": limited_live_next_tier_cap_settings[
                "limited_live_next_tier_cap_report_auto_review_enabled"
            ],
            "limited_live_next_tier_cap_required_clean_sessions": limited_live_next_tier_cap_settings[
                "limited_live_next_tier_cap_required_clean_sessions"
            ],
            "limited_live_next_tier_cap_stale_after_days": limited_live_next_tier_cap_settings[
                "limited_live_next_tier_cap_stale_after_days"
            ],
            "limited_live_next_tier_cap_target_max_notional": limited_live_next_tier_cap_settings[
                "limited_live_next_tier_cap_target_max_notional"
            ],
            "limited_live_next_tier_cap_enabled": limited_live_next_tier_cap_gate_settings[
                "limited_live_next_tier_cap_enabled"
            ],
            "limited_live_next_tier_cap_max_notional": limited_live_next_tier_cap_gate_settings[
                "limited_live_next_tier_cap_max_notional"
            ],
            "limited_live_next_tier_cap_duration_minutes": limited_live_next_tier_cap_gate_settings[
                "limited_live_next_tier_cap_duration_minutes"
            ],
            "limited_live_next_tier_cap_approval_ttl_minutes": limited_live_next_tier_cap_gate_settings[
                "limited_live_next_tier_cap_approval_ttl_minutes"
            ],
            "limited_live_next_tier_cap_max_session_orders": limited_live_next_tier_cap_gate_settings[
                "limited_live_next_tier_cap_max_session_orders"
            ],
            "limited_live_next_tier_cap_require_limit": limited_live_next_tier_cap_gate_settings[
                "limited_live_next_tier_cap_require_limit"
            ],
            "limited_live_next_tier_cap_auto_expand_enabled": limited_live_next_tier_cap_gate_settings[
                "limited_live_next_tier_cap_auto_expand_enabled"
            ],
            "limited_live_next_tier_cap_canary_enabled": limited_live_ladder_settings[
                "limited_live_next_tier_cap_canary_enabled"
            ],
            "limited_live_next_tier_cap_canary_auto_review_enabled": limited_live_ladder_settings[
                "limited_live_next_tier_cap_canary_auto_review_enabled"
            ],
            "limited_live_next_tier_cap_canary_window_sessions": limited_live_ladder_settings[
                "limited_live_next_tier_cap_canary_window_sessions"
            ],
            "limited_live_next_tier_cap_canary_required_clean_sessions": limited_live_ladder_settings[
                "limited_live_next_tier_cap_canary_required_clean_sessions"
            ],
            "limited_live_next_tier_cap_canary_stale_after_days": limited_live_ladder_settings[
                "limited_live_next_tier_cap_canary_stale_after_days"
            ],
            "limited_live_higher_cap_report_enabled": limited_live_ladder_settings[
                "limited_live_higher_cap_report_enabled"
            ],
            "limited_live_higher_cap_report_auto_review_enabled": limited_live_ladder_settings[
                "limited_live_higher_cap_report_auto_review_enabled"
            ],
            "limited_live_higher_cap_required_clean_sessions": limited_live_ladder_settings[
                "limited_live_higher_cap_required_clean_sessions"
            ],
            "limited_live_higher_cap_stale_after_days": limited_live_ladder_settings[
                "limited_live_higher_cap_stale_after_days"
            ],
            "limited_live_higher_cap_target_max_notional": limited_live_ladder_settings[
                "limited_live_higher_cap_target_max_notional"
            ],
            "limited_live_operator_checklist_required": limited_live_ladder_settings[
                "limited_live_operator_checklist_required"
            ],
        },
        "runtime": {
            "last_cycle_at": _serialize_datetime(_parse_iso_datetime(runtime_state.get("last_cycle_at"))),
            "last_success_at": _serialize_datetime(_parse_iso_datetime(runtime_state.get("last_success_at"))),
            "last_error_at": _serialize_datetime(_parse_iso_datetime(runtime_state.get("last_error_at"))),
            "last_error": str(runtime_state.get("last_error") or "").strip() or None,
            "last_action": serialize_value(runtime_state.get("last_action")),
            "last_decision": serialize_value(runtime_state.get("last_decision")),
            "last_candidate": serialize_value(runtime_state.get("last_candidate")),
            "last_rejection": serialize_value(runtime_state.get("last_rejection")),
            "next_run_at": _serialize_datetime(_parse_iso_datetime(runtime_state.get("next_run_at"))),
            "cycle_count": _normalize_int(runtime_state.get("cycle_count"), 0, minimum=0, maximum=1_000_000),
            "success_count": _normalize_int(runtime_state.get("success_count"), 0, minimum=0, maximum=1_000_000),
            "error_count": _normalize_int(runtime_state.get("error_count"), 0, minimum=0, maximum=1_000_000),
            "error_streak": _normalize_int(runtime_state.get("error_streak"), 0, minimum=0, maximum=1_000_000),
            "rejection_count": _normalize_int(runtime_state.get("rejection_count"), 0, minimum=0, maximum=1_000_000),
            "ticker_cooldowns": ticker_cooldowns,
            "last_guardrail": serialize_value(runtime_state.get("last_guardrail")),
            "last_path_evaluations": serialize_value(runtime_state.get("last_path_evaluations") or []),
            "last_option_execution": serialize_value(runtime_state.get("last_option_execution")),
            "last_options_cycle_at": _serialize_datetime(_parse_iso_datetime(runtime_state.get("last_options_cycle_at"))),
            "last_options_scan_status": str(runtime_state.get("last_options_scan_status") or "").strip().lower() or None,
            "last_options_blocker": str(runtime_state.get("last_options_blocker") or "").strip() or None,
            "last_option_entry": serialize_value(runtime_state.get("last_option_entry")),
            "last_option_exit": serialize_value(runtime_state.get("last_option_exit")),
            "open_option_position_count": _normalize_int(runtime_state.get("open_option_position_count"), 0, minimum=0, maximum=1_000_000),
            "sell_ready_option_count": _normalize_int(runtime_state.get("sell_ready_option_count"), 0, minimum=0, maximum=1_000_000),
            "collection_phase_active": _normalize_bool(runtime_state.get("collection_phase_active"), False),
            "collection_phase_label": str(runtime_state.get("collection_phase_label") or "").strip() or None,
            "collection_phase_detail": str(runtime_state.get("collection_phase_detail") or "").strip() or None,
            "auto_validation_rerun_enabled": _normalize_bool(runtime_state.get("auto_validation_rerun_enabled"), True),
            "validation_rerun_in_progress": _normalize_bool(runtime_state.get("validation_rerun_in_progress"), False),
            "last_validation_rerun_at": _serialize_datetime(_parse_iso_datetime(runtime_state.get("last_validation_rerun_at"))),
            "last_validation_rerun_event_at": _serialize_datetime(_parse_iso_datetime(runtime_state.get("last_validation_rerun_event_at"))),
            "last_validation_rerun_cycle_id": str(runtime_state.get("last_validation_rerun_cycle_id") or "").strip() or None,
            "last_validation_rerun_status": str(runtime_state.get("last_validation_rerun_status") or "").strip().lower() or None,
            "last_validation_rerun_error": str(runtime_state.get("last_validation_rerun_error") or "").strip() or None,
            "current_route_fill_count": _normalize_int(runtime_state.get("current_route_fill_count"), 0, minimum=0, maximum=1_000_000),
            "current_route_closed_trade_count": _normalize_int(runtime_state.get("current_route_closed_trade_count"), 0, minimum=0, maximum=1_000_000),
            "current_route_mismatched_count": _normalize_int(runtime_state.get("current_route_mismatched_count"), 0, minimum=0, maximum=1_000_000),
            "current_route_sample_status": str(runtime_state.get("current_route_sample_status") or "").strip().lower() or None,
            "mark_to_market_coverage_status": str(runtime_state.get("mark_to_market_coverage_status") or "").strip().lower() or None,
            "ledger_snapshot_consistency": str(runtime_state.get("ledger_snapshot_consistency") or "").strip().lower() or None,
            "metrics_source": str(runtime_state.get("metrics_source") or "").strip().lower() or None,
            "route_window_start": _serialize_datetime(_parse_iso_datetime(runtime_state.get("route_window_start"))),
            "route_window_end": _serialize_datetime(_parse_iso_datetime(runtime_state.get("route_window_end"))),
            "route_window_snapshot_count": _normalize_int(runtime_state.get("route_window_snapshot_count"), 0, minimum=0, maximum=1_000_000),
            "current_route_latest_event_at": _serialize_datetime(_parse_iso_datetime(runtime_state.get("current_route_latest_event_at"))),
            "last_collection_blocker": str(runtime_state.get("last_collection_blocker") or "").strip().lower() or None,
            "current_route_reconciliation_status": str(runtime_state.get("current_route_reconciliation_status") or "").strip().lower() or None,
            "current_route_orphan_order_event_count": _normalize_int(runtime_state.get("current_route_orphan_order_event_count"), 0, minimum=0, maximum=1_000_000),
            "legacy_orphan_order_event_count": _normalize_int(runtime_state.get("legacy_orphan_order_event_count"), 0, minimum=0, maximum=1_000_000),
            "last_submitted_current_route_order_at": _serialize_datetime(_parse_iso_datetime(runtime_state.get("last_submitted_current_route_order_at"))),
            "last_current_route_fill_at": _serialize_datetime(_parse_iso_datetime(runtime_state.get("last_current_route_fill_at"))),
            "last_current_route_close_at": _serialize_datetime(_parse_iso_datetime(runtime_state.get("last_current_route_close_at"))),
            "last_collection_audit": serialize_value(runtime_state.get("last_collection_audit")),
            "collection_audit_history": collection_audit_history,
            "ai_daily_journal": ai_review_runtime["ai_daily_journal"],
            "ai_last_note_id": ai_review_runtime["ai_last_note_id"],
            "ai_last_observation_at": ai_review_runtime["ai_last_observation_at"],
            "ai_last_review_session_day": ai_review_runtime["ai_last_review_session_day"],
            "ai_last_review_at": ai_review_runtime["ai_last_review_at"],
            "ai_last_review": ai_review_runtime["ai_last_review"],
            "ai_last_adjustment": ai_review_runtime["ai_last_adjustment"],
            "ai_review_history": ai_review_runtime["ai_review_history"],
            "accuracy_calibration_last_report": accuracy_calibration_runtime["accuracy_calibration_last_report"],
            "accuracy_calibration_last_note_id": accuracy_calibration_runtime["accuracy_calibration_last_note_id"],
            "accuracy_calibration_note_session_day": accuracy_calibration_runtime[
                "accuracy_calibration_note_session_day"
            ],
            "accuracy_calibration_last_run_at": accuracy_calibration_runtime["accuracy_calibration_last_run_at"],
            "accuracy_calibration_last_error": accuracy_calibration_runtime["accuracy_calibration_last_error"],
            "accuracy_calibration_history": accuracy_calibration_runtime["accuracy_calibration_history"],
            "accuracy_candidate_history": accuracy_calibration_runtime["accuracy_candidate_history"],
            "daily_objective_last_report": daily_objective_runtime["daily_objective_last_report"],
            "daily_objective_last_note_id": daily_objective_runtime["daily_objective_last_note_id"],
            "daily_objective_note_session_day": daily_objective_runtime["daily_objective_note_session_day"],
            "daily_objective_last_run_at": daily_objective_runtime["daily_objective_last_run_at"],
            "daily_objective_last_error": daily_objective_runtime["daily_objective_last_error"],
            "daily_objective_history": daily_objective_runtime["daily_objective_history"],
            "loss_containment_last_report": loss_containment_runtime["loss_containment_last_report"],
            "loss_containment_last_note_id": loss_containment_runtime["loss_containment_last_note_id"],
            "loss_containment_note_session_day": loss_containment_runtime["loss_containment_note_session_day"],
            "loss_containment_last_run_at": loss_containment_runtime["loss_containment_last_run_at"],
            "loss_containment_last_error": loss_containment_runtime["loss_containment_last_error"],
            "loss_containment_history": loss_containment_runtime["loss_containment_history"],
            "exit_watchdog_last_report": exit_watchdog_runtime["exit_watchdog_last_report"],
            "exit_watchdog_last_note_id": exit_watchdog_runtime["exit_watchdog_last_note_id"],
            "exit_watchdog_note_session_day": exit_watchdog_runtime["exit_watchdog_note_session_day"],
            "exit_watchdog_last_run_at": exit_watchdog_runtime["exit_watchdog_last_run_at"],
            "exit_watchdog_last_error": exit_watchdog_runtime["exit_watchdog_last_error"],
            "exit_watchdog_history": exit_watchdog_runtime["exit_watchdog_history"],
            "state_control_state": state_control_runtime["state_control_state"],
            "state_control_score": state_control_runtime["state_control_score"],
            "state_control_active_overrides": state_control_runtime["state_control_active_overrides"],
            "state_control_effective_overrides": state_control_runtime["state_control_effective_overrides"],
            "state_control_last_evaluation": state_control_runtime["state_control_last_evaluation"],
            "state_control_last_transition": state_control_runtime["state_control_last_transition"],
            "state_control_transition_history": state_control_runtime["state_control_transition_history"],
            "state_control_last_note_id": state_control_runtime["state_control_last_note_id"],
            "state_control_note_session_day": state_control_runtime["state_control_note_session_day"],
            "state_control_last_review_at": state_control_runtime["state_control_last_review_at"],
            "state_control_last_error": state_control_runtime["state_control_last_error"],
            "state_control_clean_cycle_count": state_control_runtime["state_control_clean_cycle_count"],
            "state_control_halt_active": state_control_runtime["state_control_halt_active"],
            "state_control_shadow_last_report": state_control_shadow_runtime["state_control_shadow_last_report"],
            "state_control_shadow_last_note_id": state_control_shadow_runtime["state_control_shadow_last_note_id"],
            "state_control_shadow_note_session_day": state_control_shadow_runtime["state_control_shadow_note_session_day"],
            "state_control_shadow_last_run_at": state_control_shadow_runtime["state_control_shadow_last_run_at"],
            "state_control_shadow_last_error": state_control_shadow_runtime["state_control_shadow_last_error"],
            "state_control_shadow_report_history": state_control_shadow_runtime["state_control_shadow_report_history"],
            "paper_broker_reconciliation_last_report": paper_broker_reconciliation_runtime[
                "paper_broker_reconciliation_last_report"
            ],
            "paper_broker_reconciliation_last_note_id": paper_broker_reconciliation_runtime[
                "paper_broker_reconciliation_last_note_id"
            ],
            "paper_broker_reconciliation_note_session_day": paper_broker_reconciliation_runtime[
                "paper_broker_reconciliation_note_session_day"
            ],
            "paper_broker_reconciliation_last_run_at": paper_broker_reconciliation_runtime[
                "paper_broker_reconciliation_last_run_at"
            ],
            "paper_broker_reconciliation_last_scheduled_run_at": paper_broker_reconciliation_runtime[
                "paper_broker_reconciliation_last_scheduled_run_at"
            ],
            "paper_broker_reconciliation_last_scheduled_session_day": paper_broker_reconciliation_runtime[
                "paper_broker_reconciliation_last_scheduled_session_day"
            ],
            "paper_broker_reconciliation_last_error": paper_broker_reconciliation_runtime[
                "paper_broker_reconciliation_last_error"
            ],
            "paper_broker_reconciliation_history": paper_broker_reconciliation_runtime[
                "paper_broker_reconciliation_history"
            ],
            "paper_order_lifecycle_soak_last_report": paper_order_lifecycle_soak_runtime[
                "paper_order_lifecycle_soak_last_report"
            ],
            "paper_order_lifecycle_soak_last_note_id": paper_order_lifecycle_soak_runtime[
                "paper_order_lifecycle_soak_last_note_id"
            ],
            "paper_order_lifecycle_soak_note_session_day": paper_order_lifecycle_soak_runtime[
                "paper_order_lifecycle_soak_note_session_day"
            ],
            "paper_order_lifecycle_soak_last_run_at": paper_order_lifecycle_soak_runtime[
                "paper_order_lifecycle_soak_last_run_at"
            ],
            "paper_order_lifecycle_soak_last_error": paper_order_lifecycle_soak_runtime[
                "paper_order_lifecycle_soak_last_error"
            ],
            "paper_order_lifecycle_soak_history": paper_order_lifecycle_soak_runtime[
                "paper_order_lifecycle_soak_history"
            ],
            "paper_order_lifecycle_canary_last_report": paper_order_lifecycle_canary_runtime[
                "paper_order_lifecycle_canary_last_report"
            ],
            "paper_order_lifecycle_canary_last_note_id": paper_order_lifecycle_canary_runtime[
                "paper_order_lifecycle_canary_last_note_id"
            ],
            "paper_order_lifecycle_canary_note_session_day": paper_order_lifecycle_canary_runtime[
                "paper_order_lifecycle_canary_note_session_day"
            ],
            "paper_order_lifecycle_canary_last_run_at": paper_order_lifecycle_canary_runtime[
                "paper_order_lifecycle_canary_last_run_at"
            ],
            "paper_order_lifecycle_canary_last_scheduled_run_at": paper_order_lifecycle_canary_runtime[
                "paper_order_lifecycle_canary_last_scheduled_run_at"
            ],
            "paper_order_lifecycle_canary_last_scheduled_session_day": paper_order_lifecycle_canary_runtime[
                "paper_order_lifecycle_canary_last_scheduled_session_day"
            ],
            "paper_order_lifecycle_canary_next_eligible_run_at": paper_order_lifecycle_canary_runtime[
                "paper_order_lifecycle_canary_next_eligible_run_at"
            ],
            "paper_order_lifecycle_canary_last_auto_submit_at": paper_order_lifecycle_canary_runtime[
                "paper_order_lifecycle_canary_last_auto_submit_at"
            ],
            "paper_order_lifecycle_canary_last_auto_submit_session_day": paper_order_lifecycle_canary_runtime[
                "paper_order_lifecycle_canary_last_auto_submit_session_day"
            ],
            "paper_order_lifecycle_canary_last_skipped_reason": paper_order_lifecycle_canary_runtime[
                "paper_order_lifecycle_canary_last_skipped_reason"
            ],
            "paper_order_lifecycle_canary_last_error": paper_order_lifecycle_canary_runtime[
                "paper_order_lifecycle_canary_last_error"
            ],
            "paper_order_lifecycle_canary_history": paper_order_lifecycle_canary_runtime[
                "paper_order_lifecycle_canary_history"
            ],
            "paper_canary_last_report": paper_canary_runtime["paper_canary_last_report"],
            "paper_canary_last_note_id": paper_canary_runtime["paper_canary_last_note_id"],
            "paper_canary_note_session_day": paper_canary_runtime["paper_canary_note_session_day"],
            "paper_canary_last_run_at": paper_canary_runtime["paper_canary_last_run_at"],
            "paper_canary_last_scheduled_run_at": paper_canary_runtime["paper_canary_last_scheduled_run_at"],
            "paper_canary_last_scheduled_session_day": paper_canary_runtime["paper_canary_last_scheduled_session_day"],
            "paper_canary_next_eligible_run_at": paper_canary_runtime["paper_canary_next_eligible_run_at"],
            "paper_canary_last_skipped_reason": paper_canary_runtime["paper_canary_last_skipped_reason"],
            "paper_canary_last_error": paper_canary_runtime["paper_canary_last_error"],
            "paper_canary_history": paper_canary_runtime["paper_canary_history"],
            "live_pilot_readiness_last_report": live_pilot_readiness_runtime[
                "live_pilot_readiness_last_report"
            ],
            "live_pilot_readiness_last_note_id": live_pilot_readiness_runtime[
                "live_pilot_readiness_last_note_id"
            ],
            "live_pilot_readiness_note_session_day": live_pilot_readiness_runtime[
                "live_pilot_readiness_note_session_day"
            ],
            "live_pilot_readiness_last_run_at": live_pilot_readiness_runtime[
                "live_pilot_readiness_last_run_at"
            ],
            "live_pilot_readiness_last_error": live_pilot_readiness_runtime[
                "live_pilot_readiness_last_error"
            ],
            "live_pilot_readiness_history": live_pilot_readiness_runtime[
                "live_pilot_readiness_history"
            ],
            "live_pilot_soak_last_report": live_pilot_soak_runtime["live_pilot_soak_last_report"],
            "live_pilot_soak_last_note_id": live_pilot_soak_runtime["live_pilot_soak_last_note_id"],
            "live_pilot_soak_note_session_day": live_pilot_soak_runtime[
                "live_pilot_soak_note_session_day"
            ],
            "live_pilot_soak_last_run_at": live_pilot_soak_runtime["live_pilot_soak_last_run_at"],
            "live_pilot_soak_last_error": live_pilot_soak_runtime["live_pilot_soak_last_error"],
            "live_pilot_soak_approval": live_pilot_soak_runtime["live_pilot_soak_approval"],
            "live_pilot_soak_history": live_pilot_soak_runtime["live_pilot_soak_history"],
            "live_pilot_canary_last_report": live_pilot_canary_runtime["live_pilot_canary_last_report"],
            "live_pilot_canary_last_note_id": live_pilot_canary_runtime["live_pilot_canary_last_note_id"],
            "live_pilot_canary_note_session_day": live_pilot_canary_runtime[
                "live_pilot_canary_note_session_day"
            ],
            "live_pilot_canary_last_run_at": live_pilot_canary_runtime["live_pilot_canary_last_run_at"],
            "live_pilot_canary_last_scheduled_run_at": live_pilot_canary_runtime[
                "live_pilot_canary_last_scheduled_run_at"
            ],
            "live_pilot_canary_last_scheduled_session_day": live_pilot_canary_runtime[
                "live_pilot_canary_last_scheduled_session_day"
            ],
            "live_pilot_canary_next_eligible_run_at": live_pilot_canary_runtime[
                "live_pilot_canary_next_eligible_run_at"
            ],
            "live_pilot_canary_last_skipped_reason": live_pilot_canary_runtime[
                "live_pilot_canary_last_skipped_reason"
            ],
            "live_pilot_canary_last_error": live_pilot_canary_runtime["live_pilot_canary_last_error"],
            "live_pilot_canary_history": live_pilot_canary_runtime["live_pilot_canary_history"],
            "live_pilot_expansion_last_report": live_pilot_expansion_runtime[
                "live_pilot_expansion_last_report"
            ],
            "live_pilot_expansion_last_note_id": live_pilot_expansion_runtime[
                "live_pilot_expansion_last_note_id"
            ],
            "live_pilot_expansion_note_session_day": live_pilot_expansion_runtime[
                "live_pilot_expansion_note_session_day"
            ],
            "live_pilot_expansion_last_run_at": live_pilot_expansion_runtime[
                "live_pilot_expansion_last_run_at"
            ],
            "live_pilot_expansion_last_error": live_pilot_expansion_runtime[
                "live_pilot_expansion_last_error"
            ],
            "live_pilot_expansion_approval": live_pilot_expansion_runtime[
                "live_pilot_expansion_approval"
            ],
            "live_pilot_expansion_history": live_pilot_expansion_runtime[
                "live_pilot_expansion_history"
            ],
            "live_pilot_expansion_canary_last_report": live_pilot_expansion_canary_runtime[
                "live_pilot_expansion_canary_last_report"
            ],
            "live_pilot_expansion_canary_last_note_id": live_pilot_expansion_canary_runtime[
                "live_pilot_expansion_canary_last_note_id"
            ],
            "live_pilot_expansion_canary_note_session_day": live_pilot_expansion_canary_runtime[
                "live_pilot_expansion_canary_note_session_day"
            ],
            "live_pilot_expansion_canary_last_run_at": live_pilot_expansion_canary_runtime[
                "live_pilot_expansion_canary_last_run_at"
            ],
            "live_pilot_expansion_canary_last_scheduled_run_at": live_pilot_expansion_canary_runtime[
                "live_pilot_expansion_canary_last_scheduled_run_at"
            ],
            "live_pilot_expansion_canary_last_scheduled_session_day": live_pilot_expansion_canary_runtime[
                "live_pilot_expansion_canary_last_scheduled_session_day"
            ],
            "live_pilot_expansion_canary_next_eligible_run_at": live_pilot_expansion_canary_runtime[
                "live_pilot_expansion_canary_next_eligible_run_at"
            ],
            "live_pilot_expansion_canary_last_skipped_reason": live_pilot_expansion_canary_runtime[
                "live_pilot_expansion_canary_last_skipped_reason"
            ],
            "live_pilot_expansion_canary_last_error": live_pilot_expansion_canary_runtime[
                "live_pilot_expansion_canary_last_error"
            ],
            "live_pilot_expansion_canary_history": live_pilot_expansion_canary_runtime[
                "live_pilot_expansion_canary_history"
            ],
            "live_pilot_window_last_report": live_pilot_window_runtime[
                "live_pilot_window_last_report"
            ],
            "live_pilot_window_last_note_id": live_pilot_window_runtime[
                "live_pilot_window_last_note_id"
            ],
            "live_pilot_window_note_session_day": live_pilot_window_runtime[
                "live_pilot_window_note_session_day"
            ],
            "live_pilot_window_last_run_at": live_pilot_window_runtime[
                "live_pilot_window_last_run_at"
            ],
            "live_pilot_window_last_error": live_pilot_window_runtime[
                "live_pilot_window_last_error"
            ],
            "live_pilot_window_approval": live_pilot_window_runtime[
                "live_pilot_window_approval"
            ],
            "live_pilot_window_history": live_pilot_window_runtime[
                "live_pilot_window_history"
            ],
            "live_pilot_window_canary_last_report": live_pilot_window_canary_runtime[
                "live_pilot_window_canary_last_report"
            ],
            "live_pilot_window_canary_last_note_id": live_pilot_window_canary_runtime[
                "live_pilot_window_canary_last_note_id"
            ],
            "live_pilot_window_canary_note_session_day": live_pilot_window_canary_runtime[
                "live_pilot_window_canary_note_session_day"
            ],
            "live_pilot_window_canary_last_run_at": live_pilot_window_canary_runtime[
                "live_pilot_window_canary_last_run_at"
            ],
            "live_pilot_window_canary_last_scheduled_run_at": live_pilot_window_canary_runtime[
                "live_pilot_window_canary_last_scheduled_run_at"
            ],
            "live_pilot_window_canary_last_scheduled_session_day": live_pilot_window_canary_runtime[
                "live_pilot_window_canary_last_scheduled_session_day"
            ],
            "live_pilot_window_canary_next_eligible_run_at": live_pilot_window_canary_runtime[
                "live_pilot_window_canary_next_eligible_run_at"
            ],
            "live_pilot_window_canary_last_skipped_reason": live_pilot_window_canary_runtime[
                "live_pilot_window_canary_last_skipped_reason"
            ],
            "live_pilot_window_canary_last_error": live_pilot_window_canary_runtime[
                "live_pilot_window_canary_last_error"
            ],
            "live_pilot_window_canary_history": live_pilot_window_canary_runtime[
                "live_pilot_window_canary_history"
            ],
            "live_pilot_promotion_report_last_report": live_pilot_promotion_runtime[
                "live_pilot_promotion_report_last_report"
            ],
            "live_pilot_promotion_report_last_note_id": live_pilot_promotion_runtime[
                "live_pilot_promotion_report_last_note_id"
            ],
            "live_pilot_promotion_report_note_session_day": live_pilot_promotion_runtime[
                "live_pilot_promotion_report_note_session_day"
            ],
            "live_pilot_promotion_report_last_run_at": live_pilot_promotion_runtime[
                "live_pilot_promotion_report_last_run_at"
            ],
            "live_pilot_promotion_report_last_scheduled_run_at": live_pilot_promotion_runtime[
                "live_pilot_promotion_report_last_scheduled_run_at"
            ],
            "live_pilot_promotion_report_last_scheduled_session_day": live_pilot_promotion_runtime[
                "live_pilot_promotion_report_last_scheduled_session_day"
            ],
            "live_pilot_promotion_report_next_eligible_run_at": live_pilot_promotion_runtime[
                "live_pilot_promotion_report_next_eligible_run_at"
            ],
            "live_pilot_promotion_report_last_skipped_reason": live_pilot_promotion_runtime[
                "live_pilot_promotion_report_last_skipped_reason"
            ],
            "live_pilot_promotion_report_last_error": live_pilot_promotion_runtime[
                "live_pilot_promotion_report_last_error"
            ],
            "live_pilot_promotion_report_history": live_pilot_promotion_runtime[
                "live_pilot_promotion_report_history"
            ],
            "limited_live_rollout_gate_last_report": limited_live_rollout_runtime[
                "limited_live_rollout_gate_last_report"
            ],
            "limited_live_rollout_gate_last_note_id": limited_live_rollout_runtime[
                "limited_live_rollout_gate_last_note_id"
            ],
            "limited_live_rollout_gate_note_session_day": limited_live_rollout_runtime[
                "limited_live_rollout_gate_note_session_day"
            ],
            "limited_live_rollout_gate_last_run_at": limited_live_rollout_runtime[
                "limited_live_rollout_gate_last_run_at"
            ],
            "limited_live_rollout_gate_approval": limited_live_rollout_runtime[
                "limited_live_rollout_gate_approval"
            ],
            "limited_live_rollout_gate_allowance": limited_live_rollout_runtime[
                "limited_live_rollout_gate_allowance"
            ],
            "limited_live_rollout_gate_history": limited_live_rollout_runtime[
                "limited_live_rollout_gate_history"
            ],
            "limited_live_rollout_gate_last_error": limited_live_rollout_runtime[
                "limited_live_rollout_gate_last_error"
            ],
            "limited_live_rollout_canary_last_report": limited_live_rollout_canary_runtime[
                "limited_live_rollout_canary_last_report"
            ],
            "limited_live_rollout_canary_last_note_id": limited_live_rollout_canary_runtime[
                "limited_live_rollout_canary_last_note_id"
            ],
            "limited_live_rollout_canary_note_session_day": limited_live_rollout_canary_runtime[
                "limited_live_rollout_canary_note_session_day"
            ],
            "limited_live_rollout_canary_last_run_at": limited_live_rollout_canary_runtime[
                "limited_live_rollout_canary_last_run_at"
            ],
            "limited_live_rollout_canary_last_scheduled_run_at": limited_live_rollout_canary_runtime[
                "limited_live_rollout_canary_last_scheduled_run_at"
            ],
            "limited_live_rollout_canary_last_scheduled_session_day": limited_live_rollout_canary_runtime[
                "limited_live_rollout_canary_last_scheduled_session_day"
            ],
            "limited_live_rollout_canary_next_eligible_run_at": limited_live_rollout_canary_runtime[
                "limited_live_rollout_canary_next_eligible_run_at"
            ],
            "limited_live_rollout_canary_last_skipped_reason": limited_live_rollout_canary_runtime[
                "limited_live_rollout_canary_last_skipped_reason"
            ],
            "limited_live_rollout_canary_last_error": limited_live_rollout_canary_runtime[
                "limited_live_rollout_canary_last_error"
            ],
            "limited_live_rollout_canary_history": limited_live_rollout_canary_runtime[
                "limited_live_rollout_canary_history"
            ],
            "limited_live_cap_expansion_report_last_report": limited_live_cap_expansion_runtime[
                "limited_live_cap_expansion_report_last_report"
            ],
            "limited_live_cap_expansion_report_last_note_id": limited_live_cap_expansion_runtime[
                "limited_live_cap_expansion_report_last_note_id"
            ],
            "limited_live_cap_expansion_report_note_session_day": limited_live_cap_expansion_runtime[
                "limited_live_cap_expansion_report_note_session_day"
            ],
            "limited_live_cap_expansion_report_last_run_at": limited_live_cap_expansion_runtime[
                "limited_live_cap_expansion_report_last_run_at"
            ],
            "limited_live_cap_expansion_report_last_scheduled_run_at": limited_live_cap_expansion_runtime[
                "limited_live_cap_expansion_report_last_scheduled_run_at"
            ],
            "limited_live_cap_expansion_report_last_scheduled_session_day": limited_live_cap_expansion_runtime[
                "limited_live_cap_expansion_report_last_scheduled_session_day"
            ],
            "limited_live_cap_expansion_report_next_eligible_run_at": limited_live_cap_expansion_runtime[
                "limited_live_cap_expansion_report_next_eligible_run_at"
            ],
            "limited_live_cap_expansion_report_last_skipped_reason": limited_live_cap_expansion_runtime[
                "limited_live_cap_expansion_report_last_skipped_reason"
            ],
            "limited_live_cap_expansion_report_last_error": limited_live_cap_expansion_runtime[
                "limited_live_cap_expansion_report_last_error"
            ],
            "limited_live_cap_expansion_report_history": limited_live_cap_expansion_runtime[
                "limited_live_cap_expansion_report_history"
            ],
            "limited_live_cap_expansion_gate_last_report": limited_live_cap_expansion_gate_runtime[
                "limited_live_cap_expansion_gate_last_report"
            ],
            "limited_live_cap_expansion_gate_last_note_id": limited_live_cap_expansion_gate_runtime[
                "limited_live_cap_expansion_gate_last_note_id"
            ],
            "limited_live_cap_expansion_gate_note_session_day": limited_live_cap_expansion_gate_runtime[
                "limited_live_cap_expansion_gate_note_session_day"
            ],
            "limited_live_cap_expansion_gate_last_run_at": limited_live_cap_expansion_gate_runtime[
                "limited_live_cap_expansion_gate_last_run_at"
            ],
            "limited_live_cap_expansion_gate_approval": limited_live_cap_expansion_gate_runtime[
                "limited_live_cap_expansion_gate_approval"
            ],
            "limited_live_cap_expansion_gate_allowance": limited_live_cap_expansion_gate_runtime[
                "limited_live_cap_expansion_gate_allowance"
            ],
            "limited_live_cap_expansion_gate_history": limited_live_cap_expansion_gate_runtime[
                "limited_live_cap_expansion_gate_history"
            ],
            "limited_live_cap_expansion_gate_last_error": limited_live_cap_expansion_gate_runtime[
                "limited_live_cap_expansion_gate_last_error"
            ],
            "limited_live_cap_expansion_canary_last_report": limited_live_cap_expansion_canary_runtime[
                "limited_live_cap_expansion_canary_last_report"
            ],
            "limited_live_cap_expansion_canary_last_note_id": limited_live_cap_expansion_canary_runtime[
                "limited_live_cap_expansion_canary_last_note_id"
            ],
            "limited_live_cap_expansion_canary_note_session_day": limited_live_cap_expansion_canary_runtime[
                "limited_live_cap_expansion_canary_note_session_day"
            ],
            "limited_live_cap_expansion_canary_last_run_at": limited_live_cap_expansion_canary_runtime[
                "limited_live_cap_expansion_canary_last_run_at"
            ],
            "limited_live_cap_expansion_canary_last_scheduled_run_at": limited_live_cap_expansion_canary_runtime[
                "limited_live_cap_expansion_canary_last_scheduled_run_at"
            ],
            "limited_live_cap_expansion_canary_last_scheduled_session_day": limited_live_cap_expansion_canary_runtime[
                "limited_live_cap_expansion_canary_last_scheduled_session_day"
            ],
            "limited_live_cap_expansion_canary_next_eligible_run_at": limited_live_cap_expansion_canary_runtime[
                "limited_live_cap_expansion_canary_next_eligible_run_at"
            ],
            "limited_live_cap_expansion_canary_last_skipped_reason": limited_live_cap_expansion_canary_runtime[
                "limited_live_cap_expansion_canary_last_skipped_reason"
            ],
            "limited_live_cap_expansion_canary_last_error": limited_live_cap_expansion_canary_runtime[
                "limited_live_cap_expansion_canary_last_error"
            ],
            "limited_live_cap_expansion_canary_history": limited_live_cap_expansion_canary_runtime[
                "limited_live_cap_expansion_canary_history"
            ],
            "limited_live_next_tier_cap_report_last_report": limited_live_next_tier_cap_runtime[
                "limited_live_next_tier_cap_report_last_report"
            ],
            "limited_live_next_tier_cap_report_last_note_id": limited_live_next_tier_cap_runtime[
                "limited_live_next_tier_cap_report_last_note_id"
            ],
            "limited_live_next_tier_cap_report_note_session_day": limited_live_next_tier_cap_runtime[
                "limited_live_next_tier_cap_report_note_session_day"
            ],
            "limited_live_next_tier_cap_report_last_run_at": limited_live_next_tier_cap_runtime[
                "limited_live_next_tier_cap_report_last_run_at"
            ],
            "limited_live_next_tier_cap_report_last_scheduled_run_at": limited_live_next_tier_cap_runtime[
                "limited_live_next_tier_cap_report_last_scheduled_run_at"
            ],
            "limited_live_next_tier_cap_report_last_scheduled_session_day": limited_live_next_tier_cap_runtime[
                "limited_live_next_tier_cap_report_last_scheduled_session_day"
            ],
            "limited_live_next_tier_cap_report_next_eligible_run_at": limited_live_next_tier_cap_runtime[
                "limited_live_next_tier_cap_report_next_eligible_run_at"
            ],
            "limited_live_next_tier_cap_report_last_skipped_reason": limited_live_next_tier_cap_runtime[
                "limited_live_next_tier_cap_report_last_skipped_reason"
            ],
            "limited_live_next_tier_cap_report_last_error": limited_live_next_tier_cap_runtime[
                "limited_live_next_tier_cap_report_last_error"
            ],
            "limited_live_next_tier_cap_report_history": limited_live_next_tier_cap_runtime[
                "limited_live_next_tier_cap_report_history"
            ],
            "limited_live_next_tier_cap_gate_last_report": limited_live_next_tier_cap_gate_runtime[
                "limited_live_next_tier_cap_gate_last_report"
            ],
            "limited_live_next_tier_cap_gate_last_note_id": limited_live_next_tier_cap_gate_runtime[
                "limited_live_next_tier_cap_gate_last_note_id"
            ],
            "limited_live_next_tier_cap_gate_note_session_day": limited_live_next_tier_cap_gate_runtime[
                "limited_live_next_tier_cap_gate_note_session_day"
            ],
            "limited_live_next_tier_cap_gate_last_run_at": limited_live_next_tier_cap_gate_runtime[
                "limited_live_next_tier_cap_gate_last_run_at"
            ],
            "limited_live_next_tier_cap_gate_approval": limited_live_next_tier_cap_gate_runtime[
                "limited_live_next_tier_cap_gate_approval"
            ],
            "limited_live_next_tier_cap_gate_allowance": limited_live_next_tier_cap_gate_runtime[
                "limited_live_next_tier_cap_gate_allowance"
            ],
            "limited_live_next_tier_cap_gate_history": limited_live_next_tier_cap_gate_runtime[
                "limited_live_next_tier_cap_gate_history"
            ],
            "limited_live_next_tier_cap_gate_last_error": limited_live_next_tier_cap_gate_runtime[
                "limited_live_next_tier_cap_gate_last_error"
            ],
            **limited_live_ladder_runtime,
        },
        "history": normalized_history,
    }


def _default_trade_automation_template_settings() -> dict[str, Any]:
    return dict(_normalize_trade_automation_profile_state({}).get("settings") or {})


def _normalize_trade_automation_template_settings(stored: dict[str, Any] | None) -> dict[str, Any]:
    return dict(
        _normalize_trade_automation_profile_state(
            {
                "settings": stored or {},
                "runtime": {},
                "history": [],
            }
        ).get("settings")
        or {}
    )


def _build_trade_automation_profile_key(*, scope: str | None = None, linked_account_id: str | None = None) -> str:
    normalized_scope = str(scope or _AUTOMATION_PERSONAL_PAPER_PROFILE).strip().lower()
    normalized_linked_account_id = str(linked_account_id or "").strip()
    if normalized_scope in {_AUTOMATION_PERSONAL_PAPER_PROFILE, _AUTOMATION_PERSONAL_LIVE_PROFILE}:
        return normalized_scope
    if normalized_scope == "linked":
        if not normalized_linked_account_id:
            raise ValidationError("A linked account is required for linked automation scope.")
        return f"linked:{normalized_linked_account_id}"
    if normalized_scope.startswith("linked:"):
        linked_key = normalized_scope.split(":", 1)[1].strip()
        if not linked_key:
            raise ValidationError("Linked automation scope key is invalid.")
        return f"linked:{linked_key}"
    raise ValidationError("Automation scope is not supported.")


def _split_trade_automation_profile_key(profile_key: str) -> tuple[str, str | None]:
    normalized = str(profile_key or "").strip().lower()
    if normalized in {_AUTOMATION_PERSONAL_PAPER_PROFILE, _AUTOMATION_PERSONAL_LIVE_PROFILE}:
        return normalized, None
    if normalized.startswith("linked:"):
        linked_account_id = normalized.split(":", 1)[1].strip()
        return "linked", linked_account_id or None
    return _AUTOMATION_PERSONAL_PAPER_PROFILE, None


def _read_trade_automation_store(tenant: Tenant) -> dict[str, Any]:
    try:
        tenant_state = sa_inspect(tenant)
    except Exception:
        tenant_state = None
    if tenant_state is not None and tenant_state.persistent and tenant_state.session is not None:
        metadata_attr = tenant_state.attrs.metadata_json
        if not metadata_attr.history.has_changes():
            tenant_state.session.refresh(tenant, attribute_names=["metadata_json"])
    metadata = dict(tenant.metadata_json or {})
    raw_template = metadata.get(_TRADE_AUTOMATION_TEMPLATE_KEY) or {}
    raw_profiles = metadata.get(_TRADE_AUTOMATION_PROFILES_KEY) or {}
    legacy_state = metadata.get(_TRADE_AUTOMATION_KEY) or {}

    legacy_profile = _normalize_trade_automation_profile_state(legacy_state if isinstance(legacy_state, dict) else {})
    template_settings = _normalize_trade_automation_template_settings(raw_template or legacy_profile.get("settings"))

    normalized_profiles: dict[str, dict[str, Any]] = {}
    if isinstance(raw_profiles, dict):
        for key, value in raw_profiles.items():
            normalized_key = str(key or "").strip().lower()
            if not normalized_key:
                continue
            normalized_profiles[normalized_key] = _normalize_trade_automation_profile_state(value if isinstance(value, dict) else {})

    if _AUTOMATION_PERSONAL_PAPER_PROFILE not in normalized_profiles:
        normalized_profiles[_AUTOMATION_PERSONAL_PAPER_PROFILE] = legacy_profile

    return {
        "template": template_settings,
        "profiles": normalized_profiles,
    }


def _write_trade_automation_store(tenant: Tenant, store: dict[str, Any]) -> None:
    metadata = dict(tenant.metadata_json or {})
    template_settings = _normalize_trade_automation_template_settings(dict(store.get("template") or {}))
    raw_profiles = dict(store.get("profiles") or {})
    serialized_profiles: dict[str, dict[str, Any]] = {}
    for key, value in raw_profiles.items():
        normalized_key = str(key or "").strip().lower()
        if not normalized_key:
            continue
        normalized_state = _normalize_trade_automation_profile_state(value if isinstance(value, dict) else {})
        serialized_profiles[normalized_key] = {
            "settings": dict(normalized_state.get("settings") or {}),
            "runtime": dict(normalized_state.get("runtime") or {}),
            "history": list(normalized_state.get("history") or [])[-_TRADE_AUTOMATION_HISTORY_LIMIT:],
        }
    metadata[_TRADE_AUTOMATION_TEMPLATE_KEY] = template_settings
    metadata[_TRADE_AUTOMATION_PROFILES_KEY] = serialized_profiles
    metadata.pop(_TRADE_AUTOMATION_KEY, None)
    tenant.metadata_json = metadata
    flag_modified(tenant, "metadata_json")


def _seed_trade_automation_profile_state(
    *,
    template_settings: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
) -> dict[str, Any]:
    seeded_settings = dict(template_settings or {})
    normalized_profile_key = str(profile_key or _AUTOMATION_PERSONAL_PAPER_PROFILE).strip().lower()
    if normalized_profile_key == _AUTOMATION_PERSONAL_LIVE_PROFILE:
        seeded_settings["execution_intent"] = "broker_live"
        seeded_settings["enabled"] = False
        seeded_settings["armed"] = False
        seeded_settings["kill_switch"] = False
    elif normalized_profile_key.startswith("linked:"):
        seeded_settings["execution_intent"] = "broker_paper"
        seeded_settings["enabled"] = False
        seeded_settings["armed"] = False
        seeded_settings["kill_switch"] = False
        if linked_account is not None:
            linked_profile = get_linked_account_automation_profile(linked_account)
            seeded_settings["risk_percent"] = float(
                linked_profile.get("risk_percent") or seeded_settings.get("risk_percent") or 0.5
            )
            seeded_settings["max_notional_per_trade"] = float(
                linked_profile.get("max_notional_per_trade")
                or seeded_settings.get("max_notional_per_trade")
                or 100.0
            )
            seeded_settings["max_open_positions"] = int(
                linked_profile.get("max_open_positions") or seeded_settings.get("max_open_positions") or 1
            )
            seeded_settings["fractional_shares_only"] = bool(
                linked_profile.get("fractional_shares_only") if linked_profile.get("fractional_shares_only") is not None else seeded_settings.get("fractional_shares_only")
            )
    return _normalize_trade_automation_profile_state(
        {
            "settings": seeded_settings,
            "runtime": {},
            "history": [],
        }
    )


def _ensure_trade_automation_profile_state(
    tenant: Tenant,
    *,
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    store = _read_trade_automation_store(tenant)
    normalized_key = str(profile_key or _AUTOMATION_PERSONAL_PAPER_PROFILE).strip().lower()
    state = dict(store["profiles"].get(normalized_key) or {})
    if not state:
        state = _seed_trade_automation_profile_state(
            template_settings=dict(store.get("template") or {}),
            profile_key=normalized_key,
            linked_account=linked_account,
        )
        store["profiles"][normalized_key] = state
    else:
        state = _normalize_trade_automation_profile_state(state)
        store["profiles"][normalized_key] = state
    return store, state


def _read_trade_automation_state(
    tenant: Tenant,
    *,
    profile_key: str = _AUTOMATION_PERSONAL_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
) -> dict[str, Any]:
    _, state = _ensure_trade_automation_profile_state(
        tenant,
        profile_key=profile_key,
        linked_account=linked_account,
    )
    return state


def _write_trade_automation_state(
    tenant: Tenant,
    state: dict[str, Any],
    *,
    profile_key: str = _AUTOMATION_PERSONAL_PAPER_PROFILE,
    template_settings: dict[str, Any] | None = None,
) -> None:
    store = _read_trade_automation_store(tenant)
    normalized_key = str(profile_key or _AUTOMATION_PERSONAL_PAPER_PROFILE).strip().lower()
    store["profiles"][normalized_key] = _normalize_trade_automation_profile_state(state)
    if template_settings is not None:
        store["template"] = _normalize_trade_automation_template_settings(template_settings)
    _write_trade_automation_store(tenant, store)


def _load_validation_export_profile() -> dict[str, float]:
    profile = dict(_VALIDATION_EXPORT_DEFAULTS)
    try:
        raw = json.loads(_VALIDATION_PROFILE_CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return profile

    reference = dict(raw.get("reference_path") or {})
    starting_capital = pd.to_numeric(raw.get("starting_capital"), errors="coerce")
    if pd.notna(starting_capital) and float(starting_capital) > 0:
        profile["starting_capital"] = float(starting_capital)
    for source_key, target_key in (
        ("known_peak_equity", "known_peak_equity"),
        ("known_drawdown_peak", "known_drawdown_peak"),
        ("known_drawdown_trough", "known_drawdown_trough"),
        ("known_max_drawdown_pct", "known_max_drawdown_pct"),
    ):
        value = pd.to_numeric(reference.get(source_key), errors="coerce")
        if pd.notna(value):
            profile[target_key] = float(value)
    return profile


def _load_latest_validation_summary_generated_at() -> datetime | None:
    summary_path = strategy_validation_service.RUNTIME_EXPORTS_DIR / "latest" / "summary.json"
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return _parse_iso_datetime(payload.get("generated_at"))


_COLLECTION_LIQUIDITY_REASONS = {
    "missing_spread",
    "missing_average_dollar_volume",
    "missing_edge",
    "spread_too_wide",
    "liquidity_too_low",
    "edge_cost_ratio_too_low",
    "missing_option_contract",
    "option_quote_missing",
    "stale_option_quote",
    "option_spread_too_wide",
    "option_liquidity_too_low",
    "option_open_interest_too_low",
}
_COLLECTION_RISK_REASONS = {
    "opening_window_lock",
    "closing_window_lock",
    "order_adv_cap",
    "intraday_volume_cap",
    "single_position_cap",
    "gross_exposure_cap",
    "correlated_bucket_cap",
    "capacity_reached",
    "drawdown_stop_lock",
    "drawdown_audit_lock",
    "broker_live_locked",
    "pyramiding_disabled",
    "symbol_pyramid_cap",
    "symbol_daily_entry_cap",
    "ticker_already_active",
    "averaging_down_blocked",
}


def _classify_collection_blocker(
    *,
    runtime_state: dict[str, Any],
    collection_metrics: dict[str, Any] | None = None,
    collection_audit: dict[str, Any] | None = None,
) -> str | None:
    metrics = dict(collection_metrics or {})
    audit = dict(collection_audit or {})
    if str(runtime_state.get("last_validation_rerun_status") or "").strip().lower() == "running":
        return None
    current_route_orphan_order_event_count = int(
        metrics.get("current_route_orphan_order_event_count")
        or runtime_state.get("current_route_orphan_order_event_count")
        or 0
    )
    if current_route_orphan_order_event_count > 0:
        return "broker_reconcile_failed"
    reason = str(
        (audit.get("primary_blocker") if audit else None)
        or (runtime_state.get("last_rejection") or {}).get("reason")
        or (runtime_state.get("last_decision") or {}).get("reason")
        or runtime_state.get("last_collection_blocker")
        or ""
    ).strip().lower()
    if not reason:
        return None
    if reason in {"outside_regular_session", "out_of_session"}:
        return "out_of_session"
    if reason in {"no_candidates"}:
        return "no_candidates"
    if reason in {"no_auto_entry_eligible", "no_valid_candidate", "no_eligible_candidate"}:
        return "no_auto_entry_eligible"
    if reason in _COLLECTION_LIQUIDITY_REASONS:
        return "liquidity_blocked"
    if reason in _COLLECTION_RISK_REASONS:
        return "risk_blocked"
    if reason in {"broker_submit_failed", "submission_failed"}:
        return "broker_submit_failed"
    if reason in {"ledger_persistence_failed", "persistence_failed"}:
        return "ledger_persistence_failed"
    if reason in {"broker_reconcile_failed"}:
        return "broker_reconcile_failed"
    return reason


def _new_collection_audit(*, cycle_id: str, now: datetime, collection_phase_active: bool) -> dict[str, Any]:
    return {
        "cycle_id": cycle_id,
        "recorded_at": _serialize_datetime(now),
        "collection_phase_active": bool(collection_phase_active),
        "scanned_candidate_count": 0,
        "auto_entry_eligible_candidate_count": 0,
        "blocked_candidates_by_reason": {},
        "submitted_order_count": 0,
        "broker_acknowledgement_count": 0,
        "fill_count": 0,
        "close_count": 0,
        "rerun_outcome": None,
        "primary_blocker": None,
    }


def _blocked_candidate_counts(rejections: list[dict[str, Any]] | None) -> dict[str, int]:
    counter: dict[str, int] = {}
    for item in list(rejections or []):
        reason = str(item.get("reason") or "").strip().lower()
        if not reason:
            continue
        counter[reason] = int(counter.get(reason) or 0) + 1
    return counter


def _finalize_collection_audit(
    runtime_state: dict[str, Any],
    *,
    collection_phase: dict[str, Any],
    collection_metrics: dict[str, Any],
    previous_fill_count: int,
    previous_close_count: int,
) -> None:
    audit = dict(runtime_state.pop("current_collection_audit", None) or {})
    if not audit:
        return
    current_fill_count = int(collection_metrics.get("current_route_fill_count") or 0)
    current_close_count = int(collection_metrics.get("current_route_closed_trade_count") or 0)
    audit["collection_phase_active"] = bool(collection_phase.get("collection_phase_active"))
    audit["fill_count"] = max(0, current_fill_count - int(previous_fill_count or 0))
    audit["close_count"] = max(0, current_close_count - int(previous_close_count or 0))
    audit["current_route_reconciliation_status"] = str(
        collection_metrics.get("current_route_reconciliation_status") or ""
    ).strip().lower() or None
    audit["current_route_orphan_order_event_count"] = int(
        collection_metrics.get("current_route_orphan_order_event_count") or 0
    )
    rerun_status = str(runtime_state.get("last_validation_rerun_status") or "").strip().lower()
    if rerun_status in {"succeeded", "failed"}:
        audit["rerun_outcome"] = rerun_status
    elif bool(runtime_state.get("validation_rerun_in_progress")):
        audit["rerun_outcome"] = "running"
    else:
        audit["rerun_outcome"] = audit.get("rerun_outcome") or "skipped"
    if (
        int(audit.get("submitted_order_count") or 0) > 0
        and int(audit.get("broker_acknowledgement_count") or 0) < int(audit.get("submitted_order_count") or 0)
        and not str(audit.get("primary_blocker") or "").strip()
    ):
        audit["primary_blocker"] = "broker_submit_failed"
    if (
        int(audit.get("broker_acknowledgement_count") or 0) > 0
        or int(audit.get("fill_count") or 0) > 0
        or int(audit.get("close_count") or 0) > 0
    ) and int(audit.get("current_route_orphan_order_event_count") or 0) <= 0:
        audit["primary_blocker"] = None
    audit["primary_blocker"] = _classify_collection_blocker(
        runtime_state=runtime_state,
        collection_metrics=collection_metrics,
        collection_audit=audit,
    )
    runtime_state["last_collection_audit"] = serialize_value(audit)
    history = list(runtime_state.get("collection_audit_history") or [])
    history.append(serialize_value(audit))
    runtime_state["collection_audit_history"] = history[-_COLLECTION_AUDIT_HISTORY_LIMIT:]
    runtime_state["last_collection_blocker"] = audit.get("primary_blocker")


def _build_live_current_route_collection_metrics(tenant: Tenant) -> dict[str, Any]:
    profile = _load_validation_export_profile()
    starting_capital = float(profile["starting_capital"])
    tenant_slug = str(tenant.slug or "").strip()
    tenant_id = str(tenant.id or "").strip()
    ledger = strategy_validation_service.build_trade_validation_ledger(
        tenant_slug=tenant_slug,
        starting_capital=starting_capital,
    )
    trade_frames = strategy_validation_service._load_trade_frames()
    order_events = strategy_validation_service._load_order_events(tenant_id)
    broker_reconciliation = strategy_validation_service.build_broker_reconciliation_report(
        ledger,
        open_trades=trade_frames["open_trades"],
        closed_trades=trade_frames["closed_trades"],
        pending_orders=trade_frames["pending_orders"],
        order_events=order_events,
    )
    current_route_ledger = strategy_validation_service._filter_reconciled_current_route_ledger(
        ledger,
        broker_reconciliation,
    )
    snapshots = strategy_validation_service._load_tenant_equity_snapshots(tenant_id, tenant_slug)
    alignment = strategy_validation_service.build_signal_execution_alignment_report(current_route_ledger)
    current_metrics = strategy_validation_service.compute_ledger_metrics(
        current_route_ledger,
        starting_capital=starting_capital,
    )
    current_route_fill_count = int(alignment.get("directional_fill_count") or 0)
    current_route_closed_trade_count = int(current_metrics.get("closed_trade_count") or 0)
    current_route_mismatched_count = int(alignment.get("mismatched_count") or 0)
    current_route_sample_status = (
        "sufficient"
        if (
            current_route_fill_count >= 10
            and current_route_closed_trade_count >= 5
        )
        else "insufficient"
    )
    current_route_validation_integrity = strategy_validation_service._build_current_route_validation_integrity(
        ledger,
        snapshots,
        starting_capital=starting_capital,
        current_route_sample_status=current_route_sample_status,
        current_route_ledger=current_route_ledger,
    )
    current_route_reconciliation_status = str(
        broker_reconciliation.get("current_route_reconciliation_status") or "waiting"
    ).strip().lower() or "waiting"
    current_route_orphan_order_event_count = int(
        broker_reconciliation.get("current_route_orphan_order_event_count") or 0
    )
    legacy_orphan_order_event_count = int(
        broker_reconciliation.get("legacy_orphan_order_event_count") or 0
    )
    return {
        "current_route_fill_count": current_route_fill_count,
        "current_route_closed_trade_count": current_route_closed_trade_count,
        "current_route_mismatched_count": current_route_mismatched_count,
        "current_route_sample_status": current_route_sample_status,
        "current_route_reconciliation_status": current_route_reconciliation_status,
        "current_route_orphan_order_event_count": current_route_orphan_order_event_count,
        "legacy_orphan_order_event_count": legacy_orphan_order_event_count,
        "current_route_reconciled_fill_count": current_route_fill_count,
        "current_route_reconciled_close_count": current_route_closed_trade_count,
        "mark_to_market_coverage_status": str(
            current_route_validation_integrity.get("mark_to_market_coverage_status") or ""
        ).strip().lower() or None,
        "ledger_snapshot_consistency": str(
            current_route_validation_integrity.get("ledger_snapshot_consistency") or ""
        ).strip().lower() or None,
        "metrics_source": str(
            current_route_validation_integrity.get("metrics_source") or ""
        ).strip().lower() or None,
        "current_route_validation_integrity": serialize_value(current_route_validation_integrity),
        "current_route_latest_event_at": _max_serialized_datetime(
            current_route_validation_integrity.get("route_window_end"),
            broker_reconciliation.get("last_submitted_current_route_order_at"),
            broker_reconciliation.get("last_current_route_fill_at"),
            broker_reconciliation.get("last_current_route_close_at"),
        ),
        "route_window_start": current_route_validation_integrity.get("route_window_start"),
        "route_window_end": current_route_validation_integrity.get("route_window_end"),
        "route_window_snapshot_count": int(
            current_route_validation_integrity.get("route_window_snapshot_count") or 0
        ),
        "route_window_metrics_source": str(
            current_route_validation_integrity.get("route_window_metrics_source") or ""
        ).strip().lower() or None,
        "route_window_mark_to_market_coverage_status": str(
            current_route_validation_integrity.get("route_window_mark_to_market_coverage_status") or ""
        ).strip().lower() or None,
        "route_window_ledger_snapshot_consistency": str(
            current_route_validation_integrity.get("route_window_ledger_snapshot_consistency") or ""
        ).strip().lower() or None,
        "last_submitted_current_route_order_at": broker_reconciliation.get("last_submitted_current_route_order_at"),
        "last_current_route_fill_at": broker_reconciliation.get("last_current_route_fill_at"),
        "last_current_route_close_at": broker_reconciliation.get("last_current_route_close_at"),
        "broker_reconciliation": serialize_value(broker_reconciliation),
    }


def _build_collection_phase_state(
    *,
    rollout_readiness: dict[str, Any] | None,
    runtime_state: dict[str, Any] | None = None,
    collection_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def _pick(*values: Any) -> Any:
        for value in values:
            if value is not None:
                return value
        return None

    readiness = dict(rollout_readiness or {})
    runtime = dict(runtime_state or {})
    metrics = dict(readiness.get("metrics") or {})
    ranked_entry_rollout = dict(readiness.get("ranked_entry_rollout") or {})
    collection_audit = dict(runtime.get("last_collection_audit") or {})
    validation_integrity = dict(
        readiness.get("current_route_validation_integrity")
        or ranked_entry_rollout.get("current_route_validation_integrity")
        or {}
    )
    live_metrics = dict(collection_metrics or {})

    current_route_fill_count = int(_pick(
        live_metrics.get("current_route_fill_count"),
        runtime.get("current_route_fill_count"),
        metrics.get("current_route_fill_count"),
        ranked_entry_rollout.get("current_route_fill_count"),
        ranked_entry_rollout.get("current_route_directional_fill_count"),
        0,
    ) or 0)
    current_route_closed_trade_count = int(_pick(
        live_metrics.get("current_route_closed_trade_count"),
        runtime.get("current_route_closed_trade_count"),
        metrics.get("current_route_closed_trade_count"),
        ranked_entry_rollout.get("current_route_closed_trade_count"),
        0,
    ) or 0)
    current_route_mismatched_count = int(_pick(
        live_metrics.get("current_route_mismatched_count"),
        runtime.get("current_route_mismatched_count"),
        0,
    ) or 0)
    current_route_sample_status = str(
        live_metrics.get("current_route_sample_status")
        or runtime.get("current_route_sample_status")
        or metrics.get("current_route_sample_status")
        or validation_integrity.get("current_route_sample_status")
        or ranked_entry_rollout.get("current_route_sample_status")
        or "insufficient"
    ).strip().lower() or "insufficient"
    mark_to_market_coverage_status = str(
        live_metrics.get("mark_to_market_coverage_status")
        or runtime.get("mark_to_market_coverage_status")
        or metrics.get("mark_to_market_coverage_status")
        or validation_integrity.get("mark_to_market_coverage_status")
        or ranked_entry_rollout.get("mark_to_market_coverage_status")
        or "missing"
    ).strip().lower() or "missing"
    ledger_snapshot_consistency = str(
        live_metrics.get("ledger_snapshot_consistency")
        or runtime.get("ledger_snapshot_consistency")
        or metrics.get("ledger_snapshot_consistency")
        or validation_integrity.get("ledger_snapshot_consistency")
        or ranked_entry_rollout.get("ledger_snapshot_consistency")
        or "unavailable"
    ).strip().lower() or "unavailable"
    metrics_source = str(
        live_metrics.get("metrics_source")
        or runtime.get("metrics_source")
        or metrics.get("metrics_source")
        or validation_integrity.get("metrics_source")
        or ranked_entry_rollout.get("metrics_source")
        or ""
    ).strip().lower() or None
    integrity_status = str(
        validation_integrity.get("status")
        or (
            dict(live_metrics.get("current_route_validation_integrity") or {}).get("status")
            if isinstance(live_metrics.get("current_route_validation_integrity"), dict)
            else ""
        )
        or ""
    ).strip().lower() or None
    route_window_start = _pick(
        live_metrics.get("route_window_start"),
        runtime.get("route_window_start"),
        metrics.get("route_window_start"),
        validation_integrity.get("route_window_start"),
        ranked_entry_rollout.get("route_window_start"),
    )
    route_window_end = _pick(
        live_metrics.get("route_window_end"),
        runtime.get("route_window_end"),
        metrics.get("route_window_end"),
        validation_integrity.get("route_window_end"),
        ranked_entry_rollout.get("route_window_end"),
    )
    route_window_snapshot_count = int(_pick(
        live_metrics.get("route_window_snapshot_count"),
        runtime.get("route_window_snapshot_count"),
        metrics.get("route_window_snapshot_count"),
        validation_integrity.get("route_window_snapshot_count"),
        ranked_entry_rollout.get("route_window_snapshot_count"),
        0,
    ) or 0)
    latest_event_at = _pick(
        live_metrics.get("current_route_latest_event_at"),
        runtime.get("current_route_latest_event_at"),
        route_window_end,
    )
    current_route_reconciliation_status = str(
        live_metrics.get("current_route_reconciliation_status")
        or runtime.get("current_route_reconciliation_status")
        or metrics.get("current_route_reconciliation_status")
        or ranked_entry_rollout.get("current_route_reconciliation_status")
        or "waiting"
    ).strip().lower() or "waiting"
    current_route_orphan_order_event_count = int(_pick(
        live_metrics.get("current_route_orphan_order_event_count"),
        runtime.get("current_route_orphan_order_event_count"),
        metrics.get("current_route_orphan_order_event_count"),
        ranked_entry_rollout.get("current_route_orphan_order_event_count"),
        0,
    ) or 0)
    last_submitted_current_route_order_at = _pick(
        live_metrics.get("last_submitted_current_route_order_at"),
        runtime.get("last_submitted_current_route_order_at"),
        metrics.get("last_submitted_current_route_order_at"),
        ranked_entry_rollout.get("last_submitted_current_route_order_at"),
    )
    last_current_route_fill_at = _pick(
        live_metrics.get("last_current_route_fill_at"),
        runtime.get("last_current_route_fill_at"),
        metrics.get("last_current_route_fill_at"),
        ranked_entry_rollout.get("last_current_route_fill_at"),
    )
    last_current_route_close_at = _pick(
        live_metrics.get("last_current_route_close_at"),
        runtime.get("last_current_route_close_at"),
        metrics.get("last_current_route_close_at"),
        ranked_entry_rollout.get("last_current_route_close_at"),
    )
    last_collection_blocker = _classify_collection_blocker(
        runtime_state=runtime,
        collection_metrics=live_metrics,
        collection_audit=collection_audit,
    )
    ranked_entry_accepted = bool(
        ranked_entry_rollout.get("accepted")
        or metrics.get("ranked_entry_accepted")
    )
    ranked_entry_basis = str(
        ranked_entry_rollout.get("basis")
        or ranked_entry_rollout.get("detail")
        or readiness.get("basis")
        or ""
    ).strip()
    validation_rerun_in_progress = bool(runtime.get("validation_rerun_in_progress"))
    auto_validation_rerun_enabled = bool(runtime.get("auto_validation_rerun_enabled", True))
    integrity_ready = (
        current_route_sample_status == "sufficient"
        and current_route_mismatched_count <= 0
        and mark_to_market_coverage_status == "complete"
        and ledger_snapshot_consistency == "consistent"
        and current_route_reconciliation_status == "clean"
        and current_route_orphan_order_event_count <= 0
        and integrity_status not in {"partial", "fail"}
    )
    collection_phase_active = bool(
        validation_rerun_in_progress
        or not ranked_entry_accepted
        or not integrity_ready
    )

    if validation_rerun_in_progress:
        collection_phase_label = _COLLECTION_PHASE_LABEL_RERUNNING
        collection_phase_tone = "warning"
        collection_phase_detail = "The latest qualifying paper cycle triggered an automatic validation export."
    elif last_collection_blocker == "ledger_persistence_failed":
        collection_phase_label = _COLLECTION_PHASE_LABEL_PERSISTENCE
        collection_phase_tone = "negative"
        persistence_detail = str(
            (runtime.get("last_rejection") or {}).get("detail")
            or (runtime.get("last_decision") or {}).get("detail")
            or ""
        ).strip()
        collection_phase_detail = persistence_detail or (
            "Broker-paper events were accepted, but the matching local trade-ledger rows were not written cleanly."
        )
    elif current_route_orphan_order_event_count > 0 or current_route_reconciliation_status in {"orphaned", "issues_present"}:
        collection_phase_label = "Broker reconcile issue"
        collection_phase_tone = "negative"
        collection_phase_detail = (
            f"Current-route broker reconciliation still shows {current_route_orphan_order_event_count} orphan event"
            f"{'s' if current_route_orphan_order_event_count != 1 else ''}. "
            "Paper collection stays locked until current-route order events reconcile cleanly."
        )
    elif last_collection_blocker in {"no_candidates", "no_auto_entry_eligible"}:
        collection_phase_label = "No eligible candidates"
        collection_phase_tone = "warning"
        scanned_candidate_count = int(collection_audit.get("scanned_candidate_count") or 0)
        auto_entry_eligible_candidate_count = int(collection_audit.get("auto_entry_eligible_candidate_count") or 0)
        collection_phase_detail = (
            f"The latest paper cycle scanned {scanned_candidate_count} candidate"
            f"{'s' if scanned_candidate_count != 1 else ''} and found "
            f"{auto_entry_eligible_candidate_count} auto-entry-eligible setup"
            f"{'s' if auto_entry_eligible_candidate_count != 1 else ''}."
        )
    elif last_collection_blocker in {"risk_blocked", "liquidity_blocked"}:
        collection_phase_label = "Risk or liquidity blocked"
        collection_phase_tone = "warning" if last_collection_blocker == "liquidity_blocked" else "negative"
        rejection_detail = str(
            (runtime.get("last_rejection") or {}).get("detail")
            or (runtime.get("last_decision") or {}).get("detail")
            or ""
        ).strip()
        collection_phase_detail = rejection_detail or (
            "Qualified current-route candidates were present, but the guardrails blocked unattended entry."
        )
    elif current_route_sample_status != "sufficient":
        collection_phase_label = _COLLECTION_PHASE_LABEL_COLLECTING
        collection_phase_tone = "warning"
        collection_phase_detail = (
            f"Collecting current-route paper evidence. "
            f"{current_route_fill_count}/10 directional fills and {current_route_closed_trade_count}/5 closes are recorded."
        )
    elif current_route_mismatched_count > 0:
        collection_phase_label = _COLLECTION_PHASE_LABEL_BLOCKED
        collection_phase_tone = "negative"
        collection_phase_detail = (
            f"Current-route alignment still shows {current_route_mismatched_count} mismatch"
            f"{'es' if current_route_mismatched_count != 1 else ''}, so promotion remains blocked."
        )
    elif mark_to_market_coverage_status != "complete":
        collection_phase_label = _COLLECTION_PHASE_LABEL_BLOCKED
        collection_phase_tone = "warning"
        collection_phase_detail = (
            f"Current-route snapshot coverage is {mark_to_market_coverage_status.replace('_', ' ')}, "
            "so validation remains blocked."
        )
    elif ledger_snapshot_consistency != "consistent":
        collection_phase_label = _COLLECTION_PHASE_LABEL_BLOCKED
        collection_phase_tone = "negative" if ledger_snapshot_consistency == "inconsistent" else "warning"
        collection_phase_detail = (
            f"Current-route ledger and snapshot accounting is {ledger_snapshot_consistency.replace('_', ' ')}, "
            "so rollout remains blocked."
        )
    elif not ranked_entry_accepted:
        collection_phase_label = _COLLECTION_PHASE_LABEL_BLOCKED
        collection_phase_tone = "warning"
        collection_phase_detail = ranked_entry_basis or (
            "Validation reran successfully, but the ranked-entry promotion profile is still not accepted."
        )
    else:
        collection_phase_label = _COLLECTION_PHASE_LABEL_READY
        collection_phase_tone = "positive"
        collection_phase_detail = (
            "Current-route paper evidence and ranked-entry validation are ready for rollout review."
        )

    return {
        "collection_phase_active": collection_phase_active,
        "collection_phase_label": collection_phase_label,
        "collection_phase_detail": collection_phase_detail,
        "collection_phase_tone": collection_phase_tone,
        "auto_validation_rerun_enabled": auto_validation_rerun_enabled,
        "last_validation_rerun_at": _serialize_datetime(_parse_iso_datetime(runtime.get("last_validation_rerun_at"))),
        "last_validation_rerun_cycle_id": str(runtime.get("last_validation_rerun_cycle_id") or "").strip() or None,
        "last_validation_rerun_status": str(runtime.get("last_validation_rerun_status") or "").strip().lower() or None,
        "last_validation_rerun_error": str(runtime.get("last_validation_rerun_error") or "").strip() or None,
        "validation_rerun_in_progress": validation_rerun_in_progress,
        "current_route_fill_count": current_route_fill_count,
        "current_route_closed_trade_count": current_route_closed_trade_count,
        "current_route_mismatched_count": current_route_mismatched_count,
        "current_route_sample_status": current_route_sample_status,
        "mark_to_market_coverage_status": mark_to_market_coverage_status,
        "ledger_snapshot_consistency": ledger_snapshot_consistency,
        "metrics_source": metrics_source,
        "route_window_start": route_window_start,
        "route_window_end": route_window_end,
        "route_window_snapshot_count": route_window_snapshot_count,
        "current_route_latest_event_at": latest_event_at,
        "last_collection_blocker": last_collection_blocker,
        "current_route_reconciliation_status": current_route_reconciliation_status,
        "current_route_orphan_order_event_count": current_route_orphan_order_event_count,
        "last_submitted_current_route_order_at": last_submitted_current_route_order_at,
        "last_current_route_fill_at": last_current_route_fill_at,
        "last_current_route_close_at": last_current_route_close_at,
        "last_collection_audit": serialize_value(collection_audit) if collection_audit else None,
        "ranked_entry_accepted": ranked_entry_accepted,
        "current_route_validation_integrity": serialize_value(
            live_metrics.get("current_route_validation_integrity") or validation_integrity
        ),
    }


def _apply_collection_phase_controls(
    settings_state: dict[str, Any],
    collection_phase: dict[str, Any] | None,
) -> dict[str, Any]:
    effective_settings = dict(settings_state or {})
    if not bool((collection_phase or {}).get("collection_phase_active")):
        return effective_settings
    effective_settings["execution_intent"] = _COLLECTION_PHASE_ROUTE
    if _normalize_automation_instrument(effective_settings.get("instrument_type"), default="equity") == "listed_option":
        effective_settings["regular_hours_only"] = True
        effective_settings["time_in_force"] = "day"
    else:
        effective_settings["regular_hours_only"] = False
        effective_settings["order_type"] = "limit"
        effective_settings["time_in_force"] = "day_ext"
    return effective_settings


def _primary_automation_instrument(settings_state: dict[str, Any]) -> str:
    if bool(settings_state.get("auto_trade_equities")):
        return "equity"
    return _normalize_automation_instrument(settings_state.get("instrument_type"), default="equity")


def _build_session_adjusted_automation_settings(
    settings_state: dict[str, Any],
    *,
    session_profile: Any,
) -> dict[str, Any]:
    effective = dict(settings_state or {})
    if getattr(session_profile, "force_limit_orders", False):
        effective["order_type"] = "limit"
    if str(getattr(session_profile, "time_in_force", "") or "").strip():
        effective["time_in_force"] = str(session_profile.time_in_force)
    if getattr(session_profile, "mode", "") in {"pre_market", "after_hours"}:
        effective["regular_hours_only"] = False

    risk_multiplier = float(getattr(session_profile, "risk_multiplier", 1.0) or 1.0)
    size_cap_ratio = float(getattr(session_profile, "size_cap_ratio", 1.0) or 1.0)
    if risk_multiplier < 1.0:
        effective["risk_percent"] = max(0.05, float(effective.get("risk_percent") or 0.5) * risk_multiplier)
    if size_cap_ratio < 1.0:
        effective["max_notional_per_trade"] = max(
            100.0,
            float(effective.get("max_notional_per_trade") or 100.0) * size_cap_ratio,
        )
        effective["max_total_open_notional"] = max(
            100.0,
            float(effective.get("max_total_open_notional") or 100.0) * max(size_cap_ratio, 0.25),
        )
    effective["min_edge_to_cost_ratio"] = max(
        float(effective.get("min_edge_to_cost_ratio") or 0.0),
        float(getattr(session_profile, "min_edge_to_cost_ratio", 0.0) or 0.0),
    )
    effective["max_spread_bps"] = min(
        float(effective.get("max_spread_bps") or 25.0),
        float(getattr(session_profile, "max_spread_bps", 25.0) or 25.0),
    )
    effective["cooldown_minutes"] = max(
        int(effective.get("cooldown_minutes") or 0),
        int(getattr(session_profile, "min_cooldown_minutes", 0) or 0),
    )
    max_daily_entries = getattr(session_profile, "max_daily_entries", None)
    if max_daily_entries is not None:
        effective["max_daily_entries"] = max(1, min(int(effective.get("max_daily_entries") or max_daily_entries), int(max_daily_entries)))
    effective["allow_pyramiding"] = bool(effective.get("allow_pyramiding")) and getattr(session_profile, "mode", "") != "after_hours"
    return effective


def _apply_collection_phase_runtime(
    runtime_state: dict[str, Any],
    collection_phase: dict[str, Any],
) -> None:
    runtime_state["collection_phase_active"] = bool(collection_phase.get("collection_phase_active"))
    runtime_state["collection_phase_label"] = str(collection_phase.get("collection_phase_label") or "").strip() or None
    runtime_state["collection_phase_detail"] = str(collection_phase.get("collection_phase_detail") or "").strip() or None
    runtime_state["auto_validation_rerun_enabled"] = bool(collection_phase.get("auto_validation_rerun_enabled", True))
    runtime_state["validation_rerun_in_progress"] = bool(collection_phase.get("validation_rerun_in_progress"))
    runtime_state["last_validation_rerun_at"] = collection_phase.get("last_validation_rerun_at")
    runtime_state["last_validation_rerun_cycle_id"] = collection_phase.get("last_validation_rerun_cycle_id")
    runtime_state["last_validation_rerun_status"] = collection_phase.get("last_validation_rerun_status")
    runtime_state["last_validation_rerun_error"] = collection_phase.get("last_validation_rerun_error")
    runtime_state["current_route_fill_count"] = int(collection_phase.get("current_route_fill_count") or 0)
    runtime_state["current_route_closed_trade_count"] = int(collection_phase.get("current_route_closed_trade_count") or 0)
    runtime_state["current_route_mismatched_count"] = int(collection_phase.get("current_route_mismatched_count") or 0)
    runtime_state["current_route_sample_status"] = str(collection_phase.get("current_route_sample_status") or "").strip().lower() or None
    runtime_state["mark_to_market_coverage_status"] = str(collection_phase.get("mark_to_market_coverage_status") or "").strip().lower() or None
    runtime_state["ledger_snapshot_consistency"] = str(collection_phase.get("ledger_snapshot_consistency") or "").strip().lower() or None
    runtime_state["metrics_source"] = str(collection_phase.get("metrics_source") or "").strip().lower() or None
    runtime_state["route_window_start"] = collection_phase.get("route_window_start")
    runtime_state["route_window_end"] = collection_phase.get("route_window_end")
    runtime_state["route_window_snapshot_count"] = int(collection_phase.get("route_window_snapshot_count") or 0)
    runtime_state["current_route_latest_event_at"] = collection_phase.get("current_route_latest_event_at")
    runtime_state["last_collection_blocker"] = str(collection_phase.get("last_collection_blocker") or "").strip().lower() or None
    runtime_state["current_route_reconciliation_status"] = str(collection_phase.get("current_route_reconciliation_status") or "").strip().lower() or None
    runtime_state["current_route_orphan_order_event_count"] = int(collection_phase.get("current_route_orphan_order_event_count") or 0)
    runtime_state["last_submitted_current_route_order_at"] = collection_phase.get("last_submitted_current_route_order_at")
    runtime_state["last_current_route_fill_at"] = collection_phase.get("last_current_route_fill_at")
    runtime_state["last_current_route_close_at"] = collection_phase.get("last_current_route_close_at")
    runtime_state["last_collection_audit"] = serialize_value(collection_phase.get("last_collection_audit"))


def _augment_rollout_readiness_with_collection_phase(
    rollout_readiness: dict[str, Any] | None,
    collection_phase: dict[str, Any],
) -> dict[str, Any]:
    readiness = dict(rollout_readiness or {})
    metrics = dict(readiness.get("metrics") or {})
    metrics.update(
        {
            "current_route_fill_count": int(collection_phase.get("current_route_fill_count") or 0),
            "current_route_closed_trade_count": int(collection_phase.get("current_route_closed_trade_count") or 0),
            "current_route_sample_status": collection_phase.get("current_route_sample_status"),
            "mark_to_market_coverage_status": collection_phase.get("mark_to_market_coverage_status"),
            "ledger_snapshot_consistency": collection_phase.get("ledger_snapshot_consistency"),
            "metrics_source": collection_phase.get("metrics_source"),
            "route_window_start": collection_phase.get("route_window_start"),
            "route_window_end": collection_phase.get("route_window_end"),
            "route_window_snapshot_count": int(collection_phase.get("route_window_snapshot_count") or 0),
            "last_collection_blocker": collection_phase.get("last_collection_blocker"),
            "current_route_reconciliation_status": collection_phase.get("current_route_reconciliation_status"),
            "current_route_orphan_order_event_count": int(collection_phase.get("current_route_orphan_order_event_count") or 0),
            "last_submitted_current_route_order_at": collection_phase.get("last_submitted_current_route_order_at"),
            "last_current_route_fill_at": collection_phase.get("last_current_route_fill_at"),
            "last_current_route_close_at": collection_phase.get("last_current_route_close_at"),
        }
    )
    readiness["metrics"] = metrics
    readiness["collection_phase_active"] = bool(collection_phase.get("collection_phase_active"))
    readiness["collection_phase_label"] = collection_phase.get("collection_phase_label")
    readiness["collection_phase_detail"] = collection_phase.get("collection_phase_detail")
    readiness["auto_validation_rerun_enabled"] = bool(collection_phase.get("auto_validation_rerun_enabled", True))
    readiness["last_validation_rerun_at"] = collection_phase.get("last_validation_rerun_at")
    readiness["last_validation_rerun_cycle_id"] = collection_phase.get("last_validation_rerun_cycle_id")
    readiness["last_collection_blocker"] = collection_phase.get("last_collection_blocker")
    readiness["current_route_reconciliation_status"] = collection_phase.get("current_route_reconciliation_status")
    readiness["current_route_orphan_order_event_count"] = int(collection_phase.get("current_route_orphan_order_event_count") or 0)
    current_route_validation_integrity = dict(readiness.get("current_route_validation_integrity") or {})
    current_route_validation_integrity.update(
        dict(collection_phase.get("current_route_validation_integrity") or {})
    )
    if current_route_validation_integrity:
        readiness["current_route_validation_integrity"] = serialize_value(current_route_validation_integrity)
    ranked_entry_rollout = dict(readiness.get("ranked_entry_rollout") or {})
    if ranked_entry_rollout:
        ranked_entry_rollout.update(
            {
                "current_route_fill_count": int(collection_phase.get("current_route_fill_count") or 0),
                "current_route_closed_trade_count": int(collection_phase.get("current_route_closed_trade_count") or 0),
                "current_route_sample_status": collection_phase.get("current_route_sample_status"),
                "mark_to_market_coverage_status": collection_phase.get("mark_to_market_coverage_status"),
                "ledger_snapshot_consistency": collection_phase.get("ledger_snapshot_consistency"),
                "metrics_source": collection_phase.get("metrics_source"),
                "route_window_start": collection_phase.get("route_window_start"),
                "route_window_end": collection_phase.get("route_window_end"),
                "route_window_snapshot_count": int(collection_phase.get("route_window_snapshot_count") or 0),
                "last_collection_blocker": collection_phase.get("last_collection_blocker"),
                "current_route_reconciliation_status": collection_phase.get("current_route_reconciliation_status"),
                "current_route_orphan_order_event_count": int(collection_phase.get("current_route_orphan_order_event_count") or 0),
                "last_submitted_current_route_order_at": collection_phase.get("last_submitted_current_route_order_at"),
                "last_current_route_fill_at": collection_phase.get("last_current_route_fill_at"),
                "last_current_route_close_at": collection_phase.get("last_current_route_close_at"),
                "current_route_validation_integrity": serialize_value(
                    collection_phase.get("current_route_validation_integrity") or {}
                ),
            }
        )
        readiness["ranked_entry_rollout"] = serialize_value(ranked_entry_rollout)
    return readiness


def _should_auto_rerun_validation(
    *,
    collection_phase: dict[str, Any],
    runtime_state: dict[str, Any],
    cycle_id: str,
) -> bool:
    if not bool(collection_phase.get("auto_validation_rerun_enabled", True)):
        return False
    if bool(collection_phase.get("validation_rerun_in_progress")):
        return False
    if int(collection_phase.get("current_route_fill_count") or 0) < 10:
        return False
    if int(collection_phase.get("current_route_closed_trade_count") or 0) < 5:
        return False
    if int(collection_phase.get("current_route_mismatched_count") or 0) > 0:
        return False
    if int(collection_phase.get("current_route_orphan_order_event_count") or 0) > 0:
        return False
    if str(collection_phase.get("current_route_reconciliation_status") or "").strip().lower() not in {"clean"}:
        return False
    latest_event_at = _parse_iso_datetime(collection_phase.get("current_route_latest_event_at"))
    latest_summary_generated_at = _load_latest_validation_summary_generated_at()
    last_rerun_at = _parse_iso_datetime(runtime_state.get("last_validation_rerun_at"))
    last_rerun_event_at = _parse_iso_datetime(runtime_state.get("last_validation_rerun_event_at"))
    last_rerun_status = str(runtime_state.get("last_validation_rerun_status") or "").strip().lower()
    last_rerun_cycle_id = str(runtime_state.get("last_validation_rerun_cycle_id") or "").strip()
    if last_rerun_status == "succeeded":
        if last_rerun_cycle_id == cycle_id:
            return False
        if latest_event_at is not None and last_rerun_event_at is not None and last_rerun_event_at >= latest_event_at:
            return False
        if latest_event_at is not None and last_rerun_at is not None and last_rerun_at >= latest_event_at:
            return False
        if (
            latest_event_at is not None
            and latest_summary_generated_at is not None
            and latest_summary_generated_at >= latest_event_at
        ):
            return False
    return True


def _run_validation_export_for_collection_phase(tenant: Tenant) -> None:
    profile = _load_validation_export_profile()
    strategy_validation_service.export_strategy_validation(
        tenant_slug=str(tenant.slug or "").strip(),
        starting_capital=float(profile["starting_capital"]),
        known_peak_equity=float(profile["known_peak_equity"]),
        known_drawdown_peak=float(profile["known_drawdown_peak"]),
        known_drawdown_trough=float(profile["known_drawdown_trough"]),
        known_max_drawdown_pct=float(profile["known_max_drawdown_pct"]),
    )


def _assert_trade_automation_operator(current_user: Any) -> None:
    require_current_user_permission(
        current_user,
        "tenant.manage_support",
        "Only tenant owners or admins can manage autonomous trading.",
    )


def _build_system_current_user(tenant: Tenant, actor: Any | None) -> Any:
    auth_subject = str(getattr(actor, "auth_subject", "") or getattr(actor, "id", "") or "").strip()
    return SimpleNamespace(
        tenant_id=getattr(tenant, "id", None),
        tenant_slug=getattr(tenant, "slug", None),
        tenant_name=getattr(tenant, "name", None) or str(getattr(tenant, "slug", "") or "").strip() or "Tenant",
        auth_subject=auth_subject,
        user_id=auth_subject,
        email=str(getattr(actor, "email", "") or "").strip() or "automation@system.local",
        name=str(getattr(actor, "name", "") or "").strip() or "Trade automation",
        role="owner",
        platform_role="admin",
        permissions=("tenant.manage_support", "trade.execute", "market.read", "tenant.read"),
        mode="system",
    )


def _coerce_account_balance(value: Any) -> float | None:
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return None
    return float(numeric)


def _resolve_account_effective_funds(
    account_summary: dict[str, Any] | None,
    *,
    multiplier: float = 1.0,
) -> dict[str, Any]:
    summary = dict(account_summary or {})
    normalized_multiplier = _normalize_float(multiplier, 1.0, minimum=1.0, maximum=10.0)
    actual_funds: float | None = None
    actual_funds_source: str | None = None
    for key in ("equity", "portfolio_value", "cash"):
        numeric = _coerce_account_balance(summary.get(key))
        if numeric is not None and numeric > 0:
            actual_funds = float(numeric)
            actual_funds_source = key
            break

    buying_power = _coerce_account_balance(summary.get("buying_power"))
    if actual_funds is None and buying_power is not None and buying_power > 0:
        actual_funds = float(buying_power)
        actual_funds_source = "buying_power"

    effective_funds = actual_funds
    cap_source = None
    detail = None
    if actual_funds is not None and actual_funds > 0:
        effective_funds = float(actual_funds)
        if normalized_multiplier > 1.0 and actual_funds_source != "buying_power":
            deployable_target = float(actual_funds) * normalized_multiplier
            if buying_power is not None and buying_power > actual_funds:
                effective_funds = float(min(deployable_target, buying_power))
                if effective_funds < deployable_target:
                    cap_source = "buying_power"
                    detail = (
                        f"Deployable sizing funds are capped at buying power. Base "
                        f"{actual_funds_source} multiplied by {normalized_multiplier:.2f}x would exceed available leverage."
                    )
                else:
                    detail = f"Deployable sizing funds use {actual_funds_source} × {normalized_multiplier:.2f}."
            else:
                detail = (
                    f"Deployable sizing funds stayed on {actual_funds_source} because no margin buying power above the base balance was available."
                )

    return {
        "actual_funds": actual_funds,
        "actual_funds_source": actual_funds_source,
        "effective_funds": effective_funds,
        "funds_source": actual_funds_source,
        "effective_funds_multiplier": normalized_multiplier,
        "effective_funds_cap_source": cap_source,
        "effective_funds_detail": detail,
    }


def _build_personal_account_summary(*, profile_key: str) -> dict[str, Any]:
    normalized_key = str(profile_key or _AUTOMATION_PERSONAL_PAPER_PROFILE).strip().lower()
    label = "Paper account" if normalized_key == _AUTOMATION_PERSONAL_PAPER_PROFILE else "Live account"
    provider = "alpaca_paper" if normalized_key == _AUTOMATION_PERSONAL_PAPER_PROFILE else "alpaca_live"
    if normalized_key == _AUTOMATION_PERSONAL_LIVE_PROFILE and not bool(settings.alpaca_live_api_key_id or settings.alpaca_api_key_id):
        return {
            "provider": provider,
            "label": label,
            "connected": False,
            "status": "unavailable",
            "detail": "Alpaca live credentials are not configured.",
            "equity": None,
            "cash": None,
            "portfolio_value": None,
            "buying_power": None,
            "position_market_value": None,
            "last_updated_at": _serialize_datetime(_utc_now()),
        }
    if normalized_key == _AUTOMATION_PERSONAL_PAPER_PROFILE and not bool(settings.alpaca_api_key_id and settings.alpaca_api_secret_key):
        return {
            "provider": provider,
            "label": label,
            "connected": False,
            "status": "unavailable",
            "detail": "Alpaca paper credentials are not configured.",
            "equity": None,
            "cash": None,
            "portfolio_value": None,
            "buying_power": None,
            "position_market_value": None,
            "last_updated_at": _serialize_datetime(_utc_now()),
        }
    try:
        client = build_alpaca_paper_client() if normalized_key == _AUTOMATION_PERSONAL_PAPER_PROFILE else build_alpaca_live_client()
        account = client.get_account()
    except AlpacaApiError as exc:
        return {
            "provider": provider,
            "label": label,
            "connected": False,
            "status": "error",
            "detail": str(exc),
            "equity": None,
            "cash": None,
            "portfolio_value": None,
            "buying_power": None,
            "position_market_value": None,
            "last_updated_at": _serialize_datetime(_utc_now()),
        }
    return {
        "provider": provider,
        "label": label,
        "connected": True,
        "status": "connected",
        "detail": f"Live {label.lower()} balances are coming from Alpaca.",
        "equity": _coerce_account_balance(account.get("equity")),
        "cash": _coerce_account_balance(account.get("cash")),
        "portfolio_value": _coerce_account_balance(account.get("portfolio_value")),
        "buying_power": _coerce_account_balance(account.get("buying_power")),
        "position_market_value": _coerce_account_balance(account.get("position_market_value")),
        "daytrade_count": _coerce_account_balance(account.get("daytrade_count")),
        "pattern_day_trader": bool(account.get("pattern_day_trader")) if account.get("pattern_day_trader") is not None else None,
        "last_updated_at": _serialize_datetime(_utc_now()),
    }


def _refresh_linked_account_account_summary(linked_account: BrokerageLinkedAccount) -> dict[str, Any]:
    metadata = dict(linked_account.metadata_json or {})
    fallback_account = dict(metadata.get("account") or {})
    try:
        client = build_linked_account_execution_client(linked_account)
        account = client.get_account()
    except Exception:
        account = fallback_account
    summary = {
        "provider": "linked_alpaca",
        "label": str(linked_account.label or linked_account.linked_identity_label or f"Linked {linked_account.account_environment} account").strip(),
        "connected": str(linked_account.connection_status or "").strip().lower() == "connected",
        "status": str(linked_account.connection_status or "unknown").strip().lower() or "unknown",
        "detail": "Linked account balances are coming from Alpaca OAuth." if account else "Linked account balances are unavailable.",
        "equity": _coerce_account_balance(account.get("equity")),
        "cash": _coerce_account_balance(account.get("cash")),
        "portfolio_value": _coerce_account_balance(account.get("portfolio_value")),
        "buying_power": _coerce_account_balance(account.get("buying_power")),
        "position_market_value": _coerce_account_balance(account.get("position_market_value")),
        "account_environment": str(linked_account.account_environment or "paper").strip().lower() or "paper",
        "linked_account_id": linked_account.id,
        "last_updated_at": _serialize_datetime(_utc_now()),
    }
    if account:
        metadata["account"] = {
            "id": account.get("id"),
            "status": account.get("status"),
            "currency": account.get("currency"),
            "buying_power": account.get("buying_power"),
            "cash": account.get("cash"),
            "portfolio_value": account.get("portfolio_value"),
            "equity": account.get("equity"),
            "position_market_value": account.get("position_market_value"),
            "name": summary["label"],
        }
        linked_account.metadata_json = metadata
        flag_modified(linked_account, "metadata_json")
        linked_account.last_refreshed_at = _utc_now()
    return summary


def _resolve_trade_automation_profile_account_context(
    *,
    tenant: Tenant,
    db: Session | None,
    profile_key: str,
    settings_state: dict[str, Any] | None = None,
    actor: Any | None = None,
    linked_account: BrokerageLinkedAccount | None = None,
) -> dict[str, Any]:
    scope, linked_account_id = _split_trade_automation_profile_key(profile_key)
    normalized_profile_key = str(profile_key or _AUTOMATION_PERSONAL_PAPER_PROFILE).strip().lower()
    normalized_settings = dict(settings_state or {})
    effective_funds_multiplier = _normalize_float(
        normalized_settings.get("effective_funds_multiplier"),
        1.0,
        minimum=1.0,
        maximum=10.0,
    )
    if scope == "linked":
        resolved_linked_account = linked_account
        if resolved_linked_account is None and db is not None and linked_account_id:
            resolved_linked_account = db.execute(
                select(BrokerageLinkedAccount).where(
                    BrokerageLinkedAccount.id == linked_account_id,
                    BrokerageLinkedAccount.tenant_id == tenant.id,
                )
            ).scalar_one_or_none()
        account_summary = _refresh_linked_account_account_summary(resolved_linked_account) if resolved_linked_account is not None else {}
        funds_state = _resolve_account_effective_funds(account_summary, multiplier=effective_funds_multiplier)
        owner = getattr(resolved_linked_account, "owner_user", None) if resolved_linked_account is not None else None
        return {
            "profile_key": normalized_profile_key,
            "scope": scope,
            "linked_account": resolved_linked_account,
            "account_summary": account_summary,
            **funds_state,
            "execution_intent": "broker_paper",
            "current_user": SimpleNamespace(
                **dict(getattr(_build_system_current_user(tenant, actor), "__dict__", {}) or {}),
                auth_subject=str(getattr(owner, "auth_subject", "") or getattr(actor, "auth_subject", "") or "").strip(),
                user_id=str(getattr(owner, "id", "") or getattr(resolved_linked_account, "owner_user_id", "") or getattr(actor, "user_id", "") or "").strip(),
                email=str(getattr(owner, "email", "") or getattr(actor, "email", "") or "automation@system.local").strip(),
                name=str(getattr(owner, "name", "") or getattr(actor, "name", "") or "Trade automation").strip(),
            ),
        }
    account_summary = _build_personal_account_summary(profile_key=normalized_profile_key)
    funds_state = _resolve_account_effective_funds(account_summary, multiplier=effective_funds_multiplier)
    return {
        "profile_key": normalized_profile_key,
        "scope": scope,
        "linked_account": None,
        "account_summary": account_summary,
        **funds_state,
        "execution_intent": "broker_live" if normalized_profile_key == _AUTOMATION_PERSONAL_LIVE_PROFILE else "broker_paper",
        "current_user": _build_system_current_user(tenant, actor),
    }


def _resolve_trade_automation_profile_selection(
    db: Session,
    *,
    tenant: Tenant,
    scope: str | None = None,
    scope_key: str | None = None,
    linked_account_id: str | None = None,
) -> tuple[str, BrokerageLinkedAccount | None]:
    normalized_scope_key = str(scope_key or "").strip().lower()
    if normalized_scope_key:
        profile_key = _build_trade_automation_profile_key(scope=normalized_scope_key, linked_account_id=linked_account_id)
    else:
        profile_key = _build_trade_automation_profile_key(scope=scope, linked_account_id=linked_account_id)
    resolved_linked_account: BrokerageLinkedAccount | None = None
    profile_scope, resolved_linked_account_id = _split_trade_automation_profile_key(profile_key)
    if profile_scope == "linked":
        if not resolved_linked_account_id:
            raise ValidationError("A linked account is required for linked automation scope.")
        resolved_linked_account = db.execute(
            select(BrokerageLinkedAccount).where(
                BrokerageLinkedAccount.id == resolved_linked_account_id,
                BrokerageLinkedAccount.tenant_id == tenant.id,
            )
        ).scalar_one_or_none()
        if resolved_linked_account is None:
            raise ValidationError("Linked automation profile could not find the requested broker account.")
    return profile_key, resolved_linked_account


def _list_trade_automation_profile_contexts(
    db: Session,
    *,
    tenant: Tenant,
) -> list[tuple[str, BrokerageLinkedAccount | None]]:
    store = _read_trade_automation_store(tenant)
    linked_accounts = db.execute(
        select(BrokerageLinkedAccount)
        .where(BrokerageLinkedAccount.tenant_id == tenant.id)
        .order_by(BrokerageLinkedAccount.created_at.asc())
    ).scalars().all()
    linked_by_key = {f"linked:{row.id}": row for row in linked_accounts}
    ordered_keys = [_AUTOMATION_PERSONAL_PAPER_PROFILE, _AUTOMATION_PERSONAL_LIVE_PROFILE]
    ordered_keys.extend(sorted(key for key in linked_by_key.keys() if key not in ordered_keys))
    ordered_keys.extend(
        key for key in sorted(store.get("profiles", {}).keys()) if key not in ordered_keys
    )
    return [(key, linked_by_key.get(key)) for key in ordered_keys]


def _owned_automation_rows(frame: pd.DataFrame, *, tenant_id: str, profile_key: str | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame
    marker = frame.get("automation_origin", pd.Series(dtype=str)).astype(str).str.strip().str.lower()
    scope = frame.get("automation_tenant_id", pd.Series(dtype=str)).astype(str).str.strip()
    matches = marker.eq(_AUTOMATION_MARKER) & scope.eq(str(tenant_id or "").strip())
    normalized_profile_key = str(profile_key or "").strip().lower()
    if normalized_profile_key and "automation_profile_key" in frame.columns:
        profile = frame["automation_profile_key"].astype(str).str.strip().str.lower()
        profile_matches = profile.eq(normalized_profile_key)
        if normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE:
            profile_matches = profile_matches | profile.eq("")
        matches = matches & profile_matches
    elif normalized_profile_key and normalized_profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
        matches = matches & False
    if not matches.any():
        return frame.iloc[0:0].copy()
    return frame.loc[matches].copy()


def _build_session_snapshot(*, flatten_before_close_minutes: int) -> dict[str, Any]:
    now_utc = _utc_now()
    context = build_market_session_context(now_utc)
    now_et = context["now_et"]
    session_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    session_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    flatten_at = session_close - timedelta(minutes=max(1, int(flatten_before_close_minutes)))
    regular_session = session_open <= now_et <= session_close
    cleanup_window = flatten_at <= now_et <= session_close
    session_mode = normalize_session_mode(context.get("session_mode") or context.get("session"))
    phase = "closed"
    if regular_session:
        if now_et < session_open + timedelta(minutes=30):
            phase = "opening_range"
        elif cleanup_window:
            phase = "close_cleanup"
        else:
            phase = "regular_session"
    elif session_mode == "pre_market":
        phase = "pre_market"
    elif session_mode == "after_hours":
        phase = "after_hours"

    minutes_to_close = None
    if now_et <= session_close:
        minutes_to_close = max(int((session_close - now_et).total_seconds() // 60), 0)

    return {
        "now_et": now_et,
        "regular_session": regular_session,
        "cleanup_window": cleanup_window,
        "phase": phase,
        "session_mode": session_mode,
        "session_label": context.get("label"),
        "extended_session": session_mode in {"pre_market", "after_hours"},
        "new_entries_allowed": session_mode in {"pre_market", "regular", "after_hours"},
        "minutes_to_close": minutes_to_close,
        "session_open": session_open,
        "session_close": session_close,
        "flatten_at": flatten_at,
    }


def _is_ticker_on_cooldown(
    state: dict[str, Any],
    ticker: str,
    *,
    instrument_type: str | None = None,
    now: datetime,
) -> bool:
    cooldown_minutes = int(state["settings"]["cooldown_minutes"])
    if cooldown_minutes <= 0:
        return False
    cooldown_key = _build_automation_target_key(ticker, instrument_type)
    value = (state["runtime"].get("ticker_cooldowns") or {}).get(cooldown_key)
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return False
    return parsed + timedelta(minutes=cooldown_minutes) > now


def _set_ticker_cooldown(
    state: dict[str, Any],
    ticker: str,
    *,
    instrument_type: str | None = None,
    now: datetime,
) -> None:
    cooldowns = dict(state["runtime"].get("ticker_cooldowns") or {})
    cutoff = now - timedelta(days=3)
    next_cooldowns: dict[str, str] = {}
    for key, value in cooldowns.items():
        parsed = _parse_iso_datetime(value)
        if parsed is None or parsed < cutoff:
            continue
        normalized_key = str(key or "").strip()
        if normalized_key:
            next_cooldowns[normalized_key] = _serialize_datetime(parsed)
    cooldown_key = _build_automation_target_key(ticker, instrument_type)
    if cooldown_key:
        next_cooldowns[cooldown_key] = _serialize_datetime(now)
    state["runtime"]["ticker_cooldowns"] = next_cooldowns


def _append_history(state: dict[str, Any], entry: dict[str, Any]) -> None:
    history = list(state.get("history") or [])
    history.insert(0, serialize_value(entry))
    state["history"] = history[:_TRADE_AUTOMATION_HISTORY_LIMIT]


def _reset_trade_automation_error_streak(state: dict[str, Any]) -> None:
    state["runtime"]["error_streak"] = 0


def _automation_business_day_bounds(now: datetime) -> tuple[datetime, datetime, str]:
    now_et = now.astimezone(_MARKET_TIMEZONE)
    day_start_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_et = day_start_et + timedelta(days=1)
    return (
        day_start_et.astimezone(timezone.utc),
        day_end_et.astimezone(timezone.utc),
        day_start_et.strftime("%Y-%m-%d"),
    )


def _estimate_trade_notional(row: dict[str, Any]) -> float:
    for key in ("total_position_cost", "projected_position_cost", "notional", "position_notional"):
        value = _coerce_trade_number(row.get(key))
        if value > 0:
            return value

    units = 0.0
    for key in ("suggested_contracts", "filled_quantity", "qty", "quantity"):
        value = _coerce_trade_number(row.get(key))
        if value > 0:
            units = value
            break

    price = 0.0
    for key in ("live_price_at_open", "current_underlying_price", "entry_price", "limit_price", "live_price", "close"):
        value = _coerce_trade_number(row.get(key))
        if value > 0:
            price = value
            break
    return float(units * price) if units > 0 and price > 0 else 0.0


def _count_trade_entries_today(
    state: dict[str, Any],
    *,
    start_at: datetime,
    end_at: datetime,
) -> tuple[int, dict[str, int], dict[str, int]]:
    entries_total = 0
    entries_by_ticker: dict[str, int] = {}
    entries_by_target: dict[str, int] = {}
    for item in list(state.get("history") or []):
        if str(item.get("type") or "").strip().lower() != "open_trade":
            continue
        parsed_at = _parse_iso_datetime(item.get("at"))
        if parsed_at is None or parsed_at < start_at or parsed_at >= end_at:
            continue
        entries_total += 1
        ticker = str(item.get("ticker") or "").strip().upper()
        instrument_type = _normalize_automation_instrument(item.get("instrument_type"), default="equity")
        if ticker:
            entries_by_ticker[ticker] = int(entries_by_ticker.get(ticker) or 0) + 1
            target_key = _build_automation_target_key(ticker, instrument_type)
            entries_by_target[target_key] = int(entries_by_target.get(target_key) or 0) + 1
    return entries_total, entries_by_ticker, entries_by_target


def _build_trade_automation_guardrail_snapshot(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    owned_open: pd.DataFrame,
    owned_pending: pd.DataFrame,
    effective_funds: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    settings_state = state["settings"]
    runtime_state = state["runtime"]
    owned_closed = _owned_automation_rows(sdm.read_closed_trades(), tenant_id=tenant.id, profile_key=profile_key)

    day_start_utc, day_end_utc, session_day = _automation_business_day_bounds(now)

    today_realized_pnl = 0.0
    today_closed_count = 0
    consecutive_losses = 0
    if not owned_closed.empty:
        closed_rows = owned_closed.copy()
        closed_rows["__closed_at"] = pd.to_datetime(closed_rows.get("closed_at"), errors="coerce", utc=True)
        pnl_series = pd.to_numeric(closed_rows.get("realized_pnl"), errors="coerce").fillna(0.0)
        closed_rows["__pnl"] = pnl_series

        today_mask = (
            closed_rows["__closed_at"].notna()
            & (closed_rows["__closed_at"] >= pd.Timestamp(day_start_utc))
            & (closed_rows["__closed_at"] < pd.Timestamp(day_end_utc))
        )
        today_rows = closed_rows.loc[today_mask].copy()
        today_closed_count = int(len(today_rows))
        today_realized_pnl = float(pd.to_numeric(today_rows.get("__pnl"), errors="coerce").fillna(0.0).sum())

        recent_closed = today_rows.sort_values("__closed_at", ascending=False, na_position="last")
        for pnl in pd.to_numeric(recent_closed.get("__pnl"), errors="coerce").fillna(0.0).tolist():
            if pnl < 0:
                consecutive_losses += 1
                continue
            if pnl > 0:
                break

    entries_today, entries_by_ticker, entries_by_target = _count_trade_entries_today(
        state,
        start_at=day_start_utc,
        end_at=day_end_utc,
    )
    open_notional = 0.0
    for frame in (owned_open, owned_pending):
        if frame.empty:
            continue
        for row in frame.to_dict(orient="records"):
            open_notional += _estimate_trade_notional(row)

    sizing_funds = float(effective_funds) if effective_funds is not None and effective_funds > 0 else float(settings_state["account_size"])
    risk_unit_dollars = float(sizing_funds) * (float(settings_state["risk_percent"]) / 100.0)
    max_daily_loss_dollars = float(settings_state["max_daily_loss_r"]) * risk_unit_dollars if risk_unit_dollars > 0 else 0.0
    error_streak = int(runtime_state.get("error_streak") or 0)

    status_tone = "positive"
    status_label = "Within limits"
    status_detail = "Automation-owned capital and worker guardrails are inside the current limits."
    status_reason = None

    if today_realized_pnl <= (-1.0 * max_daily_loss_dollars):
        status_tone = "negative"
        status_label = "Daily loss lock"
        status_reason = "daily_loss_lock"
        status_detail = (
            f"Automation realized {today_realized_pnl:,.2f} on {session_day}, beyond the "
            f"-${max_daily_loss_dollars:,.2f} daily loss lock."
        )
    elif consecutive_losses >= int(settings_state["max_consecutive_losses"]):
        status_tone = "negative"
        status_label = "Loss streak lock"
        status_reason = "loss_streak_lock"
        status_detail = (
            f"Automation has {consecutive_losses} consecutive losing closes, which meets the "
            f"{int(settings_state['max_consecutive_losses'])}-loss stop."
        )
    elif entries_today >= int(settings_state["max_daily_entries"]):
        status_tone = "negative"
        status_label = "Daily entry cap"
        status_reason = "daily_entry_cap"
        status_detail = (
            f"Automation already opened {entries_today} entries on {session_day}, which meets the "
            f"{int(settings_state['max_daily_entries'])}-entry cap."
        )
    elif open_notional >= float(settings_state["max_total_open_notional"]):
        status_tone = "negative"
        status_label = "Open notional cap"
        status_reason = "open_notional_cap"
        status_detail = (
            f"Automation is already carrying ${open_notional:,.2f} in open or working notional, above the "
            f"${float(settings_state['max_total_open_notional']):,.2f} cap."
        )
    elif error_streak >= int(settings_state["max_error_streak"]):
        status_tone = "negative"
        status_label = "Worker error lock"
        status_reason = "error_streak_lock"
        status_detail = (
            f"The worker has hit {error_streak} consecutive cycle error{'s' if error_streak != 1 else ''}, "
            f"which meets the auto-stop threshold."
        )
    elif today_realized_pnl < 0 or error_streak > 0:
        status_tone = "warning"
        status_label = "Near lock"
        status_detail = "Automation is still running, but realized loss or worker instability is moving toward the configured stop."

    cards = [
        {
            "key": "daily_loss",
            "label": "Daily realized PnL",
            "value": f"${today_realized_pnl:,.2f}",
            "helper": f"Lock at -${max_daily_loss_dollars:,.2f}",
            "tone": "negative" if today_realized_pnl <= (-1.0 * max_daily_loss_dollars) else ("warning" if today_realized_pnl < 0 else "positive"),
        },
        {
            "key": "loss_streak",
            "label": "Loss streak",
            "value": str(consecutive_losses),
            "helper": f"Stop after {int(settings_state['max_consecutive_losses'])} losing closes",
            "tone": "negative" if consecutive_losses >= int(settings_state["max_consecutive_losses"]) else ("warning" if consecutive_losses > 0 else "positive"),
        },
        {
            "key": "daily_entries",
            "label": "Entries today",
            "value": str(entries_today),
            "helper": f"Cap {int(settings_state['max_daily_entries'])} | {today_closed_count} closes",
            "tone": "negative" if entries_today >= int(settings_state["max_daily_entries"]) else ("warning" if entries_today > 0 else "positive"),
        },
        {
            "key": "open_notional",
            "label": "Open notional",
            "value": f"${open_notional:,.2f}",
            "helper": f"Cap ${float(settings_state['max_total_open_notional']):,.2f}",
            "tone": "negative" if open_notional >= float(settings_state["max_total_open_notional"]) else ("warning" if open_notional > 0 else "positive"),
        },
        {
            "key": "error_streak",
            "label": "Cycle error streak",
            "value": str(error_streak),
            "helper": f"Auto-stop at {int(settings_state['max_error_streak'])}",
            "tone": "negative" if error_streak >= int(settings_state["max_error_streak"]) else ("warning" if error_streak > 0 else "positive"),
        },
    ]

    return {
        "status": {
            "tone": status_tone,
            "label": status_label,
            "detail": status_detail,
            "locked": bool(status_reason),
            "reason": status_reason,
        },
        "metrics": {
            "session_day": session_day,
            "today_realized_pnl": today_realized_pnl,
            "max_daily_loss_dollars": max_daily_loss_dollars,
            "risk_unit_dollars": risk_unit_dollars,
            "consecutive_losses": consecutive_losses,
            "entries_today": entries_today,
            "today_closed_count": today_closed_count,
            "open_notional": open_notional,
            "max_total_open_notional": float(settings_state["max_total_open_notional"]),
            "max_daily_entries": int(settings_state["max_daily_entries"]),
            "max_daily_entries_per_symbol": int(settings_state["max_daily_entries_per_symbol"]),
            "max_consecutive_losses": int(settings_state["max_consecutive_losses"]),
            "error_streak": error_streak,
            "max_error_streak": int(settings_state["max_error_streak"]),
        },
        "cards": serialize_value(cards),
        "entries_by_ticker": serialize_value(entries_by_ticker),
        "entries_by_target": serialize_value(entries_by_target),
    }


def _resolve_status(state: dict[str, Any]) -> tuple[str, str, str]:
    settings_state = state["settings"]
    runtime_state = state["runtime"]
    if settings_state["kill_switch"]:
        return "killed", "Kill switch active", "Automation is hard-stopped and will not place new orders."
    if not settings_state["enabled"]:
        return "disabled", "Disabled", "Automation settings are saved but the cycle is turned off."
    if not settings_state["armed"]:
        return "configured", "Configured", "Automation is configured but not armed for unattended trading."
    next_run_at = _parse_iso_datetime(runtime_state.get("next_run_at"))
    if next_run_at is not None and next_run_at > _utc_now():
        return "scheduled", "Armed", "Automation is armed and waiting for the next cycle window."
    return "active", "Armed", "Automation is armed and eligible to trade on the next worker cycle."


def _build_linked_client_automation_snapshot(
    *,
    db: Session | None,
    tenant: Tenant,
    runtime_state: dict[str, Any],
) -> dict[str, Any]:
    if db is None:
        return {
            "eligible_linked_account_count": 0,
            "automated_linked_account_count": 0,
            "blocked_linked_account_count": 0,
            "last_automated_client_order": None,
            "block_reasons_by_account": {},
            "items": [],
            "last_cycle": serialize_value(runtime_state.get("linked_client_automation") or {}),
        }
    summary = build_linked_client_automation_summary(
        db=db,
        current_user=_build_system_current_user(tenant, None),
    )
    cycle = dict(runtime_state.get("linked_client_automation") or {})
    if cycle.get("last_automated_client_order") is not None:
        summary["last_automated_client_order"] = serialize_value(cycle.get("last_automated_client_order"))
    if cycle.get("block_reasons_by_account"):
        summary["block_reasons_by_account"] = serialize_value(cycle.get("block_reasons_by_account"))
    summary["last_cycle"] = serialize_value(cycle)
    return summary


def _format_automation_event_label(event_key: str, status: str) -> str:
    normalized_key = str(event_key or "").strip().lower()
    normalized_status = str(status or "").strip().lower()
    labels = {
        "order.submitted": "Submitted",
        "order.accepted": "Accepted",
        "order.filled": "Filled",
        "order.partially_closed": "Trimmed",
        "order.closed": "Closed",
        "order.canceled": "Canceled",
        "order.rejected": "Rejected",
    }
    if normalized_key in labels:
        return labels[normalized_key]
    if normalized_status:
        return normalized_status.replace("_", " ").title()
    return "Recorded"


def _serialize_automation_order_event(row: OrderEventRecord) -> dict[str, Any]:
    payload = dict(row.payload_json or {})
    slippage_value = pd.to_numeric(payload.get("slippage_bps"), errors="coerce")
    if pd.isna(slippage_value):
        synced = dict(payload.get("synced_order") or {})
        expected = pd.to_numeric(
            synced.get("expected_fill_price", synced.get("live_price_at_submit")),
            errors="coerce",
        )
        actual = pd.to_numeric(
            synced.get("actual_fill_price", synced.get("broker_filled_avg_price")),
            errors="coerce",
        )
        if pd.notna(expected) and float(expected) > 0 and pd.notna(actual):
            slippage_value = float(((float(actual) - float(expected)) / float(expected)) * 10000.0)
    return {
        "id": row.id,
        "trade_id": row.trade_id,
        "ticker": row.ticker,
        "event_key": row.event_key,
        "status": row.status,
        "label": _format_automation_event_label(row.event_key, row.status),
        "detail": row.detail,
        "slippage_bps": float(slippage_value) if pd.notna(slippage_value) else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _coerce_candidate_price(candidate: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = pd.to_numeric(candidate.get(key), errors="coerce")
        if pd.notna(value):
            normalized = float(value)
            if normalized > 0:
                return normalized
    return None


def _normalize_option_right(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"call", "c", "calls"}:
        return "call"
    if normalized in {"put", "p", "puts"}:
        return "put"
    return ""


def _option_contract_number(contract: dict[str, Any] | None, key: str) -> float | None:
    if not contract:
        return None
    value = pd.to_numeric(contract.get(key), errors="coerce")
    if pd.isna(value):
        return None
    return float(value)


def _option_quote_age_seconds(contract: dict[str, Any] | None, *, now: datetime | None = None) -> float | None:
    if not contract:
        return None
    raw_timestamp = contract.get("quote_timestamp")
    if raw_timestamp in (None, "", "nan", "None"):
        return None
    try:
        timestamp = pd.Timestamp(raw_timestamp)
    except Exception:
        return None
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    reference = pd.Timestamp(now or _utc_now())
    if reference.tzinfo is None:
        reference = reference.tz_localize("UTC")
    else:
        reference = reference.tz_convert("UTC")
    return max(0.0, round(float((reference - timestamp).total_seconds()), 1))


def _score_option_contract_for_automation(contract: dict[str, Any] | None, *, now: datetime | None = None) -> float:
    if not contract:
        return 0.0
    spread_pct = _option_contract_number(contract, "spread_pct")
    quote_age = _option_quote_age_seconds(contract, now=now)
    volume = _option_contract_number(contract, "volume") or 0.0
    open_interest = _option_contract_number(contract, "open_interest") or 0.0
    score = 100.0
    if spread_pct is None:
        score -= 35.0
    else:
        score -= min(35.0, max(0.0, spread_pct / _AUTOMATION_OPTION_MAX_SPREAD_PCT) * 22.0)
    if quote_age is None:
        score -= 30.0
    else:
        score -= min(30.0, max(0.0, quote_age / _AUTOMATION_OPTION_MAX_QUOTE_AGE_SECONDS) * 20.0)
    if volume < _AUTOMATION_OPTION_MIN_VOLUME:
        score -= min(20.0, (_AUTOMATION_OPTION_MIN_VOLUME - volume) / max(_AUTOMATION_OPTION_MIN_VOLUME, 1) * 20.0)
    if open_interest < _AUTOMATION_OPTION_MIN_OPEN_INTEREST:
        score -= min(
            20.0,
            (_AUTOMATION_OPTION_MIN_OPEN_INTEREST - open_interest) / max(_AUTOMATION_OPTION_MIN_OPEN_INTEREST, 1) * 20.0,
        )
    return round(max(0.0, min(100.0, score)), 2)


def _validate_option_contract_for_automation(
    contract: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> tuple[bool, str | None, str]:
    if not contract or not str(contract.get("contract_symbol") or "").strip():
        return False, "missing_option_contract", "No real option contract was available for this automation candidate."
    mid = _option_contract_number(contract, "mid")
    bid = _option_contract_number(contract, "bid")
    ask = _option_contract_number(contract, "ask")
    if mid is None or mid <= 0 or bid is None or ask is None or bid < 0 or ask <= 0:
        return False, "option_quote_missing", "The selected option contract is missing an executable bid/ask quote."
    spread_pct = _option_contract_number(contract, "spread_pct")
    if spread_pct is None:
        return False, "option_quote_missing", "The selected option contract is missing a computable spread."
    if spread_pct > _AUTOMATION_OPTION_MAX_SPREAD_PCT:
        return False, "option_spread_too_wide", "The selected option contract spread is wider than the automation limit."
    quote_age = _option_quote_age_seconds(contract, now=now)
    if quote_age is None or quote_age > _AUTOMATION_OPTION_MAX_QUOTE_AGE_SECONDS:
        return False, "stale_option_quote", "The selected option contract quote is stale or missing a quote timestamp."
    volume = _option_contract_number(contract, "volume") or 0.0
    if volume < _AUTOMATION_OPTION_MIN_VOLUME:
        return False, "option_liquidity_too_low", "The selected option contract volume is below the automation liquidity floor."
    open_interest = _option_contract_number(contract, "open_interest") or 0.0
    if open_interest < _AUTOMATION_OPTION_MIN_OPEN_INTEREST:
        return False, "option_open_interest_too_low", "The selected option contract open interest is below the automation floor."
    return True, None, "The selected option contract quote is fresh, liquid, and inside spread limits."


def _option_refresh_diagnostics(
    *,
    status: str,
    source: str,
    contract: dict[str, Any] | None,
    option_right: str,
    reason: str | None,
    detail: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    contract = dict(contract or {})
    return {
        "option_scan_status": status,
        "option_block_reason": reason,
        "detail": detail,
        "selected_contract": str(contract.get("contract_symbol") or "").strip().upper() or None,
        "option_right": option_right or None,
        "quote_source": source,
        "option_contract_score": _score_option_contract_for_automation(contract, now=now),
        "option_quote_age_seconds": _option_quote_age_seconds(contract, now=now),
        "option_spread_pct": _option_contract_number(contract, "spread_pct"),
        "bid": _option_contract_number(contract, "bid"),
        "ask": _option_contract_number(contract, "ask"),
        "mid": _option_contract_number(contract, "mid"),
        "volume": int(_option_contract_number(contract, "volume") or 0),
        "open_interest": int(_option_contract_number(contract, "open_interest") or 0),
        "last_option_refresh_at": _serialize_datetime(now or _utc_now()),
    }


def _merge_option_contract_into_candidate(candidate: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    updated = dict(candidate)
    updated.update(
        {
            "contract_symbol": str(contract.get("contract_symbol") or "").strip().upper(),
            "contract_expiration": contract.get("expiration"),
            "contract_strike": contract.get("strike"),
            "contract_mid": contract.get("mid"),
            "estimated_cost_per_contract": (
                float(contract.get("mid")) * 100.0
                if pd.notna(pd.to_numeric(contract.get("mid"), errors="coerce"))
                else candidate.get("estimated_cost_per_contract")
            ),
            "option_bid": contract.get("bid"),
            "option_ask": contract.get("ask"),
            "option_spread_pct": contract.get("spread_pct"),
            "option_quote_timestamp": contract.get("quote_timestamp"),
            "option_volume": contract.get("volume"),
            "option_open_interest": contract.get("open_interest"),
        }
    )
    return updated


def _resolve_candidate_option_right(candidate: dict[str, Any], option_right: str | None = None) -> str:
    resolved = _normalize_option_right(option_right)
    if resolved:
        return resolved
    resolved = _normalize_option_right(candidate.get("option_right") or candidate.get("direction"))
    if resolved:
        return resolved
    verdict = str(candidate.get("verdict") or "").strip().upper()
    if verdict == "BULLISH":
        return "call"
    if verdict == "BEARISH":
        return "put"
    return ""


def _refresh_automation_option_candidate(
    candidate: dict[str, Any],
    *,
    option_right: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    ticker = str(candidate.get("ticker") or "").strip().upper()
    resolved_right = _resolve_candidate_option_right(candidate, option_right)
    contract_symbol = str(candidate.get("contract_symbol") or "").strip().upper()
    expiration = str(candidate.get("contract_expiration") or candidate.get("expiration") or "").strip() or None
    exact_contract = None
    if ticker and contract_symbol:
        exact_contract = sdm.get_contract_quote_from_chain(
            ticker,
            contract_symbol,
            option_side=resolved_right,
            expiration=expiration,
        )
    allowed, reason, detail = _validate_option_contract_for_automation(exact_contract, now=now)
    if allowed:
        updated = _merge_option_contract_into_candidate(candidate, dict(exact_contract or {}))
        diagnostics = _option_refresh_diagnostics(
            status="fresh",
            source="exact_chain",
            contract=dict(exact_contract or {}),
            option_right=resolved_right,
            reason=None,
            detail=detail,
            now=now,
        )
        updated["option_execution"] = diagnostics
        return {
            "allowed": True,
            "candidate": updated,
            "option_right": resolved_right,
            "reason": None,
            "detail": detail,
            "diagnostics": diagnostics,
        }

    close_price = _coerce_candidate_price(candidate, "live_price", "close", "last_price") or 0.0
    option_plan = dict(candidate.get("option_plan") or {})
    dte_label = str(candidate.get("days_to_expiration") or option_plan.get("days_to_expiration") or "7-14 DTE")
    fallback_contract = None
    if ticker and resolved_right in {"call", "put"} and close_price > 0:
        fallback_contract = sdm.get_recommended_contract(ticker, resolved_right.upper(), close_price, dte_label)
    fallback_allowed, fallback_reason, fallback_detail = _validate_option_contract_for_automation(fallback_contract, now=now)
    if fallback_allowed:
        updated = _merge_option_contract_into_candidate(candidate, dict(fallback_contract or {}))
        diagnostics = _option_refresh_diagnostics(
            status="replaced",
            source="recommended_chain",
            contract=dict(fallback_contract or {}),
            option_right=resolved_right,
            reason=None,
            detail="The originally selected option failed refresh, so automation replaced it with the best fresh equivalent.",
            now=now,
        )
        updated["option_execution"] = diagnostics
        return {
            "allowed": True,
            "candidate": updated,
            "option_right": resolved_right,
            "reason": None,
            "detail": diagnostics["detail"],
            "diagnostics": diagnostics,
        }

    blocked_contract = exact_contract or fallback_contract
    blocked_reason = reason or fallback_reason or "option_quote_missing"
    blocked_detail = detail if exact_contract is not None else fallback_detail
    diagnostics = _option_refresh_diagnostics(
        status="blocked",
        source="exact_chain" if exact_contract is not None else "recommended_chain",
        contract=dict(blocked_contract or {}),
        option_right=resolved_right,
        reason=blocked_reason,
        detail=blocked_detail,
        now=now,
    )
    return {
        "allowed": False,
        "candidate": candidate,
        "option_right": resolved_right,
        "reason": blocked_reason,
        "detail": blocked_detail,
        "diagnostics": diagnostics,
    }


def _refresh_option_quote_for_close(row: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or _utc_now()
    ticker = str(row.get("ticker") or "").strip().upper()
    contract_symbol = str(row.get("contract_symbol") or "").strip().upper()
    option_right = _resolve_candidate_option_right(row, str(row.get("option_right") or row.get("direction") or ""))
    expiration = str(row.get("contract_expiration") or row.get("expiration") or "").strip() or None
    contract = None
    if ticker and contract_symbol:
        contract = sdm.get_contract_quote_from_chain(ticker, contract_symbol, option_side=option_right, expiration=expiration)
    allowed, reason, detail = _validate_option_contract_for_automation(contract, now=now)
    diagnostics = _option_refresh_diagnostics(
        status="fresh" if allowed else "blocked",
        source="exact_chain",
        contract=dict(contract or {}),
        option_right=option_right,
        reason=reason,
        detail=detail,
        now=now,
    )
    return {
        "allowed": allowed,
        "contract": dict(contract or {}),
        "close_contract_mid": _option_contract_number(contract, "mid") if allowed else None,
        "reason": reason,
        "detail": detail,
        "diagnostics": diagnostics,
    }


def _build_scheduled_option_diagnostics(
    candidate: dict[str, Any],
    *,
    detail: str,
    status: str = "fresh",
    reason: str | None = None,
    refreshed_at: str | None = None,
) -> dict[str, Any]:
    return {
        "option_scan_status": status,
        "option_block_reason": reason,
        "detail": detail,
        "selected_contract": str(candidate.get("contract_symbol") or "").strip().upper() or None,
        "option_right": str(candidate.get("option_right") or "").strip().lower() or None,
        "quote_source": str(candidate.get("quote_source") or candidate.get("source") or "alpaca_options_chain").strip() or "alpaca_options_chain",
        "option_contract_score": candidate.get("option_contract_score"),
        "option_quote_age_seconds": candidate.get("quote_age_seconds"),
        "option_spread_pct": candidate.get("spread_pct"),
        "bid": candidate.get("contract_bid"),
        "ask": candidate.get("contract_ask"),
        "mid": candidate.get("contract_mid"),
        "volume": int(pd.to_numeric(candidate.get("contract_volume"), errors="coerce") or 0),
        "open_interest": int(pd.to_numeric(candidate.get("contract_open_interest"), errors="coerce") or 0),
        "last_option_refresh_at": refreshed_at or _serialize_datetime(_utc_now()),
    }


def _build_scheduled_option_candidate(
    scan_candidate: dict[str, Any],
    *,
    watchlist_row: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    source_row = dict(watchlist_row or {})
    candidate = serialize_value(source_row)
    ticker = str(scan_candidate.get("underlying") or source_row.get("ticker") or "").strip().upper()
    option_right = _normalize_option_right(scan_candidate.get("right"))
    underlying_price = _coerce_candidate_price(scan_candidate, "underlying_price") or _coerce_candidate_price(
        source_row,
        "live_price",
        "close",
        "last_price",
    )
    premium_notional = pd.to_numeric(scan_candidate.get("premium_notional"), errors="coerce")
    premium_notional_value = float(premium_notional) if pd.notna(premium_notional) and float(premium_notional) > 0 else None
    contract_mid = pd.to_numeric(scan_candidate.get("mid"), errors="coerce")
    contract_mid_value = float(contract_mid) if pd.notna(contract_mid) and float(contract_mid) > 0 else None
    contract_volume = int(pd.to_numeric(scan_candidate.get("volume"), errors="coerce") or 0)
    option_dollar_volume = (
        float(contract_volume) * float(contract_mid_value) * 100.0
        if contract_mid_value is not None and contract_volume > 0
        else None
    )
    selection_score = pd.to_numeric(scan_candidate.get("selection_score"), errors="coerce")
    option_contract_score = (
        round(max(0.0, 100.0 - min(float(selection_score) * 100.0, 100.0)), 2)
        if pd.notna(selection_score)
        else None
    )
    verdict = str(source_row.get("verdict") or "").strip().upper()
    if not verdict:
        verdict = "BULLISH" if option_right == "call" else "BEARISH" if option_right == "put" else ""
    candidate.update(
        {
            "ticker": ticker,
            "live_price": underlying_price,
            "close": underlying_price,
            "last_price": underlying_price,
            "verdict": verdict or None,
            "direction": option_right,
            "option_right": option_right,
            "instrument_type": "listed_option",
            "automation_instrument_type": "listed_option",
            "auto_entry_eligible": True,
            "contract_symbol": str(scan_candidate.get("contract_symbol") or "").strip().upper() or None,
            "contract_expiration": str(scan_candidate.get("expiration") or "").strip() or None,
            "contract_strike": pd.to_numeric(scan_candidate.get("strike"), errors="coerce"),
            "contract_bid": scan_candidate.get("bid"),
            "contract_ask": scan_candidate.get("ask"),
            "contract_mid": contract_mid_value,
            "current_contract_mid": contract_mid_value,
            "entry_limit_price": pd.to_numeric(scan_candidate.get("entry_limit_price"), errors="coerce"),
            "contract_spread_pct": scan_candidate.get("spread_pct"),
            "contract_quote_timestamp": str(scan_candidate.get("quote_timestamp") or "").strip() or None,
            "contract_volume": contract_volume,
            "contract_open_interest": int(pd.to_numeric(scan_candidate.get("open_interest"), errors="coerce") or 0),
            "spread_pct": scan_candidate.get("spread_pct"),
            "volume": contract_volume,
            "open_interest": int(pd.to_numeric(scan_candidate.get("open_interest"), errors="coerce") or 0),
            "average_dollar_volume": option_dollar_volume,
            "intraday_dollar_volume": option_dollar_volume,
            "projected_position_cost": premium_notional_value,
            "position_cost": premium_notional_value,
            "total_position_cost": premium_notional_value,
            "suggested_contracts": 1.0,
            "estimated_cost_per_contract": premium_notional_value,
            "premium_notional": premium_notional_value,
            "quote_age_seconds": scan_candidate.get("quote_age_seconds"),
            "quote_source": str(scan_candidate.get("source") or "alpaca_options_chain").strip() or "alpaca_options_chain",
            "option_contract_score": option_contract_score,
            "automation_entry_reason": str(source_row.get("automation_entry_reason") or "scheduled_options_scan").strip() or "scheduled_options_scan",
            "source": "options_automation_scheduled",
            "scan_run_source": "options_automation",
        }
    )
    diagnostics = _build_scheduled_option_diagnostics(
        candidate,
        detail="Scheduled options automation is using the exact scanned contract and current quote snapshot.",
        status="fresh",
        refreshed_at=_serialize_datetime(now),
    )
    candidate["option_execution"] = diagnostics
    return serialize_value(candidate)


def _build_scheduled_option_path_evaluation(scan_snapshot: dict[str, Any]) -> dict[str, Any]:
    candidates = list(scan_snapshot.get("candidates") or [])
    ready_candidate = next((item for item in candidates if bool(item.get("ready_to_execute"))), None)
    lead_candidate = ready_candidate or (candidates[0] if candidates else {})
    blocked_reason = str(scan_snapshot.get("blocked_reason") or "").strip()
    status = (
        "eligible"
        if ready_candidate
        else "blocked"
        if str(scan_snapshot.get("status") or "").strip().lower() == "blocked"
        else "idle"
    )
    detail = (
        "Scheduled options scan found a contract that passed quote freshness, spread, liquidity, and premium risk gates."
        if ready_candidate
        else blocked_reason
        or "No listed option contract passed the scheduled automation gates on this cycle."
    )
    return {
        "instrument_type": "listed_option",
        "status": status,
        "ticker": str((lead_candidate or {}).get("underlying") or "").strip().upper() or None,
        "vehicle_recommendation": "listed_option",
        "detail": detail,
        "execution_score": float(pd.to_numeric((lead_candidate or {}).get("selection_score"), errors="coerce") or 0.0),
    }


def _record_scheduled_options_event(
    db: Session,
    *,
    tenant: Tenant,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str | None,
    payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id or "").strip() or None,
        payload={**serialize_value(payload or {}), "automation_trigger": "scheduled"},
        metadata={"automation_trigger": "scheduled", **serialize_value(metadata or {})},
    )


def _run_scheduled_options_scan(
    db: Session,
    *,
    current_user: Any,
    tickers: list[str],
) -> dict[str, Any]:
    from backend.services import options_automation_service

    request = OptionsAutomationScanRequest(
        tickers=list(tickers or []),
        limit=max(1, int(settings.options_scan_candidate_limit or 30)),
        automation_trigger="scheduled",
    )
    return options_automation_service.run_options_automation_scan(
        db,
        current_user=current_user,
        request=request,
    )


def _run_scheduled_options_refresh(
    db: Session,
    *,
    current_user: Any,
) -> dict[str, Any]:
    from backend.services import options_automation_service

    return options_automation_service.refresh_options_positions(
        db,
        current_user=current_user,
        request=OptionsAutomationRefreshRequest(automation_trigger="scheduled"),
    )


def _sync_scheduled_options_runtime(
    runtime_state: dict[str, Any],
    *,
    cycle_at: datetime | None = None,
    scan_snapshot: dict[str, Any] | None = None,
    refresh_snapshot: dict[str, Any] | None = None,
    blocker: str | None = None,
    last_entry: dict[str, Any] | None = None,
    last_exit: dict[str, Any] | None = None,
) -> None:
    if cycle_at is not None:
        runtime_state["last_options_cycle_at"] = _serialize_datetime(cycle_at)
    if scan_snapshot is not None:
        runtime_state["last_options_scan_status"] = str(scan_snapshot.get("status") or "").strip().lower() or None
        if scan_snapshot.get("blocked_reason"):
            runtime_state["last_options_blocker"] = str(scan_snapshot.get("blocked_reason") or "").strip() or None
        elif str(scan_snapshot.get("status") or "").strip().lower() in {"completed", "ready"}:
            runtime_state["last_options_blocker"] = None
    if refresh_snapshot is not None:
        runtime_state["open_option_position_count"] = int(
            refresh_snapshot.get("refreshed_count")
            if refresh_snapshot.get("refreshed_count") is not None
            else refresh_snapshot.get("open_position_count")
            or 0
        )
        runtime_state["sell_ready_option_count"] = int(refresh_snapshot.get("sell_ready_count") or 0)
    if blocker is not None:
        runtime_state["last_options_blocker"] = str(blocker).strip() or None
    if last_entry is not None:
        runtime_state["last_option_entry"] = serialize_value(last_entry)
        runtime_state["last_options_blocker"] = None
    if last_exit is not None:
        runtime_state["last_option_exit"] = serialize_value(last_exit)
        runtime_state["last_options_blocker"] = None


def _build_automation_order_fields(
    *,
    candidate: dict[str, Any],
    settings_state: dict[str, Any],
    instrument_type: str | None = None,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    order_type = str(settings_state.get("order_type") or "market").strip().lower()
    if order_type not in _AUTOMATION_ORDER_CHOICES:
        raise ValidationError("Automation currently supports market and limit orders only.")

    normalized_instrument = _normalize_automation_instrument(
        instrument_type or settings_state.get("instrument_type"),
        default="equity",
    )
    if normalized_instrument == "listed_option":
        order_type = "limit"
    option_plan = dict((report or {}).get("option_plan") or {})
    recommended_contract = dict(option_plan.get("recommended_contract") or {})

    fields: dict[str, Any] = {
        "order_type": order_type,
        "time_in_force": ("day" if normalized_instrument == "listed_option" else str(settings_state["time_in_force"])),
    }
    if order_type == "limit":
        limit_reference = None
        if normalized_instrument == "listed_option":
            limit_reference = _coerce_candidate_price(candidate, "entry_limit_price", "contract_mid", "current_contract_mid")
            contract_symbol = str(candidate.get("contract_symbol") or recommended_contract.get("contract_symbol") or "").strip()
            if limit_reference is None:
                recommended_mid = pd.to_numeric(recommended_contract.get("mid"), errors="coerce")
                if pd.notna(recommended_mid) and float(recommended_mid) > 0:
                    limit_reference = float(recommended_mid)
            if contract_symbol:
                if limit_reference is None:
                    contract_mid = pd.to_numeric(sdm.get_contract_mid_from_symbol(contract_symbol), errors="coerce")
                    if pd.notna(contract_mid) and float(contract_mid) > 0:
                        limit_reference = float(contract_mid)
        if limit_reference is None:
            limit_reference = _coerce_candidate_price(candidate, "live_price", "close", "last_price")
        if limit_reference is None:
            raise ValidationError("Automation needs a current reference price before routing a limit order.")
        fields["limit_price"] = round(float(limit_reference), 4)
    return fields


def _build_trade_automation_performance_snapshot(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str = _AUTOMATION_PERSONAL_PAPER_PROFILE,
    owned_open: pd.DataFrame,
    owned_pending: pd.DataFrame,
) -> dict[str, Any]:
    owned_closed = _owned_automation_rows(sdm.read_closed_trades(), tenant_id=tenant.id, profile_key=profile_key)
    closed_count = int(len(owned_closed))
    analytics = sdm.performance_analytics(owned_closed)
    runtime_state = state["runtime"]
    cycle_count = int(runtime_state.get("cycle_count") or 0)
    success_count = int(runtime_state.get("success_count") or 0)
    error_count = int(runtime_state.get("error_count") or 0)
    rejection_count = int(runtime_state.get("rejection_count") or 0)
    error_rate = (error_count / cycle_count) if cycle_count > 0 else None
    stand_down_rate = (rejection_count / cycle_count) if cycle_count > 0 else None

    trade_ids: set[str] = set()
    for frame in (owned_open, owned_pending, owned_closed):
        if frame.empty or "trade_id" not in frame.columns:
            continue
        for value in frame["trade_id"].astype(str).str.strip():
            if value:
                trade_ids.add(value)

    recent_events: list[dict[str, Any]] = []
    if db is not None:
        rows = (
            db.execute(
                select(OrderEventRecord)
                .where(OrderEventRecord.tenant_id == tenant.id)
                .order_by(OrderEventRecord.created_at.desc())
                .limit(250)
            )
            .scalars()
            .all()
        )
        for row in rows:
            payload = dict(row.payload_json or {})
            payload_trade = dict(payload.get("trade") or {})
            if (
                (row.trade_id and row.trade_id in trade_ids)
                or str(payload.get("automation_cycle_id") or "").strip()
                or str(payload_trade.get("automation_origin") or "").strip().lower() == _AUTOMATION_MARKER
            ):
                recent_events.append(_serialize_automation_order_event(row))
        recent_events = recent_events[:12]

    slippage_values = [abs(float(item["slippage_bps"])) for item in recent_events if item.get("slippage_bps") is not None]
    average_abs_slippage_bps = float(sum(slippage_values) / len(slippage_values)) if slippage_values else None
    worst_abs_slippage_bps = max(slippage_values) if slippage_values else None
    partial_exit_count = sum(1 for item in recent_events if item.get("event_key") == "order.partially_closed")
    closed_exit_count = sum(1 for item in recent_events if item.get("event_key") == "order.closed")
    rejected_event_count = sum(1 for item in recent_events if item.get("status") == "rejected")

    recent_closed: list[dict[str, Any]] = []
    if not owned_closed.empty:
        closed_rows = owned_closed.copy()
        closed_rows["__closed_at"] = pd.to_datetime(closed_rows.get("closed_at"), errors="coerce", utc=True)
        closed_rows = closed_rows.sort_values("__closed_at", ascending=False, na_position="last")
        for row in closed_rows.head(6).to_dict(orient="records"):
            recent_closed.append(
                {
                    "trade_id": str(row.get("trade_id") or "").strip() or None,
                    "ticker": str(row.get("ticker") or "").strip().upper() or "UNKNOWN",
                    "closed_at": str(row.get("closed_at") or "").strip() or None,
                    "status": str(row.get("status") or "").strip().upper() or "CLOSED",
                    "realized_pnl": _coerce_trade_number(row.get("realized_pnl")),
                    "close_fraction": _coerce_trade_number(row.get("close_fraction")) or 1.0,
                    "execution_intent": str(row.get("automation_execution_intent") or "").strip().lower() or None,
                }
            )

    if closed_count == 0:
        outcome_tone = "warning"
        outcome_helper = "No closed automation trades yet"
    elif analytics["expectancy"] > 0:
        outcome_tone = "positive"
        outcome_helper = f"{analytics['win_rate'] * 100:.0f}% win rate | ${analytics['expectancy']:.2f} expectancy"
    elif closed_count < 3:
        outcome_tone = "warning"
        outcome_helper = f"{analytics['win_rate'] * 100:.0f}% win rate | Early sample"
    else:
        outcome_tone = "negative"
        outcome_helper = f"{analytics['win_rate'] * 100:.0f}% win rate | ${analytics['expectancy']:.2f} expectancy"

    if len(slippage_values) < 2:
        drift_tone = "warning"
        drift_helper = f"{len(slippage_values)} fill sample{'s' if len(slippage_values) != 1 else ''} | Need more drift history"
    elif average_abs_slippage_bps is not None and average_abs_slippage_bps <= 12.0 and (worst_abs_slippage_bps or 0.0) <= 25.0:
        drift_tone = "positive"
        drift_helper = f"Worst {worst_abs_slippage_bps:.1f} bps | {len(slippage_values)} fills"
    elif average_abs_slippage_bps is not None and average_abs_slippage_bps <= 20.0 and (worst_abs_slippage_bps or 0.0) <= 40.0:
        drift_tone = "warning"
        drift_helper = f"Worst {worst_abs_slippage_bps:.1f} bps | {len(slippage_values)} fills"
    else:
        drift_tone = "negative"
        drift_helper = (
            "No stable drift yet"
            if average_abs_slippage_bps is None or worst_abs_slippage_bps is None
            else f"Worst {worst_abs_slippage_bps:.1f} bps | {len(slippage_values)} fills"
        )

    if cycle_count == 0:
        worker_tone = "warning"
        worker_helper = "The worker has not completed a cycle yet."
    elif error_count == 0:
        worker_tone = "positive"
        worker_helper = f"{success_count} successful cycles | {rejection_count} stand-downs"
    elif error_rate is not None and error_rate <= 0.10:
        worker_tone = "warning"
        worker_helper = f"{error_count} worker errors | {success_count} successes"
    else:
        worker_tone = "negative"
        worker_helper = f"{error_count} worker errors | {success_count} successes"

    tones = [outcome_tone, drift_tone, worker_tone]
    if cycle_count == 0 and closed_count == 0:
        status_tone = "neutral"
        status_label = "Collecting sample"
        status_detail = "The worker is configured, but it still needs live unattended cycles before the scorecard becomes meaningful."
    elif "negative" in tones:
        status_tone = "negative"
        status_label = "Needs review"
        status_detail = "Automation is running, but outcomes, drift, or worker stability are outside the current tolerance band."
    elif "warning" in tones:
        status_tone = "warning"
        status_label = "Under review"
        status_detail = "Automation has usable data now, but the sample or fill drift still needs review before you trust it unattended."
    else:
        status_tone = "positive"
        status_label = "Stable"
        status_detail = "Automation outcomes, fill drift, and worker reliability are all staying inside the current tolerance band."

    cards = [
        {
            "key": "outcomes",
            "label": "Closed outcomes",
            "value": str(closed_count),
            "helper": outcome_helper,
            "tone": outcome_tone,
        },
        {
            "key": "drift",
            "label": "Fill drift",
            "value": "--" if average_abs_slippage_bps is None else f"{average_abs_slippage_bps:.1f} bps",
            "helper": drift_helper,
            "tone": drift_tone,
        },
        {
            "key": "worker",
            "label": "Worker reliability",
            "value": "--" if error_rate is None else f"{(1 - error_rate) * 100:.0f}%",
            "helper": worker_helper,
            "tone": worker_tone,
        },
        {
            "key": "management",
            "label": "Position management",
            "value": f"{partial_exit_count} trims",
            "helper": f"{closed_exit_count} full exits | {rejected_event_count} rejected events",
            "tone": "positive" if partial_exit_count or closed_exit_count else "warning",
        },
    ]

    return {
        "status": {
            "tone": status_tone,
            "label": status_label,
            "detail": status_detail,
        },
        "metrics": {
            "closed_trade_count": closed_count,
            "win_rate": float(analytics.get("win_rate") or 0.0),
            "total_pnl": float(owned_closed.get("realized_pnl", pd.Series(dtype=float)).pipe(pd.to_numeric, errors="coerce").fillna(0.0).sum()) if not owned_closed.empty else 0.0,
            "expectancy": float(analytics.get("expectancy") or 0.0),
            "profit_factor": float(analytics.get("profit_factor") or 0.0),
            "average_abs_slippage_bps": average_abs_slippage_bps,
            "worst_abs_slippage_bps": worst_abs_slippage_bps,
            "slippage_sample_count": len(slippage_values),
            "cycle_count": cycle_count,
            "error_rate": error_rate,
            "stand_down_rate": stand_down_rate,
            "partial_exit_count": partial_exit_count,
            "closed_exit_count": closed_exit_count,
        },
        "cards": serialize_value(cards),
        "recent_closed": serialize_value(recent_closed),
        "recent_events": serialize_value(recent_events[:8]),
    }


def _build_snapshot_payload(
    tenant: Tenant,
    state: dict[str, Any],
    *,
    profile_key: str = _AUTOMATION_PERSONAL_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    equity_snapshot: dict[str, Any] | None = None,
    rollout_readiness: dict[str, Any] | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    normalized_profile_key = str(profile_key or _AUTOMATION_PERSONAL_PAPER_PROFILE).strip().lower()
    account_context = _resolve_trade_automation_profile_account_context(
        tenant=tenant,
        db=db,
        profile_key=normalized_profile_key,
        settings_state=state.get("settings"),
        linked_account=linked_account,
    )
    owned_open = _owned_automation_rows(sdm.read_open_trades(), tenant_id=tenant.id, profile_key=normalized_profile_key)
    owned_pending = _owned_automation_rows(sdm.read_pending_orders(), tenant_id=tenant.id, profile_key=normalized_profile_key)
    collection_phase = _build_collection_phase_state(
        rollout_readiness=rollout_readiness,
        runtime_state=state["runtime"],
    )
    effective_settings = _apply_collection_phase_controls(state["settings"], collection_phase)
    snapshot_runtime = dict(state["runtime"])
    _apply_collection_phase_runtime(snapshot_runtime, collection_phase)
    rollout_readiness = _augment_rollout_readiness_with_collection_phase(rollout_readiness, collection_phase)
    status_key, status_label, status_detail = _resolve_status({"settings": effective_settings, "runtime": snapshot_runtime})
    session = _build_session_snapshot(flatten_before_close_minutes=effective_settings["flatten_before_close_minutes"])
    session_profile = get_session_profile(
        session.get("session_mode") or session.get("phase"),
        instrument_type=_primary_automation_instrument(effective_settings),
        regular_hours_only=bool(effective_settings.get("regular_hours_only")),
    )
    last_cycle_at = _parse_iso_datetime(state["runtime"].get("last_cycle_at"))
    next_run_at = _parse_iso_datetime(state["runtime"].get("next_run_at"))
    monitored_open = sdm.monitor_open_trades()
    owned_monitored = _owned_automation_rows(monitored_open, tenant_id=tenant.id, profile_key=normalized_profile_key)
    owned_closed = _owned_automation_rows(sdm.read_closed_trades(), tenant_id=tenant.id, profile_key=normalized_profile_key)
    latest_equity_snapshot = equity_snapshot or get_latest_trade_automation_equity_snapshot(
        tenant_id=str(tenant.id or "").strip(),
        tenant_slug=str(tenant.slug or "").strip() or None,
        profile_key=normalized_profile_key,
    )
    performance = _build_trade_automation_performance_snapshot(
        db,
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        owned_open=owned_open,
        owned_pending=owned_pending,
    )
    guardrails = _build_trade_automation_guardrail_snapshot(
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        owned_open=owned_open,
        owned_pending=owned_pending,
        effective_funds=account_context.get("effective_funds"),
    )
    control_plane = automation_state_control_service.build_control_plane_snapshot(state)
    control_plane["shadow_validation"] = automation_state_control_shadow_service.build_shadow_validation_snapshot(state)
    ai_review = automation_ai_review_service.build_ai_review_snapshot(state)
    accuracy_calibration = automation_accuracy_calibration_service.build_accuracy_calibration_snapshot(state)
    daily_objective = automation_daily_objective_service.build_daily_objective_snapshot(state)
    loss_containment = automation_loss_containment_service.build_loss_containment_snapshot(state)
    exit_execution_watchdog = automation_exit_execution_watchdog_service.build_exit_watchdog_snapshot(state)
    paper_broker_reconciliation = (
        automation_paper_broker_reconciliation_service.build_paper_broker_reconciliation_snapshot(state)
    )
    paper_order_lifecycle_soak = automation_paper_order_lifecycle_soak_service.build_paper_order_lifecycle_soak_snapshot(state)
    paper_order_lifecycle_canary = (
        automation_paper_order_lifecycle_canary_service.build_paper_order_lifecycle_canary_snapshot(state)
    )
    paper_canary = automation_paper_canary_service.build_paper_canary_snapshot(state)
    live_pilot_readiness = automation_live_pilot_readiness_service.build_live_pilot_readiness_snapshot(state)
    live_pilot_soak = automation_live_pilot_soak_service.build_live_pilot_soak_snapshot(state)
    live_pilot_canary = automation_live_pilot_canary_service.build_live_pilot_canary_snapshot(state)
    live_pilot_expansion = automation_live_pilot_expansion_service.build_live_pilot_expansion_snapshot(state)
    live_pilot_expansion_canary = (
        automation_live_pilot_expansion_canary_service.build_live_pilot_expansion_canary_snapshot(state)
    )
    live_pilot_window = automation_live_pilot_window_service.build_live_pilot_window_snapshot(state)
    live_pilot_window_canary = (
        automation_live_pilot_window_canary_service.build_live_pilot_window_canary_snapshot(state)
    )
    live_pilot_promotion_report = (
        automation_live_pilot_promotion_report_service.build_live_pilot_promotion_snapshot(state)
    )
    limited_live_rollout_gate = (
        automation_limited_live_rollout_gate_service.build_limited_live_rollout_gate_snapshot(state)
    )
    limited_live_rollout_canary = (
        automation_limited_live_rollout_canary_service.build_limited_live_rollout_canary_snapshot(state)
    )
    limited_live_cap_expansion_report = (
        automation_limited_live_cap_expansion_report_service.build_limited_live_cap_expansion_snapshot(state)
    )
    limited_live_cap_expansion_gate = (
        automation_limited_live_cap_expansion_gate_service.build_limited_live_cap_expansion_gate_snapshot(state)
    )
    limited_live_cap_expansion_canary = (
        automation_limited_live_cap_expansion_canary_service.build_limited_live_cap_expansion_canary_snapshot(state)
    )
    limited_live_next_tier_cap_report = (
        automation_limited_live_next_tier_cap_report_service.build_limited_live_next_tier_cap_snapshot(state)
    )
    limited_live_next_tier_cap_gate = (
        automation_limited_live_next_tier_cap_gate_service.build_limited_live_next_tier_cap_gate_snapshot(state)
    )
    limited_live_next_tier_cap_canary = (
        automation_limited_live_safety_ladder_service.build_limited_live_next_tier_cap_canary_snapshot(state)
    )
    limited_live_broker_reconciliation = (
        automation_limited_live_safety_ladder_service.build_limited_live_broker_reconciliation_snapshot(state)
    )
    limited_live_session_closeout = (
        automation_limited_live_safety_ladder_service.build_limited_live_session_closeout_snapshot(state)
    )
    limited_live_cap_ladder = automation_limited_live_safety_ladder_service.build_limited_live_cap_ladder_snapshot(state)
    limited_live_approval_ledger = (
        automation_limited_live_safety_ladder_service.build_limited_live_approval_ledger_snapshot(state)
    )
    limited_live_higher_cap_report = (
        automation_limited_live_safety_ladder_service.build_limited_live_higher_cap_report_snapshot(state)
    )
    live_pilot_soak_approval = dict(snapshot_runtime.get("live_pilot_soak_approval") or {})
    live_pilot_soak_expires_at = _parse_iso_datetime(live_pilot_soak_approval.get("expires_at"))
    can_run_live_pilot_soak = bool(
        normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
        and str(live_pilot_soak_approval.get("status") or "").strip().lower() == "approved"
        and not live_pilot_soak_approval.get("consumed_at")
        and live_pilot_soak_expires_at is not None
        and live_pilot_soak_expires_at > _utc_now()
    )
    live_pilot_expansion_approval = dict(snapshot_runtime.get("live_pilot_expansion_approval") or {})
    live_pilot_expansion_expires_at = _parse_iso_datetime(live_pilot_expansion_approval.get("expires_at"))
    can_run_live_pilot_expansion = bool(
        normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
        and str(live_pilot_expansion_approval.get("status") or "").strip().lower() == "approved"
        and not live_pilot_expansion_approval.get("consumed_at")
        and live_pilot_expansion_expires_at is not None
        and live_pilot_expansion_expires_at > _utc_now()
    )
    live_pilot_window_approval = dict(snapshot_runtime.get("live_pilot_window_approval") or {})
    live_pilot_window_expires_at = _parse_iso_datetime(live_pilot_window_approval.get("expires_at"))
    can_run_live_pilot_window_entry = bool(
        normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
        and str(live_pilot_window_approval.get("status") or "").strip().lower() == "approved"
        and not live_pilot_window_approval.get("consumed_at")
        and live_pilot_window_expires_at is not None
        and live_pilot_window_expires_at > _utc_now()
    )
    can_run_live_pilot_window_exit = bool(
        normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
        and (
            bool((live_pilot_window or {}).get("broker_order_id"))
            or bool(live_pilot_window_approval.get("broker_order_id"))
        )
        and str((live_pilot_window or {}).get("terminal_state") or "").strip().lower() not in {"canceled", "closed"}
    )
    limited_live_rollout_approval = dict(snapshot_runtime.get("limited_live_rollout_gate_approval") or {})
    limited_live_rollout_expires_at = _parse_iso_datetime(limited_live_rollout_approval.get("expires_at"))
    limited_live_rollout_allowance = dict(snapshot_runtime.get("limited_live_rollout_gate_allowance") or {})
    limited_live_rollout_active = bool(
        str(limited_live_rollout_allowance.get("status") or "").strip().lower() == "active"
        and (
            _parse_iso_datetime(limited_live_rollout_allowance.get("expires_at")) or datetime.min.replace(tzinfo=timezone.utc)
        )
        > _utc_now()
    )
    can_activate_limited_live_rollout = bool(
        normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
        and str(limited_live_rollout_approval.get("status") or "").strip().lower() == "approved"
        and not limited_live_rollout_approval.get("consumed_at")
        and limited_live_rollout_expires_at is not None
        and limited_live_rollout_expires_at > _utc_now()
    )
    limited_live_cap_expansion_approval = dict(snapshot_runtime.get("limited_live_cap_expansion_gate_approval") or {})
    limited_live_cap_expansion_expires_at = _parse_iso_datetime(limited_live_cap_expansion_approval.get("expires_at"))
    limited_live_cap_expansion_allowance = dict(snapshot_runtime.get("limited_live_cap_expansion_gate_allowance") or {})
    limited_live_cap_expansion_active = bool(
        str(limited_live_cap_expansion_allowance.get("status") or "").strip().lower() == "active"
        and (
            _parse_iso_datetime(limited_live_cap_expansion_allowance.get("expires_at"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )
        > _utc_now()
    )
    can_activate_limited_live_cap_expansion = bool(
        normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
        and str(limited_live_cap_expansion_approval.get("status") or "").strip().lower() == "approved"
        and not limited_live_cap_expansion_approval.get("consumed_at")
        and limited_live_cap_expansion_expires_at is not None
        and limited_live_cap_expansion_expires_at > _utc_now()
    )
    limited_live_next_tier_cap_approval = dict(snapshot_runtime.get("limited_live_next_tier_cap_gate_approval") or {})
    limited_live_next_tier_cap_expires_at = _parse_iso_datetime(limited_live_next_tier_cap_approval.get("expires_at"))
    limited_live_next_tier_cap_allowance = dict(snapshot_runtime.get("limited_live_next_tier_cap_gate_allowance") or {})
    limited_live_next_tier_cap_active = bool(
        str(limited_live_next_tier_cap_allowance.get("status") or "").strip().lower() == "active"
        and (
            _parse_iso_datetime(limited_live_next_tier_cap_allowance.get("expires_at"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )
        > _utc_now()
    )
    can_activate_limited_live_next_tier_cap = bool(
        normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
        and str(limited_live_next_tier_cap_approval.get("status") or "").strip().lower() == "approved"
        and not limited_live_next_tier_cap_approval.get("consumed_at")
        and limited_live_next_tier_cap_expires_at is not None
        and limited_live_next_tier_cap_expires_at > _utc_now()
    )
    option_execution = dict(snapshot_runtime.get("last_option_execution") or {})
    last_option_entry = dict(snapshot_runtime.get("last_option_entry") or {})
    last_option_exit = dict(snapshot_runtime.get("last_option_exit") or {})
    return {
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
        },
        "status": {"key": status_key, "label": status_label, "detail": status_detail},
        "profile_key": normalized_profile_key,
        "profile_scope": serialize_value(account_context.get("scope")),
        "linked_account_id": str(getattr(linked_account, "id", "") or account_context.get("account_summary", {}).get("linked_account_id") or "").strip() or None,
        "settings": serialize_value(effective_settings),
        "runtime": serialize_value(snapshot_runtime),
        "option_execution": serialize_value(option_execution),
        "option_scan_status": option_execution.get("option_scan_status"),
        "option_contract_score": option_execution.get("option_contract_score"),
        "option_quote_age_seconds": option_execution.get("option_quote_age_seconds"),
        "option_spread_pct": option_execution.get("option_spread_pct"),
        "option_block_reason": option_execution.get("option_block_reason"),
        "last_option_refresh_at": option_execution.get("last_option_refresh_at"),
        "last_options_cycle_at": snapshot_runtime.get("last_options_cycle_at"),
        "last_options_scan_status": snapshot_runtime.get("last_options_scan_status"),
        "last_options_blocker": snapshot_runtime.get("last_options_blocker"),
        "last_option_entry": serialize_value(last_option_entry),
        "last_option_exit": serialize_value(last_option_exit),
        "open_option_position_count": int(snapshot_runtime.get("open_option_position_count") or 0),
        "sell_ready_option_count": int(snapshot_runtime.get("sell_ready_option_count") or 0),
        "template_settings": serialize_value(_read_trade_automation_store(tenant).get("template") or {}),
        "collection_phase": serialize_value(collection_phase),
        "account_summary": serialize_value(account_context.get("account_summary") or {}),
        "actual_funds": account_context.get("actual_funds"),
        "actual_funds_source": account_context.get("actual_funds_source"),
        "effective_funds": account_context.get("effective_funds"),
        "funds_source": account_context.get("funds_source"),
        "effective_funds_multiplier": account_context.get("effective_funds_multiplier"),
        "effective_funds_cap_source": account_context.get("effective_funds_cap_source"),
        "effective_funds_detail": account_context.get("effective_funds_detail"),
        "counts": {
            "open_positions": int(len(owned_open)),
            "pending_orders": int(len(owned_pending)),
            "cycle_count": int(state["runtime"]["cycle_count"]),
            "success_count": int(state["runtime"]["success_count"]),
            "error_count": int(state["runtime"]["error_count"]),
            "rejection_count": int(state["runtime"]["rejection_count"]),
        },
        "session": {
            "phase": session["phase"],
            "session_mode": session["session_mode"],
            "regular_session": session["regular_session"],
            "extended_session": session["extended_session"],
            "new_entries_allowed": session["new_entries_allowed"] and session_profile.equity_entries_allowed,
            "cleanup_window": session["cleanup_window"],
            "minutes_to_close": session["minutes_to_close"],
            "flatten_at": _serialize_datetime(session["flatten_at"].astimezone(timezone.utc)),
            "market_time": session["now_et"].strftime("%Y-%m-%d %I:%M %p %Z"),
            "profile": session_profile.to_record(),
        },
        "schedule": {
            "last_cycle_at": _serialize_datetime(last_cycle_at),
            "next_run_at": _serialize_datetime(next_run_at),
            "cycle_interval_seconds": int(effective_settings["cycle_interval_seconds"]),
        },
        "broker_routes": serialize_value(
            _build_broker_route_snapshot(execution_intent=str(effective_settings.get("execution_intent") or "broker_paper"))
        ),
        "guardrails": serialize_value(guardrails),
        "performance": serialize_value(performance),
        "control_plane": serialize_value(control_plane),
        "ai_review": serialize_value(ai_review),
        "accuracy_calibration": serialize_value(accuracy_calibration),
        "daily_objective": serialize_value(daily_objective),
        "loss_containment": serialize_value(loss_containment),
        "exit_execution_watchdog": serialize_value(exit_execution_watchdog),
        "paper_broker_reconciliation": serialize_value(paper_broker_reconciliation),
        "paper_order_lifecycle_soak": serialize_value(paper_order_lifecycle_soak),
        "paper_order_lifecycle_canary": serialize_value(paper_order_lifecycle_canary),
        "paper_canary": serialize_value(paper_canary),
        "live_pilot_readiness": serialize_value(live_pilot_readiness),
        "live_pilot_soak": serialize_value(live_pilot_soak),
        "live_pilot_canary": serialize_value(live_pilot_canary),
        "live_pilot_expansion": serialize_value(live_pilot_expansion),
        "live_pilot_expansion_canary": serialize_value(live_pilot_expansion_canary),
        "live_pilot_window": serialize_value(live_pilot_window),
        "live_pilot_window_canary": serialize_value(live_pilot_window_canary),
        "live_pilot_promotion_report": serialize_value(live_pilot_promotion_report),
        "limited_live_rollout_gate": serialize_value(limited_live_rollout_gate),
        "limited_live_rollout_canary": serialize_value(limited_live_rollout_canary),
        "limited_live_cap_expansion_report": serialize_value(limited_live_cap_expansion_report),
        "limited_live_cap_expansion_gate": serialize_value(limited_live_cap_expansion_gate),
        "limited_live_cap_expansion_canary": serialize_value(limited_live_cap_expansion_canary),
        "limited_live_next_tier_cap_report": serialize_value(limited_live_next_tier_cap_report),
        "limited_live_next_tier_cap_gate": serialize_value(limited_live_next_tier_cap_gate),
        "limited_live_next_tier_cap_canary": serialize_value(limited_live_next_tier_cap_canary),
        "limited_live_broker_reconciliation": serialize_value(limited_live_broker_reconciliation),
        "limited_live_session_closeout": serialize_value(limited_live_session_closeout),
        "limited_live_cap_ladder": serialize_value(limited_live_cap_ladder),
        "limited_live_approval_ledger": serialize_value(limited_live_approval_ledger),
        "limited_live_higher_cap_report": serialize_value(limited_live_higher_cap_report),
        "equity_snapshot": serialize_value(latest_equity_snapshot),
        "rollout_readiness": serialize_value(rollout_readiness),
        "linked_client_automation": serialize_value(
            _build_linked_client_automation_snapshot(db=db, tenant=tenant, runtime_state=state["runtime"])
        ),
        "history": serialize_value(state.get("history") or []),
        "available_actions": {
            "can_arm": bool(state["settings"]["enabled"] and not state["settings"]["armed"] and not state["settings"]["kill_switch"]),
            "can_disarm": bool(state["settings"]["armed"]),
            "can_kill": not bool(state["settings"]["kill_switch"]),
            "can_clear_kill": bool(state["settings"]["kill_switch"]),
            "can_run_cycle": bool(state["settings"]["enabled"] and not state["settings"]["kill_switch"]),
            "can_run_ai_review": bool(state["settings"].get("ai_daily_review_enabled", True)),
            "can_run_daily_objective_review": bool(
                state["settings"].get("daily_objective_enabled", True)
                and (
                    normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
                    or bool(state["settings"].get("daily_objective_apply_to_live"))
                )
            ),
            "can_run_accuracy_calibration_review": bool(
                state["settings"].get("accuracy_calibration_enabled", True)
                and (
                    normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
                    or bool(state["settings"].get("accuracy_calibration_apply_to_live"))
                )
            ),
            "can_run_loss_containment_review": bool(
                state["settings"].get("loss_containment_enabled", True)
                and (
                    normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
                    or bool(state["settings"].get("loss_containment_apply_to_live"))
                )
            ),
            "can_run_exit_watchdog_review": bool(
                state["settings"].get("exit_watchdog_enabled", True)
                and (
                    normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
                    or bool(state["settings"].get("exit_watchdog_apply_to_live"))
                )
            ),
            "can_run_state_control_review": bool(state["settings"].get("state_control_enabled", True)),
            "can_run_state_control_shadow_validation": bool(state["settings"].get("state_control_enabled", True)),
            "can_run_paper_broker_reconciliation": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_run_paper_order_lifecycle_soak": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_run_paper_order_lifecycle_canary_review": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_run_paper_canary_review": True,
            "can_run_live_pilot_readiness_review": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_prepare_live_pilot_soak": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_run_live_pilot_soak": can_run_live_pilot_soak,
            "can_run_live_pilot_canary_review": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_prepare_live_pilot_expansion": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_run_live_pilot_expansion": can_run_live_pilot_expansion,
            "can_run_live_pilot_expansion_canary_review": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_prepare_live_pilot_window": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_run_live_pilot_window_entry": can_run_live_pilot_window_entry,
            "can_run_live_pilot_window_exit": can_run_live_pilot_window_exit,
            "can_run_live_pilot_window_canary_review": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_run_live_pilot_promotion_report": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_prepare_limited_live_rollout": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_activate_limited_live_rollout": can_activate_limited_live_rollout,
            "can_rollback_limited_live_rollout": (
                normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE and limited_live_rollout_active
            ),
            "can_run_limited_live_rollout_canary_review": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_run_limited_live_cap_expansion_report": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_prepare_limited_live_cap_expansion": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_activate_limited_live_cap_expansion": can_activate_limited_live_cap_expansion,
            "can_rollback_limited_live_cap_expansion": (
                normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE and limited_live_cap_expansion_active
            ),
            "can_run_limited_live_cap_expansion_canary_review": (
                normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
            ),
            "can_run_limited_live_next_tier_cap_report": (
                normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
            ),
            "can_prepare_limited_live_next_tier_cap": normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE,
            "can_activate_limited_live_next_tier_cap": can_activate_limited_live_next_tier_cap,
            "can_rollback_limited_live_next_tier_cap": (
                normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE and limited_live_next_tier_cap_active
            ),
            "can_run_limited_live_next_tier_cap_canary_review": (
                normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
            ),
            "can_run_limited_live_broker_reconciliation": (
                normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
            ),
            "can_run_limited_live_session_closeout": (
                normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
            ),
            "can_run_limited_live_higher_cap_report": (
                normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
            ),
            "can_submit_limited_live_operator_checklist": (
                normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
            ),
        },
    }


def _finalize_trade_automation_cycle(
    db: Session,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str = _AUTOMATION_PERSONAL_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    effective_funds: float | None = None,
    rollout_readiness: dict[str, Any] | None,
    now: datetime,
    cycle_id: str,
    monitored_open: pd.DataFrame | None = None,
    pending_orders: pd.DataFrame | None = None,
    closed_trades: pd.DataFrame | None = None,
) -> dict[str, Any]:
    _write_trade_automation_state(tenant, state, profile_key=profile_key)
    db.commit()
    equity_reference = state.get("__actual_funds")
    equity_snapshot = record_trade_automation_equity_snapshot(
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        cycle_at=now,
        cycle_id=cycle_id,
        monitored_open=monitored_open,
        pending_orders=pending_orders,
        closed_trades=closed_trades,
        now=now,
        effective_funds=equity_reference if equity_reference is not None else effective_funds,
    )
    runtime_state = state["runtime"]
    previous_fill_count = int(runtime_state.get("current_route_fill_count") or 0)
    previous_close_count = int(runtime_state.get("current_route_closed_trade_count") or 0)
    refreshed_rollout_readiness = dict(rollout_readiness or {})
    try:
        collection_metrics = _build_live_current_route_collection_metrics(tenant)
    except Exception as exc:  # pragma: no cover - defensive telemetry guard
        collection_metrics = {
            "current_route_fill_count": int(runtime_state.get("current_route_fill_count") or 0),
            "current_route_closed_trade_count": int(runtime_state.get("current_route_closed_trade_count") or 0),
            "current_route_mismatched_count": int(runtime_state.get("current_route_mismatched_count") or 0),
            "current_route_sample_status": str(runtime_state.get("current_route_sample_status") or "insufficient"),
            "mark_to_market_coverage_status": str(runtime_state.get("mark_to_market_coverage_status") or "missing"),
            "ledger_snapshot_consistency": str(runtime_state.get("ledger_snapshot_consistency") or "unavailable"),
            "metrics_source": str(runtime_state.get("metrics_source") or "").strip().lower() or None,
            "route_window_start": runtime_state.get("route_window_start"),
            "route_window_end": runtime_state.get("route_window_end"),
            "route_window_snapshot_count": int(runtime_state.get("route_window_snapshot_count") or 0),
            "current_route_latest_event_at": runtime_state.get("current_route_latest_event_at"),
            "current_route_validation_integrity": serialize_value(
                refreshed_rollout_readiness.get("current_route_validation_integrity") or {}
            ),
        }
        runtime_state["last_validation_rerun_error"] = str(exc)
    collection_phase = _build_collection_phase_state(
        rollout_readiness=refreshed_rollout_readiness,
        runtime_state=runtime_state,
        collection_metrics=collection_metrics,
    )
    _apply_collection_phase_runtime(runtime_state, collection_phase)

    if _should_auto_rerun_validation(collection_phase=collection_phase, runtime_state=runtime_state, cycle_id=cycle_id):
        runtime_state["validation_rerun_in_progress"] = True
        collection_phase = _build_collection_phase_state(
            rollout_readiness=refreshed_rollout_readiness,
            runtime_state=runtime_state,
            collection_metrics=collection_metrics,
        )
        _apply_collection_phase_runtime(runtime_state, collection_phase)
        try:
            _run_validation_export_for_collection_phase(tenant)
            runtime_state["last_validation_rerun_status"] = "succeeded"
            runtime_state["last_validation_rerun_error"] = None
            runtime_state["last_validation_rerun_at"] = _serialize_datetime(_utc_now())
            runtime_state["last_validation_rerun_event_at"] = collection_phase.get("current_route_latest_event_at")
            runtime_state["last_validation_rerun_cycle_id"] = cycle_id
            refreshed_trade_summary = get_trade_summary(
                db=db,
                current_user=_build_system_current_user(tenant, None),
            )
            refreshed_rollout_readiness = dict(refreshed_trade_summary.get("rollout_readiness") or {})
        except Exception as exc:  # pragma: no cover - defensive validation rerun guard
            runtime_state["last_validation_rerun_status"] = "failed"
            runtime_state["last_validation_rerun_error"] = str(exc)
            runtime_state["last_validation_rerun_at"] = _serialize_datetime(_utc_now())
            runtime_state["last_validation_rerun_cycle_id"] = cycle_id
        finally:
            runtime_state["validation_rerun_in_progress"] = False
            try:
                collection_metrics = _build_live_current_route_collection_metrics(tenant)
            except Exception:  # pragma: no cover - preserve prior sample metrics
                pass
            collection_phase = _build_collection_phase_state(
                rollout_readiness=refreshed_rollout_readiness,
                runtime_state=runtime_state,
                collection_metrics=collection_metrics,
            )
            _apply_collection_phase_runtime(runtime_state, collection_phase)

    _finalize_collection_audit(
        runtime_state,
        collection_phase=collection_phase,
        collection_metrics=collection_metrics,
        previous_fill_count=previous_fill_count,
        previous_close_count=previous_close_count,
    )
    try:
        automation_state_control_service.evaluate_trade_automation_state_control(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            now=now,
        )
    except Exception as exc:  # pragma: no cover - state notes must not break trading automation
        runtime_state["state_control_last_error"] = str(exc)
    try:
        automation_ai_review_service.capture_trade_automation_ai_observation(
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            now=now,
            cycle_id=cycle_id,
        )
    except Exception as exc:  # pragma: no cover - notes must not break trading automation
        runtime_state["ai_last_observation_error"] = str(exc)
    _write_trade_automation_state(tenant, state, profile_key=profile_key)
    db.commit()
    if (
        str(profile_key or "").strip().lower() == _AUTOMATION_PERSONAL_PAPER_PROFILE
        and bool(state.get("settings", {}).get("auto_trade_listed_options"))
    ):
        try:
            from backend.services import options_automation_service

            options_automation_service.get_options_automation_snapshot(
                db,
                current_user=_build_system_current_user(tenant, None),
            )
        except Exception:  # pragma: no cover - export refresh must not break the worker
            pass
    refreshed_rollout_readiness = _augment_rollout_readiness_with_collection_phase(
        refreshed_rollout_readiness,
        collection_phase,
    )
    return _build_snapshot_payload(
        tenant,
        state,
        profile_key=profile_key,
        linked_account=linked_account,
        equity_snapshot=equity_snapshot,
        rollout_readiness=refreshed_rollout_readiness,
        db=db,
    )


def _row_supports_automation_instrument(row: dict[str, Any], instrument_type: str) -> bool:
    normalized_instrument = _normalize_automation_instrument(instrument_type, default="equity")
    vehicle_recommendation = str(row.get("vehicle_recommendation") or "").strip().lower()
    option_execution_profile = dict(row.get("option_execution_profile") or {})
    if normalized_instrument == "equity":
        return vehicle_recommendation in {"", "equity"}
    contract_symbol = str(row.get("contract_symbol") or "").strip()
    verdict = str(row.get("verdict") or "").strip().upper()
    option_right = str(row.get("option_right") or row.get("direction") or "").strip().lower()
    contract_quality_tier = str(option_execution_profile.get("contract_quality_tier") or row.get("contract_quality_tier") or "").strip().lower()
    execution_score = float(
        pd.to_numeric(
            option_execution_profile.get("execution_score", row.get("option_execution_score")),
            errors="coerce",
        )
        or 0.0
    )
    if option_right not in {"call", "put"}:
        if verdict == "BULLISH":
            option_right = "call"
        elif verdict == "BEARISH":
            option_right = "put"
    if vehicle_recommendation and vehicle_recommendation != "listed_option":
        return False
    quality_gate = contract_quality_tier != "weak" if contract_quality_tier else True
    score_gate = execution_score >= 70.0 if execution_score > 0 else True
    return bool(contract_symbol and option_right in {"call", "put"} and quality_gate and score_gate)


def _candidate_numeric(candidate: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = pd.to_numeric(candidate.get(key), errors="coerce")
        if pd.notna(value):
            return float(value)
    return None


def _candidate_projected_notional(candidate: dict[str, Any], settings_state: dict[str, Any], current_equity: float) -> float:
    direct = _candidate_numeric(
        candidate,
        "projected_position_cost",
        "total_position_cost",
        "position_cost",
        "projected_notional",
        "notional",
    )
    if direct is not None and direct > 0:
        return float(direct)
    baseline_risk_percent = float(settings_state.get("risk_percent") or 0.5)
    risk_budget_notional = max(float(current_equity) * (baseline_risk_percent / 100.0) * 10.0, 100.0)
    max_notional_per_trade = float(settings_state.get("max_notional_per_trade") or 0.0)
    if max_notional_per_trade > 0:
        return min(max_notional_per_trade, risk_budget_notional)
    single_cap = max(float(current_equity), 1.0) * (float(settings_state.get("max_single_position_pct") or 12.0) / 100.0)
    return float(min(single_cap, risk_budget_notional))


def _rank_automation_candidates(
    *,
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    now: datetime,
    owned_open: pd.DataFrame | None = None,
    owned_pending: pd.DataFrame | None = None,
    current_equity: float | None = None,
) -> list[dict[str, Any]]:
    settings_state = state["settings"]
    allow_pyramiding = bool(settings_state.get("allow_pyramiding"))
    owned_open = owned_open if owned_open is not None else pd.DataFrame()
    owned_pending = owned_pending if owned_pending is not None else pd.DataFrame()
    current_equity = max(float(current_equity or settings_state.get("account_size") or 10000.0), 1.0)
    enabled_instruments = _resolve_enabled_automation_instruments(settings_state)
    active_targets = _build_active_trade_targets(owned_open, owned_pending)
    active_underlyings = _build_active_trade_underlyings(owned_open, owned_pending)
    bucket_exposure = risk_control_service.build_bucket_exposure([owned_open, owned_pending], settings_state=settings_state)
    bucket_cycle_entries: dict[str, int] = {}
    planned_bucket_exposure = dict(bucket_exposure)
    planned_underlyings = set(active_underlyings)
    candidate_pool: list[dict[str, Any]] = []

    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        decision = str(row.get("trade_decision") or "").strip().upper()
        verdict = str(row.get("verdict") or "").strip().upper()
        ranking_tier = str(row.get("ranking_tier") or "").strip().lower()
        if ranking_tier == "stand_down" or bool(row.get("event_risk")):
            continue
        if decision != "VALID TRADE":
            continue

        base_execution_score = _candidate_numeric(row, "execution_score", "ranking_score", "setup_score") or 0.0
        base_portfolio_score = _candidate_numeric(row, "portfolio_score", "ranking_score", "setup_score") or 0.0
        row_auto_entry_flag = row.get("auto_entry_eligible")
        auto_entry_eligible = (
            bool(row_auto_entry_flag)
            if row_auto_entry_flag not in (None, "")
            else (base_execution_score >= 60.0 and base_portfolio_score >= 65.0)
        )
        bucket = str(row.get("proxy_correlation_bucket") or risk_control_service.bucket_for_symbol(ticker, settings_state))
        projected_notional = _candidate_projected_notional(row, settings_state, current_equity)
        bucket_before = float(bucket_exposure.get(bucket, 0.0))
        bucket_before_pct = (bucket_before / current_equity * 100.0) if current_equity > 0 else 0.0
        bucket_penalty = min(bucket_before_pct * 0.75, 24.0)

        for instrument_type in enabled_instruments:
            normalized_instrument = _normalize_automation_instrument(instrument_type, default="equity")
            if bool(settings_state.get("long_only")) and normalized_instrument == "equity" and verdict != "BULLISH":
                continue
            if not _row_supports_automation_instrument(row, normalized_instrument):
                continue
            if _is_ticker_on_cooldown(state, ticker, instrument_type=normalized_instrument, now=now):
                continue
            target_key = _build_automation_target_key(ticker, normalized_instrument)
            same_target_active = target_key in active_targets
            same_underlying_active = ticker in active_underlyings
            allow_existing_underlying = allow_pyramiding and same_underlying_active
            adjusted_portfolio_score = max(base_portfolio_score - bucket_penalty - (18.0 if same_target_active else 0.0), 0.0)
            candidate_pool.append(
                {
                    **row,
                    "automation_instrument_type": normalized_instrument,
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "automation_entry_reason": "ranked_candidate",
                    "proxy_correlation_bucket": bucket,
                    "projected_position_cost": projected_notional,
                    "bucket_exposure_before": round(bucket_before, 4),
                    "bucket_exposure_before_pct": round(bucket_before_pct, 4),
                    "portfolio_score": round(adjusted_portfolio_score, 2),
                    "base_portfolio_score": round(base_portfolio_score, 2),
                    "execution_score": round(base_execution_score, 2),
                    "auto_entry_eligible": auto_entry_eligible and (allow_existing_underlying or not same_underlying_active),
                    "same_underlying_active": same_underlying_active,
                    "allow_existing_underlying": allow_existing_underlying,
                }
            )

    candidate_pool = automation_accuracy_calibration_service.apply_accuracy_calibration_candidate_overlay(
        candidate_pool,
        state=state,
        current_equity=current_equity,
    )
    candidate_pool = automation_daily_objective_service.apply_daily_objective_candidate_overlay(
        candidate_pool,
        state=state,
        current_equity=current_equity,
    )
    candidate_pool.sort(
        key=lambda item: (
            float(_candidate_numeric(item, "accuracy_calibrated_score") or 0.0),
            float(_candidate_numeric(item, "daily_objective_score") or 0.0),
            float(_candidate_numeric(item, "portfolio_score") or 0.0),
            float(_candidate_numeric(item, "execution_score") or 0.0),
            1 if str(item.get("automation_instrument_type") or "").strip().lower() == "listed_option" else 0,
        ),
        reverse=True,
    )

    cycle_entry_rank_limit = int(settings_state.get("cycle_entry_rank_limit") or 2)
    ranked_candidates: list[dict[str, Any]] = []
    eligible_rank = 0
    for candidate in candidate_pool:
        ticker = str(candidate.get("ticker") or "").strip().upper()
        bucket = str(candidate.get("proxy_correlation_bucket") or "")
        projected_notional = float(candidate.get("projected_position_cost") or 0.0)
        bucket_after = float(planned_bucket_exposure.get(bucket, 0.0)) + projected_notional
        bucket_after_pct = (bucket_after / current_equity * 100.0) if current_equity > 0 else 0.0
        bucket_entries = int(bucket_cycle_entries.get(bucket) or 0)
        bucket_gate = bucket_entries == 0 or bucket_after_pct < 20.0
        underlying_gate = bool(ticker) and (
            ticker not in planned_underlyings or bool(candidate.get("allow_existing_underlying"))
        )
        eligible = bool(candidate.get("auto_entry_eligible")) and bucket_gate and underlying_gate
        if eligible:
            eligible_rank += 1
            candidate["portfolio_rank"] = eligible_rank
            candidate["auto_entry_eligible"] = eligible_rank <= cycle_entry_rank_limit
            if eligible_rank <= cycle_entry_rank_limit:
                bucket_cycle_entries[bucket] = bucket_entries + 1
                planned_bucket_exposure[bucket] = bucket_after
                if ticker:
                    planned_underlyings.add(ticker)
        else:
            candidate["portfolio_rank"] = None
            candidate["auto_entry_eligible"] = False
        candidate["bucket_exposure_after"] = round(bucket_after, 4)
        candidate["bucket_exposure_after_pct"] = round(bucket_after_pct, 4)
        candidate["bucket_cycle_gate"] = "under_20_pct_exception" if bucket_entries > 0 and bucket_gate else "single_entry"
        candidate["underlying_gate"] = "unique_underlying" if underlying_gate else "already_active"
        ranked_candidates.append(candidate)

    return ranked_candidates


def _select_candidate_for_instrument(
    *,
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    instrument_type: str,
    now: datetime,
    current_equity: float | None = None,
) -> dict[str, Any] | None:
    normalized_instrument = _normalize_automation_instrument(instrument_type, default="equity")
    for candidate in _rank_automation_candidates(
        state=state,
        rows=rows,
        now=now,
        current_equity=current_equity,
    ):
        if not bool(candidate.get("auto_entry_eligible")):
            continue
        if str(candidate.get("automation_instrument_type") or "").strip().lower() == normalized_instrument:
            return candidate
    return None


def _select_candidates_from_watchlist(
    state: dict[str, Any],
    *,
    owned_open: pd.DataFrame | None = None,
    owned_pending: pd.DataFrame | None = None,
    current_equity: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings_state = state["settings"]
    watchlist = build_watchlist(
        WatchlistRequest(
            tickers=settings_state["tickers"],
            interval=settings_state["interval"],
            horizon=settings_state["horizon"],
            limit=max(len(settings_state["tickers"]), settings_state["max_open_positions"] * 2),
            sort_by="ranking_score",
            descending=True,
            include_contract_lookup=True,
            include_event_lookup=True,
            include_alignment=True,
            use_fast_model=settings_state["use_fast_model"],
        )
    )
    rows = list(watchlist.get("rows") or watchlist.get("results") or [])
    now = _utc_now()
    ranked_candidates = _rank_automation_candidates(
        state=state,
        rows=rows,
        now=now,
        owned_open=owned_open,
        owned_pending=owned_pending,
        current_equity=current_equity,
    )
    automation_accuracy_calibration_service.record_candidate_snapshot(
        state,
        candidates=ranked_candidates,
        now=now,
        cycle_id=str(state.get("runtime", {}).get("current_cycle_id") or "") or None,
    )
    enabled_instruments = _resolve_enabled_automation_instruments(settings_state)
    path_evaluations: list[dict[str, Any]] = []
    for instrument_type in enabled_instruments:
        normalized_instrument = _normalize_automation_instrument(instrument_type, default="equity")
        matching_candidates = [
            candidate
            for candidate in ranked_candidates
            if str(candidate.get("automation_instrument_type") or "").strip().lower() == normalized_instrument
        ]
        top_candidate = matching_candidates[0] if matching_candidates else {}
        path_evaluations.append(
            {
                "instrument_type": normalized_instrument,
                "status": (
                    "eligible"
                    if top_candidate and bool(top_candidate.get("auto_entry_eligible"))
                    else "blocked"
                    if top_candidate
                    else "idle"
                ),
                "ticker": str(top_candidate.get("ticker") or "").strip().upper() or None,
                "vehicle_recommendation": str(top_candidate.get("vehicle_recommendation") or "").strip().lower() or None,
                "detail": (
                    str(top_candidate.get("vehicle_reason") or "").strip()
                    or str(top_candidate.get("reject_reason") or "").strip()
                    or "Candidate did not clear the path-specific automation gate."
                    if top_candidate
                    else "No watchlist candidate matched this instrument path."
                ),
                "execution_score": float(pd.to_numeric((top_candidate.get("option_execution_profile") or {}).get("execution_score"), errors="coerce") or 0.0)
                if normalized_instrument == "listed_option"
                else float(pd.to_numeric(top_candidate.get("execution_score"), errors="coerce") or 0.0)
                if top_candidate
                else 0.0,
            }
        )
    watchlist["path_evaluations"] = serialize_value(path_evaluations)
    candidates = [candidate for candidate in ranked_candidates if bool(candidate.get("auto_entry_eligible"))]
    return candidates, watchlist


def _select_candidate_from_watchlist(
    state: dict[str, Any],
    *,
    current_equity: float | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    candidates, watchlist = _select_candidates_from_watchlist(state, current_equity=current_equity)
    return (candidates[0] if candidates else None), watchlist


def _build_active_trade_targets(open_trades: pd.DataFrame, pending_orders: pd.DataFrame) -> set[str]:
    active_targets: set[str] = set()
    for frame in (open_trades, pending_orders):
        if frame.empty:
            continue
        for row in frame.to_dict(orient="records"):
            target_key = _build_automation_target_key(
                row.get("ticker"),
                _normalize_automation_instrument(row.get("instrument_type"), default="equity"),
            )
            if target_key:
                active_targets.add(target_key)
    return active_targets


def _build_active_trade_underlyings(open_trades: pd.DataFrame, pending_orders: pd.DataFrame) -> set[str]:
    active_underlyings: set[str] = set()
    for frame in (open_trades, pending_orders):
        if frame.empty or "ticker" not in frame.columns:
            continue
        for value in frame["ticker"].astype(str).str.strip().str.upper():
            if value:
                active_underlyings.add(value)
    return active_underlyings


def _persist_watchlist_validation_snapshot(
    *,
    current_user: Any,
    cycle_id: str,
    captured_at: datetime,
    watchlist: dict[str, Any],
) -> None:
    validation_artifact = dict(watchlist.get("validation_artifact") or {})
    if not validation_artifact:
        return
    user_id = getattr(current_user, "user_id", None)
    tenant_slug = getattr(current_user, "tenant_slug", None)
    captured_label = captured_at.astimezone(_MARKET_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S ET")
    workspace = save_workspace(
        user_id,
        name=f"Automation board {captured_label}",
        page="watchlist",
        payload={
            "automation_origin": _AUTOMATION_MARKER,
            "automation_cycle_id": cycle_id,
            "validation_artifact": serialize_value(validation_artifact),
        },
        notes="System-saved board snapshot for rollout replay validation.",
        pinned=False,
        tags=[_AUTOMATION_BOARD_TAG, "system", "validation"],
        tenant_slug=tenant_slug,
    )
    scoped = list_workspaces(
        user_id,
        page="watchlist",
        tag=_AUTOMATION_BOARD_TAG,
        sort_by="updated_desc",
        tenant_slug=tenant_slug,
    )
    items = list(scoped.get("items") or [])
    for stale in items[_AUTOMATION_BOARD_HISTORY_LIMIT:]:
        stale_id = str(stale.get("id") or "").strip()
        if stale_id and stale_id != str(workspace.get("id") or "").strip():
            delete_workspace(user_id, stale_id, tenant_slug=tenant_slug)


def _apply_automation_marker(payload: dict[str, Any], *, tenant: Tenant, cycle_id: str, execution_intent: str) -> dict[str, Any]:
    return {
        **dict(payload or {}),
        "automation_origin": _AUTOMATION_MARKER,
        "automation_tenant_id": tenant.id,
        "automation_tenant_slug": tenant.slug,
        "automation_cycle_id": cycle_id,
        "automation_execution_intent": execution_intent,
    }


def _submit_linked_client_automation_entries(
    db: Session,
    *,
    current_user: Any,
    tenant: Tenant,
    cycle_id: str,
    candidate: dict[str, Any],
    settings_state: dict[str, Any],
    effective_risk_percent: float,
    instrument_type: str,
    option_right: str | None,
    order_fields: dict[str, Any],
) -> dict[str, Any]:
    eligible_accounts = list_eligible_linked_accounts_for_automation(db=db, current_user=current_user)
    submitted_items: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []
    block_reasons_by_account: dict[str, str] = {}

    for linked_account in eligible_accounts:
        automation_profile = get_linked_account_automation_profile(linked_account)
        owner = getattr(linked_account, "owner_user", None)
        target_identity = dict(getattr(current_user, "__dict__", {}) or {})
        target_identity["auth_subject"] = str(
            linked_account.owner_user_id or getattr(current_user, "auth_subject", "") or ""
        ).strip()
        target_identity["user_id"] = str(linked_account.owner_user_id or getattr(current_user, "user_id", "") or "").strip()
        target_identity["email"] = str(
            getattr(owner, "email", "") or getattr(current_user, "email", "") or "automation@system.local"
        ).strip()
        target_identity["name"] = str(
            getattr(owner, "name", "") or getattr(current_user, "name", "") or "Trade automation"
        ).strip()
        target_current_user = SimpleNamespace(**target_identity)
        try:
            open_result = open_trade_from_request(
                OpenTradeRequest(
                    ticker=str(candidate.get("ticker") or "").strip().upper(),
                    interval=settings_state["interval"],
                    horizon=int(settings_state["horizon"]),
                    live_price=_coerce_candidate_price(candidate, "live_price", "close", "last_price"),
                    account_size=float(automation_profile.get("account_size") or settings_state["account_size"]),
                    risk_percent=float(automation_profile.get("risk_percent") or effective_risk_percent),
                    instrument_type=instrument_type,
                    option_strategy="single_leg" if instrument_type == "listed_option" else None,
                    option_right=option_right or None,
                    contract_symbol=str(candidate.get("contract_symbol") or "").strip() or None,
                    execution_intent="broker_paper",
                    extended_hours=(
                        False
                        if instrument_type == "listed_option"
                        else str(order_fields.get("time_in_force") or "").strip().lower() == "day_ext"
                    ),
                    capital_preservation_mode=True,
                    fractional_shares_only=bool(settings_state["fractional_shares_only"]) if instrument_type == "equity" else False,
                    regular_hours_only=True if instrument_type == "listed_option" else bool(settings_state["regular_hours_only"]),
                    max_daily_loss_r=float(settings_state["max_daily_loss_r"]),
                    max_consecutive_losses=int(settings_state["max_consecutive_losses"]),
                    max_open_positions=int(automation_profile.get("max_open_positions") or settings_state["max_open_positions"]),
                    max_notional_per_trade=float(
                        automation_profile.get("max_notional_per_trade") or settings_state["max_notional_per_trade"]
                    ),
                    equities_only=bool(settings_state["equities_only"]) if instrument_type == "equity" else False,
                    limit_orders_only=str(order_fields["order_type"]) != "market",
                    long_only=bool(settings_state["long_only"]),
                    route_family=str(candidate.get("route_family") or "current"),
                    route_version=str(candidate.get("route_version") or "ranked_entry_v1"),
                    automation_entry_reason=str(candidate.get("automation_entry_reason") or "ranked_candidate"),
                    thesis_direction=str(candidate.get("verdict") or "").strip().upper() or None,
                    account_target_type="linked_client",
                    linked_account_id=linked_account.id,
                    execution_mode="automated_entry",
                    **order_fields,
                ),
                db=db,
                current_user=target_current_user,
            )
            trade_intent = dict(open_result.get("trade_intent") or {})
            submitted_items.append(
                {
                    "linked_account_id": linked_account.id,
                    "label": linked_account.label or linked_account.linked_identity_label or f"Alpaca {linked_account.account_environment}",
                    "ticker": str(candidate.get("ticker") or "").strip().upper(),
                    "trade_intent_id": trade_intent.get("id"),
                    "execution": serialize_value(open_result.get("execution") or {}),
                }
            )
        except Exception as exc:
            reason = str(getattr(exc, "error_code", None) or "").strip() or str(exc)
            block_reasons_by_account[linked_account.id] = reason
            blocked_items.append(
                {
                    "linked_account_id": linked_account.id,
                    "label": linked_account.label or linked_account.linked_identity_label or f"Alpaca {linked_account.account_environment}",
                    "reason": reason,
                    "detail": str(exc),
                }
            )

    return {
        "eligible_linked_account_count": len(eligible_accounts),
        "automated_linked_account_count": len(submitted_items),
        "blocked_linked_account_count": len(blocked_items),
        "last_automated_client_order": serialize_value(submitted_items[-1]) if submitted_items else None,
        "block_reasons_by_account": serialize_value(block_reasons_by_account),
        "submitted_items": serialize_value(submitted_items),
        "blocked_items": serialize_value(blocked_items),
        "cycle_id": cycle_id,
    }


def _flatten_automation_positions(
    db: Session,
    *,
    tenant: Tenant,
    actor: Any | None,
    profile_key: str,
    state: dict[str, Any],
    cycle_id: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    canceled_items: list[dict[str, Any]] = []
    closed_items: list[dict[str, Any]] = []
    failed_items: list[dict[str, Any]] = []

    pending_orders = _owned_automation_rows(sdm.read_pending_orders(), tenant_id=tenant.id, profile_key=profile_key)
    for row in pending_orders.to_dict(orient="records") if not pending_orders.empty else []:
        order_id = str(row.get("order_id") or "").strip()
        adapter_name = str(row.get("broker_name") or "desk").strip().lower() or "desk"
        try:
            adapter = get_execution_adapter_for(adapter_name)
            result = adapter.cancel_order(order_id=order_id)
            if result is None:
                raise ValidationError("Working order could not be canceled during close cleanup.")
            canceled = dict(result.canceled_order or row)
            _record_order_event(
                db,
                tenant=tenant,
                actor=actor,
                trade_id=resolve_trade_identifier(canceled),
                ticker=str(canceled.get("ticker") or ""),
                event_key="order.canceled",
                status="canceled",
                order_type=str(canceled.get("order_type") or ""),
                time_in_force=str(canceled.get("time_in_force") or ""),
                route_state="canceled",
                book_state="flat",
                detail="Automation canceled the working order during the close-cleanup window.",
                payload={
                    "automation_cycle_id": cycle_id,
                    "session": serialize_value(session),
                    "order": serialize_value(canceled),
                },
                audit_event_type="trade.order_canceled",
            )
            canceled_items.append(
                {
                    "ticker": str(canceled.get("ticker") or "").strip().upper(),
                    "order_id": order_id,
                    "broker_name": adapter_name,
                    "status": "canceled",
                }
            )
        except Exception as exc:  # pragma: no cover - defensive automation guard
            failed_items.append(
                {
                    "ticker": str(row.get("ticker") or "").strip().upper(),
                    "order_id": order_id,
                    "broker_name": adapter_name,
                    "status": "cancel_failed",
                    "detail": str(exc),
                }
            )

    open_trades = _owned_automation_rows(sdm.read_open_trades(), tenant_id=tenant.id, profile_key=profile_key)
    open_rows = []
    if not open_trades.empty:
        indexed = open_trades.reset_index()
        open_rows = sorted(indexed.to_dict(orient="records"), key=lambda item: int(item.get("index", 0)), reverse=True)

    for row in open_rows:
        trade_index = int(row.get("index", 0))
        ticker = str(row.get("ticker") or "").strip().upper()
        adapter_name = str(row.get("broker_name") or "desk").strip().lower() or "desk"
        try:
            adapter = get_execution_adapter_for(adapter_name)
            analysis = analyze_market(
                AnalyzeRequest(
                    ticker=ticker,
                    interval=str(row.get("interval") or state["settings"]["interval"] or "5m"),
                    horizon=int(pd.to_numeric(row.get("horizon"), errors="coerce") or state["settings"]["horizon"]),
                    include_history=False,
                    include_live_price=True,
                    include_contract_lookup=False,
                    include_event_lookup=False,
                    include_alignment=False,
                    use_fast_model=bool(state["settings"]["use_fast_model"]),
                ),
                current_user=SimpleNamespace(tenant_id=tenant.id, tenant_slug=tenant.slug),
            )
            live_price = float(
                analysis.get("live_price")
                or row.get("current_underlying_price")
                or row.get("live_price_at_open")
                or row.get("close")
                or 0.0
            )
            if live_price <= 0:
                raise ValidationError(f"{ticker} did not return a valid live price for close cleanup.")
            instrument_type = str(row.get("instrument_type") or "listed_option").strip().lower()
            option_close_refresh: dict[str, Any] = {}
            if instrument_type == "equity":
                close_contract_mid = live_price / 100.0
            else:
                option_close_refresh = _refresh_option_quote_for_close(row, now=_utc_now())
                state["runtime"]["last_option_execution"] = serialize_value(option_close_refresh["diagnostics"])
                if not bool(option_close_refresh["allowed"]):
                    raise ValidationError(str(option_close_refresh["detail"]))
                close_contract_mid = float(option_close_refresh["close_contract_mid"] or 0.0)
            if close_contract_mid <= 0:
                raise ValidationError(f"{ticker} did not produce a valid close midpoint for close cleanup.")
            result = adapter.close_position(
                request=CloseTradeRequest(
                    trade_index=trade_index,
                    close_underlying_price=live_price,
                    close_contract_mid=close_contract_mid,
                ),
                target_trade=row,
            )
            closed_trade = dict(result.closed_trade or row)
            _record_order_event(
                db,
                tenant=tenant,
                actor=actor,
                trade_id=resolve_trade_identifier(closed_trade),
                ticker=ticker,
                event_key="order.closed",
                status="closed",
                order_type=str(closed_trade.get("order_type") or ""),
                time_in_force=str(closed_trade.get("time_in_force") or ""),
                route_state="closed",
                book_state="flat",
                detail="Automation flattened the live position during the close-cleanup window.",
                payload={
                    "automation_cycle_id": cycle_id,
                    "session": serialize_value(session),
                    "trade": serialize_value(closed_trade),
                    "close_underlying_price": live_price,
                    "close_contract_mid": close_contract_mid,
                    "option_execution": serialize_value(option_close_refresh.get("diagnostics") or {}),
                },
                audit_event_type="trade.order_closed",
            )
            closed_items.append(
                {
                    "ticker": ticker,
                    "trade_id": str(closed_trade.get("trade_id") or "").strip() or None,
                    "broker_name": adapter_name,
                    "instrument_type": instrument_type,
                    "status": "closed",
                    "option_execution": serialize_value(option_close_refresh.get("diagnostics") or {}),
                }
            )
        except Exception as exc:  # pragma: no cover - defensive automation guard
            failed_items.append(
                {
                    "ticker": ticker,
                    "trade_index": trade_index,
                    "broker_name": adapter_name,
                    "instrument_type": instrument_type if "instrument_type" in locals() else str(row.get("instrument_type") or "").strip().lower() or "equity",
                    "status": "close_failed",
                    "detail": str(exc),
                }
            )

    return {
        "canceled_count": len(canceled_items),
        "closed_count": len(closed_items),
        "failed_count": len(failed_items),
        "canceled_items": canceled_items,
        "closed_items": closed_items,
        "failed_items": failed_items,
    }


def _coerce_trade_number(value: Any) -> float:
    normalized = pd.to_numeric(value, errors="coerce")
    if pd.isna(normalized):
        return 0.0
    return float(normalized)


def _has_trade_text(value: Any) -> bool:
    if value is None:
        return False
    if pd.isna(value):
        return False
    text = str(value).strip()
    return bool(text and text.lower() not in {"nan", "none", "null"})


def _coerce_trade_text(value: Any) -> str:
    return str(value).strip() if _has_trade_text(value) else ""


def _build_position_management_plan(action: str, trade_row: dict[str, Any]) -> dict[str, Any] | None:
    normalized_action = str(action or "").strip().upper()
    contracts = _coerce_trade_number(trade_row.get("suggested_contracts"))
    if contracts <= 0:
        return None

    broker_name = str(trade_row.get("broker_name") or "desk").strip().lower() or "desk"
    supports_fractional_trim = broker_name == "desk" or bool(trade_row.get("broker_fractionable"))
    tp1_taken = _has_trade_text(trade_row.get("tp1_taken_at")) or _has_trade_text(
        trade_row.get("automation_tp1_taken_at")
    )
    tp2_taken = _has_trade_text(trade_row.get("tp2_taken_at")) or _has_trade_text(
        trade_row.get("automation_tp2_taken_at")
    )

    if normalized_action in {"STOP HIT", "EXIT FULLY NOW", "TIME STOP"}:
        return {
            "close_fraction": 1.0,
            "mark_tp1": False,
            "mark_tp2": False,
            "status": "closed",
            "reason": normalized_action.lower().replace(" ", "_"),
        }

    if normalized_action == "SELL 50% NOW":
        if tp1_taken:
            return None
        return {
            "close_fraction": 0.5 if supports_fractional_trim or contracts > 1 else 1.0,
            "mark_tp1": True,
            "mark_tp2": False,
            "status": "partial",
            "reason": "take_profit_1",
        }

    if normalized_action == "SELL MORE NOW":
        if tp2_taken:
            return None
        return {
            "close_fraction": (0.5 if tp1_taken else 0.75) if supports_fractional_trim or contracts > 1 else 1.0,
            "mark_tp1": not tp1_taken,
            "mark_tp2": True,
            "status": "partial",
            "reason": "take_profit_2",
        }

    return None


def _manage_automation_positions(
    db: Session,
    *,
    tenant: Tenant,
    actor: Any | None,
    profile_key: str,
    cycle_id: str,
    state: dict[str, Any] | None = None,
    forced_actions: dict[str, str] | None = None,
) -> dict[str, Any]:
    open_trades = sdm.read_open_trades()
    monitored = sdm.monitor_open_trades()
    owned_open = _owned_automation_rows(open_trades, tenant_id=tenant.id, profile_key=profile_key)
    owned_monitored = _owned_automation_rows(monitored, tenant_id=tenant.id, profile_key=profile_key)
    acted_items: list[dict[str, Any]] = []
    failed_items: list[dict[str, Any]] = []
    trigger_actions = {"STOP HIT", "EXIT FULLY NOW", "SELL MORE NOW", "SELL 50% NOW", "TIME STOP"}

    if owned_open.empty or owned_monitored.empty:
        return {"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []}

    open_rows = open_trades.to_dict(orient="records")
    for row in owned_monitored.to_dict(orient="records"):
        action = str(row.get("monitor_action") or "HOLD").strip().upper()
        if forced_actions:
            trade_id_key = str(row.get("trade_id") or "").strip()
            order_id_key = str(row.get("order_id") or "").strip()
            ticker_key = str(row.get("ticker") or "").strip().upper()
            action = (
                forced_actions.get(f"trade_id:{trade_id_key}")
                or forced_actions.get(f"order_id:{order_id_key}")
                or forced_actions.get(f"ticker:{ticker_key}")
                or action
            )
            action = str(action or "HOLD").strip().upper()
        if action not in trigger_actions:
            continue
        trade_id = str(row.get("trade_id") or "").strip()
        order_id = str(row.get("order_id") or "").strip()
        match_index = None
        for index, candidate in enumerate(open_rows):
            if trade_id and str(candidate.get("trade_id") or "").strip() == trade_id:
                match_index = index
                break
            if order_id and str(candidate.get("order_id") or "").strip() == order_id:
                match_index = index
                break
        if match_index is None:
            failed_items.append(
                {
                    "ticker": str(row.get("ticker") or "").strip().upper(),
                    "status": "not_found",
                    "detail": "Open position could not be matched back to the live desk for automation closeout.",
                }
            )
            continue

        target_trade = dict(open_rows[match_index])
        plan = _build_position_management_plan(action, target_trade)
        if plan is None:
            continue

        adapter_name = str(row.get("broker_name") or "desk").strip().lower() or "desk"
        try:
            adapter = get_execution_adapter_for(adapter_name)
            close_underlying_price = _coerce_trade_number(row.get("current_underlying"))
            if close_underlying_price <= 0:
                close_underlying_price = _coerce_trade_number(row.get("current_underlying_price"))
            instrument_type = str(target_trade.get("instrument_type") or row.get("instrument_type") or "listed_option").strip().lower()
            option_close_refresh: dict[str, Any] = {}
            if instrument_type == "listed_option":
                option_close_refresh = _refresh_option_quote_for_close({**target_trade, **row}, now=_utc_now())
                if state is not None:
                    state["runtime"]["last_option_execution"] = serialize_value(option_close_refresh["diagnostics"])
                if not bool(option_close_refresh["allowed"]):
                    raise ValidationError(str(option_close_refresh["detail"]))
                close_contract_mid = float(option_close_refresh["close_contract_mid"] or 0.0)
            else:
                close_contract_mid = _coerce_trade_number(row.get("current_contract_mid"))
                if close_contract_mid <= 0:
                    close_contract_mid = _coerce_trade_number(row.get("contract_mid_at_open"))
            if close_underlying_price <= 0 or close_contract_mid <= 0:
                raise ValidationError("Automation could not resolve a valid live price for the monitored exit.")
            result = adapter.close_position(
                request=CloseTradeRequest(
                    trade_index=match_index,
                    close_underlying_price=close_underlying_price,
                    close_contract_mid=close_contract_mid,
                    close_fraction=float(plan["close_fraction"]),
                ),
                target_trade=target_trade,
            )
            closed_trade = dict(result.closed_trade or target_trade)
            remaining_contracts = _coerce_trade_number(closed_trade.get("remaining_contracts_after_close"))
            close_fraction_value = pd.to_numeric(closed_trade.get("close_fraction"), errors="coerce")
            close_fraction = float(close_fraction_value) if pd.notna(close_fraction_value) else float(plan["close_fraction"])
            partial_close = remaining_contracts > 0
            event_key = "order.partially_closed" if partial_close else "order.closed"
            event_status = "partially_closed" if partial_close else "closed"
            book_state = "open" if partial_close else "flat"
            detail = (
                f"Automation trimmed the live position after monitor action flagged {action}."
                if partial_close
                else f"Automation closed the live position because monitor action flagged {action}."
            )
            _record_order_event(
                db,
                tenant=tenant,
                actor=actor,
                trade_id=resolve_trade_identifier(closed_trade),
                ticker=str(closed_trade.get("ticker") or ""),
                event_key=event_key,
                status=event_status,
                order_type=str(closed_trade.get("order_type") or ""),
                time_in_force=str(closed_trade.get("time_in_force") or ""),
                route_state="managed" if partial_close else "closed",
                book_state=book_state,
                detail=detail,
                payload={
                    "automation_cycle_id": cycle_id,
                    "monitor_action": action,
                    "close_fraction": close_fraction,
                    "trade": serialize_value(closed_trade),
                    "close_underlying_price": close_underlying_price,
                    "close_contract_mid": close_contract_mid,
                    "option_execution": serialize_value(option_close_refresh.get("diagnostics") or {}),
                },
                audit_event_type="trade.order_partially_closed" if partial_close else "trade.order_closed",
            )
            if instrument_type == "listed_option":
                _record_scheduled_options_event(
                    db,
                    tenant=tenant,
                    event_type="options.paper_exit_submitted",
                    aggregate_type="option_position",
                    aggregate_id=resolve_trade_identifier(closed_trade),
                    payload={
                        "trade_id": resolve_trade_identifier(closed_trade),
                        "ticker": str(closed_trade.get("ticker") or "").strip().upper() or None,
                        "contract_symbol": str(closed_trade.get("contract_symbol") or "").strip().upper() or None,
                        "order_id": str(closed_trade.get("order_id") or "").strip() or None,
                        "broker_order_id": str(
                            closed_trade.get("broker_order_id") or closed_trade.get("broker_close_order_id") or ""
                        ).strip()
                        or None,
                        "broker_close_order_id": str(closed_trade.get("broker_close_order_id") or "").strip() or None,
                        "broker_status": str(
                            closed_trade.get("broker_close_status") or closed_trade.get("broker_status") or ""
                        ).strip()
                        or None,
                        "close_fraction": close_fraction,
                        "monitor_action": action,
                        "partial_close": partial_close,
                        "position_closed": not partial_close,
                        "close_underlying_price": close_underlying_price,
                        "close_contract_mid": close_contract_mid,
                        "option_execution": serialize_value(option_close_refresh.get("diagnostics") or {}),
                    },
                    metadata={"cycle_id": cycle_id, "profile_key": profile_key},
                )
            now_iso = _serialize_datetime(_utc_now())
            if partial_close:
                bars_held_value = pd.to_numeric(row.get("bars_held"), errors="coerce")
                updates = {
                    "tp1_taken_at": _coerce_trade_text(target_trade.get("tp1_taken_at")),
                    "tp2_taken_at": _coerce_trade_text(target_trade.get("tp2_taken_at")),
                    "automation_last_manage_action": action,
                    "automation_last_manage_cycle_id": cycle_id,
                    "automation_last_manage_at": now_iso,
                    "last_exit_reason": str(plan.get("reason") or action).strip(),
                    "active_stop_price": float(
                        pd.to_numeric(row.get("active_stop_price"), errors="coerce")
                        if row.get("active_stop_price") not in (None, "")
                        else float("nan")
                    ),
                    "bars_held": int(float(bars_held_value)) if pd.notna(bars_held_value) else 0,
                }
                if bool(plan.get("mark_tp1")):
                    updates["tp1_taken_at"] = now_iso
                    updates["automation_tp1_taken_at"] = now_iso
                if bool(plan.get("mark_tp2")):
                    updates["tp2_taken_at"] = now_iso
                    updates["automation_tp2_taken_at"] = now_iso
                updated_trade = sdm.update_open_trade(
                    updates,
                    trade_id=trade_id or str(target_trade.get("trade_id") or "").strip() or None,
                    order_id=order_id or str(target_trade.get("order_id") or "").strip() or None,
                )
                if updated_trade is not None:
                    open_rows[match_index] = dict(updated_trade)
                else:
                    open_rows[match_index].update(updates)
                    open_rows[match_index]["suggested_contracts"] = remaining_contracts
            else:
                open_rows.pop(match_index)
            acted_items.append(
                {
                    "ticker": str(closed_trade.get("ticker") or "").strip().upper(),
                    "trade_id": resolve_trade_identifier(closed_trade),
                    "broker_name": adapter_name,
                    "instrument_type": instrument_type,
                    "monitor_action": action,
                    "close_fraction": close_fraction,
                    "status": "partial" if partial_close else "closed",
                    "option_execution": serialize_value(option_close_refresh.get("diagnostics") or {}),
                }
            )
        except Exception as exc:  # pragma: no cover - defensive automation guard
            if str(instrument_type if "instrument_type" in locals() else row.get("instrument_type") or "").strip().lower() == "listed_option":
                _record_scheduled_options_event(
                    db,
                    tenant=tenant,
                    event_type="options.paper_exit_blocked",
                    aggregate_type="option_position",
                    aggregate_id=trade_id or order_id or None,
                    payload={
                        "trade_id": trade_id or None,
                        "order_id": order_id or None,
                        "ticker": str(row.get("ticker") or "").strip().upper() or None,
                        "contract_symbol": str(row.get("contract_symbol") or "").strip().upper() or None,
                        "monitor_action": action,
                        "reason": "scheduled_exit_failed",
                        "detail": str(exc),
                        "option_execution": serialize_value(option_close_refresh.get("diagnostics") or {}),
                    },
                    metadata={"cycle_id": cycle_id, "profile_key": profile_key},
                )
            failed_items.append(
                {
                    "ticker": str(row.get("ticker") or "").strip().upper(),
                    "broker_name": adapter_name,
                    "instrument_type": instrument_type if "instrument_type" in locals() else str(row.get("instrument_type") or "").strip().lower() or "equity",
                    "monitor_action": action,
                    "status": "close_failed",
                    "detail": str(exc),
                    "option_execution": serialize_value(option_close_refresh.get("diagnostics") or {}),
                }
            )

    return {
        "acted_count": len(acted_items),
        "failed_count": len(failed_items),
        "items": acted_items,
        "failed_items": failed_items,
    }


def _run_trade_automation_cycle(
    db: Session,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str = _AUTOMATION_PERSONAL_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    forced: bool = False,
    actor: Any | None = None,
) -> dict[str, Any]:
    normalized_profile_key = str(profile_key or _AUTOMATION_PERSONAL_PAPER_PROFILE).strip().lower()
    settings_state = state["settings"]
    runtime_state = state["runtime"]
    now = _utc_now()
    session = _build_session_snapshot(flatten_before_close_minutes=settings_state["flatten_before_close_minutes"])
    profile_context = _resolve_trade_automation_profile_account_context(
        tenant=tenant,
        db=db,
        profile_key=normalized_profile_key,
        settings_state=settings_state,
        actor=actor,
        linked_account=linked_account,
    )
    current_user = profile_context["current_user"]
    resolved_linked_account = profile_context.get("linked_account")
    effective_funds = profile_context.get("effective_funds")
    actual_funds = profile_context.get("actual_funds")
    state["__actual_funds"] = actual_funds
    state["__effective_funds"] = effective_funds
    effective_execution_intent = str(
        profile_context.get("execution_intent") or settings_state.get("execution_intent") or "broker_paper"
    ).strip().lower() or "broker_paper"
    cycle_id = str(uuid4())
    scheduled_options_enabled = (
        normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE
        and bool(settings_state.get("auto_trade_listed_options"))
    )
    session_profile = get_session_profile(
        session.get("session_mode") or session.get("phase"),
        instrument_type=_primary_automation_instrument(settings_state),
        regular_hours_only=bool(settings_state.get("regular_hours_only")),
    )

    runtime_state["last_cycle_at"] = _serialize_datetime(now)
    runtime_state["current_cycle_id"] = cycle_id
    runtime_state["next_run_at"] = _serialize_datetime(
        now + timedelta(seconds=int(settings_state["cycle_interval_seconds"]))
    )
    runtime_state["cycle_count"] = int(runtime_state.get("cycle_count") or 0) + 1
    runtime_state["last_error"] = None
    runtime_state["last_error_at"] = None
    if scheduled_options_enabled:
        _sync_scheduled_options_runtime(runtime_state, cycle_at=now)

    rollout_readiness: dict[str, Any] = {}
    try:
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        collection_phase = _build_collection_phase_state(
            rollout_readiness=rollout_readiness,
            runtime_state=runtime_state,
        )
        _apply_collection_phase_runtime(runtime_state, collection_phase)
        runtime_state["current_collection_audit"] = _new_collection_audit(
            cycle_id=cycle_id,
            now=now,
            collection_phase_active=bool(collection_phase.get("collection_phase_active")),
        )
        settings_state = _apply_collection_phase_controls(settings_state, collection_phase)
        state["settings"] = settings_state
        session_profile = get_session_profile(
            session.get("session_mode") or session.get("phase"),
            instrument_type=_primary_automation_instrument(settings_state),
            regular_hours_only=bool(settings_state.get("regular_hours_only")),
        )
        runtime_state["active_session_profile"] = serialize_value(session_profile.to_record())

        if settings_state["auto_sync_orders"]:
            sync_result = sync_pending_orders_from_broker(db=db, current_user=current_user) or {}
            if scheduled_options_enabled:
                pending_option_frame = _owned_automation_rows(
                    sdm.read_pending_orders(),
                    tenant_id=tenant.id,
                    profile_key=normalized_profile_key,
                )
                pending_option_frame = pending_option_frame.loc[
                    pending_option_frame.get("instrument_type", pd.Series("", index=pending_option_frame.index))
                    .astype(str)
                    .str.lower()
                    .eq("listed_option")
                ]
                synced_items = list(sync_result.get("items") or [])
                option_synced_items = [
                    item
                    for item in synced_items
                    if str(item.get("instrument_type") or "").strip().lower() == "listed_option"
                    or str(item.get("contract_symbol") or "").strip()
                ]
                _record_scheduled_options_event(
                    db,
                    tenant=tenant,
                    event_type="options.lifecycle_synced",
                    aggregate_type="options_automation",
                    aggregate_id=None,
                    payload={
                        "pending_option_order_count": int(len(pending_option_frame.index)),
                        "synced_item_count": int(len(option_synced_items)),
                        "synced_items": serialize_value(option_synced_items[:20]),
                        "synced_at": _serialize_datetime(now),
                    },
                    metadata={"cycle_id": cycle_id, "profile_key": normalized_profile_key},
                )

        if settings_state["kill_switch"]:
            decision = {
                "decision": "blocked",
                "reason": "kill_switch",
                "detail": "Kill switch is active, so automation did not place or manage new trades.",
            }
            runtime_state["last_decision"] = decision
            runtime_state["last_action"] = {"type": "blocked", "detail": decision["detail"]}
            _append_history(
                state,
                {"type": "blocked", "cycle_id": cycle_id, "at": _serialize_datetime(now), **decision},
            )
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )

        if not settings_state["enabled"]:
            decision = {
                "decision": "blocked",
                "reason": "disabled",
                "detail": "Automation is disabled and did not run an unattended cycle.",
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["last_decision"] = decision
            runtime_state["last_action"] = {"type": "blocked", "detail": decision["detail"]}
            _append_history(
                state,
                {"type": "blocked", "cycle_id": cycle_id, "at": _serialize_datetime(now), **decision},
            )
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )

        if not settings_state["armed"] and not forced:
            decision = {
                "decision": "blocked",
                "reason": "disarmed",
                "detail": "Automation is configured but not armed for unattended trading.",
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["last_decision"] = decision
            runtime_state["last_action"] = {"type": "blocked", "detail": decision["detail"]}
            _append_history(
                state,
                {"type": "blocked", "cycle_id": cycle_id, "at": _serialize_datetime(now), **decision},
            )
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )

        if normalized_profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE:
            settings_state["execution_intent"] = "broker_paper"
        elif normalized_profile_key == _AUTOMATION_PERSONAL_LIVE_PROFILE:
            settings_state["execution_intent"] = "broker_live"
        elif normalized_profile_key.startswith("linked:"):
            settings_state["execution_intent"] = "broker_paper"
            if resolved_linked_account is None:
                rejection = {
                    "decision": "blocked",
                    "reason": "brokerage_account_missing",
                    "detail": "The linked brokerage account for this automation profile could not be resolved.",
                }
                runtime_state["last_rejection"] = rejection
                runtime_state["last_decision"] = rejection
                runtime_state["last_action"] = {"type": "blocked", "detail": rejection["detail"]}
                _append_history(
                    state,
                    {"type": "blocked", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection},
                )
                return _finalize_trade_automation_cycle(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=normalized_profile_key,
                    linked_account=resolved_linked_account,
                    effective_funds=effective_funds,
                    rollout_readiness=rollout_readiness,
                    now=now,
                    cycle_id=cycle_id,
                )
            connection_status = str(getattr(resolved_linked_account, "connection_status", "") or "").strip().lower()
            token_health = str(getattr(resolved_linked_account, "token_health", "") or "").strip().lower()
            account_environment = str(getattr(resolved_linked_account, "account_environment", "") or "").strip().lower()
            if connection_status != "connected" or token_health not in {"healthy", "unknown"}:
                rejection = {
                    "decision": "blocked",
                    "reason": "brokerage_account_unavailable",
                    "detail": "The linked brokerage account is disconnected or needs to be relinked before automation can run.",
                }
                runtime_state["last_rejection"] = rejection
                runtime_state["last_decision"] = rejection
                runtime_state["last_action"] = {"type": "blocked", "detail": rejection["detail"]}
                _append_history(
                    state,
                    {"type": "blocked", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection},
                )
                return _finalize_trade_automation_cycle(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=normalized_profile_key,
                    linked_account=resolved_linked_account,
                    effective_funds=effective_funds,
                    rollout_readiness=rollout_readiness,
                    now=now,
                    cycle_id=cycle_id,
                )
            if account_environment != "paper":
                rejection = {
                    "decision": "blocked",
                    "reason": "brokerage_profile_paper_only",
                    "detail": "Brokerage automation remains paper-only for this profile.",
                }
                runtime_state["last_rejection"] = rejection
                runtime_state["last_decision"] = rejection
                runtime_state["last_action"] = {"type": "blocked", "detail": rejection["detail"]}
                _append_history(
                    state,
                    {"type": "blocked", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection},
                )
                return _finalize_trade_automation_cycle(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=normalized_profile_key,
                    linked_account=resolved_linked_account,
                    effective_funds=effective_funds,
                    rollout_readiness=rollout_readiness,
                    now=now,
                    cycle_id=cycle_id,
                )

        if effective_funds is None or float(effective_funds) <= 0:
            rejection = {
                "decision": "blocked",
                "reason": "account_funds_unavailable",
                "detail": "Automation could not resolve a valid broker balance for this account profile.",
            }
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_action"] = {"type": "blocked", "detail": rejection["detail"]}
            _append_history(
                state,
                {"type": "blocked", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection},
            )
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )

        if settings_state["auto_flatten_before_close"] and session["cleanup_window"]:
            flatten_result = _flatten_automation_positions(
                db,
                tenant=tenant,
                actor=actor,
                state=state,
                cycle_id=cycle_id,
                session=session,
                profile_key=normalized_profile_key,
            )
            _reset_trade_automation_error_streak(state)
            runtime_state["success_count"] = int(runtime_state.get("success_count") or 0) + 1
            runtime_state["last_success_at"] = _serialize_datetime(now)
            runtime_state["last_rejection"] = None
            runtime_state["last_candidate"] = None
            runtime_state["last_guardrail"] = None
            runtime_state["last_decision"] = {
                "decision": "cleanup",
                "reason": "close_window",
                "detail": "Automation ran close cleanup instead of opening a new position.",
            }
            runtime_state["last_action"] = {
                "type": "flatten",
                "cycle_id": cycle_id,
                "summary": serialize_value(flatten_result),
            }
            _append_history(
                state,
                {
                    "type": "flatten",
                    "cycle_id": cycle_id,
                    "at": _serialize_datetime(now),
                    "summary": serialize_value(flatten_result),
                },
            )
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )
        options_refresh_snapshot: dict[str, Any] | None = None
        if scheduled_options_enabled and session["regular_session"]:
            options_refresh_snapshot = _run_scheduled_options_refresh(
                db,
                current_user=current_user,
            )
            refresh_reason = str(options_refresh_snapshot.get("reason") or "").strip() or None
            _sync_scheduled_options_runtime(
                runtime_state,
                cycle_at=now,
                refresh_snapshot=options_refresh_snapshot,
                blocker=None if refresh_reason and "no open long option paper positions" in refresh_reason.lower() else refresh_reason,
            )
        if settings_state.get("auto_manage_positions") and (
            session["regular_session"]
            or (session.get("extended_session") and bool(settings_state.get("auto_trade_equities")))
        ):
            management_result = _manage_automation_positions(
                db,
                tenant=tenant,
                actor=actor,
                cycle_id=cycle_id,
                profile_key=normalized_profile_key,
                state=state,
            )
            option_exit_items = [
                item
                for item in list(management_result.get("items") or [])
                if str(item.get("instrument_type") or "").strip().lower() == "listed_option"
            ]
            option_exit_failures = [
                item
                for item in list(management_result.get("failed_items") or [])
                if str(item.get("instrument_type") or "").strip().lower() == "listed_option"
            ]
            if option_exit_items:
                last_option_exit = dict(option_exit_items[-1] or {})
                last_option_exit["at"] = _serialize_datetime(now)
                _sync_scheduled_options_runtime(
                    runtime_state,
                    cycle_at=now,
                    last_exit=last_option_exit,
                )
            elif option_exit_failures:
                _sync_scheduled_options_runtime(
                    runtime_state,
                    cycle_at=now,
                    blocker=str(option_exit_failures[0].get("detail") or "Option exit failed."),
                )
            if management_result["acted_count"] or management_result["failed_count"]:
                _reset_trade_automation_error_streak(state)
                runtime_state["success_count"] = int(runtime_state.get("success_count") or 0) + 1
                runtime_state["last_success_at"] = _serialize_datetime(now)
                runtime_state["last_rejection"] = None
                runtime_state["last_candidate"] = None
                runtime_state["last_guardrail"] = None
                runtime_state["last_decision"] = {
                    "decision": "manage_positions",
                    "detail": "Automation reviewed live positions and acted on monitored exit signals.",
                }
                runtime_state["last_action"] = {
                    "type": "manage_positions",
                    "cycle_id": cycle_id,
                    "summary": serialize_value(management_result),
                }
                _append_history(
                    state,
                    {
                        "type": "manage_positions",
                        "cycle_id": cycle_id,
                        "at": _serialize_datetime(now),
                        "summary": serialize_value(management_result),
                    },
                )
                return _finalize_trade_automation_cycle(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=normalized_profile_key,
                    linked_account=resolved_linked_account,
                    effective_funds=effective_funds,
                    rollout_readiness=rollout_readiness,
                    now=now,
                    cycle_id=cycle_id,
                )
        if settings_state["regular_hours_only"] and not session["regular_session"]:
            try:
                _, prep_watchlist = _select_candidate_from_watchlist(state)
                _persist_watchlist_validation_snapshot(
                    current_user=current_user,
                    cycle_id=cycle_id,
                    captured_at=now,
                    watchlist=prep_watchlist,
                )
            except Exception:
                pass
            rejection = {
                "decision": "stand_down",
                "reason": "outside_regular_session",
                "detail": "Regular-hours-only automation is standing down until the core session opens.",
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_guardrail"] = None
            runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
            _append_history(
                state,
                {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection},
            )
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )

        if not bool(session.get("new_entries_allowed")):
            try:
                _, prep_watchlist = _select_candidate_from_watchlist(state)
                _persist_watchlist_validation_snapshot(
                    current_user=current_user,
                    cycle_id=cycle_id,
                    captured_at=now,
                    watchlist=prep_watchlist,
                )
            except Exception:
                pass
            rejection = {
                "decision": "stand_down",
                "reason": "closed_session_planning",
                "detail": "Closed-session automation is running sync, monitoring, and planning, but new entries are blocked until pre-market opens.",
                "session_mode": session.get("session_mode"),
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_guardrail"] = None
            runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
            _append_history(
                state,
                {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection},
            )
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )

        open_trades = sdm.read_open_trades()
        pending_orders = sdm.read_pending_orders()
        owned_open = _owned_automation_rows(open_trades, tenant_id=tenant.id, profile_key=normalized_profile_key)
        owned_pending = _owned_automation_rows(pending_orders, tenant_id=tenant.id, profile_key=normalized_profile_key)
        owned_closed = _owned_automation_rows(
            sdm.read_closed_trades(),
            tenant_id=tenant.id,
            profile_key=normalized_profile_key,
        )
        monitored_open = sdm.monitor_open_trades()
        owned_monitored = _owned_automation_rows(
            monitored_open,
            tenant_id=tenant.id,
            profile_key=normalized_profile_key,
        )
        equity_reference = float(actual_funds or effective_funds or settings_state.get("account_size") or 10000.0)
        equity_snapshot = risk_control_service.compute_current_equity(
            account_size=equity_reference,
            closed_frame=owned_closed,
            open_frame=owned_open,
            monitored_frame=owned_monitored,
        )
        drawdown_snapshot = risk_control_service.update_high_water_runtime(
            runtime_state,
            current_equity=float(equity_snapshot.get("current_equity_estimate") or equity_reference),
            starting_equity=equity_reference,
        )
        current_equity = float(drawdown_snapshot.get("current_equity_estimate") or equity_reference)
        drawdown_pct = float(drawdown_snapshot.get("drawdown_pct") or 0.0)
        drawdown_stop_pct = float(
            settings_state.get("drawdown_stop_pct") or risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["drawdown_stop_pct"]
        )
        drawdown_audit_pct = float(
            settings_state.get("drawdown_audit_pct") or risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["drawdown_audit_pct"]
        )
        loss_containment_report = automation_loss_containment_service.evaluate_loss_containment_entry_gate(
            db,
            tenant=tenant,
            state=state,
            profile_key=normalized_profile_key,
            linked_account=resolved_linked_account,
            owned_open=owned_open,
            owned_pending=owned_pending,
            monitored_open=owned_monitored,
            effective_funds=effective_funds,
            now=now,
            actor=actor,
        )
        forced_loss_actions = automation_loss_containment_service.build_forced_exit_actions(loss_containment_report)
        if forced_loss_actions and settings_state.get("auto_manage_positions"):
            management_result = _manage_automation_positions(
                db,
                tenant=tenant,
                actor=actor,
                cycle_id=cycle_id,
                profile_key=normalized_profile_key,
                state=state,
                forced_actions=forced_loss_actions,
            )
            if management_result["acted_count"] or management_result["failed_count"]:
                fresh_owned_closed = _owned_automation_rows(
                    sdm.read_closed_trades(),
                    tenant_id=tenant.id,
                    profile_key=normalized_profile_key,
                )
                exit_watchdog_report = automation_exit_execution_watchdog_service.evaluate_exit_watchdog_entry_gate(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=normalized_profile_key,
                    owned_closed=fresh_owned_closed,
                    now=now,
                    actor=actor,
                )
                _reset_trade_automation_error_streak(state)
                runtime_state["success_count"] = int(runtime_state.get("success_count") or 0) + 1
                runtime_state["last_success_at"] = _serialize_datetime(now)
                runtime_state["last_rejection"] = None
                runtime_state["last_candidate"] = None
                runtime_state["last_guardrail"] = None
                runtime_state["last_decision"] = {
                    "decision": "loss_containment_exit",
                    "detail": "Loss containment acted on defensive paper exit rules.",
                    "loss_containment": serialize_value(loss_containment_report),
                    "exit_watchdog": serialize_value(exit_watchdog_report),
                }
                runtime_state["last_action"] = {
                    "type": "loss_containment_exit",
                    "cycle_id": cycle_id,
                    "summary": serialize_value(management_result),
                    "exit_watchdog": serialize_value(exit_watchdog_report),
                }
                _append_history(
                    state,
                    {
                        "type": "loss_containment_exit",
                        "cycle_id": cycle_id,
                        "at": _serialize_datetime(now),
                        "summary": serialize_value(management_result),
                        "exit_watchdog": serialize_value(exit_watchdog_report),
                    },
                )
                return _finalize_trade_automation_cycle(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=normalized_profile_key,
                    linked_account=resolved_linked_account,
                    effective_funds=effective_funds,
                    rollout_readiness=rollout_readiness,
                    now=now,
                    cycle_id=cycle_id,
                )
        exit_watchdog_report = automation_exit_execution_watchdog_service.evaluate_exit_watchdog_entry_gate(
            db,
            tenant=tenant,
            state=state,
            profile_key=normalized_profile_key,
            owned_closed=owned_closed,
            now=now,
            actor=actor,
        )
        control_plane = automation_state_control_service.build_control_plane_snapshot(state, now=now)
        runtime_state["state_control_effective_overrides"] = serialize_value(
            automation_state_control_service.build_policy_overrides(settings_state, control_plane)
        )
        if automation_state_control_service.should_block_new_entries(control_plane):
            rejection = {
                "decision": "stand_down",
                "reason": "state_control_halt",
                "detail": "State-control is in halt mode, so all new automation entries are blocked until an operator clears the safety lock.",
                "state_control": serialize_value(control_plane),
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_guardrail"] = {
                "reason": rejection["reason"],
                "label": "State-control halt",
                "detail": rejection["detail"],
                "at": _serialize_datetime(now),
            }
            runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
            _append_history(state, {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection})
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )
        if bool(exit_watchdog_report.get("entries_blocked")):
            rejection = {
                "decision": "stand_down",
                "reason": "exit_watchdog_block",
                "detail": "Exit watchdog blocked new entries because defensive exit confirmation is missing, stuck, or failed.",
                "exit_watchdog": serialize_value(exit_watchdog_report),
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_guardrail"] = {
                "reason": rejection["reason"],
                "label": "Exit watchdog block",
                "detail": rejection["detail"],
                "at": _serialize_datetime(now),
            }
            runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
            _append_history(state, {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection})
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )
        if bool(loss_containment_report.get("entries_blocked")):
            rejection = {
                "decision": "stand_down",
                "reason": "loss_containment_block",
                "detail": "Loss containment blocked new entries because open heat, stale quotes, or an open-position breach is active.",
                "loss_containment": serialize_value(loss_containment_report),
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_guardrail"] = {
                "reason": rejection["reason"],
                "label": "Loss containment block",
                "detail": rejection["detail"],
                "at": _serialize_datetime(now),
            }
            runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
            _append_history(state, {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection})
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )
        controlled_settings_state = automation_state_control_service.apply_state_control_overlay(
            settings_state,
            control_plane,
        )
        control_entry_state = {
            **state,
            "settings": controlled_settings_state,
            "profile_key": normalized_profile_key,
        }
        entry_settings_state = _build_session_adjusted_automation_settings(
            controlled_settings_state,
            session_profile=session_profile,
        )
        entry_state = {
            **state,
            "settings": entry_settings_state,
            "profile_key": normalized_profile_key,
        }
        effective_risk_percent = risk_control_service.effective_risk_percent(entry_settings_state, drawdown_pct)
        guardrails = _build_trade_automation_guardrail_snapshot(
            tenant=tenant,
            state=control_entry_state,
            profile_key=normalized_profile_key,
            owned_open=owned_open,
            owned_pending=owned_pending,
            effective_funds=effective_funds,
            now=now,
        )
        guardrail_status = dict(guardrails.get("status") or {})
        if guardrail_status.get("locked"):
            rejection = {
                "decision": "stand_down",
                "reason": str(guardrail_status.get("reason") or "guardrail_lock"),
                "detail": str(guardrail_status.get("detail") or "Automation capital guardrails blocked a new unattended entry."),
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_guardrail"] = {
                "reason": rejection["reason"],
                "label": str(guardrail_status.get("label") or "Guardrail lock"),
                "detail": rejection["detail"],
                "at": _serialize_datetime(now),
            }
            runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
            _append_history(
                state,
                {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection},
            )
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )
        if drawdown_pct >= drawdown_audit_pct:
            rejection = {
                "decision": "stand_down",
                "reason": "drawdown_audit_lock",
                "detail": (
                    f"Automation drawdown is {drawdown_pct:.2f}%, which is beyond the {drawdown_audit_pct:.2f}% audit threshold."
                ),
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_guardrail"] = {
                "reason": rejection["reason"],
                "label": "Drawdown audit lock",
                "detail": rejection["detail"],
                "at": _serialize_datetime(now),
            }
            runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
            _append_history(state, {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection})
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )
        if drawdown_pct >= drawdown_stop_pct:
            rejection = {
                "decision": "stand_down",
                "reason": "drawdown_stop_lock",
                "detail": (
                    f"Automation drawdown is {drawdown_pct:.2f}%, which is beyond the {drawdown_stop_pct:.2f}% new-entry stop."
                ),
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_guardrail"] = {
                "reason": rejection["reason"],
                "label": "Drawdown stop lock",
                "detail": rejection["detail"],
                "at": _serialize_datetime(now),
            }
            runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
            _append_history(state, {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection})
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )
        daily_objective_report = automation_daily_objective_service.evaluate_daily_objective_entry_gate(
            db,
            tenant=tenant,
            state=state,
            profile_key=normalized_profile_key,
            linked_account=resolved_linked_account,
            owned_open=owned_open,
            owned_pending=owned_pending,
            owned_closed=owned_closed,
            monitored_open=None,
            effective_funds=effective_funds,
            now=now,
            actor=actor,
        )
        if bool(daily_objective_report.get("entries_blocked")):
            rejection = {
                "decision": "stand_down",
                "reason": "daily_objective_loss_budget_lock",
                "detail": (
                    "Daily objective loss budget is breached, so new paper entries are blocked for the session."
                ),
                "daily_objective": serialize_value(daily_objective_report),
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_guardrail"] = {
                "reason": rejection["reason"],
                "label": "Daily objective loss budget",
                "detail": rejection["detail"],
                "at": _serialize_datetime(now),
            }
            runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
            _append_history(state, {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection})
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )
        if len(owned_open) + len(owned_pending) >= int(settings_state["max_open_positions"]):
            rejection = {
                "decision": "stand_down",
                "reason": "capacity_reached",
                "detail": "Automation already has the maximum number of active tickets open or working.",
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_guardrail"] = None
            runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
            _append_history(
                state,
                {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection},
            )
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )

        candidates, watchlist = _select_candidates_from_watchlist(
            entry_state,
            owned_open=owned_open,
            owned_pending=owned_pending,
            current_equity=effective_funds,
        )
        settings_state = entry_settings_state
        current_collection_audit = dict(runtime_state.get("current_collection_audit") or {})
        watchlist_rows = list(watchlist.get("rows") or watchlist.get("results") or [])
        watchlist_rows_by_ticker = {
            str(item.get("ticker") or "").strip().upper(): dict(item)
            for item in watchlist_rows
            if str(item.get("ticker") or "").strip()
        }
        equity_candidates = [
            candidate
            for candidate in candidates
            if _normalize_automation_instrument(candidate.get("automation_instrument_type"), default="equity") != "listed_option"
        ]
        option_candidates: list[dict[str, Any]] = []
        path_evaluations = [
            item
            for item in list(watchlist.get("path_evaluations") or [])
            if str(item.get("instrument_type") or "").strip().lower() != "listed_option"
        ]
        if scheduled_options_enabled:
            options_scan_snapshot = _run_scheduled_options_scan(
                db,
                current_user=current_user,
                tickers=list(settings_state.get("tickers") or []),
            )
            option_candidates = [
                _build_scheduled_option_candidate(
                    scan_candidate,
                    watchlist_row=watchlist_rows_by_ticker.get(
                        str(scan_candidate.get("underlying") or "").strip().upper()
                    ),
                    now=now,
                )
                for scan_candidate in list(options_scan_snapshot.get("candidates") or [])
                if bool(scan_candidate.get("ready_to_execute"))
            ]
            path_evaluations.append(_build_scheduled_option_path_evaluation(options_scan_snapshot))
            _sync_scheduled_options_runtime(
                runtime_state,
                cycle_at=now,
                scan_snapshot=options_scan_snapshot,
                blocker=str(options_scan_snapshot.get("blocked_reason") or "").strip() or None,
            )
            current_collection_audit["options_scanned_candidate_count"] = int(
                options_scan_snapshot.get("candidate_count") or 0
            )
            current_collection_audit["options_ready_candidate_count"] = int(
                options_scan_snapshot.get("ready_candidate_count") or 0
            )
            if option_candidates:
                runtime_state["last_option_execution"] = serialize_value(
                    dict(option_candidates[0].get("option_execution") or {})
                )
            elif str(options_scan_snapshot.get("blocked_reason") or "").strip():
                runtime_state["last_option_execution"] = serialize_value(
                    {
                        "option_scan_status": str(options_scan_snapshot.get("status") or "").strip().lower() or "blocked",
                        "option_block_reason": str(options_scan_snapshot.get("blocked_reason") or "").strip() or None,
                        "detail": str(options_scan_snapshot.get("blocked_reason") or "").strip()
                        or "Scheduled options scan did not find a routeable contract.",
                        "last_option_refresh_at": _serialize_datetime(now),
                    }
                )
        candidates = equity_candidates + option_candidates
        automation_daily_objective_service.evaluate_daily_objective_entry_gate(
            db,
            tenant=tenant,
            state=state,
            profile_key=normalized_profile_key,
            linked_account=resolved_linked_account,
            owned_open=owned_open,
            owned_pending=owned_pending,
            owned_closed=owned_closed,
            monitored_open=None,
            effective_funds=effective_funds,
            clean_candidate_count=sum(1 for item in candidates if bool(item.get("auto_entry_eligible"))),
            now=now,
            actor=actor,
        )
        current_collection_audit["scanned_candidate_count"] = len(watchlist_rows)
        current_collection_audit["auto_entry_eligible_candidate_count"] = int(
            sum(1 for item in watchlist_rows if bool(item.get("auto_entry_eligible"))) + len(option_candidates)
        )
        runtime_state["current_collection_audit"] = serialize_value(current_collection_audit)
        runtime_state["last_path_evaluations"] = serialize_value(path_evaluations)
        watchlist["path_evaluations"] = serialize_value(path_evaluations)
        _persist_watchlist_validation_snapshot(
            current_user=current_user,
            cycle_id=cycle_id,
            captured_at=now,
            watchlist=watchlist,
        )
        if not candidates:
            watchlist_count = len(watchlist_rows)
            rejection = {
                "decision": "stand_down",
                "reason": "no_candidates" if watchlist_count <= 0 else "no_auto_entry_eligible",
                "detail": "No watchlist candidate cleared the automation filters on this cycle.",
                "watchlist_count": watchlist_count,
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
            runtime_state["last_candidate"] = None
            runtime_state["last_guardrail"] = None
            current_collection_audit = dict(runtime_state.get("current_collection_audit") or {})
            current_collection_audit["primary_blocker"] = str(rejection["reason"] or "").strip().lower() or None
            runtime_state["current_collection_audit"] = serialize_value(current_collection_audit)
            _append_history(
                state,
                {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection},
            )
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )

        if settings_state["execution_intent"] == "broker_live" and not bool(rollout_readiness.get("allows_live_rollout")):
            gated_candidate = dict(candidates[0] or {}) if candidates else {}
            rejection = {
                "decision": "stand_down",
                "reason": "broker_live_locked",
                "detail": str(rollout_readiness.get("basis") or "Broker-live routing is still locked behind the paper gate."),
                "ticker": str(gated_candidate.get("ticker") or "").strip().upper() or None,
            }
            _reset_trade_automation_error_streak(state)
            runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
            runtime_state["last_rejection"] = rejection
            runtime_state["last_decision"] = rejection
            runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
            runtime_state["last_candidate"] = serialize_value(gated_candidate) if gated_candidate else None
            runtime_state["last_guardrail"] = None
            _append_history(
                state,
                {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection},
            )
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )
        limited_live_rollout_gate_state: dict[str, Any] | None = None
        limited_live_rollout_gate_check: dict[str, Any] | None = None
        limited_live_cap_expansion_gate_check: dict[str, Any] | None = None
        limited_live_next_tier_cap_gate_check: dict[str, Any] | None = None
        if settings_state["execution_intent"] == "broker_live":
            limited_live_rollout_gate_state = _read_trade_automation_state(
                tenant,
                profile_key=_AUTOMATION_PERSONAL_PAPER_PROFILE,
            )
            limited_live_rollout_gate_check = (
                automation_limited_live_rollout_gate_service.evaluate_limited_live_rollout_entry_gate(
                    limited_live_rollout_gate_state,
                    now=now,
                    order_type=settings_state.get("order_type"),
                )
            )
            if not bool(limited_live_rollout_gate_check.get("allowed")):
                gated_candidate = dict(candidates[0] or {}) if candidates else {}
                rejection = {
                    "decision": "stand_down",
                    "reason": str(
                        limited_live_rollout_gate_check.get("reason")
                        or "limited_live_rollout_gate_locked"
                    ),
                    "detail": str(
                        limited_live_rollout_gate_check.get("detail")
                        or "Broker-live automation requires an active limited-live rollout allowance."
                    ),
                    "ticker": str(gated_candidate.get("ticker") or "").strip().upper() or None,
                    "limited_live_rollout_gate": serialize_value(limited_live_rollout_gate_check),
                }
                _reset_trade_automation_error_streak(state)
                runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
                runtime_state["last_rejection"] = rejection
                runtime_state["last_decision"] = rejection
                runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
                runtime_state["last_candidate"] = serialize_value(gated_candidate) if gated_candidate else None
                runtime_state["last_guardrail"] = {
                    "reason": rejection["reason"],
                    "label": "Limited-live rollout gate",
                    "detail": rejection["detail"],
                    "at": _serialize_datetime(now),
                }
                if "state_control_halt" in rejection["reason"] and limited_live_rollout_gate_state is not None:
                    automation_limited_live_safety_ladder_service.disable_limited_live_allowances_for_fault(
                        limited_live_rollout_gate_state,
                        reason=rejection["reason"],
                        now=now,
                        actor=actor,
                    )
                    _write_trade_automation_state(
                        tenant,
                        limited_live_rollout_gate_state,
                        profile_key=_AUTOMATION_PERSONAL_PAPER_PROFILE,
                    )
                _append_history(
                    state,
                    {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection},
                )
                return _finalize_trade_automation_cycle(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=normalized_profile_key,
                    linked_account=resolved_linked_account,
                    effective_funds=effective_funds,
                    rollout_readiness=rollout_readiness,
                    now=now,
                    cycle_id=cycle_id,
                )
            limited_live_cap_expansion_gate_check = (
                automation_limited_live_cap_expansion_gate_service.evaluate_limited_live_cap_expansion_entry_gate(
                    limited_live_rollout_gate_state,
                    base_gate_result=limited_live_rollout_gate_check,
                    now=now,
                    order_type=settings_state.get("order_type"),
                )
            )
            if not bool(limited_live_cap_expansion_gate_check.get("allowed")):
                gated_candidate = dict(candidates[0] or {}) if candidates else {}
                rejection = {
                    "decision": "stand_down",
                    "reason": str(
                        limited_live_cap_expansion_gate_check.get("reason")
                        or "limited_live_cap_expansion_gate_locked"
                    ),
                    "detail": str(
                        limited_live_cap_expansion_gate_check.get("detail")
                        or "Broker-live automation requires a valid limited-live cap allowance."
                    ),
                    "ticker": str(gated_candidate.get("ticker") or "").strip().upper() or None,
                    "limited_live_rollout_gate": serialize_value(limited_live_rollout_gate_check),
                    "limited_live_cap_expansion_gate": serialize_value(limited_live_cap_expansion_gate_check),
                }
                _reset_trade_automation_error_streak(state)
                runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
                runtime_state["last_rejection"] = rejection
                runtime_state["last_decision"] = rejection
                runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
                runtime_state["last_candidate"] = serialize_value(gated_candidate) if gated_candidate else None
                runtime_state["last_guardrail"] = {
                    "allowed": False,
                    "reason": rejection["reason"],
                    "detail": rejection["detail"],
                    "at": _serialize_datetime(now),
                }
                runtime_state["last_collection_blocker"] = rejection["reason"]
                _sync_scheduled_options_runtime(
                    runtime_state,
                    cycle_at=now,
                    blocker=rejection["reason"],
                )
                if "state_control_halt" in rejection["reason"] and limited_live_rollout_gate_state is not None:
                    automation_limited_live_safety_ladder_service.disable_limited_live_allowances_for_fault(
                        limited_live_rollout_gate_state,
                        reason=rejection["reason"],
                        now=now,
                        actor=actor,
                    )
                    _write_trade_automation_state(
                        tenant,
                        limited_live_rollout_gate_state,
                        profile_key=_AUTOMATION_PERSONAL_PAPER_PROFILE,
                    )
                return _finalize_trade_automation_cycle(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=profile_key,
                    linked_account=resolved_linked_account,
                    effective_funds=effective_funds,
                    rollout_readiness=rollout_readiness,
                    now=now,
                    cycle_id=cycle_id,
                )
            limited_live_next_tier_cap_gate_check = (
                automation_limited_live_next_tier_cap_gate_service.evaluate_limited_live_next_tier_cap_entry_gate(
                    limited_live_rollout_gate_state,
                    base_gate_result=limited_live_cap_expansion_gate_check,
                    now=now,
                    order_type=settings_state.get("order_type"),
                )
            )
            if not bool(limited_live_next_tier_cap_gate_check.get("allowed")):
                gated_candidate = dict(candidates[0] or {}) if candidates else {}
                rejection = {
                    "decision": "stand_down",
                    "reason": str(
                        limited_live_next_tier_cap_gate_check.get("reason")
                        or "limited_live_next_tier_cap_gate_locked"
                    ),
                    "detail": str(
                        limited_live_next_tier_cap_gate_check.get("detail")
                        or "Broker-live automation requires a valid limited-live next-tier cap allowance for higher-cap routing."
                    ),
                    "ticker": str(gated_candidate.get("ticker") or "").strip().upper() or None,
                    "limited_live_rollout_gate": serialize_value(limited_live_rollout_gate_check),
                    "limited_live_cap_expansion_gate": serialize_value(limited_live_cap_expansion_gate_check),
                    "limited_live_next_tier_cap_gate": serialize_value(limited_live_next_tier_cap_gate_check),
                }
                _reset_trade_automation_error_streak(state)
                runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
                runtime_state["last_rejection"] = rejection
                runtime_state["last_decision"] = rejection
                runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
                runtime_state["last_candidate"] = serialize_value(gated_candidate) if gated_candidate else None
                runtime_state["last_guardrail"] = {
                    "allowed": False,
                    "reason": rejection["reason"],
                    "detail": rejection["detail"],
                    "at": _serialize_datetime(now),
                }
                runtime_state["last_collection_blocker"] = rejection["reason"]
                _sync_scheduled_options_runtime(
                    runtime_state,
                    cycle_at=now,
                    blocker=rejection["reason"],
                )
                if "state_control_halt" in rejection["reason"] and limited_live_rollout_gate_state is not None:
                    automation_limited_live_safety_ladder_service.disable_limited_live_allowances_for_fault(
                        limited_live_rollout_gate_state,
                        reason=rejection["reason"],
                        now=now,
                        actor=actor,
                    )
                    _write_trade_automation_state(
                        tenant,
                        limited_live_rollout_gate_state,
                        profile_key=_AUTOMATION_PERSONAL_PAPER_PROFILE,
                    )
                return _finalize_trade_automation_cycle(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=profile_key,
                    linked_account=resolved_linked_account,
                    effective_funds=effective_funds,
                    rollout_readiness=rollout_readiness,
                    now=now,
                    cycle_id=cycle_id,
                )
            if bool(limited_live_next_tier_cap_gate_check.get("next_tier_cap_active")):
                settings_state = automation_limited_live_next_tier_cap_gate_service.apply_limited_live_next_tier_cap_entry_overlay(
                    settings_state,
                    limited_live_next_tier_cap_gate_check,
                )
            else:
                settings_state = automation_limited_live_cap_expansion_gate_service.apply_limited_live_cap_expansion_entry_overlay(
                    settings_state,
                    limited_live_cap_expansion_gate_check,
                )
        remaining_capacity = max(0, int(settings_state["max_open_positions"]) - (len(owned_open) + len(owned_pending)))
        active_targets = _build_active_trade_targets(open_trades, pending_orders)
        entries_by_target = dict(guardrails.get("entries_by_target") or {})
        opened_items: list[dict[str, Any]] = []
        candidate_rejections: list[dict[str, Any]] = []

        for candidate in candidates:
            if remaining_capacity <= 0:
                break

            ticker = str(candidate.get("ticker") or "").strip().upper()
            instrument_type = _normalize_automation_instrument(
                candidate.get("automation_instrument_type") or settings_state.get("instrument_type"),
                default="equity",
            )
            target_key = _build_automation_target_key(ticker, instrument_type)
            if not ticker or not target_key:
                continue

            if int(entries_by_target.get(target_key) or 0) >= int(settings_state["max_daily_entries_per_symbol"]):
                candidate_rejections.append(
                    {
                        "decision": "stand_down",
                        "reason": "symbol_daily_entry_cap",
                        "detail": f"{ticker} already reached the per-symbol daily entry cap for unattended {instrument_type.replace('_', ' ')} automation.",
                        "ticker": ticker,
                        "instrument_type": instrument_type,
                    }
                )
                _set_ticker_cooldown(state, ticker, instrument_type=instrument_type, now=now)
                continue

            open_same_symbol = (
                owned_open.loc[
                    owned_open.get("ticker", pd.Series("", index=owned_open.index)).astype(str).str.strip().str.upper().eq(ticker)
                ].copy()
                if not owned_open.empty and "ticker" in owned_open.columns
                else pd.DataFrame()
            )
            pending_same_symbol = (
                owned_pending.loc[
                    owned_pending.get("ticker", pd.Series("", index=owned_pending.index)).astype(str).str.strip().str.upper().eq(ticker)
                ].copy()
                if not owned_pending.empty and "ticker" in owned_pending.columns
                else pd.DataFrame()
            )
            same_symbol_rows = pd.concat([open_same_symbol, pending_same_symbol], ignore_index=True)

            if target_key in active_targets and same_symbol_rows.empty:
                candidate_rejections.append(
                    {
                        "decision": "stand_down",
                        "reason": "ticker_already_active",
                        "detail": f"{ticker} already has an open {instrument_type.replace('_', ' ')} position or working order on the desk.",
                        "ticker": ticker,
                        "instrument_type": instrument_type,
                    }
                )
                _set_ticker_cooldown(state, ticker, instrument_type=instrument_type, now=now)
                continue

            if not same_symbol_rows.empty:
                if not bool(settings_state.get("allow_pyramiding")):
                    candidate_rejections.append(
                        {
                            "decision": "stand_down",
                            "reason": "pyramiding_disabled",
                            "detail": f"{ticker} already has an active position and pyramiding is disabled.",
                            "ticker": ticker,
                            "instrument_type": instrument_type,
                        }
                    )
                    continue
                if len(same_symbol_rows.index) >= 2:
                    candidate_rejections.append(
                        {
                            "decision": "stand_down",
                            "reason": "symbol_pyramid_cap",
                            "detail": f"{ticker} already has a prior pyramid add and will not be increased again automatically.",
                            "ticker": ticker,
                            "instrument_type": instrument_type,
                        }
                    )
                    continue
                current_price = _coerce_candidate_price(candidate, "live_price", "close", "last_price")
                winner_rows = 0
                for active_row in same_symbol_rows.to_dict(orient="records"):
                    entry_price = _coerce_candidate_price(
                        active_row,
                        "live_price_at_open",
                        "actual_fill_price",
                        "broker_filled_avg_price",
                    )
                    active_verdict = str(active_row.get("verdict") or candidate.get("verdict") or "").strip().upper()
                    if current_price is None or entry_price is None:
                        continue
                    if active_verdict == "BULLISH" and float(current_price) > float(entry_price):
                        winner_rows += 1
                    elif active_verdict == "BEARISH" and float(current_price) < float(entry_price):
                        winner_rows += 1
                if winner_rows <= 0:
                    candidate_rejections.append(
                        {
                            "decision": "stand_down",
                            "reason": "averaging_down_blocked",
                            "detail": f"{ticker} is not trading in favor of the existing position, so the algorithm will not add to it.",
                            "ticker": ticker,
                            "instrument_type": instrument_type,
                        }
                    )
                    continue

            risk_decision = risk_control_service.evaluate_candidate_risk_controls(
                candidate,
                settings_state=settings_state,
                owned_open=owned_open,
                owned_pending=owned_pending,
                session=session,
                current_equity=current_equity,
            )
            if not risk_decision.allowed:
                if instrument_type == "listed_option":
                    _record_scheduled_options_event(
                        db,
                        tenant=tenant,
                        event_type="options.paper_entry_blocked",
                        aggregate_type="option_automation_scan",
                        aggregate_id=str(candidate.get("contract_symbol") or ticker).strip() or None,
                        payload={
                            "ticker": ticker,
                            "contract_symbol": str(candidate.get("contract_symbol") or "").strip().upper() or None,
                            "reason": risk_decision.reason,
                            "detail": risk_decision.detail,
                            "risk_metrics": serialize_value(risk_decision.metrics),
                        },
                        metadata={"cycle_id": cycle_id, "profile_key": normalized_profile_key},
                    )
                candidate_rejections.append(
                    {
                        "decision": "stand_down",
                        "reason": risk_decision.reason,
                        "detail": risk_decision.detail,
                        "ticker": ticker,
                        "instrument_type": instrument_type,
                        "risk_metrics": serialize_value(risk_decision.metrics),
                    }
                )
                continue

            option_right = str(candidate.get("option_right") or candidate.get("direction") or "").strip().lower()
            verdict = str(candidate.get("verdict") or "").strip().upper()
            if option_right not in {"call", "put"}:
                if verdict == "BULLISH":
                    option_right = "call"
                elif verdict == "BEARISH":
                    option_right = "put"

            if instrument_type == "listed_option":
                if str(candidate.get("scan_run_source") or "").strip().lower() == "options_automation":
                    option_refresh = {
                        "allowed": True,
                        "candidate": dict(candidate),
                        "option_right": option_right,
                        "reason": None,
                        "detail": "Scheduled options automation is using the exact scanned contract from the latest paper-ready scan.",
                        "diagnostics": dict(candidate.get("option_execution") or {}),
                    }
                else:
                    option_refresh = _refresh_automation_option_candidate(
                        candidate,
                        option_right=option_right,
                        now=now,
                    )
                runtime_state["last_option_execution"] = serialize_value(option_refresh["diagnostics"])
                if not bool(option_refresh["allowed"]):
                    current_collection_audit = dict(runtime_state.get("current_collection_audit") or {})
                    current_collection_audit["primary_blocker"] = option_refresh["reason"]
                    current_collection_audit["options_last_blocker"] = option_refresh["reason"]
                    runtime_state["current_collection_audit"] = serialize_value(current_collection_audit)
                    _sync_scheduled_options_runtime(
                        runtime_state,
                        cycle_at=now,
                        blocker=str(option_refresh["detail"] or option_refresh["reason"] or "").strip() or None,
                    )
                    candidate_rejections.append(
                        {
                            "decision": "stand_down",
                            "reason": option_refresh["reason"],
                            "detail": option_refresh["detail"],
                            "ticker": ticker,
                            "instrument_type": instrument_type,
                            "option_execution": serialize_value(option_refresh["diagnostics"]),
                        }
                    )
                    _record_scheduled_options_event(
                        db,
                        tenant=tenant,
                        event_type="options.paper_entry_blocked",
                        aggregate_type="option_automation_scan",
                        aggregate_id=str(candidate.get("contract_symbol") or ticker).strip() or None,
                        payload={
                            "ticker": ticker,
                            "contract_symbol": str(candidate.get("contract_symbol") or "").strip().upper() or None,
                            "reason": option_refresh["reason"],
                            "detail": option_refresh["detail"],
                            "option_execution": serialize_value(option_refresh["diagnostics"]),
                        },
                        metadata={"cycle_id": cycle_id, "profile_key": normalized_profile_key},
                    )
                    continue
                candidate = dict(option_refresh["candidate"])
                option_right = str(option_refresh.get("option_right") or option_right or "").strip().lower()

            order_fields = _build_automation_order_fields(
                candidate=candidate,
                settings_state=settings_state,
                instrument_type=instrument_type,
            )
            if settings_state["execution_intent"] == "broker_live" and limited_live_rollout_gate_state is not None:
                limited_live_rollout_gate_check = (
                    automation_limited_live_rollout_gate_service.evaluate_limited_live_rollout_entry_gate(
                        limited_live_rollout_gate_state,
                        now=now,
                        order_type=str(order_fields.get("order_type") or settings_state.get("order_type") or ""),
                    )
                )
                if not bool(limited_live_rollout_gate_check.get("allowed")):
                    candidate_rejections.append(
                        {
                            "decision": "stand_down",
                            "reason": limited_live_rollout_gate_check.get("reason")
                            or "limited_live_rollout_gate_locked",
                            "detail": limited_live_rollout_gate_check.get("detail")
                            or "Broker-live automation requires an active limited-live rollout allowance.",
                            "ticker": ticker,
                            "instrument_type": instrument_type,
                            "limited_live_rollout_gate": serialize_value(limited_live_rollout_gate_check),
                        }
                    )
                    continue
                limited_live_cap_expansion_gate_check = (
                    automation_limited_live_cap_expansion_gate_service.evaluate_limited_live_cap_expansion_entry_gate(
                        limited_live_rollout_gate_state,
                        base_gate_result=limited_live_rollout_gate_check,
                        now=now,
                        order_type=str(order_fields.get("order_type") or settings_state.get("order_type") or ""),
                    )
                )
                if not bool(limited_live_cap_expansion_gate_check.get("allowed")):
                    candidate_rejections.append(
                        {
                            "decision": "stand_down",
                            "reason": limited_live_cap_expansion_gate_check.get("reason")
                            or "limited_live_cap_expansion_gate_locked",
                            "detail": limited_live_cap_expansion_gate_check.get("detail")
                            or "Broker-live automation requires a valid limited-live cap allowance.",
                            "ticker": ticker,
                            "instrument_type": instrument_type,
                            "limited_live_rollout_gate": serialize_value(limited_live_rollout_gate_check),
                            "limited_live_cap_expansion_gate": serialize_value(limited_live_cap_expansion_gate_check),
                        }
                    )
                    continue
                limited_live_next_tier_cap_gate_check = (
                    automation_limited_live_next_tier_cap_gate_service.evaluate_limited_live_next_tier_cap_entry_gate(
                        limited_live_rollout_gate_state,
                        base_gate_result=limited_live_cap_expansion_gate_check,
                        now=now,
                        order_type=str(order_fields.get("order_type") or settings_state.get("order_type") or ""),
                        estimated_notional=float(settings_state.get("max_notional_per_trade") or 0.0),
                    )
                )
                if not bool(limited_live_next_tier_cap_gate_check.get("allowed")):
                    candidate_rejections.append(
                        {
                            "decision": "stand_down",
                            "reason": limited_live_next_tier_cap_gate_check.get("reason")
                            or "limited_live_next_tier_cap_gate_locked",
                            "detail": limited_live_next_tier_cap_gate_check.get("detail")
                            or "Broker-live automation requires a valid limited-live next-tier cap allowance for higher-cap routing.",
                            "ticker": ticker,
                            "instrument_type": instrument_type,
                            "limited_live_rollout_gate": serialize_value(limited_live_rollout_gate_check),
                            "limited_live_cap_expansion_gate": serialize_value(limited_live_cap_expansion_gate_check),
                            "limited_live_next_tier_cap_gate": serialize_value(limited_live_next_tier_cap_gate_check),
                        }
                    )
                    continue
            current_collection_audit = dict(runtime_state.get("current_collection_audit") or {})
            current_collection_audit["submitted_order_count"] = int(
                current_collection_audit.get("submitted_order_count") or 0
            ) + 1
            runtime_state["current_collection_audit"] = serialize_value(current_collection_audit)
            live_limited_route_family = "limited_live_rollout"
            if (
                settings_state["execution_intent"] == "broker_live"
                and limited_live_next_tier_cap_gate_check
                and limited_live_next_tier_cap_gate_check.get("allowed")
                and limited_live_next_tier_cap_gate_check.get("next_tier_cap_active")
            ):
                live_limited_route_family = "limited_live_next_tier_cap"
            elif (
                settings_state["execution_intent"] == "broker_live"
                and limited_live_cap_expansion_gate_check
                and limited_live_cap_expansion_gate_check.get("allowed")
                and limited_live_cap_expansion_gate_check.get("expansion_active")
            ):
                live_limited_route_family = "limited_live_cap_expansion"
            try:
                open_result = open_trade_from_request(
                    OpenTradeRequest(
                        ticker=ticker,
                        interval=settings_state["interval"],
                        horizon=int(settings_state["horizon"]),
                        live_price=_coerce_candidate_price(candidate, "live_price", "close", "last_price"),
                        account_size=float(effective_funds),
                        risk_percent=float(effective_risk_percent),
                        instrument_type=instrument_type,
                        broker_side="buy",
                        option_strategy="long_option" if instrument_type == "listed_option" else None,
                        option_right=option_right or None,
                        contract_symbol=str(candidate.get("contract_symbol") or "").strip() or None,
                        contract_expiration=str(candidate.get("contract_expiration") or "").strip() or None,
                        contract_strike=_coerce_candidate_price(candidate, "contract_strike"),
                        contract_bid=_coerce_candidate_price(candidate, "contract_bid"),
                        contract_ask=_coerce_candidate_price(candidate, "contract_ask"),
                        contract_mid=_coerce_candidate_price(candidate, "contract_mid", "current_contract_mid"),
                        contract_spread_pct=_coerce_candidate_price(candidate, "contract_spread_pct", "spread_pct"),
                        contract_volume=int(pd.to_numeric(candidate.get("contract_volume"), errors="coerce") or 0)
                        if instrument_type == "listed_option"
                        else None,
                        contract_open_interest=int(pd.to_numeric(candidate.get("contract_open_interest"), errors="coerce") or 0)
                        if instrument_type == "listed_option"
                        else None,
                        contract_quote_timestamp=str(candidate.get("contract_quote_timestamp") or "").strip() or None,
                        execution_intent=effective_execution_intent,
                        extended_hours=(
                            False
                            if instrument_type == "listed_option"
                            else str(order_fields.get("time_in_force") or "").strip().lower() == "day_ext"
                        ),
                        capital_preservation_mode=True,
                        fractional_shares_only=bool(settings_state["fractional_shares_only"]) if instrument_type == "equity" else False,
                        regular_hours_only=True if instrument_type == "listed_option" else bool(settings_state["regular_hours_only"]),
                        max_daily_loss_r=float(settings_state["max_daily_loss_r"]),
                        max_consecutive_losses=int(settings_state["max_consecutive_losses"]),
                        max_open_positions=int(settings_state["max_open_positions"]),
                        max_notional_per_trade=float(settings_state["max_notional_per_trade"]),
                        equities_only=bool(settings_state["equities_only"]) if instrument_type == "equity" else False,
                        limit_orders_only=str(order_fields["order_type"]) != "market",
                        long_only=bool(settings_state["long_only"]),
                        route_family=(
                            live_limited_route_family
                            if settings_state["execution_intent"] == "broker_live"
                            and limited_live_rollout_gate_check
                            and limited_live_rollout_gate_check.get("allowed")
                            else str(candidate.get("route_family") or "current")
                        ),
                        route_version=str(candidate.get("route_version") or "ranked_entry_v1"),
                        automation_entry_reason=(
                            live_limited_route_family
                            if settings_state["execution_intent"] == "broker_live"
                            and limited_live_rollout_gate_check
                            and limited_live_rollout_gate_check.get("allowed")
                            else str(candidate.get("automation_entry_reason") or "ranked_candidate")
                        ),
                        thesis_direction=str(candidate.get("verdict") or "").strip().upper() or None,
                        account_target_type="linked_client" if normalized_profile_key.startswith("linked:") else "personal",
                        linked_account_id=getattr(resolved_linked_account, "id", None) if normalized_profile_key.startswith("linked:") else None,
                        execution_mode="automated_entry",
                        source="options_automation_scheduled" if instrument_type == "listed_option" else "trade_automation",
                        **order_fields,
                    ),
                    db=db,
                    current_user=current_user,
                )
            except Exception as exc:
                if instrument_type == "listed_option":
                    _record_scheduled_options_event(
                        db,
                        tenant=tenant,
                        event_type="options.paper_entry_blocked",
                        aggregate_type="option_automation_scan",
                        aggregate_id=str(candidate.get("contract_symbol") or ticker).strip() or None,
                        payload={
                            "ticker": ticker,
                            "contract_symbol": str(candidate.get("contract_symbol") or "").strip().upper() or None,
                            "reason": "scheduled_entry_failed",
                            "detail": str(exc),
                            "option_execution": serialize_value(dict(candidate.get("option_execution") or {})),
                        },
                        metadata={"cycle_id": cycle_id, "profile_key": normalized_profile_key},
                    )
                raise
            current_collection_audit = dict(runtime_state.get("current_collection_audit") or {})
            current_collection_audit["broker_acknowledgement_count"] = int(
                current_collection_audit.get("broker_acknowledgement_count") or 0
            ) + 1
            runtime_state["current_collection_audit"] = serialize_value(current_collection_audit)

            record = dict(open_result.get("record") or {})
            pending_order = dict(open_result.get("pending_order") or {})
            limited_live_rollout_id = None
            limited_live_cap_expansion_id = None
            limited_live_next_tier_cap_id = None
            if settings_state["execution_intent"] == "broker_live" and limited_live_rollout_gate_check:
                limited_live_rollout_id = str(limited_live_rollout_gate_check.get("rollout_id") or "").strip() or None
            if settings_state["execution_intent"] == "broker_live" and limited_live_cap_expansion_gate_check:
                limited_live_cap_expansion_id = (
                    str(limited_live_cap_expansion_gate_check.get("cap_expansion_id") or "").strip() or None
                )
            if settings_state["execution_intent"] == "broker_live" and limited_live_next_tier_cap_gate_check:
                limited_live_next_tier_cap_id = (
                    str(limited_live_next_tier_cap_gate_check.get("next_tier_cap_id") or "").strip() or None
                )
            marker_fields = {
                "automation_origin": _AUTOMATION_MARKER,
                "automation_tenant_id": tenant.id,
                "automation_tenant_slug": tenant.slug,
                "automation_profile_key": normalized_profile_key,
                "automation_linked_account_id": getattr(resolved_linked_account, "id", None),
                "automation_cycle_id": cycle_id,
                "automation_execution_intent": effective_execution_intent,
                "automation_pyramid_leg": 1 if same_symbol_rows.empty else 2,
            }
            marker_fields.update(automation_accuracy_calibration_service.build_accuracy_marker_fields(candidate))
            if limited_live_rollout_id:
                marker_fields["automation_entry_reason"] = live_limited_route_family
                marker_fields["route_family"] = live_limited_route_family
                marker_fields["limited_live_rollout_id"] = limited_live_rollout_id
            if limited_live_cap_expansion_id:
                marker_fields["limited_live_cap_expansion_id"] = limited_live_cap_expansion_id
            if limited_live_next_tier_cap_id:
                marker_fields["limited_live_next_tier_cap_id"] = limited_live_next_tier_cap_id
            marker_order_id = str(pending_order.get("order_id") or record.get("order_id") or "").strip()
            if open_result.get("position_opened"):
                updated_marker_row = sdm.update_open_trade(
                    marker_fields,
                    trade_id=str(record.get("trade_id") or "").strip() or None,
                    order_id=str(record.get("order_id") or "").strip() or None,
                )
            else:
                updated_marker_row = sdm.update_pending_order(
                    marker_order_id,
                    marker_fields,
                )
            if updated_marker_row is None:
                marker_warning = {
                    "stage": "automation_marker_update",
                    "trade_id": str(record.get("trade_id") or pending_order.get("trade_id") or "").strip() or None,
                    "order_id": marker_order_id or None,
                    "route_correlation_id": str(
                        record.get("route_correlation_id") or pending_order.get("route_correlation_id") or ""
                    ).strip()
                    or None,
                }
                runtime_state["last_marker_update_warning"] = marker_warning
                current_collection_audit = dict(runtime_state.get("current_collection_audit") or {})
                current_collection_audit["marker_update_warning_count"] = int(
                    current_collection_audit.get("marker_update_warning_count") or 0
                ) + 1
                runtime_state["current_collection_audit"] = serialize_value(current_collection_audit)
            if limited_live_rollout_id and limited_live_rollout_gate_state is not None:
                order_evidence = {
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "instrument_type": instrument_type,
                    "order_id": marker_order_id or None,
                    "trade_id": str(record.get("trade_id") or pending_order.get("trade_id") or "").strip()
                    or None,
                    "broker_order_id": str(
                        record.get("broker_order_id") or pending_order.get("broker_order_id") or ""
                    ).strip()
                    or None,
                    "broker_status": str(
                        record.get("broker_status") or pending_order.get("broker_status") or ""
                    ).strip()
                    or None,
                    "position_opened": bool(open_result.get("position_opened")),
                    "order_type": order_fields.get("order_type"),
                    "limit_price": order_fields.get("limit_price"),
                    "route_family": live_limited_route_family,
                }
                automation_limited_live_rollout_gate_service.record_limited_live_rollout_order_use(
                    db,
                    tenant=tenant,
                    state=limited_live_rollout_gate_state,
                    profile_key=_AUTOMATION_PERSONAL_PAPER_PROFILE,
                    linked_account=resolved_linked_account,
                    actor=actor,
                    now=now,
                    order_evidence=order_evidence,
                )
                if limited_live_cap_expansion_id:
                    automation_limited_live_cap_expansion_gate_service.record_limited_live_cap_expansion_order_use(
                        db,
                        tenant=tenant,
                        state=limited_live_rollout_gate_state,
                        profile_key=_AUTOMATION_PERSONAL_PAPER_PROFILE,
                        linked_account=resolved_linked_account,
                        actor=actor,
                        now=now,
                        order_evidence={
                            **order_evidence,
                            "limited_live_cap_expansion_id": limited_live_cap_expansion_id,
                        },
                    )
                if limited_live_next_tier_cap_id:
                    automation_limited_live_next_tier_cap_gate_service.record_limited_live_next_tier_cap_order_use(
                        db,
                        tenant=tenant,
                        state=limited_live_rollout_gate_state,
                        profile_key=_AUTOMATION_PERSONAL_PAPER_PROFILE,
                        linked_account=resolved_linked_account,
                        actor=actor,
                        now=now,
                        order_evidence={
                            **order_evidence,
                            "limited_live_cap_expansion_id": limited_live_cap_expansion_id,
                            "limited_live_next_tier_cap_id": limited_live_next_tier_cap_id,
                        },
                    )
                _write_trade_automation_state(
                    tenant,
                    limited_live_rollout_gate_state,
                    profile_key=_AUTOMATION_PERSONAL_PAPER_PROFILE,
                )
            if instrument_type == "listed_option":
                _record_scheduled_options_event(
                    db,
                    tenant=tenant,
                    event_type="options.paper_entry_submitted",
                    aggregate_type="option_position",
                    aggregate_id=str(record.get("trade_id") or pending_order.get("order_id") or candidate.get("contract_symbol") or "").strip() or None,
                    payload={
                        "ticker": ticker,
                        "contract_symbol": str(candidate.get("contract_symbol") or "").strip().upper() or None,
                        "trade_id": str(record.get("trade_id") or "").strip() or None,
                        "order_id": str(pending_order.get("order_id") or record.get("order_id") or "").strip() or None,
                        "broker_order_id": str(record.get("broker_order_id") or pending_order.get("broker_order_id") or "").strip() or None,
                        "broker_status": str(record.get("broker_status") or pending_order.get("broker_status") or "").strip() or None,
                        "execution_intent": effective_execution_intent,
                        "scope": normalized_profile_key,
                        "entry_limit_price": order_fields.get("limit_price"),
                        "mid": _coerce_candidate_price(candidate, "contract_mid", "current_contract_mid"),
                        "bid": _coerce_candidate_price(candidate, "contract_bid"),
                        "ask": _coerce_candidate_price(candidate, "contract_ask"),
                        "quote_timestamp": str(candidate.get("contract_quote_timestamp") or "").strip() or None,
                        "position_opened": bool(open_result.get("position_opened")),
                        "option_execution": serialize_value(dict(candidate.get("option_execution") or {})),
                    },
                    metadata={"cycle_id": cycle_id, "profile_key": normalized_profile_key},
                )

            execution_snapshot = serialize_value(open_result.get("execution") or {})
            opened_items.append(
                {
                    "ticker": ticker,
                    "instrument_type": instrument_type,
                    "position_opened": bool(open_result.get("position_opened")),
                    "execution": execution_snapshot,
                    "candidate": serialize_value(candidate),
                }
            )
            automation_accuracy_calibration_service.record_selected_candidate(
                state,
                candidate=candidate,
                now=now,
                cycle_id=cycle_id,
            )
            if instrument_type == "listed_option":
                _sync_scheduled_options_runtime(
                    runtime_state,
                    cycle_at=now,
                    last_entry={
                        "ticker": ticker,
                        "contract_symbol": str(candidate.get("contract_symbol") or "").strip().upper() or None,
                        "option_right": option_right or None,
                        "limit_price": order_fields.get("limit_price"),
                        "position_opened": bool(open_result.get("position_opened")),
                        "at": _serialize_datetime(now),
                    },
                )
            active_targets.add(target_key)
            entries_by_target[target_key] = int(entries_by_target.get(target_key) or 0) + 1
            remaining_capacity -= 1
            _set_ticker_cooldown(state, ticker, instrument_type=instrument_type, now=now)
            _append_history(
                state,
                {
                    "type": "open_trade",
                    "cycle_id": cycle_id,
                    "at": _serialize_datetime(now),
                    "ticker": ticker,
                    "instrument_type": instrument_type,
                    "position_opened": bool(open_result.get("position_opened")),
                    "execution": execution_snapshot,
                    "candidate": serialize_value(candidate),
                },
            )

        current_collection_audit = dict(runtime_state.get("current_collection_audit") or {})
        current_collection_audit["blocked_candidates_by_reason"] = _blocked_candidate_counts(candidate_rejections)
        runtime_state["current_collection_audit"] = serialize_value(current_collection_audit)
        if opened_items:
            _reset_trade_automation_error_streak(state)
            runtime_state["success_count"] = int(runtime_state.get("success_count") or 0) + 1
            runtime_state["last_success_at"] = _serialize_datetime(now)
            runtime_state["last_rejection"] = serialize_value(candidate_rejections[0]) if candidate_rejections else None
            runtime_state["last_candidate"] = serialize_value(opened_items[-1]["candidate"])
            runtime_state["last_guardrail"] = None
            routed_labels = ", ".join(
                f"{item['ticker']} {str(item['instrument_type']).replace('_', ' ')}" for item in opened_items
            )
            runtime_state["last_decision"] = {
                "decision": "opened",
                "count": len(opened_items),
                "detail": f"Automation routed {routed_labels} through {effective_execution_intent.replace('_', ' ')}.",
            }
            runtime_state["last_action"] = {
                "type": "open_trade",
                "cycle_id": cycle_id,
                "opened_items": serialize_value(opened_items),
            }
            return _finalize_trade_automation_cycle(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=resolved_linked_account,
                effective_funds=effective_funds,
                rollout_readiness=rollout_readiness,
                now=now,
                cycle_id=cycle_id,
            )

        rejection = candidate_rejections[0] if candidate_rejections else {
            "decision": "stand_down",
            "reason": "no_eligible_candidate",
            "detail": "Candidates were present, but none cleared the unattended entry guardrails for this cycle.",
        }
        current_collection_audit = dict(runtime_state.get("current_collection_audit") or {})
        current_collection_audit["blocked_candidates_by_reason"] = _blocked_candidate_counts(candidate_rejections)
        runtime_state["current_collection_audit"] = serialize_value(current_collection_audit)
        _reset_trade_automation_error_streak(state)
        runtime_state["rejection_count"] = int(runtime_state.get("rejection_count") or 0) + 1
        runtime_state["last_rejection"] = rejection
        runtime_state["last_decision"] = rejection
        runtime_state["last_action"] = {"type": "stand_down", "detail": rejection["detail"]}
        runtime_state["last_candidate"] = serialize_value(candidates[0]) if candidates else None
        runtime_state["last_guardrail"] = {
            "reason": rejection["reason"],
            "label": "Entry filter",
            "detail": rejection["detail"],
            "at": _serialize_datetime(now),
        }
        _append_history(
            state,
            {"type": "stand_down", "cycle_id": cycle_id, "at": _serialize_datetime(now), **rejection},
        )
        return _finalize_trade_automation_cycle(
            db,
            tenant=tenant,
            state=state,
            profile_key=normalized_profile_key,
            linked_account=resolved_linked_account,
            effective_funds=effective_funds,
            rollout_readiness=rollout_readiness,
            now=now,
            cycle_id=cycle_id,
        )
    except Exception as exc:  # pragma: no cover - defensive automation guard
        error_reason = ""
        if isinstance(exc, ServiceError):
            error_reason = str(exc.details.get("collection_blocker") or exc.error_code or "").strip().lower()
        current_collection_audit = dict(runtime_state.get("current_collection_audit") or {})
        if error_reason == "ledger_persistence_failed":
            current_collection_audit["primary_blocker"] = "ledger_persistence_failed"
            runtime_state["last_collection_blocker"] = "ledger_persistence_failed"
            runtime_state["last_rejection"] = {
                "decision": "error",
                "reason": "ledger_persistence_failed",
                "detail": str(exc),
            }
        elif (
            int(current_collection_audit.get("submitted_order_count") or 0)
            > int(current_collection_audit.get("broker_acknowledgement_count") or 0)
        ):
            current_collection_audit["primary_blocker"] = "broker_submit_failed"
        runtime_state["current_collection_audit"] = serialize_value(current_collection_audit)
        runtime_state["error_count"] = int(runtime_state.get("error_count") or 0) + 1
        runtime_state["error_streak"] = int(runtime_state.get("error_streak") or 0) + 1
        runtime_state["last_error_at"] = _serialize_datetime(_utc_now())
        runtime_state["last_error"] = str(exc)
        runtime_state["last_action"] = {"type": "error", "cycle_id": cycle_id, "detail": str(exc)}
        runtime_state["last_decision"] = {
            "decision": "error",
            "reason": error_reason or "cycle_failed",
            "detail": str(exc),
        }
        if not runtime_state.get("last_rejection"):
            runtime_state["last_rejection"] = {
                "decision": "error",
                "reason": error_reason or "cycle_failed",
                "detail": str(exc),
            }
        if int(runtime_state.get("error_streak") or 0) >= int(settings_state.get("max_error_streak") or 0):
            settings_state["kill_switch"] = True
            runtime_state["last_guardrail"] = {
                "reason": "error_streak_lock",
                "label": "Worker error lock",
                "detail": (
                    f"The worker hit {int(runtime_state.get('error_streak') or 0)} consecutive cycle "
                    f"error{'s' if int(runtime_state.get('error_streak') or 0) != 1 else ''} and auto-stopped."
                ),
                "at": _serialize_datetime(now),
            }
            _append_history(
                state,
                {
                    "type": "guardrail_lock",
                    "cycle_id": cycle_id,
                    "at": _serialize_datetime(now),
                    "reason": "error_streak_lock",
                    "detail": runtime_state["last_guardrail"]["detail"],
                },
            )
        _append_history(
            state,
            {
                "type": "error",
                "cycle_id": cycle_id,
                "at": _serialize_datetime(now),
                "detail": str(exc),
            },
        )
        return _finalize_trade_automation_cycle(
            db,
            tenant=tenant,
            state=state,
            profile_key=normalized_profile_key,
            linked_account=resolved_linked_account,
            effective_funds=effective_funds,
            rollout_readiness=rollout_readiness,
            now=now,
            cycle_id=cycle_id,
        )


def get_tenant_trade_automation_snapshot(
    db: Session,
    *,
    current_user: Any,
    scope: str | None = None,
    scope_key: str | None = None,
    linked_account_id: str | None = None,
) -> dict[str, Any]:
    _assert_trade_automation_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    profile_key, linked_account = _resolve_trade_automation_profile_selection(
        db,
        tenant=tenant,
        scope=scope,
        scope_key=scope_key,
        linked_account_id=linked_account_id,
    )
    state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
    trade_summary = get_trade_summary(db=db, current_user=current_user)
    return _build_snapshot_payload(
        tenant,
        state,
        profile_key=profile_key,
        linked_account=linked_account,
        rollout_readiness=dict(trade_summary.get("rollout_readiness") or {}),
        db=db,
    )


def update_tenant_trade_automation_settings(
    db: Session,
    *,
    current_user: Any,
    updates: OrganizationTradeAutomationUpdateRequest | dict[str, Any],
) -> dict[str, Any]:
    _assert_trade_automation_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    payload = updates.model_dump(exclude_unset=True) if hasattr(updates, "model_dump") else dict(updates or {})
    profile_key, linked_account = _resolve_trade_automation_profile_selection(
        db,
        tenant=tenant,
        scope=payload.get("scope"),
        scope_key=payload.get("scope_key"),
        linked_account_id=payload.get("linked_account_id"),
    )
    state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
    trade_summary = get_trade_summary(db=db, current_user=current_user)
    rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
    initial_collection_phase = _build_collection_phase_state(
        rollout_readiness=rollout_readiness,
        runtime_state=state["runtime"],
    )
    _apply_collection_phase_runtime(state["runtime"], initial_collection_phase)
    settings_state = dict(state["settings"])

    if "enabled" in payload:
        settings_state["enabled"] = _normalize_bool(payload.get("enabled"), settings_state["enabled"])
    if "execution_intent" in payload and profile_key not in {
        _AUTOMATION_PERSONAL_PAPER_PROFILE,
        _AUTOMATION_PERSONAL_LIVE_PROFILE,
    }:
        execution_intent = str(payload.get("execution_intent") or "").strip().lower()
        if execution_intent not in _AUTOMATION_ROUTE_CHOICES:
            raise ValidationError("Automation execution route is not supported.")
        settings_state["execution_intent"] = execution_intent
    if "tickers" in payload:
        raw_tickers = payload.get("tickers")
        if isinstance(raw_tickers, str):
            raw_tickers = [item for item in raw_tickers.replace("\n", ",").split(",")]
        tickers = _normalize_tickers(raw_tickers)
        if not tickers:
            raise ValidationError("Automation needs at least one ticker in its board.")
        settings_state["tickers"] = tickers
    if "interval" in payload:
        interval = str(payload.get("interval") or "").strip().lower()
        if interval not in _AUTOMATION_INTERVAL_CHOICES:
            raise ValidationError("Automation interval is not supported.")
        settings_state["interval"] = interval
    if "horizon" in payload:
        settings_state["horizon"] = _normalize_int(payload.get("horizon"), settings_state["horizon"], minimum=1, maximum=50)
    if "cycle_interval_seconds" in payload:
        settings_state["cycle_interval_seconds"] = _normalize_int(
            payload.get("cycle_interval_seconds"),
            settings_state["cycle_interval_seconds"],
            minimum=15,
            maximum=3600,
        )
    if "cooldown_minutes" in payload:
        settings_state["cooldown_minutes"] = _normalize_int(
            payload.get("cooldown_minutes"),
            settings_state["cooldown_minutes"],
            minimum=0,
            maximum=1440,
        )
    if "risk_percent" in payload:
        settings_state["risk_percent"] = _normalize_float(
            payload.get("risk_percent"),
            settings_state["risk_percent"],
            minimum=0.05,
            maximum=5.0,
        )
    if "effective_funds_multiplier" in payload:
        settings_state["effective_funds_multiplier"] = _normalize_float(
            payload.get("effective_funds_multiplier"),
            settings_state.get("effective_funds_multiplier", 1.0),
            minimum=1.0,
            maximum=10.0,
        )
    if "auto_trade_equities" in payload:
        settings_state["auto_trade_equities"] = _normalize_bool(
            payload.get("auto_trade_equities"),
            settings_state.get("auto_trade_equities", True),
        )
    if "auto_trade_listed_options" in payload:
        settings_state["auto_trade_listed_options"] = _normalize_bool(
            payload.get("auto_trade_listed_options"),
            settings_state.get("auto_trade_listed_options", True),
        )
    if "instrument_type" in payload:
        instrument_type = _normalize_automation_instrument(payload.get("instrument_type"), default="equity")
        if instrument_type not in _AUTOMATION_INSTRUMENT_CHOICES:
            raise ValidationError("Automation instrument type is not supported.")
        settings_state["instrument_type"] = instrument_type
        if "auto_trade_equities" not in payload and "auto_trade_listed_options" not in payload:
            settings_state["auto_trade_equities"] = instrument_type == "equity"
            settings_state["auto_trade_listed_options"] = instrument_type == "listed_option"
    if "order_type" in payload:
        order_type = str(payload.get("order_type") or "").strip().lower()
        if order_type not in _AUTOMATION_ORDER_CHOICES:
            raise ValidationError("Automation currently supports market and limit orders only.")
        settings_state["order_type"] = order_type
    if "time_in_force" in payload:
        time_in_force = str(payload.get("time_in_force") or "").strip().lower()
        if time_in_force not in _AUTOMATION_TIF_CHOICES:
            raise ValidationError("Automation time-in-force is not supported.")
        settings_state["time_in_force"] = time_in_force

    for key in (
        "regular_hours_only",
        "auto_sync_orders",
        "auto_manage_positions",
        "auto_flatten_before_close",
        "long_only",
        "equities_only",
        "fractional_shares_only",
        "use_fast_model",
        "allow_review_candidates",
        "allow_pyramiding",
        "allow_averaging_down",
        "require_liquidity_fields",
        "require_edge_fields",
        "ai_daily_review_enabled",
        "ai_auto_adjust_enabled",
        "ai_adjust_live_enabled",
        "accuracy_calibration_enabled",
        "accuracy_calibration_apply_to_live",
        "daily_objective_enabled",
        "daily_objective_apply_to_live",
        "loss_containment_enabled",
        "loss_containment_apply_to_live",
        "loss_containment_auto_close_paper",
        "loss_containment_auto_close_live",
        "exit_watchdog_enabled",
        "exit_watchdog_apply_to_live",
        "exit_watchdog_block_entries_on_unconfirmed_exit",
        "state_control_enabled",
        "state_control_auto_throttle_enabled",
        "state_control_auto_halt_enabled",
        "paper_canary_enabled",
        "paper_canary_auto_review_enabled",
        "paper_order_lifecycle_canary_enabled",
        "paper_order_lifecycle_auto_submit_enabled",
        "live_pilot_soak_enabled",
        "live_pilot_canary_enabled",
        "live_pilot_canary_auto_review_enabled",
        "live_pilot_expansion_enabled",
        "live_pilot_expansion_require_limit",
        "live_pilot_expansion_allow_autonomous_entries",
        "live_pilot_expansion_canary_enabled",
        "live_pilot_expansion_canary_auto_review_enabled",
        "live_pilot_window_enabled",
        "live_pilot_window_require_limit",
        "live_pilot_window_canary_enabled",
        "live_pilot_window_canary_auto_review_enabled",
        "live_pilot_promotion_report_enabled",
        "live_pilot_promotion_report_auto_review_enabled",
        "limited_live_rollout_enabled",
        "limited_live_rollout_require_limit",
        "limited_live_rollout_auto_expand_enabled",
        "limited_live_rollout_canary_enabled",
        "limited_live_rollout_canary_auto_review_enabled",
        "limited_live_cap_expansion_report_enabled",
        "limited_live_cap_expansion_report_auto_review_enabled",
        "limited_live_cap_expansion_enabled",
        "limited_live_cap_expansion_require_limit",
        "limited_live_cap_expansion_auto_expand_enabled",
        "limited_live_next_tier_cap_enabled",
        "limited_live_next_tier_cap_require_limit",
        "limited_live_next_tier_cap_auto_expand_enabled",
        "limited_live_next_tier_cap_canary_enabled",
        "limited_live_next_tier_cap_canary_auto_review_enabled",
        "limited_live_higher_cap_report_enabled",
        "limited_live_higher_cap_report_auto_review_enabled",
        "limited_live_operator_checklist_required",
    ):
        if key in payload:
            settings_state[key] = _normalize_bool(payload.get(key), settings_state[key])

    if "flatten_before_close_minutes" in payload:
        settings_state["flatten_before_close_minutes"] = _normalize_int(
            payload.get("flatten_before_close_minutes"),
            settings_state["flatten_before_close_minutes"],
            minimum=1,
            maximum=90,
        )
    if "max_open_positions" in payload:
        settings_state["max_open_positions"] = _normalize_int(
            payload.get("max_open_positions"),
            settings_state["max_open_positions"],
            minimum=1,
            maximum=25,
        )
    if "cycle_entry_rank_limit" in payload:
        settings_state["cycle_entry_rank_limit"] = _normalize_int(
            payload.get("cycle_entry_rank_limit"),
            settings_state.get("cycle_entry_rank_limit", 2),
            minimum=1,
            maximum=10,
        )
    if "max_notional_per_trade" in payload:
        settings_state["max_notional_per_trade"] = _normalize_float(
            payload.get("max_notional_per_trade"),
            settings_state["max_notional_per_trade"],
            minimum=100.0,
            maximum=5_000_000.0,
        )
    if "max_gross_leverage" in payload:
        settings_state["max_gross_leverage"] = _normalize_float(
            payload.get("max_gross_leverage"),
            settings_state.get("max_gross_leverage", 1.5),
            minimum=0.1,
            maximum=10.0,
        )
    if "max_single_position_pct" in payload:
        settings_state["max_single_position_pct"] = _normalize_float(
            payload.get("max_single_position_pct"),
            settings_state.get("max_single_position_pct", 12.0),
            minimum=1.0,
            maximum=100.0,
        )
    if "max_correlated_bucket_pct" in payload:
        settings_state["max_correlated_bucket_pct"] = _normalize_float(
            payload.get("max_correlated_bucket_pct"),
            settings_state.get("max_correlated_bucket_pct", 35.0),
            minimum=1.0,
            maximum=100.0,
        )
    if "min_edge_to_cost_ratio" in payload:
        settings_state["min_edge_to_cost_ratio"] = _normalize_float(
            payload.get("min_edge_to_cost_ratio"),
            settings_state.get("min_edge_to_cost_ratio", 2.5),
            minimum=0.0,
            maximum=25.0,
        )
    for key, default, minimum, maximum in (
        ("max_daily_loss_pct", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["max_daily_loss_pct"], 0.10, 50.0),
        ("max_weekly_loss_pct", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["max_weekly_loss_pct"], 0.10, 50.0),
        ("drawdown_size_cut_pct", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["drawdown_size_cut_pct"], 0.10, 90.0),
        ("drawdown_stop_pct", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["drawdown_stop_pct"], 0.10, 95.0),
        ("drawdown_audit_pct", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["drawdown_audit_pct"], 0.10, 99.0),
        ("risk_cut_multiplier", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["risk_cut_multiplier"], 0.05, 1.0),
        ("market_slippage_bps", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["market_slippage_bps"], 0.0, 500.0),
        ("limit_slippage_bps", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["limit_slippage_bps"], 0.0, 500.0),
        ("max_spread_bps", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["max_spread_bps"], 0.0, 1000.0),
        ("min_average_dollar_volume", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["min_average_dollar_volume"], 0.0, 1_000_000_000_000.0),
        ("max_order_adv_pct", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["max_order_adv_pct"], 0.001, 100.0),
        ("max_intraday_volume_pct", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["max_intraday_volume_pct"], 0.001, 100.0),
        ("ai_max_step_pct", automation_ai_review_service.AI_SETTINGS_DEFAULTS["ai_max_step_pct"], 1.0, 50.0),
        (
            "accuracy_calibration_max_candidate_penalty",
            automation_accuracy_calibration_service.ACCURACY_CALIBRATION_SETTINGS_DEFAULTS[
                "accuracy_calibration_max_candidate_penalty"
            ],
            0.0,
            100.0,
        ),
        (
            "daily_profit_target_pct",
            automation_daily_objective_service.DAILY_OBJECTIVE_SETTINGS_DEFAULTS["daily_profit_target_pct"],
            0.1,
            10.0,
        ),
        (
            "daily_profit_target_dollars",
            automation_daily_objective_service.DAILY_OBJECTIVE_SETTINGS_DEFAULTS["daily_profit_target_dollars"],
            1.0,
            1_000_000.0,
        ),
        (
            "daily_loss_budget_pct",
            automation_daily_objective_service.DAILY_OBJECTIVE_SETTINGS_DEFAULTS["daily_loss_budget_pct"],
            0.1,
            10.0,
        ),
        (
            "loss_containment_max_open_heat_pct",
            automation_loss_containment_service.LOSS_CONTAINMENT_SETTINGS_DEFAULTS[
                "loss_containment_max_open_heat_pct"
            ],
            0.05,
            10.0,
        ),
        (
            "loss_containment_max_position_loss_r",
            automation_loss_containment_service.LOSS_CONTAINMENT_SETTINGS_DEFAULTS[
                "loss_containment_max_position_loss_r"
            ],
            0.05,
            10.0,
        ),
        (
            "loss_containment_max_position_mae_pct",
            automation_loss_containment_service.LOSS_CONTAINMENT_SETTINGS_DEFAULTS[
                "loss_containment_max_position_mae_pct"
            ],
            0.05,
            25.0,
        ),
        (
            "loss_containment_profit_protect_trigger_r",
            automation_loss_containment_service.LOSS_CONTAINMENT_SETTINGS_DEFAULTS[
                "loss_containment_profit_protect_trigger_r"
            ],
            0.05,
            25.0,
        ),
        (
            "loss_containment_profit_protect_floor_r",
            automation_loss_containment_service.LOSS_CONTAINMENT_SETTINGS_DEFAULTS[
                "loss_containment_profit_protect_floor_r"
            ],
            -10.0,
            25.0,
        ),
        ("state_control_watch_score", automation_state_control_service.STATE_CONTROL_SETTINGS_DEFAULTS["state_control_watch_score"], 1.0, 100.0),
        ("state_control_derisk_score", automation_state_control_service.STATE_CONTROL_SETTINGS_DEFAULTS["state_control_derisk_score"], 1.0, 100.0),
        ("state_control_halt_score", automation_state_control_service.STATE_CONTROL_SETTINGS_DEFAULTS["state_control_halt_score"], 0.0, 99.0),
        (
            "live_pilot_max_notional",
            automation_live_pilot_soak_service.LIVE_PILOT_SETTINGS_DEFAULTS["live_pilot_max_notional"],
            1.0,
            automation_live_pilot_soak_service.LIVE_PILOT_MAX_NOTIONAL_CEILING,
        ),
        (
            "live_pilot_expansion_max_notional",
            automation_live_pilot_expansion_service.LIVE_PILOT_EXPANSION_SETTINGS_DEFAULTS[
                "live_pilot_expansion_max_notional"
            ],
            1.0,
            automation_live_pilot_expansion_service.LIVE_PILOT_EXPANSION_MAX_NOTIONAL_CEILING,
        ),
        (
            "live_pilot_window_max_notional",
            automation_live_pilot_window_service.LIVE_PILOT_WINDOW_SETTINGS_DEFAULTS[
                "live_pilot_window_max_notional"
            ],
            1.0,
            automation_live_pilot_window_service.LIVE_PILOT_WINDOW_MAX_NOTIONAL_CEILING,
        ),
        (
            "limited_live_rollout_max_notional",
            automation_limited_live_rollout_gate_service.LIMITED_LIVE_ROLLOUT_SETTINGS_DEFAULTS[
                "limited_live_rollout_max_notional"
            ],
            1.0,
            automation_limited_live_rollout_gate_service.LIMITED_LIVE_ROLLOUT_MAX_NOTIONAL_CEILING,
        ),
        (
            "limited_live_cap_expansion_target_max_notional",
            automation_limited_live_cap_expansion_report_service.LIMITED_LIVE_CAP_EXPANSION_SETTINGS_DEFAULTS[
                "limited_live_cap_expansion_target_max_notional"
            ],
            1.0,
            automation_limited_live_cap_expansion_report_service.LIMITED_LIVE_CAP_EXPANSION_TARGET_NOTIONAL_CEILING,
        ),
        (
            "limited_live_cap_expansion_max_notional",
            automation_limited_live_cap_expansion_gate_service.LIMITED_LIVE_CAP_EXPANSION_GATE_SETTINGS_DEFAULTS[
                "limited_live_cap_expansion_max_notional"
            ],
            1.0,
            automation_limited_live_cap_expansion_gate_service.LIMITED_LIVE_CAP_EXPANSION_GATE_MAX_NOTIONAL_CEILING,
        ),
        (
            "limited_live_next_tier_cap_max_notional",
            automation_limited_live_next_tier_cap_gate_service.LIMITED_LIVE_NEXT_TIER_CAP_GATE_SETTINGS_DEFAULTS[
                "limited_live_next_tier_cap_max_notional"
            ],
            1.0,
            automation_limited_live_next_tier_cap_gate_service.LIMITED_LIVE_NEXT_TIER_CAP_GATE_MAX_NOTIONAL_CEILING,
        ),
        (
            "limited_live_higher_cap_target_max_notional",
            automation_limited_live_safety_ladder_service.LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS[
                "limited_live_higher_cap_target_max_notional"
            ],
            1.0,
            automation_limited_live_safety_ladder_service.LIMITED_LIVE_HIGHER_CAP_TARGET,
        ),
    ):
        if key in payload:
            settings_state[key] = _normalize_float(
                payload.get(key),
                settings_state.get(key, default),
                minimum=minimum,
                maximum=maximum,
            )
    if "max_total_open_notional" in payload:
        settings_state["max_total_open_notional"] = _normalize_float(
            payload.get("max_total_open_notional"),
            settings_state["max_total_open_notional"],
            minimum=100.0,
            maximum=5_000_000.0,
        )
    if "max_daily_loss_r" in payload:
        settings_state["max_daily_loss_r"] = _normalize_float(
            payload.get("max_daily_loss_r"),
            settings_state["max_daily_loss_r"],
            minimum=0.25,
            maximum=25.0,
        )
    if "max_consecutive_losses" in payload:
        settings_state["max_consecutive_losses"] = _normalize_int(
            payload.get("max_consecutive_losses"),
            settings_state["max_consecutive_losses"],
            minimum=1,
            maximum=25,
        )
    if "max_daily_entries" in payload:
        settings_state["max_daily_entries"] = _normalize_int(
            payload.get("max_daily_entries"),
            settings_state["max_daily_entries"],
            minimum=1,
            maximum=100,
        )
    if "max_daily_entries_per_symbol" in payload:
        settings_state["max_daily_entries_per_symbol"] = _normalize_int(
            payload.get("max_daily_entries_per_symbol"),
            settings_state["max_daily_entries_per_symbol"],
            minimum=1,
            maximum=25,
        )
    if "max_error_streak" in payload:
        settings_state["max_error_streak"] = _normalize_int(
            payload.get("max_error_streak"),
            settings_state["max_error_streak"],
            minimum=1,
            maximum=25,
        )
    for key, default, minimum, maximum in (
        ("no_new_entries_first_minutes", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["no_new_entries_first_minutes"], 0, 120),
        ("no_new_entries_before_close_minutes", risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["no_new_entries_before_close_minutes"], 0, 240),
        ("ai_review_min_trades", automation_ai_review_service.AI_SETTINGS_DEFAULTS["ai_review_min_trades"], 0, 100),
        ("ai_max_daily_setting_changes", automation_ai_review_service.AI_SETTINGS_DEFAULTS["ai_max_daily_setting_changes"], 0, 12),
        (
            "accuracy_calibration_min_samples",
            automation_accuracy_calibration_service.ACCURACY_CALIBRATION_SETTINGS_DEFAULTS[
                "accuracy_calibration_min_samples"
            ],
            1,
            500,
        ),
        (
            "accuracy_calibration_stale_after_sessions",
            automation_accuracy_calibration_service.ACCURACY_CALIBRATION_SETTINGS_DEFAULTS[
                "accuracy_calibration_stale_after_sessions"
            ],
            1,
            60,
        ),
        (
            "loss_containment_time_stop_minutes",
            automation_loss_containment_service.LOSS_CONTAINMENT_SETTINGS_DEFAULTS[
                "loss_containment_time_stop_minutes"
            ],
            1,
            480,
        ),
        (
            "loss_containment_stale_quote_seconds",
            automation_loss_containment_service.LOSS_CONTAINMENT_SETTINGS_DEFAULTS[
                "loss_containment_stale_quote_seconds"
            ],
            15,
            3600,
        ),
        (
            "exit_watchdog_max_confirmation_seconds",
            automation_exit_execution_watchdog_service.EXIT_WATCHDOG_SETTINGS_DEFAULTS[
                "exit_watchdog_max_confirmation_seconds"
            ],
            5,
            3600,
        ),
        (
            "exit_watchdog_max_partial_minutes",
            automation_exit_execution_watchdog_service.EXIT_WATCHDOG_SETTINGS_DEFAULTS[
                "exit_watchdog_max_partial_minutes"
            ],
            1,
            240,
        ),
        ("state_control_recovery_cycles", automation_state_control_service.STATE_CONTROL_SETTINGS_DEFAULTS["state_control_recovery_cycles"], 1, 20),
        ("paper_canary_window_sessions", automation_paper_canary_service.PAPER_CANARY_SETTINGS_DEFAULTS["paper_canary_window_sessions"], 1, 20),
        (
            "live_pilot_approval_ttl_minutes",
            automation_live_pilot_soak_service.LIVE_PILOT_SETTINGS_DEFAULTS["live_pilot_approval_ttl_minutes"],
            1,
            60,
        ),
        (
            "live_pilot_cancel_timeout_seconds",
            automation_live_pilot_soak_service.LIVE_PILOT_SETTINGS_DEFAULTS["live_pilot_cancel_timeout_seconds"],
            5,
            120,
        ),
        (
            "paper_order_lifecycle_window_sessions",
            automation_paper_order_lifecycle_canary_service.PAPER_ORDER_LIFECYCLE_CANARY_SETTINGS_DEFAULTS[
                "paper_order_lifecycle_window_sessions"
            ],
            1,
            20,
        ),
        (
            "live_pilot_canary_window_sessions",
            automation_live_pilot_canary_service.LIVE_PILOT_CANARY_SETTINGS_DEFAULTS[
                "live_pilot_canary_window_sessions"
            ],
            1,
            20,
        ),
        (
            "live_pilot_expansion_max_daily_orders",
            automation_live_pilot_expansion_service.LIVE_PILOT_EXPANSION_SETTINGS_DEFAULTS[
                "live_pilot_expansion_max_daily_orders"
            ],
            1,
            3,
        ),
        (
            "live_pilot_expansion_approval_ttl_minutes",
            automation_live_pilot_expansion_service.LIVE_PILOT_EXPANSION_SETTINGS_DEFAULTS[
                "live_pilot_expansion_approval_ttl_minutes"
            ],
            1,
            30,
        ),
        (
            "live_pilot_expansion_canary_window_sessions",
            automation_live_pilot_expansion_canary_service.LIVE_PILOT_EXPANSION_CANARY_SETTINGS_DEFAULTS[
                "live_pilot_expansion_canary_window_sessions"
            ],
            1,
            20,
        ),
        (
            "live_pilot_window_max_session_orders",
            automation_live_pilot_window_service.LIVE_PILOT_WINDOW_SETTINGS_DEFAULTS[
                "live_pilot_window_max_session_orders"
            ],
            1,
            1,
        ),
        (
            "live_pilot_window_approval_ttl_minutes",
            automation_live_pilot_window_service.LIVE_PILOT_WINDOW_SETTINGS_DEFAULTS[
                "live_pilot_window_approval_ttl_minutes"
            ],
            1,
            30,
        ),
        (
            "live_pilot_window_duration_minutes",
            automation_live_pilot_window_service.LIVE_PILOT_WINDOW_SETTINGS_DEFAULTS[
                "live_pilot_window_duration_minutes"
            ],
            5,
            240,
        ),
        (
            "live_pilot_window_canary_window_sessions",
            automation_live_pilot_window_canary_service.LIVE_PILOT_WINDOW_CANARY_SETTINGS_DEFAULTS[
                "live_pilot_window_canary_window_sessions"
            ],
            1,
            20,
        ),
        (
            "live_pilot_promotion_required_window_clean_sessions",
            automation_live_pilot_promotion_report_service.LIVE_PILOT_PROMOTION_SETTINGS_DEFAULTS[
                "live_pilot_promotion_required_window_clean_sessions"
            ],
            1,
            20,
        ),
        (
            "live_pilot_promotion_stale_after_days",
            automation_live_pilot_promotion_report_service.LIVE_PILOT_PROMOTION_SETTINGS_DEFAULTS[
                "live_pilot_promotion_stale_after_days"
            ],
            1,
            30,
        ),
        (
            "limited_live_rollout_max_session_orders",
            automation_limited_live_rollout_gate_service.LIMITED_LIVE_ROLLOUT_SETTINGS_DEFAULTS[
                "limited_live_rollout_max_session_orders"
            ],
            1,
            1,
        ),
        (
            "limited_live_rollout_duration_minutes",
            automation_limited_live_rollout_gate_service.LIMITED_LIVE_ROLLOUT_SETTINGS_DEFAULTS[
                "limited_live_rollout_duration_minutes"
            ],
            5,
            240,
        ),
        (
            "limited_live_rollout_approval_ttl_minutes",
            automation_limited_live_rollout_gate_service.LIMITED_LIVE_ROLLOUT_SETTINGS_DEFAULTS[
                "limited_live_rollout_approval_ttl_minutes"
            ],
            1,
            30,
        ),
        (
            "limited_live_rollout_canary_window_sessions",
            automation_limited_live_rollout_canary_service.LIMITED_LIVE_ROLLOUT_CANARY_SETTINGS_DEFAULTS[
                "limited_live_rollout_canary_window_sessions"
            ],
            1,
            20,
        ),
        (
            "limited_live_rollout_canary_stale_after_days",
            automation_limited_live_rollout_canary_service.LIMITED_LIVE_ROLLOUT_CANARY_SETTINGS_DEFAULTS[
                "limited_live_rollout_canary_stale_after_days"
            ],
            1,
            30,
        ),
        (
            "limited_live_cap_expansion_required_clean_sessions",
            automation_limited_live_cap_expansion_report_service.LIMITED_LIVE_CAP_EXPANSION_SETTINGS_DEFAULTS[
                "limited_live_cap_expansion_required_clean_sessions"
            ],
            1,
            20,
        ),
        (
            "limited_live_cap_expansion_stale_after_days",
            automation_limited_live_cap_expansion_report_service.LIMITED_LIVE_CAP_EXPANSION_SETTINGS_DEFAULTS[
                "limited_live_cap_expansion_stale_after_days"
            ],
            1,
            30,
        ),
        (
            "limited_live_cap_expansion_duration_minutes",
            automation_limited_live_cap_expansion_gate_service.LIMITED_LIVE_CAP_EXPANSION_GATE_SETTINGS_DEFAULTS[
                "limited_live_cap_expansion_duration_minutes"
            ],
            5,
            240,
        ),
        (
            "limited_live_cap_expansion_approval_ttl_minutes",
            automation_limited_live_cap_expansion_gate_service.LIMITED_LIVE_CAP_EXPANSION_GATE_SETTINGS_DEFAULTS[
                "limited_live_cap_expansion_approval_ttl_minutes"
            ],
            1,
            30,
        ),
        (
            "limited_live_cap_expansion_max_session_orders",
            automation_limited_live_cap_expansion_gate_service.LIMITED_LIVE_CAP_EXPANSION_GATE_SETTINGS_DEFAULTS[
                "limited_live_cap_expansion_max_session_orders"
            ],
            1,
            1,
        ),
        (
            "limited_live_next_tier_cap_duration_minutes",
            automation_limited_live_next_tier_cap_gate_service.LIMITED_LIVE_NEXT_TIER_CAP_GATE_SETTINGS_DEFAULTS[
                "limited_live_next_tier_cap_duration_minutes"
            ],
            5,
            240,
        ),
        (
            "limited_live_next_tier_cap_approval_ttl_minutes",
            automation_limited_live_next_tier_cap_gate_service.LIMITED_LIVE_NEXT_TIER_CAP_GATE_SETTINGS_DEFAULTS[
                "limited_live_next_tier_cap_approval_ttl_minutes"
            ],
            1,
            30,
        ),
        (
            "limited_live_next_tier_cap_max_session_orders",
            automation_limited_live_next_tier_cap_gate_service.LIMITED_LIVE_NEXT_TIER_CAP_GATE_SETTINGS_DEFAULTS[
                "limited_live_next_tier_cap_max_session_orders"
            ],
            1,
            1,
        ),
        (
            "limited_live_next_tier_cap_canary_window_sessions",
            automation_limited_live_safety_ladder_service.LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS[
                "limited_live_next_tier_cap_canary_window_sessions"
            ],
            1,
            20,
        ),
        (
            "limited_live_next_tier_cap_canary_required_clean_sessions",
            automation_limited_live_safety_ladder_service.LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS[
                "limited_live_next_tier_cap_canary_required_clean_sessions"
            ],
            1,
            20,
        ),
        (
            "limited_live_next_tier_cap_canary_stale_after_days",
            automation_limited_live_safety_ladder_service.LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS[
                "limited_live_next_tier_cap_canary_stale_after_days"
            ],
            1,
            30,
        ),
        (
            "limited_live_higher_cap_required_clean_sessions",
            automation_limited_live_safety_ladder_service.LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS[
                "limited_live_higher_cap_required_clean_sessions"
            ],
            1,
            20,
        ),
        (
            "limited_live_higher_cap_stale_after_days",
            automation_limited_live_safety_ladder_service.LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS[
                "limited_live_higher_cap_stale_after_days"
            ],
            1,
            30,
        ),
    ):
        if key in payload:
            settings_state[key] = _normalize_int(
                payload.get(key),
                settings_state.get(key, default),
                minimum=minimum,
                maximum=maximum,
            )
    if "paper_canary_required_clean_sessions" in payload:
        settings_state["paper_canary_required_clean_sessions"] = _normalize_int(
            payload.get("paper_canary_required_clean_sessions"),
            settings_state.get(
                "paper_canary_required_clean_sessions",
                automation_paper_canary_service.PAPER_CANARY_SETTINGS_DEFAULTS["paper_canary_required_clean_sessions"],
            ),
            minimum=1,
            maximum=max(1, int(settings_state.get("paper_canary_window_sessions") or 20)),
        )
    if "paper_order_lifecycle_required_clean_sessions" in payload:
        settings_state["paper_order_lifecycle_required_clean_sessions"] = _normalize_int(
            payload.get("paper_order_lifecycle_required_clean_sessions"),
            settings_state.get(
                "paper_order_lifecycle_required_clean_sessions",
                automation_paper_order_lifecycle_canary_service.PAPER_ORDER_LIFECYCLE_CANARY_SETTINGS_DEFAULTS[
                    "paper_order_lifecycle_required_clean_sessions"
                ],
            ),
            minimum=1,
            maximum=max(1, int(settings_state.get("paper_order_lifecycle_window_sessions") or 20)),
        )
    if "live_pilot_canary_required_clean_sessions" in payload:
        settings_state["live_pilot_canary_required_clean_sessions"] = _normalize_int(
            payload.get("live_pilot_canary_required_clean_sessions"),
            settings_state.get(
                "live_pilot_canary_required_clean_sessions",
                automation_live_pilot_canary_service.LIVE_PILOT_CANARY_SETTINGS_DEFAULTS[
                    "live_pilot_canary_required_clean_sessions"
                ],
            ),
            minimum=1,
            maximum=max(1, int(settings_state.get("live_pilot_canary_window_sessions") or 20)),
        )
    if "live_pilot_expansion_canary_required_clean_sessions" in payload:
        settings_state["live_pilot_expansion_canary_required_clean_sessions"] = _normalize_int(
            payload.get("live_pilot_expansion_canary_required_clean_sessions"),
            settings_state.get(
                "live_pilot_expansion_canary_required_clean_sessions",
                automation_live_pilot_expansion_canary_service.LIVE_PILOT_EXPANSION_CANARY_SETTINGS_DEFAULTS[
                    "live_pilot_expansion_canary_required_clean_sessions"
                ],
            ),
            minimum=1,
            maximum=max(1, int(settings_state.get("live_pilot_expansion_canary_window_sessions") or 20)),
        )
    if "live_pilot_window_canary_required_clean_sessions" in payload:
        settings_state["live_pilot_window_canary_required_clean_sessions"] = _normalize_int(
            payload.get("live_pilot_window_canary_required_clean_sessions"),
            settings_state.get(
                "live_pilot_window_canary_required_clean_sessions",
                automation_live_pilot_window_canary_service.LIVE_PILOT_WINDOW_CANARY_SETTINGS_DEFAULTS[
                    "live_pilot_window_canary_required_clean_sessions"
                ],
            ),
            minimum=1,
            maximum=max(1, int(settings_state.get("live_pilot_window_canary_window_sessions") or 20)),
        )
    if "limited_live_rollout_canary_required_clean_sessions" in payload:
        settings_state["limited_live_rollout_canary_required_clean_sessions"] = _normalize_int(
            payload.get("limited_live_rollout_canary_required_clean_sessions"),
            settings_state.get(
                "limited_live_rollout_canary_required_clean_sessions",
                automation_limited_live_rollout_canary_service.LIMITED_LIVE_ROLLOUT_CANARY_SETTINGS_DEFAULTS[
                    "limited_live_rollout_canary_required_clean_sessions"
                ],
            ),
            minimum=1,
            maximum=max(1, int(settings_state.get("limited_live_rollout_canary_window_sessions") or 20)),
        )
    if "live_pilot_symbol" in payload:
        live_symbol = str(payload.get("live_pilot_symbol") or "").strip().upper()
        if not live_symbol or len(live_symbol) > 12:
            raise ValidationError("Live pilot symbol must be a valid short ticker.")
        settings_state["live_pilot_symbol"] = live_symbol
    settings_state["live_pilot_expansion_require_limit"] = True
    settings_state["live_pilot_expansion_allow_autonomous_entries"] = False
    settings_state["live_pilot_window_require_limit"] = True
    settings_state["limited_live_rollout_require_limit"] = True
    settings_state["limited_live_rollout_auto_expand_enabled"] = False
    settings_state["limited_live_cap_expansion_require_limit"] = True
    settings_state["limited_live_cap_expansion_auto_expand_enabled"] = False
    settings_state["limited_live_next_tier_cap_require_limit"] = True
    settings_state["limited_live_next_tier_cap_auto_expand_enabled"] = False

    if bool(settings_state.get("auto_trade_listed_options")):
        settings_state["equities_only"] = False
        settings_state["fractional_shares_only"] = False
    if _normalize_automation_instrument(settings_state.get("instrument_type"), default="equity") == "listed_option":
        settings_state["regular_hours_only"] = True
        settings_state["time_in_force"] = "day"
        settings_state["order_type"] = "limit"

    if not bool(settings_state.get("auto_trade_equities")) and not bool(settings_state.get("auto_trade_listed_options")):
        raise ValidationError("Automation must have at least one instrument path enabled.")

    if settings_state["regular_hours_only"] and settings_state["time_in_force"] == "day_ext":
        settings_state["time_in_force"] = "day"
    if profile_key == _AUTOMATION_PERSONAL_PAPER_PROFILE:
        settings_state["execution_intent"] = "broker_paper"
    elif profile_key == _AUTOMATION_PERSONAL_LIVE_PROFILE:
        settings_state["execution_intent"] = "broker_live"
    elif profile_key.startswith("linked:"):
        settings_state["execution_intent"] = "broker_paper"

    collection_phase = _build_collection_phase_state(
        rollout_readiness=rollout_readiness,
        runtime_state=state["runtime"],
    )
    settings_state = _apply_collection_phase_controls(settings_state, collection_phase)
    state["settings"] = settings_state
    _apply_collection_phase_runtime(state["runtime"], collection_phase)
    if "max_error_streak" in payload and int(state["runtime"].get("error_streak") or 0) < int(settings_state["max_error_streak"]):
        state["runtime"]["last_guardrail"] = None
    state["runtime"]["last_action"] = {
        "type": "settings_updated",
        "updated_fields": sorted(payload.keys()),
        "at": _serialize_datetime(_utc_now()),
    }
    _append_history(
        state,
        {
            "type": "settings_updated",
            "at": _serialize_datetime(_utc_now()),
            "updated_fields": sorted(payload.keys()),
        },
    )
    _write_trade_automation_state(tenant, state, profile_key=profile_key)
    record_audit_event(
        db,
        event_type="trade_automation.updated",
        tenant=tenant,
        user=actor,
        payload={
            "profile_key": profile_key,
            "linked_account_id": getattr(linked_account, "id", None),
            "updated_fields": sorted(payload.keys()),
            "settings": serialize_value(settings_state),
        },
    )
    db.commit()
    return _build_snapshot_payload(
        tenant,
        state,
        profile_key=profile_key,
        linked_account=linked_account,
        rollout_readiness=rollout_readiness,
        db=db,
    )


def run_tenant_trade_automation_action(
    db: Session,
    *,
    current_user: Any,
    request: OrganizationTradeAutomationActionRequest | dict[str, Any] | str,
) -> dict[str, Any]:
    _assert_trade_automation_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    if isinstance(request, str):
        action = str(request).strip().lower()
        scope = None
        scope_key = None
        linked_account_id = None
        action_payload: dict[str, Any] = {}
    elif hasattr(request, "action"):
        action = str(request.action).strip().lower()
        scope = getattr(request, "scope", None)
        scope_key = getattr(request, "scope_key", None)
        linked_account_id = getattr(request, "linked_account_id", None)
        action_payload = {"checklist": getattr(request, "checklist", None)}
    else:
        action = str((request or {}).get("action") or "").strip().lower()
        scope = (request or {}).get("scope")
        scope_key = (request or {}).get("scope_key")
        linked_account_id = (request or {}).get("linked_account_id")
        action_payload = dict(request or {})
    if not action:
        raise ValidationError("Automation action is required.")
    profile_key, linked_account = _resolve_trade_automation_profile_selection(
        db,
        tenant=tenant,
        scope=scope,
        scope_key=scope_key,
        linked_account_id=linked_account_id,
    )
    state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)

    if action == "arm":
        if state["settings"]["kill_switch"]:
            raise ValidationError("Clear the kill switch before arming automation.")
        state["settings"]["enabled"] = True
        state["settings"]["armed"] = True
        detail = "Automation is armed and ready for unattended worker cycles."
    elif action == "disarm":
        state["settings"]["armed"] = False
        detail = "Automation is disarmed and will not open new unattended trades."
    elif action == "kill_switch":
        state["settings"]["kill_switch"] = True
        state["settings"]["armed"] = False
        detail = "Kill switch is active and all new unattended automation is blocked."
    elif action == "clear_kill_switch":
        state["settings"]["kill_switch"] = False
        state["runtime"]["error_streak"] = 0
        state["runtime"]["last_guardrail"] = None
        automation_state_control_service.clear_state_control_halt(state)
        detail = "Kill switch cleared. Automation can be armed again."
    elif action == "reset_from_template":
        template_settings = dict(_read_trade_automation_store(tenant).get("template") or {})
        state = _seed_trade_automation_profile_state(
            template_settings=template_settings,
            profile_key=profile_key,
            linked_account=linked_account,
        )
        detail = "Automation settings were reset from the shared template."
    elif action == "run_cycle":
        if not state["settings"]["enabled"]:
            raise ValidationError("Enable automation before forcing a cycle.")
        return _run_trade_automation_cycle(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            forced=True,
            actor=actor,
        )
    elif action == "run_ai_review":
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        review = automation_ai_review_service.run_trade_automation_ai_review(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            forced=True,
            actor=actor,
            live_route_allowed=(
                bool(rollout_readiness.get("allows_live_rollout"))
                if profile_key == _AUTOMATION_PERSONAL_LIVE_PROFILE
                else True
            ),
        )
        state["runtime"]["last_action"] = {
            "type": "ai_review",
            "action": action,
            "detail": "AI trading review completed.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "ai_review",
                "at": _serialize_datetime(_utc_now()),
                "session_day": review.get("session_day"),
                "applied_change_count": len(review.get("applied_changes") or []),
                "skipped_change_count": len(review.get("skipped_changes") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_daily_objective_review":
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        account_context = _resolve_trade_automation_profile_account_context(
            tenant=tenant,
            db=db,
            profile_key=profile_key,
            settings_state=state.get("settings"),
            actor=actor,
            linked_account=linked_account,
        )
        state["__actual_funds"] = account_context.get("actual_funds")
        state["__effective_funds"] = account_context.get("effective_funds")
        review = automation_daily_objective_service.run_daily_objective_review(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "daily_objective_review",
            "action": action,
            "detail": "Daily objective review completed.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "daily_objective_review",
                "at": _serialize_datetime(_utc_now()),
                "status": review.get("status"),
                "session_day": review.get("session_day"),
                "target_progress_pct": review.get("target_progress_pct"),
                "loss_budget_used_pct": review.get("loss_budget_used_pct"),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_accuracy_calibration_review":
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        review = automation_accuracy_calibration_service.run_accuracy_calibration_review(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "accuracy_calibration_review",
            "action": action,
            "detail": "Decision-PnL accuracy calibration review completed.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "accuracy_calibration_review",
                "at": _serialize_datetime(_utc_now()),
                "status": review.get("status"),
                "session_day": review.get("session_day"),
                "decision_pnl_accuracy": review.get("decision_pnl_accuracy"),
                "calibrated_expectancy": review.get("calibrated_expectancy"),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_loss_containment_review":
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        account_context = _resolve_trade_automation_profile_account_context(
            tenant=tenant,
            db=db,
            profile_key=profile_key,
            settings_state=state.get("settings"),
            actor=actor,
            linked_account=linked_account,
        )
        state["__actual_funds"] = account_context.get("actual_funds")
        state["__effective_funds"] = account_context.get("effective_funds")
        review = automation_loss_containment_service.run_loss_containment_review(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "loss_containment_review",
            "action": action,
            "detail": "Loss containment review completed.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "loss_containment_review",
                "at": _serialize_datetime(_utc_now()),
                "status": review.get("status"),
                "session_day": review.get("session_day"),
                "open_heat_pct": review.get("open_heat_pct"),
                "entries_blocked": review.get("entries_blocked"),
                "defensive_action_count": len(review.get("defensive_actions") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_exit_watchdog_review":
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        review = automation_exit_execution_watchdog_service.run_exit_watchdog_review(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "exit_watchdog_review",
            "action": action,
            "detail": "Exit execution watchdog review completed.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "exit_watchdog_review",
                "at": _serialize_datetime(_utc_now()),
                "status": review.get("status"),
                "session_day": review.get("session_day"),
                "pending_exit_count": review.get("pending_exit_count"),
                "stuck_exit_count": review.get("stuck_exit_count"),
                "entries_blocked": review.get("entries_blocked"),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_state_control_review":
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        review = automation_state_control_service.evaluate_trade_automation_state_control(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            forced=True,
            actor=actor,
        )
        state["runtime"]["last_action"] = {
            "type": "state_control_review",
            "action": action,
            "detail": "State-control review completed.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "state_control_review",
                "at": _serialize_datetime(_utc_now()),
                "state": review.get("state"),
                "score": review.get("score"),
                "override_count": len(review.get("active_overrides") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_state_control_shadow_validation":
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        report = automation_state_control_shadow_service.run_state_control_shadow_validation(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
        )
        state["runtime"]["last_action"] = {
            "type": "state_control_shadow_validation",
            "action": action,
            "detail": "State-control shadow validation completed.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "state_control_shadow_validation",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "scenario_count": report.get("scenario_count"),
                "failed_count": report.get("failed_count"),
                "worst_state": report.get("worst_state"),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_paper_canary_review":
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        report = automation_paper_canary_service.run_paper_canary_review(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "paper_canary_review",
            "action": action,
            "detail": "Paper canary readiness review completed.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "paper_canary_review",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "clean_session_count": report.get("clean_session_count"),
                "required_clean_sessions": report.get("required_clean_sessions"),
                "blocker_count": len(report.get("blockers") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_live_pilot_readiness_review":
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Live pilot readiness review is only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        report = automation_live_pilot_readiness_service.run_live_pilot_readiness_review(
            db,
            tenant=tenant,
            paper_state=state,
            live_state=live_state,
            rollout_readiness=rollout_readiness,
            actor=actor,
        )
        state["runtime"]["last_action"] = {
            "type": "live_pilot_readiness_review",
            "action": action,
            "detail": "Live pilot readiness review completed without changing live gates.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "live_pilot_readiness_review",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "broker_live_gate_status": report.get("broker_live_gate_status"),
                "safety_lock_status": report.get("safety_lock_status"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action in {"prepare_live_pilot_soak", "run_live_pilot_soak"}:
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Live pilot soak is only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        if action == "prepare_live_pilot_soak":
            report = automation_live_pilot_soak_service.prepare_live_pilot_soak(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                rollout_readiness=rollout_readiness,
                linked_account=linked_account,
                actor=actor,
            )
            action_detail = "Tiny live pilot soak prepared without touching the live order path."
        else:
            report = automation_live_pilot_soak_service.run_live_pilot_soak(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                rollout_readiness=rollout_readiness,
                linked_account=linked_account,
                actor=actor,
                current_user=current_user,
            )
            action_detail = "Tiny live pilot soak run completed without enabling or arming live automation."
        state["runtime"]["last_action"] = {
            "type": action,
            "action": action,
            "detail": action_detail,
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": action,
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "terminal_state": report.get("terminal_state"),
                "broker_order_id": report.get("broker_order_id"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action in {"prepare_live_pilot_expansion", "run_live_pilot_expansion"}:
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Live pilot expansion is only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        if action == "prepare_live_pilot_expansion":
            report = automation_live_pilot_expansion_service.prepare_live_pilot_expansion(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                rollout_readiness=rollout_readiness,
                linked_account=linked_account,
                actor=actor,
            )
            action_detail = "Live pilot expansion prepared without touching the live order path."
        else:
            report = automation_live_pilot_expansion_service.run_live_pilot_expansion(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                rollout_readiness=rollout_readiness,
                linked_account=linked_account,
                actor=actor,
                current_user=current_user,
            )
            action_detail = "Live pilot expansion run completed without enabling or arming live automation."
        state["runtime"]["last_action"] = {
            "type": action,
            "action": action,
            "detail": action_detail,
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": action,
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "terminal_state": report.get("terminal_state"),
                "broker_order_id": report.get("broker_order_id"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action in {"prepare_live_pilot_window", "run_live_pilot_window_entry", "run_live_pilot_window_exit"}:
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Supervised live pilot window is only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        if action == "prepare_live_pilot_window":
            report = automation_live_pilot_window_service.prepare_live_pilot_window(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                rollout_readiness=rollout_readiness,
                linked_account=linked_account,
                actor=actor,
            )
            action_detail = "Supervised live pilot window prepared without touching the live order path."
        elif action == "run_live_pilot_window_entry":
            report = automation_live_pilot_window_service.run_live_pilot_window_entry(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                rollout_readiness=rollout_readiness,
                linked_account=linked_account,
                actor=actor,
            )
            action_detail = "Supervised live pilot window entry submitted one capped live limit order."
        else:
            report = automation_live_pilot_window_service.run_live_pilot_window_exit(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                linked_account=linked_account,
                actor=actor,
            )
            action_detail = "Supervised live pilot window exit/cancel completed for pilot-owned evidence."
        state["runtime"]["last_action"] = {
            "type": action,
            "action": action,
            "detail": action_detail,
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": action,
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "terminal_state": report.get("terminal_state"),
                "broker_order_id": report.get("broker_order_id"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_live_pilot_window_canary_review":
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Supervised live pilot window canary review is only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        report = automation_live_pilot_window_canary_service.run_live_pilot_window_canary_review(
            db,
            tenant=tenant,
            paper_state=state,
            live_state=live_state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "live_pilot_window_canary_review",
            "action": action,
            "detail": "Supervised live pilot window canary review completed without touching the live order path.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "live_pilot_window_canary_review",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "clean_session_count": report.get("clean_session_count"),
                "required_clean_sessions": report.get("required_clean_sessions"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_live_pilot_promotion_report":
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Live pilot promotion report is only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        report = automation_live_pilot_promotion_report_service.run_live_pilot_promotion_report(
            db,
            tenant=tenant,
            paper_state=state,
            live_state=live_state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "live_pilot_promotion_report",
            "action": action,
            "detail": "Live pilot promotion report completed without touching live trading authority.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "live_pilot_promotion_report",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action in {
        "prepare_limited_live_rollout",
        "activate_limited_live_rollout",
        "rollback_limited_live_rollout",
    }:
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Limited-live rollout gate actions are only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        if action == "prepare_limited_live_rollout":
            report = automation_limited_live_rollout_gate_service.prepare_limited_live_rollout(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                rollout_readiness=rollout_readiness,
                linked_account=linked_account,
                actor=actor,
            )
            action_type = "limited_live_rollout_prepared"
            detail = "Limited-live rollout approval review completed without touching the live order path."
        elif action == "activate_limited_live_rollout":
            report = automation_limited_live_rollout_gate_service.activate_limited_live_rollout(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                rollout_readiness=rollout_readiness,
                linked_account=linked_account,
                actor=actor,
            )
            action_type = "limited_live_rollout_activated"
            detail = "Limited-live rollout activation wrote a runtime-only allowance inside hard caps."
        else:
            report = automation_limited_live_rollout_gate_service.rollback_limited_live_rollout(
                db,
                tenant=tenant,
                paper_state=state,
                linked_account=linked_account,
                actor=actor,
            )
            action_type = "limited_live_rollout_rolled_back"
            detail = "Limited-live rollout rollback disabled the runtime allowance."
        state["runtime"]["last_action"] = {
            "type": action_type,
            "action": action,
            "detail": detail,
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": action_type,
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "rollout_active": report.get("rollout_active"),
                "consumed_order_count": report.get("consumed_order_count"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action in {
        "prepare_limited_live_cap_expansion",
        "activate_limited_live_cap_expansion",
        "rollback_limited_live_cap_expansion",
    }:
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Limited-live cap expansion gate actions are only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        if action == "prepare_limited_live_cap_expansion":
            report = automation_limited_live_cap_expansion_gate_service.prepare_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                rollout_readiness=rollout_readiness,
                linked_account=linked_account,
                actor=actor,
            )
            action_type = "limited_live_cap_expansion_prepared"
            detail = "Limited-live cap expansion approval review completed without touching the live order path."
        elif action == "activate_limited_live_cap_expansion":
            report = automation_limited_live_cap_expansion_gate_service.activate_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                rollout_readiness=rollout_readiness,
                linked_account=linked_account,
                actor=actor,
            )
            action_type = "limited_live_cap_expansion_activated"
            detail = "Limited-live cap expansion activation wrote a runtime-only allowance inside hard caps."
        else:
            report = automation_limited_live_cap_expansion_gate_service.rollback_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=state,
                linked_account=linked_account,
                actor=actor,
            )
            action_type = "limited_live_cap_expansion_rolled_back"
            detail = "Limited-live cap expansion rollback disabled the runtime allowance."
        state["runtime"]["last_action"] = {
            "type": action_type,
            "action": action,
            "detail": detail,
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": action_type,
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "expansion_active": report.get("expansion_active"),
                "current_max_notional": report.get("current_max_notional"),
                "expanded_max_notional": report.get("expanded_max_notional"),
                "consumed_order_count": report.get("consumed_order_count"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_limited_live_rollout_canary_review":
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Limited-live rollout canary review is only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        report = automation_limited_live_rollout_canary_service.run_limited_live_rollout_canary_review(
            db,
            tenant=tenant,
            paper_state=state,
            live_state=live_state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "limited_live_rollout_canary_review",
            "action": action,
            "detail": "Limited-live rollout canary review completed without touching live orders or rollout allowances.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "limited_live_rollout_canary_review",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "clean_session_count": report.get("clean_session_count"),
                "required_clean_sessions": report.get("required_clean_sessions"),
                "consumed_order_count": report.get("consumed_order_count"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_limited_live_cap_expansion_report":
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Limited-live cap expansion report is only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        report = automation_limited_live_cap_expansion_report_service.run_limited_live_cap_expansion_report(
            db,
            tenant=tenant,
            paper_state=state,
            live_state=live_state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "limited_live_cap_expansion_report",
            "action": action,
            "detail": "Limited-live cap expansion report completed without touching live orders, caps, gates, or rollout allowances.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "limited_live_cap_expansion_report",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "current_max_notional": report.get("current_max_notional"),
                "recommended_next_max_notional": report.get("recommended_next_max_notional"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_limited_live_cap_expansion_canary_review":
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Limited-live cap expansion canary review is only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        report = automation_limited_live_cap_expansion_canary_service.run_limited_live_cap_expansion_canary_review(
            db,
            tenant=tenant,
            paper_state=state,
            live_state=live_state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "limited_live_cap_expansion_canary_review",
            "action": action,
            "detail": "Limited-live cap expansion canary review completed without touching live orders, caps, gates, or allowances.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "limited_live_cap_expansion_canary_review",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "clean_session_count": report.get("clean_session_count"),
                "required_clean_sessions": report.get("required_clean_sessions"),
                "current_max_notional": report.get("current_max_notional"),
                "expanded_max_notional": report.get("expanded_max_notional"),
                "consumed_order_count": report.get("consumed_order_count"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_limited_live_next_tier_cap_report":
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Limited-live next-tier cap report is only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        report = automation_limited_live_next_tier_cap_report_service.run_limited_live_next_tier_cap_report(
            db,
            tenant=tenant,
            paper_state=state,
            live_state=live_state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "limited_live_next_tier_cap_report",
            "action": action,
            "detail": "Limited-live next-tier cap report completed without touching live orders, caps, gates, or allowances.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "limited_live_next_tier_cap_report",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "current_max_notional": report.get("current_max_notional"),
                "recommended_next_max_notional": report.get("recommended_next_max_notional"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action in {
        "prepare_limited_live_next_tier_cap",
        "activate_limited_live_next_tier_cap",
        "rollback_limited_live_next_tier_cap",
    }:
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Limited-live next-tier cap gate actions are only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        if action == "prepare_limited_live_next_tier_cap":
            report = automation_limited_live_next_tier_cap_gate_service.prepare_limited_live_next_tier_cap(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                rollout_readiness=rollout_readiness,
                linked_account=linked_account,
                actor=actor,
            )
            action_type = "limited_live_next_tier_cap_prepared"
            detail = "Limited-live next-tier cap approval review completed without touching the live order path."
        elif action == "activate_limited_live_next_tier_cap":
            report = automation_limited_live_next_tier_cap_gate_service.activate_limited_live_next_tier_cap(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                rollout_readiness=rollout_readiness,
                linked_account=linked_account,
                actor=actor,
            )
            action_type = "limited_live_next_tier_cap_activated"
            detail = "Limited-live next-tier cap activation wrote a runtime-only allowance inside hard caps."
        else:
            report = automation_limited_live_next_tier_cap_gate_service.rollback_limited_live_next_tier_cap(
                db,
                tenant=tenant,
                paper_state=state,
                linked_account=linked_account,
                actor=actor,
            )
            action_type = "limited_live_next_tier_cap_rolled_back"
            detail = "Limited-live next-tier cap rollback disabled the runtime allowance."
        state["runtime"]["last_action"] = {
            "type": action_type,
            "action": action,
            "detail": detail,
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": action_type,
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "next_tier_cap_active": report.get("next_tier_cap_active"),
                "current_max_notional": report.get("current_max_notional"),
                "next_max_notional": report.get("next_max_notional") or report.get("expanded_max_notional"),
                "consumed_order_count": report.get("consumed_order_count"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action in {
        "run_limited_live_next_tier_cap_canary_review",
        "run_limited_live_broker_reconciliation",
        "run_limited_live_session_closeout",
        "run_limited_live_higher_cap_report",
        "submit_limited_live_operator_checklist",
    }:
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Limited-live safety ladder actions are only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        if action == "run_limited_live_next_tier_cap_canary_review":
            report = automation_limited_live_safety_ladder_service.run_limited_live_next_tier_cap_canary_review(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                profile_key=profile_key,
                linked_account=linked_account,
                actor=actor,
                run_source="manual",
            )
            action_type = "limited_live_next_tier_cap_canary_review"
            detail = "Limited-live next-tier cap canary completed without touching live orders, gates, settings, or allowances."
        elif action == "run_limited_live_broker_reconciliation":
            report = automation_limited_live_safety_ladder_service.run_limited_live_broker_reconciliation(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                profile_key=profile_key,
                linked_account=linked_account,
                actor=actor,
                run_source="manual",
            )
            action_type = "limited_live_broker_reconciliation"
            detail = "Limited-live broker reconciliation completed read-only against local live evidence."
        elif action == "run_limited_live_session_closeout":
            report = automation_limited_live_safety_ladder_service.run_limited_live_session_closeout(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                profile_key=profile_key,
                linked_account=linked_account,
                actor=actor,
                run_source="manual",
            )
            action_type = "limited_live_session_closeout"
            detail = "Limited-live session closeout completed and can disable runtime allowances only on hard-fault evidence."
        elif action == "run_limited_live_higher_cap_report":
            report = automation_limited_live_safety_ladder_service.run_limited_live_higher_cap_report(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                profile_key=profile_key,
                linked_account=linked_account,
                actor=actor,
                run_source="manual",
            )
            action_type = "limited_live_higher_cap_report"
            detail = "Limited-live higher-cap report completed as advisory evidence only."
        else:
            checklist_payload = action_payload.get("checklist") if isinstance(action_payload, dict) else None
            report = automation_limited_live_safety_ladder_service.submit_limited_live_operator_checklist(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=live_state,
                profile_key=profile_key,
                actor=actor,
                checklist=checklist_payload if isinstance(checklist_payload, dict) else None,
            )
            action_type = "limited_live_operator_checklist_submitted"
            detail = "Limited-live operator checklist submitted without changing live trading authority."
        state["runtime"]["last_action"] = {
            "type": action_type,
            "action": action,
            "detail": detail,
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": action_type,
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "clean_session_count": report.get("clean_session_count"),
                "required_clean_sessions": report.get("required_clean_sessions"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_live_pilot_canary_review":
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Live pilot canary review is only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        report = automation_live_pilot_canary_service.run_live_pilot_canary_review(
            db,
            tenant=tenant,
            paper_state=state,
            live_state=live_state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "live_pilot_canary_review",
            "action": action,
            "detail": "Live pilot canary review completed without touching the live order path.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "live_pilot_canary_review",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "clean_session_count": report.get("clean_session_count"),
                "required_clean_sessions": report.get("required_clean_sessions"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_live_pilot_expansion_canary_review":
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Live pilot expansion canary review is only available from the personal paper automation profile.")
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
        live_state = _read_trade_automation_state(
            tenant,
            profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
        )
        report = automation_live_pilot_expansion_canary_service.run_live_pilot_expansion_canary_review(
            db,
            tenant=tenant,
            paper_state=state,
            live_state=live_state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "live_pilot_expansion_canary_review",
            "action": action,
            "detail": "Live pilot expansion canary review completed without touching the live order path.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "live_pilot_expansion_canary_review",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "clean_session_count": report.get("clean_session_count"),
                "required_clean_sessions": report.get("required_clean_sessions"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=rollout_readiness,
            db=db,
        )
    elif action == "run_paper_order_lifecycle_canary_review":
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Paper order lifecycle canary is only available for the personal paper automation profile.")
        report = automation_paper_order_lifecycle_canary_service.run_paper_order_lifecycle_canary_review(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "paper_order_lifecycle_canary_review",
            "action": action,
            "detail": "Paper order lifecycle canary review completed.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "paper_order_lifecycle_canary_review",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "clean_session_count": report.get("clean_session_count"),
                "required_clean_sessions": report.get("required_clean_sessions"),
                "blocker_count": len(report.get("blockers") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=dict(trade_summary.get("rollout_readiness") or {}),
            db=db,
        )
    elif action == "run_paper_broker_reconciliation":
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Paper broker reconciliation is only available for the personal paper automation profile.")
        report = automation_paper_broker_reconciliation_service.run_paper_broker_reconciliation(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            run_source="manual",
        )
        state["runtime"]["last_action"] = {
            "type": "paper_broker_reconciliation",
            "action": action,
            "detail": "Paper broker reconciliation completed.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "paper_broker_reconciliation",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "matched_count": report.get("matched_count"),
                "blocker_count": len(report.get("blockers") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=dict(trade_summary.get("rollout_readiness") or {}),
            db=db,
        )
    elif action == "run_paper_order_lifecycle_soak":
        if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
            raise ValidationError("Paper order lifecycle soak is only available for the personal paper automation profile.")
        report = automation_paper_order_lifecycle_soak_service.run_paper_order_lifecycle_soak(
            db,
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            linked_account=linked_account,
            actor=actor,
            current_user=current_user,
        )
        state["runtime"]["last_action"] = {
            "type": "paper_order_lifecycle_soak",
            "action": action,
            "detail": "Paper order lifecycle soak completed.",
            "at": _serialize_datetime(_utc_now()),
        }
        _append_history(
            state,
            {
                "type": "paper_order_lifecycle_soak",
                "at": _serialize_datetime(_utc_now()),
                "status": report.get("status"),
                "terminal_state": report.get("terminal_state"),
                "broker_order_id": report.get("broker_order_id"),
                "blocker_count": len(report.get("blockers") or []),
            },
        )
        _write_trade_automation_state(tenant, state, profile_key=profile_key)
        db.commit()
        trade_summary = get_trade_summary(db=db, current_user=current_user)
        return _build_snapshot_payload(
            tenant,
            state,
            profile_key=profile_key,
            linked_account=linked_account,
            rollout_readiness=dict(trade_summary.get("rollout_readiness") or {}),
            db=db,
        )
    else:
        raise ValidationError("Automation action is not supported.")

    state["runtime"]["last_action"] = {"type": "control_action", "action": action, "detail": detail, "at": _serialize_datetime(_utc_now())}
    _append_history(
        state,
        {"type": "control_action", "action": action, "detail": detail, "at": _serialize_datetime(_utc_now())},
    )
    _write_trade_automation_state(tenant, state, profile_key=profile_key)
    record_audit_event(
        db,
        event_type=f"trade_automation.{action}",
        tenant=tenant,
        user=actor,
        payload={
            "profile_key": profile_key,
            "linked_account_id": getattr(linked_account, "id", None),
            "action": action,
            "settings": serialize_value(state["settings"]),
        },
    )
    db.commit()
    trade_summary = get_trade_summary(db=db, current_user=current_user)
    return _build_snapshot_payload(
        tenant,
        state,
        profile_key=profile_key,
        linked_account=linked_account,
        rollout_readiness=dict(trade_summary.get("rollout_readiness") or {}),
        db=db,
    )


def run_enabled_trade_automation_cycles(db: Session, *, limit: int | None = None) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    summary = {
        "processed": 0,
        "eligible": 0,
        "skipped": 0,
        "errors": 0,
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    now = _utc_now()
    for tenant in tenants:
        if processed >= batch_limit:
            break
        tenant_id = tenant.id
        tenant_slug = tenant.slug
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            settings_state = state["settings"]
            if not settings_state["enabled"] or not settings_state["armed"] or settings_state["kill_switch"]:
                continue
            summary["eligible"] += 1
            next_run_at = _parse_iso_datetime(state["runtime"].get("next_run_at"))
            if next_run_at is not None and next_run_at > now:
                summary["skipped"] += 1
                continue
            processed += 1
            try:
                snapshot = _run_trade_automation_cycle(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    forced=False,
                    actor=None,
                )
                summary["processed"] += 1
                summary["items"].append(
                    {
                        "tenant_id": tenant_id,
                        "tenant_slug": tenant_slug,
                        "profile_key": profile_key,
                        "linked_account_id": getattr(linked_account, "id", None),
                        "status": snapshot.get("status", {}).get("key"),
                        "last_action": snapshot.get("runtime", {}).get("last_action"),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append(
                    {
                        "tenant_id": tenant_id,
                        "tenant_slug": tenant_slug,
                        "profile_key": profile_key,
                        "linked_account_id": getattr(linked_account, "id", None),
                        "status": "error",
                        "detail": str(exc),
                    }
                )
    return summary


def run_trade_automation_daily_ai_reviews(db: Session, *, limit: int | None = None) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    summary = {
        "processed": 0,
        "reviewed": 0,
        "skipped": 0,
        "errors": 0,
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            if not bool(state["settings"].get("ai_daily_review_enabled", True)):
                summary["skipped"] += 1
                continue
            runtime_state = state.get("runtime") or {}
            has_journal = bool(runtime_state.get("ai_daily_journal"))
            has_cycles = int(runtime_state.get("cycle_count") or 0) > 0
            if not has_journal and not has_cycles and not bool(state["settings"].get("enabled")):
                summary["skipped"] += 1
                continue
            processed += 1
            rollout_readiness: dict[str, Any] = {}
            if profile_key == _AUTOMATION_PERSONAL_LIVE_PROFILE:
                try:
                    trade_summary = get_trade_summary(
                        db=db,
                        current_user=_build_system_current_user(tenant, None),
                    )
                    rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
                except Exception:
                    rollout_readiness = {}
            try:
                review = automation_ai_review_service.run_trade_automation_ai_review(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    forced=False,
                    actor=None,
                    live_route_allowed=(
                        bool(rollout_readiness.get("allows_live_rollout"))
                        if profile_key == _AUTOMATION_PERSONAL_LIVE_PROFILE
                        else True
                    ),
                )
                if review.get("status") == "reviewed":
                    _append_history(
                        state,
                        {
                            "type": "ai_review",
                            "at": review.get("reviewed_at"),
                            "session_day": review.get("session_day"),
                            "applied_change_count": len(review.get("applied_changes") or []),
                            "skipped_change_count": len(review.get("skipped_changes") or []),
                        },
                    )
                    _write_trade_automation_state(tenant, state, profile_key=profile_key)
                    db.commit()
                    summary["reviewed"] += 1
                else:
                    db.rollback()
                    summary["skipped"] += 1
                summary["processed"] += 1
                summary["items"].append(
                    {
                        "tenant_id": tenant.id,
                        "tenant_slug": tenant.slug,
                        "profile_key": profile_key,
                        "linked_account_id": getattr(linked_account, "id", None),
                        "status": review.get("status"),
                        "reason": review.get("reason"),
                        "session_day": review.get("session_day"),
                        "applied_change_count": len(review.get("applied_changes") or []),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append(
                    {
                        "tenant_id": tenant.id,
                        "tenant_slug": tenant.slug,
                        "profile_key": profile_key,
                        "linked_account_id": getattr(linked_account, "id", None),
                        "status": "error",
                        "detail": str(exc),
                    }
                )
    return summary


def run_trade_automation_paper_canary_reviews(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    current_time = now or _utc_now()
    session_day, review_window_open = automation_paper_canary_service.canary_review_session_day_for(
        current_time,
        forced=False,
    )
    next_eligible = automation_paper_canary_service.next_eligible_canary_review_at(current_time)
    summary = {
        "processed": 0,
        "reviewed": 0,
        "skipped": 0,
        "errors": 0,
        "session_day": session_day,
        "review_window_open": review_window_open,
        "next_eligible_run_at": _serialize_datetime(next_eligible),
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
                continue
            processed += 1
            summary["processed"] += 1
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            settings_state = dict(state.get("settings") or {})
            runtime_state = dict(state.get("runtime") or {})
            item_base = {
                "tenant_id": tenant.id,
                "tenant_slug": tenant.slug,
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
            }
            if not bool(settings_state.get("paper_canary_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append({**item_base, "status": "skipped", "reason": "paper_canary_disabled"})
                continue
            if not bool(settings_state.get("paper_canary_auto_review_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append({**item_base, "status": "skipped", "reason": "paper_canary_auto_review_disabled"})
                continue
            if not review_window_open:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "review_window_not_open",
                        "next_eligible_run_at": _serialize_datetime(next_eligible),
                    }
                )
                continue
            if str(runtime_state.get("paper_canary_last_scheduled_session_day") or "").strip() == session_day:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "already_reviewed_for_session",
                        "last_scheduled_run_at": runtime_state.get("paper_canary_last_scheduled_run_at"),
                    }
                )
                continue
            try:
                trade_summary = get_trade_summary(
                    db=db,
                    current_user=_build_system_current_user(tenant, None),
                )
                rollout_readiness = dict(trade_summary.get("rollout_readiness") or {})
                report = automation_paper_canary_service.run_paper_canary_review(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    rollout_readiness=rollout_readiness,
                    actor=None,
                    now=current_time,
                    run_source="scheduled",
                )
                _append_history(
                    state,
                    {
                        "type": "paper_canary_scheduled_review",
                        "at": report.get("evaluated_at"),
                        "status": report.get("status"),
                        "session_day": report.get("session_day"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "blocker_count": len(report.get("blockers") or []),
                    },
                )
                _write_trade_automation_state(tenant, state, profile_key=profile_key)
                db.commit()
                summary["reviewed"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": report.get("status"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "blocker_count": len(report.get("blockers") or []),
                        "note_id": report.get("note_id") or report.get("related_note_id"),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append({**item_base, "status": "error", "detail": str(exc)})
    return summary


def run_trade_automation_paper_broker_reconciliations(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    current_time = now or _utc_now()
    session_day, review_window_open = automation_paper_canary_service.canary_review_session_day_for(
        current_time,
        forced=False,
    )
    summary = {
        "processed": 0,
        "reviewed": 0,
        "skipped": 0,
        "errors": 0,
        "session_day": session_day,
        "review_window_open": review_window_open,
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
                continue
            processed += 1
            summary["processed"] += 1
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            runtime_state = dict(state.get("runtime") or {})
            item_base = {
                "tenant_id": tenant.id,
                "tenant_slug": tenant.slug,
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
            }
            if not review_window_open:
                summary["skipped"] += 1
                summary["items"].append({**item_base, "status": "skipped", "reason": "review_window_not_open"})
                continue
            if str(runtime_state.get("paper_broker_reconciliation_last_scheduled_session_day") or "").strip() == session_day:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "already_reconciled_for_session",
                        "last_scheduled_run_at": runtime_state.get("paper_broker_reconciliation_last_scheduled_run_at"),
                    }
                )
                continue
            try:
                report = automation_paper_broker_reconciliation_service.run_paper_broker_reconciliation(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    actor=None,
                    now=current_time,
                    run_source="scheduled",
                )
                _append_history(
                    state,
                    {
                        "type": "paper_broker_scheduled_reconciliation",
                        "at": report.get("checked_at"),
                        "status": report.get("status"),
                        "session_day": report.get("session_day"),
                        "matched_count": report.get("matched_count"),
                        "blocker_count": len(report.get("blockers") or []),
                    },
                )
                _write_trade_automation_state(tenant, state, profile_key=profile_key)
                db.commit()
                summary["reviewed"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": report.get("status"),
                        "matched_count": report.get("matched_count"),
                        "blocker_count": len(report.get("blockers") or []),
                        "note_id": report.get("note_id") or report.get("related_note_id"),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append({**item_base, "status": "error", "detail": str(exc)})
    return summary


def run_trade_automation_paper_order_lifecycle_canary_reviews(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    current_time = now or _utc_now()
    session_day, review_window_open = automation_paper_order_lifecycle_canary_service.lifecycle_canary_session_day_for(
        current_time,
        forced=False,
    )
    next_eligible = automation_paper_order_lifecycle_canary_service.next_eligible_lifecycle_canary_review_at(current_time)
    summary = {
        "processed": 0,
        "reviewed": 0,
        "submitted": 0,
        "skipped": 0,
        "errors": 0,
        "session_day": session_day,
        "review_window_open": review_window_open,
        "next_eligible_run_at": _serialize_datetime(next_eligible),
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
                continue
            processed += 1
            summary["processed"] += 1
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            settings_state = dict(state.get("settings") or {})
            runtime_state = dict(state.get("runtime") or {})
            item_base = {
                "tenant_id": tenant.id,
                "tenant_slug": tenant.slug,
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
            }
            if not bool(settings_state.get("paper_order_lifecycle_canary_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append({**item_base, "status": "skipped", "reason": "paper_order_lifecycle_canary_disabled"})
                continue
            if not review_window_open:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "review_window_not_open",
                        "next_eligible_run_at": _serialize_datetime(next_eligible),
                    }
                )
                continue
            if str(runtime_state.get("paper_order_lifecycle_canary_last_scheduled_session_day") or "").strip() == session_day:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "already_reviewed_for_session",
                        "last_scheduled_run_at": runtime_state.get("paper_order_lifecycle_canary_last_scheduled_run_at"),
                    }
                )
                continue
            try:
                auto_submit_enabled = bool(settings_state.get("paper_order_lifecycle_auto_submit_enabled", False))
                auto_submit_status = "disabled"
                last_soak_for_session = automation_paper_order_lifecycle_canary_service._runtime_soak_by_day(
                    runtime_state,
                    session_day,
                )
                prior_canary = dict(runtime_state.get("paper_order_lifecycle_canary_last_report") or {})
                prior_blocked = bool(prior_canary.get("blockers")) or str(prior_canary.get("status") or "").lower() == "blocked"
                if auto_submit_enabled and not last_soak_for_session:
                    if prior_blocked:
                        auto_submit_status = "skipped_prior_blocker"
                    elif bool(settings_state.get("kill_switch")):
                        auto_submit_status = "skipped_kill_switch"
                    elif str(settings_state.get("execution_intent") or "").strip().lower() != "broker_paper":
                        auto_submit_status = "skipped_non_paper_route"
                    else:
                        soak_report = automation_paper_order_lifecycle_soak_service.run_paper_order_lifecycle_soak(
                            db,
                            tenant=tenant,
                            state=state,
                            profile_key=profile_key,
                            linked_account=linked_account,
                            actor=None,
                            current_user=_build_system_current_user(tenant, None),
                            now=current_time,
                        )
                        runtime = state.setdefault("runtime", {})
                        runtime["paper_order_lifecycle_canary_last_auto_submit_at"] = soak_report.get("checked_at")
                        runtime["paper_order_lifecycle_canary_last_auto_submit_session_day"] = session_day
                        auto_submit_status = str(soak_report.get("status") or "submitted")
                        summary["submitted"] += 1
                        _write_trade_automation_state(tenant, state, profile_key=profile_key)
                        db.commit()
                elif auto_submit_enabled and last_soak_for_session:
                    auto_submit_status = "already_has_session_soak"

                report = automation_paper_order_lifecycle_canary_service.run_paper_order_lifecycle_canary_review(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    actor=None,
                    now=current_time,
                    run_source="scheduled",
                )
                _append_history(
                    state,
                    {
                        "type": "paper_order_lifecycle_canary_scheduled_review",
                        "at": report.get("evaluated_at"),
                        "status": report.get("status"),
                        "session_day": report.get("session_day"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "blocker_count": len(report.get("blockers") or []),
                        "auto_submit_status": auto_submit_status,
                    },
                )
                _write_trade_automation_state(tenant, state, profile_key=profile_key)
                db.commit()
                summary["reviewed"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": report.get("status"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "blocker_count": len(report.get("blockers") or []),
                        "note_id": report.get("note_id") or report.get("related_note_id"),
                        "auto_submit_status": auto_submit_status,
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append({**item_base, "status": "error", "detail": str(exc)})
    return summary


def run_trade_automation_live_pilot_canary_reviews(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    current_time = now or _utc_now()
    session_day, review_window_open = automation_live_pilot_canary_service.live_pilot_canary_session_day_for(
        current_time,
        forced=False,
    )
    next_eligible = automation_live_pilot_canary_service.next_eligible_live_pilot_canary_review_at(current_time)
    summary = {
        "processed": 0,
        "reviewed": 0,
        "skipped": 0,
        "errors": 0,
        "session_day": session_day,
        "review_window_open": review_window_open,
        "next_eligible_run_at": _serialize_datetime(next_eligible),
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
                continue
            processed += 1
            summary["processed"] += 1
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            settings_state = dict(state.get("settings") or {})
            runtime_state = dict(state.get("runtime") or {})
            item_base = {
                "tenant_id": tenant.id,
                "tenant_slug": tenant.slug,
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
            }
            if not bool(settings_state.get("live_pilot_canary_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append({**item_base, "status": "skipped", "reason": "live_pilot_canary_disabled"})
                continue
            if not bool(settings_state.get("live_pilot_canary_auto_review_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {**item_base, "status": "skipped", "reason": "live_pilot_canary_auto_review_disabled"}
                )
                continue
            if not review_window_open:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "review_window_not_open",
                        "next_eligible_run_at": _serialize_datetime(next_eligible),
                    }
                )
                continue
            if str(runtime_state.get("live_pilot_canary_last_scheduled_session_day") or "").strip() == session_day:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "already_reviewed_for_session",
                        "last_scheduled_run_at": runtime_state.get("live_pilot_canary_last_scheduled_run_at"),
                    }
                )
                continue
            try:
                live_state = _read_trade_automation_state(
                    tenant,
                    profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
                )
                report = automation_live_pilot_canary_service.run_live_pilot_canary_review(
                    db,
                    tenant=tenant,
                    paper_state=state,
                    live_state=live_state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    actor=None,
                    now=current_time,
                    run_source="scheduled",
                )
                _append_history(
                    state,
                    {
                        "type": "live_pilot_canary_scheduled_review",
                        "at": report.get("evaluated_at"),
                        "status": report.get("status"),
                        "session_day": report.get("session_day"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "blocker_count": len(report.get("blockers") or []),
                    },
                )
                _write_trade_automation_state(tenant, state, profile_key=profile_key)
                db.commit()
                summary["reviewed"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": report.get("status"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "blocker_count": len(report.get("blockers") or []),
                        "note_id": report.get("note_id") or report.get("related_note_id"),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append({**item_base, "status": "error", "detail": str(exc)})
    return summary


def run_trade_automation_live_pilot_expansion_canary_reviews(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    current_time = now or _utc_now()
    session_day, review_window_open = (
        automation_live_pilot_expansion_canary_service.live_pilot_expansion_canary_session_day_for(
            current_time,
            forced=False,
        )
    )
    next_eligible = (
        automation_live_pilot_expansion_canary_service.next_eligible_live_pilot_expansion_canary_review_at(
            current_time
        )
    )
    summary = {
        "processed": 0,
        "reviewed": 0,
        "skipped": 0,
        "errors": 0,
        "session_day": session_day,
        "review_window_open": review_window_open,
        "next_eligible_run_at": _serialize_datetime(next_eligible),
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
                continue
            processed += 1
            summary["processed"] += 1
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            settings_state = dict(state.get("settings") or {})
            runtime_state = dict(state.get("runtime") or {})
            item_base = {
                "tenant_id": tenant.id,
                "tenant_slug": tenant.slug,
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
            }
            if not bool(settings_state.get("live_pilot_expansion_canary_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {**item_base, "status": "skipped", "reason": "live_pilot_expansion_canary_disabled"}
                )
                continue
            if not bool(settings_state.get("live_pilot_expansion_canary_auto_review_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "live_pilot_expansion_canary_auto_review_disabled",
                    }
                )
                continue
            if not review_window_open:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "review_window_not_open",
                        "next_eligible_run_at": _serialize_datetime(next_eligible),
                    }
                )
                continue
            if str(runtime_state.get("live_pilot_expansion_canary_last_scheduled_session_day") or "").strip() == session_day:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "already_reviewed_for_session",
                        "last_scheduled_run_at": runtime_state.get(
                            "live_pilot_expansion_canary_last_scheduled_run_at"
                        ),
                    }
                )
                continue
            try:
                live_state = _read_trade_automation_state(
                    tenant,
                    profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
                )
                report = automation_live_pilot_expansion_canary_service.run_live_pilot_expansion_canary_review(
                    db,
                    tenant=tenant,
                    paper_state=state,
                    live_state=live_state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    actor=None,
                    now=current_time,
                    run_source="scheduled",
                )
                _append_history(
                    state,
                    {
                        "type": "live_pilot_expansion_canary_scheduled_review",
                        "at": report.get("evaluated_at"),
                        "status": report.get("status"),
                        "session_day": report.get("session_day"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "blocker_count": len(report.get("blockers") or []),
                    },
                )
                _write_trade_automation_state(tenant, state, profile_key=profile_key)
                db.commit()
                summary["reviewed"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": report.get("status"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "blocker_count": len(report.get("blockers") or []),
                        "note_id": report.get("note_id") or report.get("related_note_id"),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append({**item_base, "status": "error", "detail": str(exc)})
    return summary


def run_trade_automation_live_pilot_window_canary_reviews(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    current_time = now or _utc_now()
    session_day, review_window_open = (
        automation_live_pilot_window_canary_service.live_pilot_window_canary_session_day_for(
            current_time,
            forced=False,
        )
    )
    next_eligible = automation_live_pilot_window_canary_service.next_eligible_live_pilot_window_canary_review_at(
        current_time
    )
    summary = {
        "processed": 0,
        "reviewed": 0,
        "skipped": 0,
        "errors": 0,
        "session_day": session_day,
        "review_window_open": review_window_open,
        "next_eligible_run_at": _serialize_datetime(next_eligible),
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
                continue
            processed += 1
            summary["processed"] += 1
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            settings_state = dict(state.get("settings") or {})
            runtime_state = dict(state.get("runtime") or {})
            item_base = {
                "tenant_id": tenant.id,
                "tenant_slug": tenant.slug,
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
            }
            if not bool(settings_state.get("live_pilot_window_canary_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {**item_base, "status": "skipped", "reason": "live_pilot_window_canary_disabled"}
                )
                continue
            if not bool(settings_state.get("live_pilot_window_canary_auto_review_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "live_pilot_window_canary_auto_review_disabled",
                    }
                )
                continue
            if not review_window_open:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "review_window_not_open",
                        "next_eligible_run_at": _serialize_datetime(next_eligible),
                    }
                )
                continue
            if str(runtime_state.get("live_pilot_window_canary_last_scheduled_session_day") or "").strip() == session_day:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "already_reviewed_for_session",
                        "last_scheduled_run_at": runtime_state.get(
                            "live_pilot_window_canary_last_scheduled_run_at"
                        ),
                    }
                )
                continue
            try:
                live_state = _read_trade_automation_state(
                    tenant,
                    profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
                )
                report = automation_live_pilot_window_canary_service.run_live_pilot_window_canary_review(
                    db,
                    tenant=tenant,
                    paper_state=state,
                    live_state=live_state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    actor=None,
                    now=current_time,
                    run_source="scheduled",
                )
                _append_history(
                    state,
                    {
                        "type": "live_pilot_window_canary_scheduled_review",
                        "at": report.get("evaluated_at"),
                        "status": report.get("status"),
                        "session_day": report.get("session_day"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "blocker_count": len(report.get("blockers") or []),
                    },
                )
                _write_trade_automation_state(tenant, state, profile_key=profile_key)
                db.commit()
                summary["reviewed"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": report.get("status"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "blocker_count": len(report.get("blockers") or []),
                        "note_id": report.get("note_id") or report.get("related_note_id"),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append({**item_base, "status": "error", "detail": str(exc)})
    return summary


def run_trade_automation_live_pilot_promotion_reports(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    current_time = now or _utc_now()
    session_day, review_window_open = (
        automation_live_pilot_promotion_report_service.live_pilot_promotion_session_day_for(
            current_time,
            forced=False,
        )
    )
    next_eligible = automation_live_pilot_promotion_report_service.next_eligible_live_pilot_promotion_review_at(
        current_time
    )
    summary = {
        "processed": 0,
        "reviewed": 0,
        "skipped": 0,
        "errors": 0,
        "session_day": session_day,
        "review_window_open": review_window_open,
        "next_eligible_run_at": _serialize_datetime(next_eligible),
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
                continue
            processed += 1
            summary["processed"] += 1
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            settings_state = dict(state.get("settings") or {})
            runtime_state = dict(state.get("runtime") or {})
            item_base = {
                "tenant_id": tenant.id,
                "tenant_slug": tenant.slug,
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
            }
            if not bool(settings_state.get("live_pilot_promotion_report_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {**item_base, "status": "skipped", "reason": "live_pilot_promotion_report_disabled"}
                )
                continue
            if not bool(settings_state.get("live_pilot_promotion_report_auto_review_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "live_pilot_promotion_report_auto_review_disabled",
                    }
                )
                continue
            if not review_window_open:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "review_window_not_open",
                        "next_eligible_run_at": _serialize_datetime(next_eligible),
                    }
                )
                continue
            if str(runtime_state.get("live_pilot_promotion_report_last_scheduled_session_day") or "").strip() == session_day:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "already_reviewed_for_session",
                        "last_scheduled_run_at": runtime_state.get(
                            "live_pilot_promotion_report_last_scheduled_run_at"
                        ),
                    }
                )
                continue
            try:
                live_state = _read_trade_automation_state(
                    tenant,
                    profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
                )
                report = automation_live_pilot_promotion_report_service.run_live_pilot_promotion_report(
                    db,
                    tenant=tenant,
                    paper_state=state,
                    live_state=live_state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    actor=None,
                    now=current_time,
                    run_source="scheduled",
                )
                _append_history(
                    state,
                    {
                        "type": "live_pilot_promotion_report_scheduled_review",
                        "at": report.get("evaluated_at"),
                        "status": report.get("status"),
                        "session_day": report.get("session_day"),
                        "blocker_count": len(report.get("blockers") or []),
                        "warning_count": len(report.get("warnings") or []),
                    },
                )
                _write_trade_automation_state(tenant, state, profile_key=profile_key)
                db.commit()
                summary["reviewed"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": report.get("status"),
                        "blocker_count": len(report.get("blockers") or []),
                        "warning_count": len(report.get("warnings") or []),
                        "note_id": report.get("note_id") or report.get("related_note_id"),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append({**item_base, "status": "error", "detail": str(exc)})
    return summary


def run_trade_automation_limited_live_rollout_canary_reviews(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    current_time = now or _utc_now()
    session_day, review_window_open = (
        automation_limited_live_rollout_canary_service.limited_live_rollout_canary_session_day_for(
            current_time,
            forced=False,
        )
    )
    next_eligible = (
        automation_limited_live_rollout_canary_service.next_eligible_limited_live_rollout_canary_review_at(
            current_time
        )
    )
    summary = {
        "processed": 0,
        "reviewed": 0,
        "skipped": 0,
        "errors": 0,
        "session_day": session_day,
        "review_window_open": review_window_open,
        "next_eligible_run_at": _serialize_datetime(next_eligible),
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
                continue
            processed += 1
            summary["processed"] += 1
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            settings_state = dict(state.get("settings") or {})
            runtime_state = dict(state.get("runtime") or {})
            item_base = {
                "tenant_id": tenant.id,
                "tenant_slug": tenant.slug,
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
            }
            if not bool(settings_state.get("limited_live_rollout_canary_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {**item_base, "status": "skipped", "reason": "limited_live_rollout_canary_disabled"}
                )
                continue
            if not bool(settings_state.get("limited_live_rollout_canary_auto_review_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "limited_live_rollout_canary_auto_review_disabled",
                    }
                )
                continue
            if not review_window_open:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "review_window_not_open",
                        "next_eligible_run_at": _serialize_datetime(next_eligible),
                    }
                )
                continue
            if str(runtime_state.get("limited_live_rollout_canary_last_scheduled_session_day") or "").strip() == session_day:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "already_reviewed_for_session",
                        "last_scheduled_run_at": runtime_state.get(
                            "limited_live_rollout_canary_last_scheduled_run_at"
                        ),
                    }
                )
                continue
            try:
                live_state = _read_trade_automation_state(
                    tenant,
                    profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
                )
                report = automation_limited_live_rollout_canary_service.run_limited_live_rollout_canary_review(
                    db,
                    tenant=tenant,
                    paper_state=state,
                    live_state=live_state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    actor=None,
                    now=current_time,
                    run_source="scheduled",
                )
                _append_history(
                    state,
                    {
                        "type": "limited_live_rollout_canary_scheduled_review",
                        "at": report.get("evaluated_at"),
                        "status": report.get("status"),
                        "session_day": report.get("session_day"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "consumed_order_count": report.get("consumed_order_count"),
                        "blocker_count": len(report.get("blockers") or []),
                        "warning_count": len(report.get("warnings") or []),
                    },
                )
                _write_trade_automation_state(tenant, state, profile_key=profile_key)
                db.commit()
                summary["reviewed"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": report.get("status"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "consumed_order_count": report.get("consumed_order_count"),
                        "blocker_count": len(report.get("blockers") or []),
                        "warning_count": len(report.get("warnings") or []),
                        "note_id": report.get("note_id") or report.get("related_note_id"),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append({**item_base, "status": "error", "detail": str(exc)})
    return summary


def run_trade_automation_limited_live_cap_expansion_reports(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    current_time = now or _utc_now()
    session_day, review_window_open = (
        automation_limited_live_cap_expansion_report_service.limited_live_cap_expansion_session_day_for(
            current_time,
            forced=False,
        )
    )
    next_eligible = (
        automation_limited_live_cap_expansion_report_service.next_eligible_limited_live_cap_expansion_review_at(
            current_time
        )
    )
    summary = {
        "processed": 0,
        "reviewed": 0,
        "skipped": 0,
        "errors": 0,
        "session_day": session_day,
        "review_window_open": review_window_open,
        "next_eligible_run_at": _serialize_datetime(next_eligible),
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
                continue
            processed += 1
            summary["processed"] += 1
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            settings_state = dict(state.get("settings") or {})
            runtime_state = dict(state.get("runtime") or {})
            item_base = {
                "tenant_id": tenant.id,
                "tenant_slug": tenant.slug,
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
            }
            if not bool(settings_state.get("limited_live_cap_expansion_report_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {**item_base, "status": "skipped", "reason": "limited_live_cap_expansion_report_disabled"}
                )
                continue
            if not bool(settings_state.get("limited_live_cap_expansion_report_auto_review_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "limited_live_cap_expansion_report_auto_review_disabled",
                    }
                )
                continue
            if not review_window_open:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "review_window_not_open",
                        "next_eligible_run_at": _serialize_datetime(next_eligible),
                    }
                )
                continue
            if str(runtime_state.get("limited_live_cap_expansion_report_last_scheduled_session_day") or "").strip() == session_day:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "already_reviewed_for_session",
                        "last_scheduled_run_at": runtime_state.get(
                            "limited_live_cap_expansion_report_last_scheduled_run_at"
                        ),
                    }
                )
                continue
            try:
                live_state = _read_trade_automation_state(
                    tenant,
                    profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
                )
                report = automation_limited_live_cap_expansion_report_service.run_limited_live_cap_expansion_report(
                    db,
                    tenant=tenant,
                    paper_state=state,
                    live_state=live_state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    actor=None,
                    now=current_time,
                    run_source="scheduled",
                )
                _append_history(
                    state,
                    {
                        "type": "limited_live_cap_expansion_report_scheduled_review",
                        "at": report.get("evaluated_at"),
                        "status": report.get("status"),
                        "session_day": report.get("session_day"),
                        "current_max_notional": report.get("current_max_notional"),
                        "recommended_next_max_notional": report.get("recommended_next_max_notional"),
                        "blocker_count": len(report.get("blockers") or []),
                        "warning_count": len(report.get("warnings") or []),
                    },
                )
                _write_trade_automation_state(tenant, state, profile_key=profile_key)
                db.commit()
                summary["reviewed"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": report.get("status"),
                        "current_max_notional": report.get("current_max_notional"),
                        "recommended_next_max_notional": report.get("recommended_next_max_notional"),
                        "blocker_count": len(report.get("blockers") or []),
                        "warning_count": len(report.get("warnings") or []),
                        "note_id": report.get("note_id") or report.get("related_note_id"),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append({**item_base, "status": "error", "detail": str(exc)})
    return summary


def run_trade_automation_limited_live_cap_expansion_canary_reviews(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    current_time = now or _utc_now()
    session_day, review_window_open = (
        automation_limited_live_cap_expansion_canary_service.limited_live_cap_expansion_canary_session_day_for(
            current_time,
            forced=False,
        )
    )
    next_eligible = (
        automation_limited_live_cap_expansion_canary_service.next_eligible_limited_live_cap_expansion_canary_review_at(
            current_time
        )
    )
    summary = {
        "processed": 0,
        "reviewed": 0,
        "skipped": 0,
        "errors": 0,
        "session_day": session_day,
        "review_window_open": review_window_open,
        "next_eligible_run_at": _serialize_datetime(next_eligible),
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
                continue
            processed += 1
            summary["processed"] += 1
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            settings_state = dict(state.get("settings") or {})
            runtime_state = dict(state.get("runtime") or {})
            item_base = {
                "tenant_id": tenant.id,
                "tenant_slug": tenant.slug,
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
            }
            if not bool(settings_state.get("limited_live_cap_expansion_canary_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {**item_base, "status": "skipped", "reason": "limited_live_cap_expansion_canary_disabled"}
                )
                continue
            if not bool(settings_state.get("limited_live_cap_expansion_canary_auto_review_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "limited_live_cap_expansion_canary_auto_review_disabled",
                    }
                )
                continue
            if not review_window_open:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "review_window_not_open",
                        "next_eligible_run_at": _serialize_datetime(next_eligible),
                    }
                )
                continue
            if str(runtime_state.get("limited_live_cap_expansion_canary_last_scheduled_session_day") or "").strip() == session_day:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "already_reviewed_for_session",
                        "last_scheduled_run_at": runtime_state.get(
                            "limited_live_cap_expansion_canary_last_scheduled_run_at"
                        ),
                    }
                )
                continue
            try:
                live_state = _read_trade_automation_state(
                    tenant,
                    profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
                )
                report = automation_limited_live_cap_expansion_canary_service.run_limited_live_cap_expansion_canary_review(
                    db,
                    tenant=tenant,
                    paper_state=state,
                    live_state=live_state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    actor=None,
                    now=current_time,
                    run_source="scheduled",
                )
                _append_history(
                    state,
                    {
                        "type": "limited_live_cap_expansion_canary_scheduled_review",
                        "at": report.get("evaluated_at"),
                        "status": report.get("status"),
                        "session_day": report.get("session_day"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "current_max_notional": report.get("current_max_notional"),
                        "expanded_max_notional": report.get("expanded_max_notional"),
                        "consumed_order_count": report.get("consumed_order_count"),
                        "blocker_count": len(report.get("blockers") or []),
                        "warning_count": len(report.get("warnings") or []),
                    },
                )
                _write_trade_automation_state(tenant, state, profile_key=profile_key)
                db.commit()
                summary["reviewed"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": report.get("status"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "consumed_order_count": report.get("consumed_order_count"),
                        "blocker_count": len(report.get("blockers") or []),
                        "warning_count": len(report.get("warnings") or []),
                        "note_id": report.get("note_id") or report.get("related_note_id"),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append({**item_base, "status": "error", "detail": str(exc)})
    return summary


def run_trade_automation_limited_live_next_tier_cap_reports(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    current_time = now or _utc_now()
    session_day, review_window_open = (
        automation_limited_live_next_tier_cap_report_service.limited_live_next_tier_cap_session_day_for(
            current_time,
            forced=False,
        )
    )
    next_eligible = (
        automation_limited_live_next_tier_cap_report_service.next_eligible_limited_live_next_tier_cap_review_at(
            current_time
        )
    )
    summary = {
        "processed": 0,
        "reviewed": 0,
        "skipped": 0,
        "errors": 0,
        "session_day": session_day,
        "review_window_open": review_window_open,
        "next_eligible_run_at": _serialize_datetime(next_eligible),
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
                continue
            processed += 1
            summary["processed"] += 1
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            settings_state = dict(state.get("settings") or {})
            runtime_state = dict(state.get("runtime") or {})
            item_base = {
                "tenant_id": tenant.id,
                "tenant_slug": tenant.slug,
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
            }
            if not bool(settings_state.get("limited_live_next_tier_cap_report_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {**item_base, "status": "skipped", "reason": "limited_live_next_tier_cap_report_disabled"}
                )
                continue
            if not bool(settings_state.get("limited_live_next_tier_cap_report_auto_review_enabled", True)):
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "limited_live_next_tier_cap_report_auto_review_disabled",
                    }
                )
                continue
            if not review_window_open:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "review_window_not_open",
                        "next_eligible_run_at": _serialize_datetime(next_eligible),
                    }
                )
                continue
            if str(runtime_state.get("limited_live_next_tier_cap_report_last_scheduled_session_day") or "").strip() == session_day:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "already_reviewed_for_session",
                        "last_scheduled_run_at": runtime_state.get(
                            "limited_live_next_tier_cap_report_last_scheduled_run_at"
                        ),
                    }
                )
                continue
            try:
                live_state = _read_trade_automation_state(
                    tenant,
                    profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
                )
                report = automation_limited_live_next_tier_cap_report_service.run_limited_live_next_tier_cap_report(
                    db,
                    tenant=tenant,
                    paper_state=state,
                    live_state=live_state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    actor=None,
                    now=current_time,
                    run_source="scheduled",
                )
                _append_history(
                    state,
                    {
                        "type": "limited_live_next_tier_cap_scheduled_review",
                        "at": report.get("evaluated_at"),
                        "status": report.get("status"),
                        "session_day": report.get("session_day"),
                        "current_max_notional": report.get("current_max_notional"),
                        "recommended_next_max_notional": report.get("recommended_next_max_notional"),
                        "blocker_count": len(report.get("blockers") or []),
                        "warning_count": len(report.get("warnings") or []),
                    },
                )
                _write_trade_automation_state(tenant, state, profile_key=profile_key)
                db.commit()
                summary["reviewed"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": report.get("status"),
                        "current_max_notional": report.get("current_max_notional"),
                        "recommended_next_max_notional": report.get("recommended_next_max_notional"),
                        "blocker_count": len(report.get("blockers") or []),
                        "warning_count": len(report.get("warnings") or []),
                        "note_id": report.get("note_id") or report.get("related_note_id"),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append({**item_base, "status": "error", "detail": str(exc)})
    return summary


def _run_trade_automation_limited_live_ladder_scheduled_review(
    db: Session,
    *,
    limit: int | None,
    now: datetime | None,
    enabled_key: str,
    auto_key: str,
    runtime_prefix: str,
    disabled_reason: str,
    auto_disabled_reason: str,
    service_func: Any,
    history_type: str,
) -> dict[str, Any]:
    batch_limit = max(1, int(limit or 10))
    processed = 0
    current_time = now or _utc_now()
    session_day, review_window_open = automation_limited_live_safety_ladder_service.limited_live_ladder_session_day_for(
        current_time,
        forced=False,
    )
    next_eligible = automation_limited_live_safety_ladder_service.next_eligible_limited_live_ladder_review_at(
        current_time
    )
    summary = {
        "processed": 0,
        "reviewed": 0,
        "skipped": 0,
        "errors": 0,
        "session_day": session_day,
        "review_window_open": review_window_open,
        "next_eligible_run_at": _serialize_datetime(next_eligible),
        "items": [],
    }
    db.expire_all()
    tenants = list(
        db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .execution_options(populate_existing=True)
        ).scalars()
    )
    for tenant in tenants:
        if processed >= batch_limit:
            break
        if str(tenant.status or "active").strip().lower() != "active":
            continue
        for profile_key, linked_account in _list_trade_automation_profile_contexts(db, tenant=tenant):
            if processed >= batch_limit:
                break
            if profile_key != _AUTOMATION_PERSONAL_PAPER_PROFILE:
                continue
            processed += 1
            summary["processed"] += 1
            state = _read_trade_automation_state(tenant, profile_key=profile_key, linked_account=linked_account)
            settings_state = dict(state.get("settings") or {})
            runtime_state = dict(state.get("runtime") or {})
            item_base = {
                "tenant_id": tenant.id,
                "tenant_slug": tenant.slug,
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
            }
            if not bool(settings_state.get(enabled_key, True)):
                summary["skipped"] += 1
                summary["items"].append({**item_base, "status": "skipped", "reason": disabled_reason})
                continue
            if auto_key and not bool(settings_state.get(auto_key, True)):
                summary["skipped"] += 1
                summary["items"].append({**item_base, "status": "skipped", "reason": auto_disabled_reason})
                continue
            if not review_window_open:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "review_window_not_open",
                        "next_eligible_run_at": _serialize_datetime(next_eligible),
                    }
                )
                continue
            if str(runtime_state.get(f"{runtime_prefix}_last_scheduled_session_day") or "").strip() == session_day:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": "skipped",
                        "reason": "already_reviewed_for_session",
                        "last_scheduled_run_at": runtime_state.get(f"{runtime_prefix}_last_scheduled_run_at"),
                    }
                )
                continue
            try:
                live_state = _read_trade_automation_state(
                    tenant,
                    profile_key=_AUTOMATION_PERSONAL_LIVE_PROFILE,
                )
                report = service_func(
                    db,
                    tenant=tenant,
                    paper_state=state,
                    live_state=live_state,
                    profile_key=profile_key,
                    linked_account=linked_account,
                    actor=None,
                    now=current_time,
                    run_source="scheduled",
                )
                _append_history(
                    state,
                    {
                        "type": history_type,
                        "at": report.get("evaluated_at") or report.get("checked_at"),
                        "status": report.get("status"),
                        "session_day": report.get("session_day"),
                        "clean_session_count": report.get("clean_session_count"),
                        "required_clean_sessions": report.get("required_clean_sessions"),
                        "blocker_count": len(report.get("blockers") or []),
                        "warning_count": len(report.get("warnings") or []),
                    },
                )
                _write_trade_automation_state(tenant, state, profile_key=profile_key)
                db.commit()
                summary["reviewed"] += 1
                summary["items"].append(
                    {
                        **item_base,
                        "status": report.get("status"),
                        "blocker_count": len(report.get("blockers") or []),
                        "warning_count": len(report.get("warnings") or []),
                        "note_id": report.get("note_id") or report.get("related_note_id"),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive worker guard
                db.rollback()
                summary["errors"] += 1
                summary["items"].append({**item_base, "status": "error", "detail": str(exc)})
    return summary


def run_trade_automation_limited_live_next_tier_cap_canary_reviews(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    return _run_trade_automation_limited_live_ladder_scheduled_review(
        db,
        limit=limit,
        now=now,
        enabled_key="limited_live_next_tier_cap_canary_enabled",
        auto_key="limited_live_next_tier_cap_canary_auto_review_enabled",
        runtime_prefix="limited_live_next_tier_cap_canary",
        disabled_reason="limited_live_next_tier_cap_canary_disabled",
        auto_disabled_reason="limited_live_next_tier_cap_canary_auto_review_disabled",
        service_func=automation_limited_live_safety_ladder_service.run_limited_live_next_tier_cap_canary_review,
        history_type="limited_live_next_tier_cap_canary_scheduled_review",
    )


def run_trade_automation_limited_live_higher_cap_reports(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    return _run_trade_automation_limited_live_ladder_scheduled_review(
        db,
        limit=limit,
        now=now,
        enabled_key="limited_live_higher_cap_report_enabled",
        auto_key="limited_live_higher_cap_report_auto_review_enabled",
        runtime_prefix="limited_live_higher_cap_report",
        disabled_reason="limited_live_higher_cap_report_disabled",
        auto_disabled_reason="limited_live_higher_cap_report_auto_review_disabled",
        service_func=automation_limited_live_safety_ladder_service.run_limited_live_higher_cap_report,
        history_type="limited_live_higher_cap_scheduled_report",
    )


def run_trade_automation_limited_live_daily_closeouts(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    return _run_trade_automation_limited_live_ladder_scheduled_review(
        db,
        limit=limit,
        now=now,
        enabled_key="limited_live_next_tier_cap_canary_enabled",
        auto_key="limited_live_next_tier_cap_canary_auto_review_enabled",
        runtime_prefix="limited_live_session_closeout",
        disabled_reason="limited_live_closeout_disabled",
        auto_disabled_reason="limited_live_closeout_auto_review_disabled",
        service_func=automation_limited_live_safety_ladder_service.run_limited_live_session_closeout,
        history_type="limited_live_session_closeout_scheduled_review",
    )


def run_trade_automation_limited_live_broker_reconciliations(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    return _run_trade_automation_limited_live_ladder_scheduled_review(
        db,
        limit=limit,
        now=now,
        enabled_key="limited_live_next_tier_cap_canary_enabled",
        auto_key="limited_live_next_tier_cap_canary_auto_review_enabled",
        runtime_prefix="limited_live_broker_reconciliation",
        disabled_reason="limited_live_reconciliation_disabled",
        auto_disabled_reason="limited_live_reconciliation_auto_review_disabled",
        service_func=automation_limited_live_safety_ladder_service.run_limited_live_broker_reconciliation,
        history_type="limited_live_broker_reconciliation_scheduled_review",
    )
