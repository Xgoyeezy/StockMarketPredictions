from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, Tenant
from backend.services import notes_service, risk_control_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

MARKET_TIMEZONE = ZoneInfo("America/New_York")
LOSS_CONTAINMENT_NOTE_OWNER = "automation-ai"
LOSS_CONTAINMENT_HISTORY_LIMIT = 12
LOSS_CONTAINMENT_NOTE_LIMIT = 250
LOSS_CONTAINMENT_PERSONAL_PAPER_PROFILE = "personal_paper"
LOSS_CONTAINMENT_PERSONAL_LIVE_PROFILE = "personal_live"

LOSS_CONTAINMENT_SETTINGS_DEFAULTS: dict[str, Any] = {
    "loss_containment_enabled": True,
    "loss_containment_apply_to_live": False,
    "loss_containment_auto_close_paper": True,
    "loss_containment_auto_close_live": False,
    "loss_containment_max_open_heat_pct": 0.35,
    "loss_containment_max_position_loss_r": 0.50,
    "loss_containment_max_position_mae_pct": 0.35,
    "loss_containment_profit_protect_trigger_r": 0.75,
    "loss_containment_profit_protect_floor_r": 0.15,
    "loss_containment_time_stop_minutes": 45,
    "loss_containment_stale_quote_seconds": 120,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on"}:
            return True
        if cleaned in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if pd.isna(parsed):
        return float(default)
    return float(parsed)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _clamp_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    return max(float(minimum), min(float(maximum), _coerce_float(value, default)))


def _clamp_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    return max(int(minimum), min(int(maximum), _coerce_int(value, default)))


def _session_day_for(now: datetime | None = None) -> str:
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(MARKET_TIMEZONE).date().isoformat()


def _session_bounds_utc(session_day: str) -> tuple[datetime, datetime]:
    day = datetime.strptime(session_day, "%Y-%m-%d").date()
    start_local = datetime.combine(day, time.min, tzinfo=MARKET_TIMEZONE)
    end_local = datetime.combine(day, time.max, tzinfo=MARKET_TIMEZONE)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _profile_tag(profile_key: str) -> str:
    cleaned = str(profile_key or "").strip().lower().replace(":", "-") or LOSS_CONTAINMENT_PERSONAL_PAPER_PROFILE
    return f"profile-{cleaned}"


def _normalize_profile_key(profile_key: str | None) -> str:
    return str(profile_key or LOSS_CONTAINMENT_PERSONAL_PAPER_PROFILE).strip().lower()


def normalize_loss_containment_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    defaults = LOSS_CONTAINMENT_SETTINGS_DEFAULTS
    return {
        "loss_containment_enabled": _coerce_bool(
            state.get("loss_containment_enabled"),
            bool(defaults["loss_containment_enabled"]),
        ),
        "loss_containment_apply_to_live": _coerce_bool(
            state.get("loss_containment_apply_to_live"),
            bool(defaults["loss_containment_apply_to_live"]),
        ),
        "loss_containment_auto_close_paper": _coerce_bool(
            state.get("loss_containment_auto_close_paper"),
            bool(defaults["loss_containment_auto_close_paper"]),
        ),
        "loss_containment_auto_close_live": _coerce_bool(
            state.get("loss_containment_auto_close_live"),
            bool(defaults["loss_containment_auto_close_live"]),
        ),
        "loss_containment_max_open_heat_pct": _clamp_float(
            state.get("loss_containment_max_open_heat_pct"),
            float(defaults["loss_containment_max_open_heat_pct"]),
            minimum=0.05,
            maximum=10.0,
        ),
        "loss_containment_max_position_loss_r": _clamp_float(
            state.get("loss_containment_max_position_loss_r"),
            float(defaults["loss_containment_max_position_loss_r"]),
            minimum=0.05,
            maximum=10.0,
        ),
        "loss_containment_max_position_mae_pct": _clamp_float(
            state.get("loss_containment_max_position_mae_pct"),
            float(defaults["loss_containment_max_position_mae_pct"]),
            minimum=0.05,
            maximum=25.0,
        ),
        "loss_containment_profit_protect_trigger_r": _clamp_float(
            state.get("loss_containment_profit_protect_trigger_r"),
            float(defaults["loss_containment_profit_protect_trigger_r"]),
            minimum=0.05,
            maximum=25.0,
        ),
        "loss_containment_profit_protect_floor_r": _clamp_float(
            state.get("loss_containment_profit_protect_floor_r"),
            float(defaults["loss_containment_profit_protect_floor_r"]),
            minimum=-10.0,
            maximum=25.0,
        ),
        "loss_containment_time_stop_minutes": _clamp_int(
            state.get("loss_containment_time_stop_minutes"),
            int(defaults["loss_containment_time_stop_minutes"]),
            minimum=1,
            maximum=480,
        ),
        "loss_containment_stale_quote_seconds": _clamp_int(
            state.get("loss_containment_stale_quote_seconds"),
            int(defaults["loss_containment_stale_quote_seconds"]),
            minimum=15,
            maximum=3600,
        ),
    }


def normalize_loss_containment_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    history = [
        serialize_value(item)
        for item in list(runtime.get("loss_containment_history") or [])[:LOSS_CONTAINMENT_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "loss_containment_last_report": serialize_value(runtime.get("loss_containment_last_report") or {}),
        "loss_containment_last_note_id": str(runtime.get("loss_containment_last_note_id") or "").strip() or None,
        "loss_containment_note_session_day": str(runtime.get("loss_containment_note_session_day") or "").strip()
        or None,
        "loss_containment_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("loss_containment_last_run_at"))
        ),
        "loss_containment_last_error": str(runtime.get("loss_containment_last_error") or "").strip() or None,
        "loss_containment_history": history,
    }


def build_loss_containment_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    settings = normalize_loss_containment_settings(state.get("settings"))
    runtime = normalize_loss_containment_runtime(state.get("runtime"))
    report = dict(runtime.get("loss_containment_last_report") or {})
    if not report:
        return {
            "status": "not_run" if settings["loss_containment_enabled"] else "disabled",
            "label": "Not run" if settings["loss_containment_enabled"] else "Disabled",
            "enabled": settings["loss_containment_enabled"],
            "apply_to_live": settings["loss_containment_apply_to_live"],
            "entries_blocked": False,
            "open_heat_pct": 0.0,
            "defensive_actions": [],
            "related_note_id": runtime.get("loss_containment_last_note_id"),
            "history": runtime.get("loss_containment_history") or [],
        }
    report.setdefault("enabled", settings["loss_containment_enabled"])
    report.setdefault("apply_to_live", settings["loss_containment_apply_to_live"])
    report.setdefault("related_note_id", runtime.get("loss_containment_last_note_id"))
    report["history"] = runtime.get("loss_containment_history") or []
    return serialize_value(report)


def _owned_rows(frame: pd.DataFrame | None, *, tenant_id: str | None, profile_key: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame
    if "automation_origin" in result.columns:
        marker = result["automation_origin"].astype(str).str.strip().str.lower()
        result = result[marker.eq("trade_automation")]
    if tenant_id and "automation_tenant_id" in result.columns:
        scope = result["automation_tenant_id"].astype(str).str.strip()
        result = result[scope.eq(str(tenant_id).strip())]
    if profile_key and "automation_profile_key" in result.columns:
        profile = result["automation_profile_key"].astype(str).str.strip().str.lower()
        result = result[profile.eq(profile_key)]
    return result.copy()


def _monitor_lookup(monitored_frame: pd.DataFrame | None) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    if monitored_frame is None or monitored_frame.empty:
        return lookup
    for row in monitored_frame.to_dict(orient="records"):
        for key in _position_keys(row):
            lookup[key] = row
    return lookup


def _position_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for field in ("trade_id", "order_id", "broker_order_id"):
        value = str(row.get(field) or "").strip()
        if value:
            keys.append(f"{field}:{value}")
    ticker = str(row.get("ticker") or "").strip().upper()
    instrument = str(row.get("instrument_type") or row.get("automation_instrument_type") or "").strip().lower()
    if ticker:
        keys.append(f"ticker:{ticker}:{instrument}")
    return keys


def _match_monitor_row(row: dict[str, Any], lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for key in _position_keys(row):
        if key in lookup:
            return lookup[key]
    return {}


def _row_float(row: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            value = _coerce_float(row.get(key), float("nan"))
            if not pd.isna(value):
                return float(value)
    return float(default)


def _first_datetime(row: dict[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        parsed = _parse_datetime(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _position_quantity(row: dict[str, Any]) -> float:
    return _row_float(row, "suggested_contracts", "filled_contracts", "broker_qty", "qty", "quantity")


def _position_multiplier(row: dict[str, Any]) -> float:
    instrument = str(row.get("instrument_type") or row.get("automation_instrument_type") or "equity").strip().lower()
    return 100.0 if instrument == "listed_option" else 1.0


def _position_unrealized_pnl(open_row: dict[str, Any], monitor_row: dict[str, Any]) -> float:
    for key in ("unrealized_pnl", "current_unrealized_pnl", "open_pnl", "floating_pnl"):
        value = _row_float(monitor_row, key, default=float("nan"))
        if not pd.isna(value):
            return float(value)
    quantity = _position_quantity(open_row)
    entry_price = _row_float(
        open_row,
        "actual_fill_price",
        "broker_filled_avg_price",
        "entry_price",
        "live_price_at_open",
        "contract_mid_at_open",
    )
    current_price = _row_float(
        monitor_row,
        "current_contract_mid",
        "contract_mid",
        "current_underlying",
        "current_underlying_price",
        default=0.0,
    )
    if current_price <= 0:
        current_price = _row_float(open_row, "current_underlying_price", "live_price_at_open", default=0.0)
    if quantity <= 0 or entry_price <= 0 or current_price <= 0:
        return 0.0
    return float((current_price - entry_price) * quantity * _position_multiplier(open_row))


def _quote_age_seconds(row: dict[str, Any], now: datetime) -> float | None:
    direct = _row_float(
        row,
        "quote_age_seconds",
        "option_quote_age_seconds",
        "market_data_age_seconds",
        "last_quote_age_seconds",
        default=float("nan"),
    )
    if not pd.isna(direct):
        return max(0.0, float(direct))
    parsed = _first_datetime(row, "quote_at", "last_quote_at", "updated_at", "last_update_at", "as_of")
    if parsed is None:
        return None
    return max(0.0, float((now - parsed).total_seconds()))


def _position_age_minutes(row: dict[str, Any], now: datetime) -> float | None:
    opened = _first_datetime(row, "opened_at", "entry_at", "created_at", "submitted_at", "filled_at")
    if opened is None:
        return None
    return max(0.0, float((now - opened).total_seconds() / 60.0))


def _position_cost(row: dict[str, Any]) -> float:
    return max(risk_control_service.estimate_trade_notional(row), 1.0)


def _mae_pct(open_row: dict[str, Any], monitor_row: dict[str, Any], pnl: float) -> float:
    direct = abs(
        _row_float(
            {**open_row, **monitor_row},
            "max_adverse_excursion_pct",
            "mae_pct",
            "max_adverse_pct",
            default=float("nan"),
        )
    )
    if not pd.isna(direct):
        return float(direct)
    cost = _position_cost(open_row)
    return max(0.0, (-pnl / cost) * 100.0) if pnl < 0 and cost > 0 else 0.0


def _mfe_r(open_row: dict[str, Any], monitor_row: dict[str, Any], risk_unit: float, pnl: float) -> float:
    direct = _row_float(
        {**open_row, **monitor_row},
        "max_favorable_excursion_r",
        "mfe_r",
        default=float("nan"),
    )
    if not pd.isna(direct):
        return float(direct)
    max_favorable = _row_float(
        {**open_row, **monitor_row},
        "max_favorable_excursion",
        "mfe",
        default=float("nan"),
    )
    if not pd.isna(max_favorable) and risk_unit > 0:
        return float(max_favorable / risk_unit)
    return max(float(pnl / risk_unit), 0.0) if risk_unit > 0 else 0.0


def _build_position_evaluations(
    *,
    open_frame: pd.DataFrame,
    monitored_frame: pd.DataFrame | None,
    settings: dict[str, Any],
    equity_base: float,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    monitor_lookup = _monitor_lookup(monitored_frame)
    evaluations: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    defensive_actions: list[dict[str, Any]] = []
    risk_percent = _coerce_float(settings.get("risk_percent"), 0.50)
    risk_unit = max(equity_base * (risk_percent / 100.0), 1.0)

    for open_row in open_frame.to_dict(orient="records") if not open_frame.empty else []:
        monitor_row = _match_monitor_row(open_row, monitor_lookup)
        ticker = str(open_row.get("ticker") or monitor_row.get("ticker") or "").strip().upper()
        trade_id = str(open_row.get("trade_id") or monitor_row.get("trade_id") or "").strip() or None
        order_id = str(open_row.get("order_id") or monitor_row.get("order_id") or "").strip() or None
        pnl = _position_unrealized_pnl(open_row, monitor_row)
        current_r = pnl / risk_unit if risk_unit > 0 else 0.0
        loss_r = max(-current_r, 0.0)
        mae_pct = _mae_pct(open_row, monitor_row, pnl)
        mfe_r = _mfe_r(open_row, monitor_row, risk_unit, pnl)
        quote_age = _quote_age_seconds({**open_row, **monitor_row}, now)
        age_minutes = _position_age_minutes(open_row, now)
        action: str | None = None
        reason: str | None = None

        if loss_r >= float(settings["loss_containment_max_position_loss_r"]):
            action = "EXIT FULLY NOW"
            reason = "position_loss_r_breach"
            blockers.append(
                {
                    "key": reason,
                    "ticker": ticker,
                    "detail": f"{ticker or 'Position'} is down {loss_r:.2f}R, beyond the configured loss limit.",
                }
            )
        elif mae_pct >= float(settings["loss_containment_max_position_mae_pct"]):
            action = "EXIT FULLY NOW"
            reason = "position_mae_breach"
            blockers.append(
                {
                    "key": reason,
                    "ticker": ticker,
                    "detail": f"{ticker or 'Position'} adverse excursion is {mae_pct:.2f}%, beyond the configured limit.",
                }
            )
        elif (
            mfe_r >= float(settings["loss_containment_profit_protect_trigger_r"])
            and current_r <= float(settings["loss_containment_profit_protect_floor_r"])
        ):
            action = "EXIT FULLY NOW"
            reason = "profit_protect_giveback"
            blockers.append(
                {
                    "key": reason,
                    "ticker": ticker,
                    "detail": f"{ticker or 'Position'} reached {mfe_r:.2f}R and gave back to {current_r:.2f}R.",
                }
            )
        elif age_minutes is not None and age_minutes >= float(settings["loss_containment_time_stop_minutes"]) and current_r <= 0:
            action = "TIME STOP"
            reason = "time_stop_negative"
            warnings.append(
                {
                    "key": reason,
                    "ticker": ticker,
                    "detail": f"{ticker or 'Position'} is still non-positive after {age_minutes:.0f} minutes.",
                }
            )

        stale_quote_blocks_auto_close = False
        if quote_age is None:
            warnings.append(
                {
                    "key": "missing_quote_freshness",
                    "ticker": ticker,
                    "detail": f"{ticker or 'Position'} has no quote freshness evidence.",
                }
            )
        elif quote_age >= float(settings["loss_containment_stale_quote_seconds"]):
            stale_quote_blocks_auto_close = True
            blockers.append(
                {
                    "key": "stale_quote",
                    "ticker": ticker,
                    "detail": f"{ticker or 'Position'} quote age is {quote_age:.0f} seconds.",
                }
            )

        evaluation = {
            "ticker": ticker,
            "trade_id": trade_id,
            "order_id": order_id,
            "unrealized_pnl": round(float(pnl), 4),
            "current_r": round(float(current_r), 4),
            "loss_r": round(float(loss_r), 4),
            "mae_pct": round(float(mae_pct), 4),
            "mfe_r": round(float(mfe_r), 4),
            "quote_age_seconds": round(float(quote_age), 4) if quote_age is not None else None,
            "age_minutes": round(float(age_minutes), 4) if age_minutes is not None else None,
            "recommended_action": action,
            "reason": reason,
        }
        evaluations.append(evaluation)
        if action:
            defensive_actions.append(
                {
                    "trade_id": trade_id,
                    "order_id": order_id,
                    "ticker": ticker,
                    "action": action,
                    "reason": reason,
                    "current_r": evaluation["current_r"],
                    "unrealized_pnl": evaluation["unrealized_pnl"],
                    "auto_close_eligible": not stale_quote_blocks_auto_close,
                    "stale_quote_blocks_auto_close": stale_quote_blocks_auto_close,
                    "requires_fresh_quote": stale_quote_blocks_auto_close,
                }
            )
    return evaluations, blockers, warnings, defensive_actions


def build_loss_containment_report(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    owned_open: pd.DataFrame | None,
    owned_pending: pd.DataFrame | None,
    monitored_open: pd.DataFrame | None = None,
    effective_funds: float | None = None,
    now: datetime | None = None,
    run_source: str = "cycle",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_profile_key = _normalize_profile_key(profile_key)
    settings_state = dict(state.get("settings") or {})
    settings = normalize_loss_containment_settings(settings_state)
    session_day = _session_day_for(now)
    equity_base = max(
        _coerce_float(
            effective_funds,
            _coerce_float(state.get("__actual_funds"), _coerce_float(state.get("__effective_funds"), _coerce_float(settings_state.get("account_size"), 100000.0))),
        ),
        1.0,
    )
    open_frame = owned_open if owned_open is not None else pd.DataFrame()
    pending_frame = owned_pending if owned_pending is not None else pd.DataFrame()
    unrealized_pnl = risk_control_service.estimate_unrealized_pnl(open_frame, monitored_open)
    open_heat_dollars = max(-float(unrealized_pnl), 0.0)
    open_heat_pct = (open_heat_dollars / equity_base * 100.0) if equity_base > 0 else 0.0
    position_evaluations, blockers, warnings, defensive_actions = _build_position_evaluations(
        open_frame=open_frame,
        monitored_frame=monitored_open,
        settings={**settings_state, **settings},
        equity_base=equity_base,
        now=now,
    )
    if open_heat_pct >= float(settings["loss_containment_max_open_heat_pct"]):
        blockers.append(
            {
                "key": "open_heat_breach",
                "detail": (
                    f"Open heat is {open_heat_pct:.2f}% of equity, above the "
                    f"{settings['loss_containment_max_open_heat_pct']:.2f}% limit."
                ),
            }
        )

    allowed_scope = normalized_profile_key == LOSS_CONTAINMENT_PERSONAL_PAPER_PROFILE or bool(
        settings["loss_containment_apply_to_live"]
    )
    live_scope_blocked = (
        normalized_profile_key == LOSS_CONTAINMENT_PERSONAL_LIVE_PROFILE
        and not bool(settings["loss_containment_apply_to_live"])
    )
    if live_scope_blocked and (blockers or defensive_actions):
        warnings.append(
            {
                "key": "live_scope_advisory_only",
                "detail": "Loss containment is paper-first; live profile actions are advisory unless live scope is enabled.",
            }
        )

    entries_blocked = bool(settings["loss_containment_enabled"] and allowed_scope and blockers)
    auto_close_allowed = bool(
        settings["loss_containment_enabled"]
        and (
            normalized_profile_key == LOSS_CONTAINMENT_PERSONAL_PAPER_PROFILE
            and settings["loss_containment_auto_close_paper"]
            or normalized_profile_key == LOSS_CONTAINMENT_PERSONAL_LIVE_PROFILE
            and settings["loss_containment_apply_to_live"]
            and settings["loss_containment_auto_close_live"]
        )
    )
    for item in defensive_actions:
        item["auto_close_eligible"] = bool(
            auto_close_allowed
            and allowed_scope
            and not bool(item.get("stale_quote_blocks_auto_close"))
        )
    auto_close_required = any(bool(item.get("auto_close_eligible")) for item in defensive_actions)

    if not settings["loss_containment_enabled"]:
        status = "disabled"
        label = "Loss containment disabled"
    elif not allowed_scope:
        status = "not_applicable"
        label = "Paper scope only"
    elif auto_close_required:
        status = "action_required"
        label = "Defensive exit required"
    elif blockers:
        status = "blocked"
        label = "Loss containment blocking entries"
    elif warnings:
        status = "watch"
        label = "Loss containment watch"
    else:
        status = "clean"
        label = "Loss containment clean"

    worst_position = None
    if position_evaluations:
        worst_position = sorted(
            position_evaluations,
            key=lambda item: (float(item.get("loss_r") or 0.0), float(item.get("mae_pct") or 0.0)),
            reverse=True,
        )[0]

    effective_overlays = [
        {
            "field": "new_entries",
            "before": "allowed",
            "effective": "blocked" if entries_blocked else "allowed",
            "reason": "Open heat, stale quotes, or position loss breached loss-containment limits."
            if entries_blocked
            else "Loss-containment limits are clean.",
        },
        {
            "field": "position_management",
            "before": "monitored stop/target actions",
            "effective": "defensive paper exits" if auto_close_required else "monitor only",
            "reason": "Paper defensive exits reuse existing safe close mechanics.",
        },
    ]

    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": normalized_profile_key,
            "session_day": session_day,
            "evaluated_at": _serialize_datetime(now),
            "run_source": str(run_source or "cycle").strip().lower() or "cycle",
            "equity_base": round(float(equity_base), 4),
            "unrealized_pnl": round(float(unrealized_pnl), 4),
            "open_heat_dollars": round(float(open_heat_dollars), 4),
            "open_heat_pct": round(float(open_heat_pct), 4),
            "max_open_heat_pct": float(settings["loss_containment_max_open_heat_pct"]),
            "open_position_count": int(len(open_frame)),
            "pending_order_count": int(len(pending_frame)),
            "entries_blocked": bool(entries_blocked),
            "auto_close_allowed": bool(auto_close_allowed),
            "apply_to_live": bool(settings["loss_containment_apply_to_live"]),
            "worst_position": worst_position,
            "position_evaluations": position_evaluations[:20],
            "defensive_actions": defensive_actions[:20],
            "blockers": blockers[:20],
            "warnings": warnings[:20],
            "effective_overlays": effective_overlays,
            "skipped_changes": [
                {
                    "field": "baseline_settings",
                    "reason": "Loss containment is runtime-only; no baseline settings were auto-tuned.",
                }
            ],
        }
    )


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LOSS_CONTAINMENT_NOTE_OWNER,
            limit=LOSS_CONTAINMENT_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "loss-containment",
        "defensive-exit",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags") or []}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _format_note_rows(items: list[dict[str, Any]], *, empty: str) -> list[str]:
    if not items:
        return [f"- {empty}"]
    rows = []
    for item in items[:10]:
        key = str(item.get("field") or item.get("key") or item.get("ticker") or item.get("action") or "item").replace("_", " ")
        detail = str(item.get("reason") or item.get("detail") or item.get("status") or "").strip()
        rows.append(f"- {key}: {detail}" if detail else f"- {key}")
    return rows


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    worst = dict(report.get("worst_position") or {})
    lines = [
        f"Loss containment review for {getattr(tenant, 'name', None) or getattr(tenant, 'slug', '') or 'tenant'}",
        "",
        f"- Profile: {profile_key}",
        f"- Session: {report.get('session_day')}",
        f"- Status: {report.get('status')}",
        f"- Open heat: {float(report.get('open_heat_pct') or 0.0):.2f}% (${float(report.get('open_heat_dollars') or 0.0):,.2f})",
        f"- Unrealized PnL: ${float(report.get('unrealized_pnl') or 0.0):,.2f}",
        f"- Open positions: {int(report.get('open_position_count') or 0)}",
        f"- Pending orders: {int(report.get('pending_order_count') or 0)}",
        f"- New entries blocked: {'yes' if report.get('entries_blocked') else 'no'}",
        f"- Auto close allowed: {'yes' if report.get('auto_close_allowed') else 'no'}",
        f"- Worst position: {worst.get('ticker') or '--'} {float(worst.get('current_r') or 0.0):.2f}R / MAE {float(worst.get('mae_pct') or 0.0):.2f}%",
        "",
        "Defensive actions",
    ]
    lines.extend(_format_note_rows(list(report.get("defensive_actions") or []), empty="No defensive actions."))
    lines.extend(["", "Effective overlays"])
    lines.extend(_format_note_rows(list(report.get("effective_overlays") or []), empty="No effective overlay changes."))
    lines.extend(["", "Skipped changes"])
    lines.extend(_format_note_rows(list(report.get("skipped_changes") or []), empty="No skipped changes."))
    if report.get("blockers"):
        lines.extend(["", "Blockers"])
        lines.extend(_format_note_rows(list(report.get("blockers") or []), empty="No blockers."))
    if report.get("warnings"):
        lines.extend(["", "Warnings"])
        lines.extend(_format_note_rows(list(report.get("warnings") or []), empty="No warnings."))
    return "\n".join(lines).strip()


def _sync_loss_containment_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "loss-containment",
        "defensive-exit",
        _profile_tag(profile_key),
        f"session-{session_day}",
    ]
    title = f"Loss containment review - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = (
        str(report.get("related_note_id") or report.get("note_id") or "").strip()
        or _find_existing_note_id(profile_key, session_day)
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": LOSS_CONTAINMENT_NOTE_OWNER,
        "note_type": "risk_review",
        "priority": "high" if report.get("entries_blocked") or report.get("defensive_actions") else "medium",
    }
    if note_id:
        try:
            updated = notes_service.update_note(note_id, payload)
            return str(updated.get("id") or note_id)
        except Exception:
            note_id = None
    try:
        created = notes_service.create_note(**payload)
        return str(created.get("id") or "").strip() or None
    except Exception:
        return None


def _persist_report(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    report: dict[str, Any],
    actor: Any = None,
    write_note: bool = False,
) -> dict[str, Any]:
    normalized_profile_key = _normalize_profile_key(profile_key)
    if write_note:
        note_id = _sync_loss_containment_note(tenant=tenant, profile_key=normalized_profile_key, report=report)
        if note_id:
            report["note_id"] = note_id
            report["related_note_id"] = note_id
    runtime = state.setdefault("runtime", {})
    summary_keys = {
        "status",
        "label",
        "profile_key",
        "session_day",
        "evaluated_at",
        "run_source",
        "equity_base",
        "unrealized_pnl",
        "open_heat_dollars",
        "open_heat_pct",
        "max_open_heat_pct",
        "open_position_count",
        "pending_order_count",
        "entries_blocked",
        "auto_close_allowed",
        "apply_to_live",
        "worst_position",
        "position_evaluations",
        "defensive_actions",
        "blockers",
        "warnings",
        "effective_overlays",
        "skipped_changes",
        "note_id",
        "related_note_id",
    }
    runtime["loss_containment_last_report"] = serialize_value(
        {key: report.get(key) for key in summary_keys if key in report}
    )
    runtime["loss_containment_last_run_at"] = report.get("evaluated_at")
    runtime["loss_containment_last_note_id"] = report.get("related_note_id") or report.get("note_id")
    runtime["loss_containment_note_session_day"] = report.get("session_day")
    runtime["loss_containment_last_error"] = None
    history = list(runtime.get("loss_containment_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "open_heat_pct": report.get("open_heat_pct"),
            "entries_blocked": report.get("entries_blocked"),
            "defensive_action_count": len(report.get("defensive_actions") or []),
            "note_id": report.get("related_note_id") or report.get("note_id"),
            "run_source": report.get("run_source"),
        },
    )
    runtime["loss_containment_history"] = serialize_value(history[:LOSS_CONTAINMENT_HISTORY_LIMIT])
    if db is not None and write_note:
        record_audit_event(
            db,
            event_type="trade_automation.loss_containment_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": normalized_profile_key,
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "open_heat_pct": report.get("open_heat_pct"),
                "entries_blocked": report.get("entries_blocked"),
                "defensive_action_count": len(report.get("defensive_actions") or []),
                "note_id": report.get("related_note_id") or report.get("note_id"),
                "run_source": report.get("run_source"),
            },
        )
    return serialize_value(report)


def evaluate_loss_containment_entry_gate(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    owned_open: pd.DataFrame | None = None,
    owned_pending: pd.DataFrame | None = None,
    monitored_open: pd.DataFrame | None = None,
    effective_funds: float | None = None,
    now: datetime | None = None,
    actor: Any = None,
) -> dict[str, Any]:
    normalized_profile_key = _normalize_profile_key(profile_key)
    report = build_loss_containment_report(
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        owned_open=owned_open,
        owned_pending=owned_pending,
        monitored_open=monitored_open,
        effective_funds=effective_funds,
        now=now,
        run_source="cycle",
    )
    should_write_note = bool(report.get("entries_blocked") or report.get("defensive_actions"))
    if normalized_profile_key != LOSS_CONTAINMENT_PERSONAL_PAPER_PROFILE and not bool(report.get("apply_to_live")):
        should_write_note = False
    _ = linked_account
    return _persist_report(
        db,
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        report=report,
        actor=actor,
        write_note=should_write_note,
    )


def run_loss_containment_review(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_profile_key = _normalize_profile_key(profile_key)
    tenant_id = str(getattr(tenant, "id", "") or "").strip()
    open_frame = _owned_rows(sdm.read_open_trades(), tenant_id=tenant_id, profile_key=normalized_profile_key)
    pending_frame = _owned_rows(sdm.read_pending_orders(), tenant_id=tenant_id, profile_key=normalized_profile_key)
    monitored_frame = _owned_rows(sdm.monitor_open_trades(), tenant_id=tenant_id, profile_key=normalized_profile_key)
    equity_base = _coerce_float(
        state.get("__actual_funds"),
        _coerce_float(
            state.get("__effective_funds"),
            _coerce_float((state.get("settings") or {}).get("account_size"), 100000.0),
        ),
    )
    _ = linked_account
    report = build_loss_containment_report(
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        owned_open=open_frame,
        owned_pending=pending_frame,
        monitored_open=monitored_frame,
        effective_funds=equity_base,
        now=now,
        run_source=run_source,
    )
    return _persist_report(
        db,
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        report=report,
        actor=actor,
        write_note=True,
    )


def build_forced_exit_actions(report: dict[str, Any] | None) -> dict[str, str]:
    actions: dict[str, str] = {}
    if not report:
        return actions
    for item in list(report.get("defensive_actions") or []):
        if not bool(item.get("auto_close_eligible")):
            continue
        action = str(item.get("action") or "EXIT FULLY NOW").strip().upper() or "EXIT FULLY NOW"
        for field in ("trade_id", "order_id"):
            value = str(item.get(field) or "").strip()
            if value:
                actions[f"{field}:{value}"] = action
        ticker = str(item.get("ticker") or "").strip().upper()
        if ticker:
            actions[f"ticker:{ticker}"] = action
    return actions
