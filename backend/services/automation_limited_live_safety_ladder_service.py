from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.services import (
    automation_ai_review_service,
    automation_limited_live_cap_expansion_gate_service,
    automation_limited_live_next_tier_cap_gate_service,
    automation_limited_live_rollout_gate_service,
    notes_service,
)
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

LIMITED_LIVE_LADDER_NOTE_OWNER = "automation-ai"
LIMITED_LIVE_LADDER_NOTE_LIMIT = 300
LIMITED_LIVE_LADDER_HISTORY_LIMIT = 16
LIMITED_LIVE_LADDER_PAPER_PROFILE = "personal_paper"
LIMITED_LIVE_LADDER_LIVE_PROFILE = "personal_live"
LIMITED_LIVE_HIGHER_CAP_TARGET = 1000.0
LIMITED_LIVE_BLOCK_SLIPPAGE_BPS = 100.0
LIMITED_LIVE_WARN_SLIPPAGE_BPS = 50.0

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE

LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS: dict[str, Any] = {
    "limited_live_next_tier_cap_canary_enabled": True,
    "limited_live_next_tier_cap_canary_auto_review_enabled": True,
    "limited_live_next_tier_cap_canary_window_sessions": 5,
    "limited_live_next_tier_cap_canary_required_clean_sessions": 3,
    "limited_live_next_tier_cap_canary_stale_after_days": 2,
    "limited_live_higher_cap_report_enabled": True,
    "limited_live_higher_cap_report_auto_review_enabled": True,
    "limited_live_higher_cap_required_clean_sessions": 3,
    "limited_live_higher_cap_stale_after_days": 2,
    "limited_live_higher_cap_target_max_notional": LIMITED_LIVE_HIGHER_CAP_TARGET,
    "limited_live_operator_checklist_required": True,
}

CAP_LADDER_TIERS: list[dict[str, Any]] = [
    {
        "key": "limited_live_rollout",
        "label": "$100 rollout",
        "max_notional": 100.0,
        "max_session_orders": 1,
        "required_clean_sessions": 3,
        "stale_after_days": 2,
        "require_limit": True,
        "allowance_key": "limited_live_rollout_gate_allowance",
        "gate_report_key": "limited_live_rollout_gate_last_report",
        "canary_report_key": "limited_live_rollout_canary_last_report",
        "requires": [],
    },
    {
        "key": "limited_live_cap_expansion",
        "label": "$250 cap expansion",
        "max_notional": 250.0,
        "max_session_orders": 1,
        "required_clean_sessions": 3,
        "stale_after_days": 2,
        "require_limit": True,
        "allowance_key": "limited_live_cap_expansion_gate_allowance",
        "gate_report_key": "limited_live_cap_expansion_gate_last_report",
        "canary_report_key": "limited_live_cap_expansion_canary_last_report",
        "requires": ["limited_live_rollout"],
    },
    {
        "key": "limited_live_next_tier_cap",
        "label": "$500 next-tier cap",
        "max_notional": 500.0,
        "max_session_orders": 1,
        "required_clean_sessions": 3,
        "stale_after_days": 2,
        "require_limit": True,
        "allowance_key": "limited_live_next_tier_cap_gate_allowance",
        "gate_report_key": "limited_live_next_tier_cap_gate_last_report",
        "canary_report_key": "limited_live_next_tier_cap_canary_last_report",
        "requires": ["limited_live_rollout", "limited_live_cap_expansion"],
    },
    {
        "key": "limited_live_higher_cap",
        "label": "$1000 higher-cap recommendation",
        "max_notional": LIMITED_LIVE_HIGHER_CAP_TARGET,
        "max_session_orders": 1,
        "required_clean_sessions": 3,
        "stale_after_days": 2,
        "require_limit": True,
        "allowance_key": None,
        "gate_report_key": None,
        "canary_report_key": None,
        "requires": ["limited_live_rollout", "limited_live_cap_expansion", "limited_live_next_tier_cap"],
        "advisory_only": True,
    },
]


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


def _coerce_int(value: Any, default: int = 0, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        numeric = int(default)
    if minimum is not None:
        numeric = max(int(minimum), numeric)
    if maximum is not None:
        numeric = min(int(maximum), numeric)
    return numeric


def _coerce_float(value: Any, default: float = 0.0, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = float(default)
    if minimum is not None:
        numeric = max(float(minimum), numeric)
    if maximum is not None:
        numeric = min(float(maximum), numeric)
    return numeric


def _issue(key: str, detail: str, *, component: str = "limited_live_ladder", severity: str = "blocker") -> dict[str, Any]:
    return {"key": key, "component": component, "severity": severity, "detail": detail}


def _profile_tag(profile_key: str) -> str:
    return "profile-" + str(profile_key or LIMITED_LIVE_LADDER_PAPER_PROFILE).strip().lower().replace(":", "-")


def _session_day_for(value: datetime | None = None) -> str:
    return (value or _utc_now()).astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def limited_live_ladder_session_day_for(value: datetime | None = None, *, forced: bool = False) -> tuple[str, bool]:
    return automation_ai_review_service.review_session_day_for(value, forced=forced)


def _review_time_for_session_day(session_day: str) -> datetime:
    local_day = date.fromisoformat(session_day)
    local_review = datetime.combine(
        local_day,
        time(16, automation_ai_review_service.AI_POST_CLOSE_BUFFER_MINUTES),
        tzinfo=MARKET_TIMEZONE,
    )
    return local_review.astimezone(timezone.utc)


def _next_trading_day_after(session_day: str) -> str:
    cursor = date.fromisoformat(session_day) + timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    return cursor.isoformat()


def next_eligible_limited_live_ladder_review_at(value: datetime | None = None) -> datetime:
    now = value or _utc_now()
    session_day, review_window_open = limited_live_ladder_session_day_for(now, forced=False)
    if review_window_open:
        return now
    return _review_time_for_session_day(session_day)


def next_eligible_limited_live_ladder_review_after_session(session_day: str) -> datetime:
    return _review_time_for_session_day(_next_trading_day_after(session_day))


def _session_bounds_for_day(session_day: str) -> tuple[datetime, datetime]:
    local_day = date.fromisoformat(session_day)
    local_start = datetime.combine(local_day, time.min, tzinfo=MARKET_TIMEZONE)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _recent_trading_days(now: datetime, *, count: int) -> list[str]:
    cursor = now.astimezone(MARKET_TIMEZONE).date()
    days: list[str] = []
    while len(days) < max(1, count):
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return days


def normalize_limited_live_ladder_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    window = _coerce_int(
        state.get("limited_live_next_tier_cap_canary_window_sessions"),
        LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS["limited_live_next_tier_cap_canary_window_sessions"],
        minimum=1,
        maximum=20,
    )
    return {
        "limited_live_next_tier_cap_canary_enabled": _coerce_bool(
            state.get("limited_live_next_tier_cap_canary_enabled"),
            LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS["limited_live_next_tier_cap_canary_enabled"],
        ),
        "limited_live_next_tier_cap_canary_auto_review_enabled": _coerce_bool(
            state.get("limited_live_next_tier_cap_canary_auto_review_enabled"),
            LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS["limited_live_next_tier_cap_canary_auto_review_enabled"],
        ),
        "limited_live_next_tier_cap_canary_window_sessions": window,
        "limited_live_next_tier_cap_canary_required_clean_sessions": _coerce_int(
            state.get("limited_live_next_tier_cap_canary_required_clean_sessions"),
            LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS["limited_live_next_tier_cap_canary_required_clean_sessions"],
            minimum=1,
            maximum=window,
        ),
        "limited_live_next_tier_cap_canary_stale_after_days": _coerce_int(
            state.get("limited_live_next_tier_cap_canary_stale_after_days"),
            LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS["limited_live_next_tier_cap_canary_stale_after_days"],
            minimum=1,
            maximum=30,
        ),
        "limited_live_higher_cap_report_enabled": _coerce_bool(
            state.get("limited_live_higher_cap_report_enabled"),
            LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS["limited_live_higher_cap_report_enabled"],
        ),
        "limited_live_higher_cap_report_auto_review_enabled": _coerce_bool(
            state.get("limited_live_higher_cap_report_auto_review_enabled"),
            LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS["limited_live_higher_cap_report_auto_review_enabled"],
        ),
        "limited_live_higher_cap_required_clean_sessions": _coerce_int(
            state.get("limited_live_higher_cap_required_clean_sessions"),
            LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS["limited_live_higher_cap_required_clean_sessions"],
            minimum=1,
            maximum=20,
        ),
        "limited_live_higher_cap_stale_after_days": _coerce_int(
            state.get("limited_live_higher_cap_stale_after_days"),
            LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS["limited_live_higher_cap_stale_after_days"],
            minimum=1,
            maximum=30,
        ),
        "limited_live_higher_cap_target_max_notional": _coerce_float(
            state.get("limited_live_higher_cap_target_max_notional"),
            LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS["limited_live_higher_cap_target_max_notional"],
            minimum=1.0,
            maximum=LIMITED_LIVE_HIGHER_CAP_TARGET,
        ),
        "limited_live_operator_checklist_required": _coerce_bool(
            state.get("limited_live_operator_checklist_required"),
            LIMITED_LIVE_LADDER_SETTINGS_DEFAULTS["limited_live_operator_checklist_required"],
        ),
    }


def _history(runtime: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [
        serialize_value(item)
        for item in list(runtime.get(key) or [])[:LIMITED_LIVE_LADDER_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]


def normalize_limited_live_ladder_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    normalized: dict[str, Any] = {}
    for prefix in (
        "limited_live_next_tier_cap_canary",
        "limited_live_broker_reconciliation",
        "limited_live_session_closeout",
        "limited_live_higher_cap_report",
        "limited_live_operator_checklist",
    ):
        report_key = f"{prefix}_last_report"
        last_report = runtime.get(report_key)
        if not isinstance(last_report, dict):
            last_report = {}
        normalized[report_key] = serialize_value(last_report)
        normalized[f"{prefix}_last_note_id"] = str(runtime.get(f"{prefix}_last_note_id") or "").strip() or None
        normalized[f"{prefix}_note_session_day"] = str(runtime.get(f"{prefix}_note_session_day") or "").strip() or None
        normalized[f"{prefix}_last_run_at"] = _serialize_datetime(_parse_datetime(runtime.get(f"{prefix}_last_run_at")))
        normalized[f"{prefix}_history"] = _history(runtime, f"{prefix}_history")
    for prefix in (
        "limited_live_next_tier_cap_canary",
        "limited_live_higher_cap_report",
        "limited_live_session_closeout",
        "limited_live_broker_reconciliation",
    ):
        normalized[f"{prefix}_last_scheduled_run_at"] = _serialize_datetime(
            _parse_datetime(runtime.get(f"{prefix}_last_scheduled_run_at"))
        )
        normalized[f"{prefix}_last_scheduled_session_day"] = (
            str(runtime.get(f"{prefix}_last_scheduled_session_day") or "").strip() or None
        )
        normalized[f"{prefix}_next_eligible_run_at"] = (
            _serialize_datetime(_parse_datetime(runtime.get(f"{prefix}_next_eligible_run_at")))
            or _serialize_datetime(next_eligible_limited_live_ladder_review_at())
        )
        normalized[f"{prefix}_last_skipped_reason"] = str(runtime.get(f"{prefix}_last_skipped_reason") or "").strip() or None
        normalized[f"{prefix}_last_error"] = str(runtime.get(f"{prefix}_last_error") or "").strip() or None
    ledger = [
        serialize_value(item)
        for item in list(runtime.get("limited_live_approval_ledger") or [])[:LIMITED_LIVE_LADDER_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    checklist = runtime.get("limited_live_operator_checklist")
    if not isinstance(checklist, dict):
        checklist = {}
    normalized["limited_live_approval_ledger"] = ledger
    normalized["limited_live_operator_checklist"] = serialize_value(checklist)
    return normalized


def _report_note_id(report: dict[str, Any]) -> str | None:
    return str(report.get("related_note_id") or report.get("note_id") or "").strip() or None


def _report_time(report: dict[str, Any]) -> datetime | None:
    for key in ("evaluated_at", "checked_at", "last_run_at", "at", "created_at", "updated_at"):
        parsed = _parse_datetime(report.get(key))
        if parsed is not None:
            return parsed
    return None


def _is_stale(report: dict[str, Any], now: datetime, stale_after_days: int) -> bool:
    timestamp = _report_time(report)
    if timestamp is None:
        return True
    return (now - timestamp).total_seconds() > max(1, stale_after_days) * 86400


def _allowance_active(allowance: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or _utc_now()
    if str(allowance.get("status") or "").strip().lower() != "active":
        return False
    expires_at = _parse_datetime(allowance.get("expires_at"))
    if expires_at is None or expires_at <= now:
        return False
    session_day = str(allowance.get("session_day") or "").strip()
    return not session_day or session_day == _session_day_for(now)


def _append_runtime_history(runtime: dict[str, Any], key: str, item: dict[str, Any]) -> None:
    history = [serialize_value(item)] + [
        serialize_value(existing)
        for existing in list(runtime.get(key) or [])[: LIMITED_LIVE_LADDER_HISTORY_LIMIT - 1]
        if isinstance(existing, dict)
    ]
    runtime[key] = history


def append_limited_live_approval_ledger_event(
    state: dict[str, Any],
    *,
    event_type: str,
    detail: str,
    actor: Any = None,
    now: datetime | None = None,
    **extra: Any,
) -> dict[str, Any]:
    now = now or _utc_now()
    runtime = state.setdefault("runtime", {})
    event = {
        "event_type": event_type,
        "detail": detail,
        "at": _serialize_datetime(now),
        "session_day": _session_day_for(now),
        "approver": getattr(actor, "email", None) or getattr(actor, "username", None) or None,
        **serialize_value(extra),
    }
    ledger = [event] + [
        serialize_value(item)
        for item in list(runtime.get("limited_live_approval_ledger") or [])[: LIMITED_LIVE_LADDER_HISTORY_LIMIT - 1]
        if isinstance(item, dict)
    ]
    runtime["limited_live_approval_ledger"] = ledger
    return event


def _safe_frame_records(read_fn: Any) -> list[dict[str, Any]]:
    try:
        frame = read_fn()
    except Exception:
        return []
    if frame is None or getattr(frame, "empty", True):
        return []
    records: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        try:
            records.append({str(key): serialize_value(row.get(key)) for key in row.index})
        except Exception:
            continue
    return records


LIMITED_LIVE_MARKERS = (
    "limited_live_rollout_id",
    "limited_live_cap_expansion_id",
    "limited_live_next_tier_cap_id",
)


def _row_marker(row: dict[str, Any]) -> str | None:
    for key in LIMITED_LIVE_MARKERS:
        value = str(row.get(key) or "").strip()
        if value:
            return key
    return None


def _is_live_row(row: dict[str, Any]) -> bool:
    profile = str(row.get("automation_profile_key") or row.get("profile_key") or "").strip().lower()
    intent = str(row.get("automation_execution_intent") or row.get("execution_intent") or "").strip().lower()
    return profile == LIMITED_LIVE_LADDER_LIVE_PROFILE or intent == "broker_live"


def _is_limited_live_row(row: dict[str, Any]) -> bool:
    return _is_live_row(row) and _row_marker(row) is not None


def _is_uncontrolled_live_row(row: dict[str, Any]) -> bool:
    if not _is_live_row(row):
        return False
    known_markers = (
        "live_pilot_soak_id",
        "live_pilot_expansion_id",
        "live_pilot_window_id",
        *LIMITED_LIVE_MARKERS,
    )
    return not any(str(row.get(key) or "").strip() for key in known_markers)


def _row_notional(row: dict[str, Any]) -> float | None:
    for key in ("notional", "estimated_notional", "submitted_notional", "order_notional"):
        if row.get(key) is not None:
            return abs(_coerce_float(row.get(key), 0.0))
    qty = row.get("qty") or row.get("quantity") or row.get("shares")
    price = row.get("limit_price") or row.get("submitted_price") or row.get("entry_price") or row.get("price")
    if qty is not None and price is not None:
        return abs(_coerce_float(qty, 0.0) * _coerce_float(price, 0.0))
    return None


def _row_order_type(row: dict[str, Any]) -> str:
    return str(row.get("order_type") or row.get("type") or "").strip().lower()


def _row_created_at(row: dict[str, Any]) -> datetime | None:
    for key in ("created_at", "submitted_at", "opened_at", "closed_at", "updated_at", "filled_at"):
        parsed = _parse_datetime(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _session_rows(rows: list[dict[str, Any]], session_day: str) -> list[dict[str, Any]]:
    start_at, end_at = _session_bounds_for_day(session_day)
    selected: list[dict[str, Any]] = []
    for row in rows:
        parsed = _row_created_at(row)
        if parsed is None or start_at <= parsed < end_at:
            selected.append(row)
    return selected


def _recent_order_events(db: Session | None, *, tenant: Tenant, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
    if db is None:
        return []
    rows = (
        db.query(OrderEventRecord)
        .filter(OrderEventRecord.tenant_id == tenant.id)
        .filter(OrderEventRecord.created_at >= start_at)
        .filter(OrderEventRecord.created_at < end_at)
        .order_by(OrderEventRecord.created_at.asc())
        .all()
    )
    events: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row.payload_json or {})
        profile = str(payload.get("automation_profile_key") or payload.get("profile_key") or "").strip().lower()
        if profile and profile != LIMITED_LIVE_LADDER_LIVE_PROFILE:
            continue
        if not any(str(payload.get(key) or "").strip() for key in LIMITED_LIVE_MARKERS):
            route_family = str(payload.get("route_family") or payload.get("source") or "").strip().lower()
            reason = str(payload.get("automation_entry_reason") or "").strip().lower()
            if "limited_live" not in route_family and "limited_live" not in reason:
                continue
        events.append(
            serialize_value(
                {
                    "event_key": row.event_key,
                    "status": row.status,
                    "detail": row.detail,
                    "order_type": row.order_type,
                    "created_at": _serialize_datetime(row.created_at),
                    "payload": payload,
                }
            )
        )
    return events


def _event_notional(event: dict[str, Any]) -> float | None:
    payload = dict(event.get("payload") or {})
    request_payload = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    for source in (payload, request_payload):
        value = _row_notional(source)
        if value is not None:
            return value
    return None


def _slippage_bps(item: dict[str, Any]) -> float | None:
    for key in ("slippage_bps", "slippage_bp", "average_slippage_bps"):
        if item.get(key) is not None:
            return _coerce_float(item.get(key), 0.0)
    payload = item.get("payload")
    if isinstance(payload, dict):
        return _slippage_bps(payload)
    return None


def _realized_pnl(item: dict[str, Any]) -> float | None:
    for key in ("realized_pnl", "pnl", "realized_pnl_dollars"):
        if item.get(key) is not None:
            return _coerce_float(item.get(key), 0.0)
    payload = item.get("payload")
    if isinstance(payload, dict):
        return _realized_pnl(payload)
    return None


def _latest_reconciliation(runtime: dict[str, Any]) -> dict[str, Any]:
    report = runtime.get("limited_live_broker_reconciliation_last_report")
    return dict(report) if isinstance(report, dict) else {}


def _latest_closeout(runtime: dict[str, Any]) -> dict[str, Any]:
    report = runtime.get("limited_live_session_closeout_last_report")
    return dict(report) if isinstance(report, dict) else {}


def _operator_checklist_current(runtime: dict[str, Any], session_day: str | None = None) -> bool:
    checklist = runtime.get("limited_live_operator_checklist")
    if not isinstance(checklist, dict):
        return False
    if str(checklist.get("status") or "").strip().lower() not in {"submitted", "accepted", "complete"}:
        return False
    if session_day and str(checklist.get("session_day") or "").strip() != session_day:
        return False
    return True


def build_limited_live_cap_ladder_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    runtime = dict(state.get("runtime") or {})
    settings = normalize_limited_live_ladder_settings(state.get("settings"))
    tiers: list[dict[str, Any]] = []
    for tier in CAP_LADDER_TIERS:
        allowance = dict(runtime.get(str(tier.get("allowance_key") or "")) or {})
        gate = dict(runtime.get(str(tier.get("gate_report_key") or "")) or {})
        canary = dict(runtime.get(str(tier.get("canary_report_key") or "")) or {})
        active = _allowance_active(allowance)
        tiers.append(
            serialize_value(
                {
                    **tier,
                    "allowance_active": active,
                    "allowance_status": str(allowance.get("status") or "inactive").strip().lower() or "inactive",
                    "allowance_expires_at": allowance.get("expires_at"),
                    "consumed_order_count": _coerce_int(allowance.get("consumed_order_count"), 0, minimum=0),
                    "gate_status": str(gate.get("status") or "not_run").strip().lower() or "not_run",
                    "canary_status": str(canary.get("status") or "not_run").strip().lower() or "not_run",
                    "note_id": _report_note_id(gate) or _report_note_id(canary),
                }
            )
        )
    return {
        "status": "active_policy",
        "label": "Limited-live cap ladder policy",
        "base_profile": LIMITED_LIVE_LADDER_LIVE_PROFILE,
        "settings": settings,
        "tiers": tiers,
        "routing_policy": "Amounts above a rung require the active base rollout allowance and every active cap allowance up to that rung.",
        "safety_invariants": [
            "limit_only",
            "one_session_order_per_rung",
            "runtime_allowances_only",
            "no_lock_clearing",
            "no_live_gate_mutation",
            "no_baseline_tuning",
        ],
    }


def _runtime_report(runtime: dict[str, Any], key: str) -> dict[str, Any]:
    value = runtime.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _report_status(report: dict[str, Any]) -> str:
    return str(report.get("status") or report.get("state") or "unknown").strip().lower()


def _report_blockers(report: dict[str, Any]) -> list[str]:
    raw = report.get("blockers")
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    if raw:
        return [str(raw)]
    return []


def _event_failure_detected(runtime: dict[str, Any]) -> bool:
    for key in (
        "last_order_event_status",
        "last_order_persistence_status",
        "last_broker_ledger_status",
        "last_trade_automation_finalize_status",
    ):
        value = str(runtime.get(key) or "").strip().lower()
        if value in {"failed", "error", "blocked"}:
            return True
    return _coerce_int(runtime.get("worker_error_streak"), 0, minimum=0) >= 3


def _market_data_stale(runtime: dict[str, Any], settings: dict[str, Any]) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    last_option_execution = runtime.get("last_option_execution")
    if isinstance(last_option_execution, dict):
        quote_age = _coerce_float(last_option_execution.get("option_quote_age_seconds"), 0.0)
        max_age = _coerce_float(settings.get("loss_containment_stale_quote_seconds"), 120.0, minimum=1.0)
        if quote_age > max_age:
            warnings.append(f"Latest option quote age {quote_age:.0f}s exceeds stale-data limit {max_age:.0f}s.")
        if last_option_execution.get("option_spread_pct") is None:
            warnings.append("Latest option execution evidence is missing spread data.")
    option_block_reason = str(runtime.get("option_execution_block_reason") or "").strip().lower()
    if option_block_reason in {"stale_option_quote", "option_quote_missing", "missing_spread", "spread_too_wide"}:
        warnings.append(f"Latest option execution gate blocked entries for {option_block_reason}.")
    return bool(warnings), warnings


def build_limited_live_hard_fault_snapshot(
    state: dict[str, Any] | None,
    *,
    live_state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Collect hard safety faults that should disable runtime allowances."""

    state = state or {}
    now = now or _utc_now()
    runtime = dict(state.get("runtime") or {})
    settings = normalize_limited_live_ladder_settings(state.get("settings"))
    live_settings = dict((live_state or {}).get("settings") or {})

    blockers: list[str] = []
    warnings: list[str] = []
    signals: list[dict[str, Any]] = []

    def add_blocker(key: str, detail: str) -> None:
        blockers.append(detail)
        signals.append({"key": key, "severity": "blocker", "detail": detail})

    def add_warning(key: str, detail: str) -> None:
        warnings.append(detail)
        signals.append({"key": key, "severity": "warning", "detail": detail})

    state_control_state = _state_control_status(state)
    if state_control_state == "halt":
        add_blocker("state_control_halt", "State control is halted.")
    elif state_control_state == "de_risk":
        add_warning("state_control_derisk", "State control is in de-risk mode.")

    if _coerce_bool(dict(state.get("settings") or {}).get("kill_switch"), False) or _coerce_bool(
        live_settings.get("kill_switch"),
        False,
    ):
        add_blocker("active_kill_switch", "A paper or live kill switch is active.")

    for key, label in (
        ("limited_live_broker_reconciliation_last_report", "Limited-live broker reconciliation"),
        ("limited_live_session_closeout_last_report", "Limited-live session closeout"),
        ("limited_live_rollout_gate_last_report", "Limited-live rollout gate"),
        ("limited_live_cap_expansion_gate_last_report", "Limited-live cap expansion gate"),
        ("limited_live_next_tier_cap_gate_last_report", "Limited-live next-tier gate"),
    ):
        report = _runtime_report(runtime, key)
        status = _report_status(report)
        report_blockers = _report_blockers(report)
        if status in {"blocked", "error", "failed", "halt"}:
            add_blocker(key, f"{label} is {status}.")
        for blocker in report_blockers:
            lowered = blocker.lower()
            if any(
                token in lowered
                for token in (
                    "broker/local",
                    "mismatch",
                    "cap breach",
                    "non-limit",
                    "uncontrolled live",
                    "order event",
                    "terminal evidence",
                )
            ):
                add_blocker(key, f"{label}: {blocker}")

    exit_watchdog = _runtime_report(runtime, "exit_watchdog_last_report")
    if exit_watchdog:
        stuck_exit_count = _coerce_int(exit_watchdog.get("stuck_exit_count"), 0, minimum=0)
        if stuck_exit_count > 0 or _coerce_bool(exit_watchdog.get("entries_blocked"), False):
            add_blocker("exit_watchdog_stuck", "Exit execution watchdog has unconfirmed defensive exits.")
        elif str(exit_watchdog.get("status") or "").strip().lower() == "warning":
            add_warning("exit_watchdog_warning", "Exit execution watchdog has warnings.")

    market_stale, market_warnings = _market_data_stale(runtime, settings)
    if market_stale:
        for warning in market_warnings:
            add_blocker("market_data_freshness", warning)

    if _event_failure_detected(runtime):
        add_blocker("order_event_failure", "Recent order-event or worker persistence failed.")

    status = "blocked" if blockers else ("warning" if warnings else "clear")
    return serialize_value(
        {
            "status": status,
            "should_disable_allowances": bool(blockers),
            "blockers": blockers,
            "warnings": warnings,
            "signals": signals,
            "state_control_status": state_control_state,
            "safety_lock_status": _safety_lock_status(state, live_state),
            "evaluated_at": _serialize_datetime(now),
        }
    )


def build_limited_live_approval_ledger_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict((state or {}).get("runtime") or {})
    entries: list[dict[str, Any]] = []
    entries.extend([item for item in list(runtime.get("limited_live_approval_ledger") or []) if isinstance(item, dict)])
    for prefix in (
        "limited_live_rollout_gate",
        "limited_live_cap_expansion_gate",
        "limited_live_next_tier_cap_gate",
    ):
        approval = runtime.get(f"{prefix}_approval")
        allowance = runtime.get(f"{prefix}_allowance")
        last_report = runtime.get(f"{prefix}_last_report")
        if isinstance(approval, dict) and approval:
            entries.append({"event_type": f"{prefix}_approval", "at": approval.get("approved_at") or approval.get("prepared_at"), **serialize_value(approval)})
        if isinstance(allowance, dict) and allowance:
            entries.append({"event_type": f"{prefix}_allowance", "at": allowance.get("activated_at") or allowance.get("disabled_at") or allowance.get("rolled_back_at"), **serialize_value(allowance)})
        if isinstance(last_report, dict) and last_report:
            entries.append(
                {
                    "event_type": f"{prefix}_report",
                    "at": last_report.get("evaluated_at"),
                    "status": last_report.get("status"),
                    "note_id": _report_note_id(last_report),
                }
            )
    entries = sorted(
        [serialize_value(item) for item in entries if isinstance(item, dict)],
        key=lambda item: _parse_datetime(item.get("at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:LIMITED_LIVE_LADDER_HISTORY_LIMIT]
    return {"status": "ready", "entry_count": len(entries), "entries": entries}


def _base_snapshot_from_report(
    state: dict[str, Any] | None,
    *,
    prefix: str,
    not_run_label: str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = normalize_limited_live_ladder_runtime((state or {}).get("runtime"))
    report = dict(runtime.get(f"{prefix}_last_report") or {})
    if report:
        report.setdefault("related_note_id", runtime.get(f"{prefix}_last_note_id"))
        report.setdefault("note_id", runtime.get(f"{prefix}_last_note_id"))
        report.setdefault("last_run_at", runtime.get(f"{prefix}_last_run_at"))
        report.setdefault("history", runtime.get(f"{prefix}_history") or [])
        for key in (
            "last_scheduled_run_at",
            "last_scheduled_session_day",
            "next_eligible_run_at",
            "last_skipped_reason",
            "last_error",
        ):
            runtime_key = f"{prefix}_{key}"
            if runtime_key in runtime:
                report.setdefault(key.replace("last_", "", 1) if key == "last_skipped_reason" else key, runtime.get(runtime_key))
        return serialize_value(report)
    return {
        "status": "not_run",
        "label": not_run_label,
        "enabled": (settings or {}).get(f"{prefix}_enabled", True),
        "auto_review_enabled": (settings or {}).get(f"{prefix}_auto_review_enabled", True),
        "related_note_id": runtime.get(f"{prefix}_last_note_id"),
        "note_id": runtime.get(f"{prefix}_last_note_id"),
        "last_run_at": runtime.get(f"{prefix}_last_run_at"),
        "history": runtime.get(f"{prefix}_history") or [],
        "blockers": [],
        "warnings": [],
    }


def build_limited_live_broker_reconciliation_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    return _base_snapshot_from_report(
        state,
        prefix="limited_live_broker_reconciliation",
        not_run_label="Limited-live broker reconciliation not run",
    )


def build_limited_live_session_closeout_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    return _base_snapshot_from_report(
        state,
        prefix="limited_live_session_closeout",
        not_run_label="Limited-live session closeout not run",
    )


def build_limited_live_next_tier_cap_canary_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    settings = normalize_limited_live_ladder_settings((state or {}).get("settings"))
    snapshot = _base_snapshot_from_report(
        state,
        prefix="limited_live_next_tier_cap_canary",
        not_run_label="Limited-live next-tier cap canary not run",
        settings=settings,
    )
    snapshot.setdefault("required_clean_sessions", settings["limited_live_next_tier_cap_canary_required_clean_sessions"])
    snapshot.setdefault("window_session_count", 0)
    snapshot.setdefault("clean_session_count", 0)
    return snapshot


def build_limited_live_higher_cap_report_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    settings = normalize_limited_live_ladder_settings((state or {}).get("settings"))
    snapshot = _base_snapshot_from_report(
        state,
        prefix="limited_live_higher_cap_report",
        not_run_label="Limited-live higher-cap report not run",
        settings=settings,
    )
    snapshot.setdefault("recommended_next_max_notional", settings["limited_live_higher_cap_target_max_notional"])
    snapshot.setdefault("target_max_notional", settings["limited_live_higher_cap_target_max_notional"])
    return snapshot


def _find_existing_note_id(profile_key: str, session_day: str, tag: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIMITED_LIVE_LADDER_NOTE_OWNER,
            limit=LIMITED_LIVE_LADDER_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required = {"automation-ai", tag, _profile_tag(profile_key), f"session-{session_day}"}
    for item in list(payload.get("items") or []):
        tags = {str(tag_value or "").strip().lower() for tag_value in item.get("tags", [])}
        if required.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _note_body(kind: str, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    pnl = dict(report.get("pnl_summary") or {})
    slippage = dict(report.get("slippage_summary") or {})
    lines = [
        f"Automation {kind} for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Run source: {str(report.get('run_source') or 'manual').replace('_', ' ')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Broker-live gate: {report.get('broker_gate_status') or report.get('broker_live_gate_status') or 'unknown'}",
        f"Safety locks: {report.get('safety_lock_status') or 'unknown'}",
        f"State control: {report.get('state_control_status') or 'unknown'}",
        f"Reconciliation: {report.get('reconciliation_status') or report.get('latest_reconciliation_status') or 'unknown'}",
        f"Consumed orders: {report.get('consumed_order_count') or 0}",
        f"PnL samples: {pnl.get('sample_count', 0)} | total ${float(pnl.get('realized_pnl') or 0.0):.2f}",
        f"Slippage samples: {slippage.get('sample_count', 0)} | worst {slippage.get('worst_abs_bps') if slippage.get('worst_abs_bps') is not None else '--'} bps",
        "",
        "This item is safety evidence. It does not place, cancel, or close orders, enable or arm automation, clear locks, mutate broker-live gates, tune baseline settings, or activate allowances.",
        "",
        "Blockers",
    ]
    lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in blockers[:20]) if blockers else lines.append("- None.")
    lines.append("")
    lines.append("Warnings")
    lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in warnings[:20]) if warnings else lines.append("- None.")
    actions = [item for item in list(report.get("required_operator_actions") or []) if isinstance(item, dict)]
    if actions:
        lines.extend(["", "Required operator actions"])
        lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in actions[:12])
    return "\n".join(lines).strip()


def _sync_note(
    *,
    tenant: Tenant,
    profile_key: str,
    report: dict[str, Any],
    tag: str,
    title: str,
    extra_tags: list[str] | None = None,
) -> str | None:
    session_day = str(report.get("session_day") or _session_day_for()).strip()
    tags = [
        "automation-ai",
        tag,
        "limited-live-rollout",
        _profile_tag(profile_key),
        f"session-{session_day}",
        *(extra_tags or []),
    ]
    body = _note_body(title, tenant, profile_key, report)
    note_id = (
        str(report.get("note_id") or report.get("related_note_id") or "").strip()
        or _find_existing_note_id(profile_key, session_day, tag)
    )
    if note_id:
        try:
            updated = notes_service.update_note(
                note_id,
                {
                    "title": title,
                    "body": body,
                    "tags": tags,
                    "owner": LIMITED_LIVE_LADDER_NOTE_OWNER,
                    "note_type": "risk_review",
                    "priority": "high" if report.get("blockers") else "medium",
                },
            )
            return str(updated.get("id") or note_id)
        except Exception:
            return note_id
    try:
        created = notes_service.create_note(
            title=title,
            body=body,
            tags=tags,
            owner=LIMITED_LIVE_LADDER_NOTE_OWNER,
            note_type="risk_review",
            priority="high" if report.get("blockers") else "medium",
        )
        return str(created.get("id") or "").strip() or None
    except Exception:
        return None


def _finalize_report(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    actor: Any,
    report: dict[str, Any],
    prefix: str,
    note_tag: str,
    note_title: str,
    audit_event: str,
    extra_note_tags: list[str] | None = None,
) -> dict[str, Any]:
    runtime = state.setdefault("runtime", {})
    now = _parse_datetime(report.get("evaluated_at") or report.get("checked_at")) or _utc_now()
    note_id = _sync_note(
        tenant=tenant,
        profile_key=profile_key,
        report=report,
        tag=note_tag,
        title=note_title,
        extra_tags=extra_note_tags,
    )
    if note_id:
        report["note_id"] = note_id
        report["related_note_id"] = note_id
        runtime[f"{prefix}_last_note_id"] = note_id
        runtime[f"{prefix}_note_session_day"] = report.get("session_day")
    runtime[f"{prefix}_last_report"] = serialize_value(report)
    runtime[f"{prefix}_last_run_at"] = _serialize_datetime(now)
    _append_runtime_history(runtime, f"{prefix}_history", report)
    if str(report.get("run_source") or "").strip().lower() == "scheduled":
        runtime[f"{prefix}_last_scheduled_run_at"] = report.get("evaluated_at") or report.get("checked_at")
        runtime[f"{prefix}_last_scheduled_session_day"] = report.get("session_day")
        runtime[f"{prefix}_next_eligible_run_at"] = report.get("next_eligible_run_at")
        runtime[f"{prefix}_last_skipped_reason"] = None
        runtime[f"{prefix}_last_error"] = None
    if db is not None:
        record_audit_event(
            db,
            event_type=audit_event,
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "status": report.get("status"),
                "session_day": report.get("session_day"),
                "run_source": report.get("run_source"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
                "note_id": note_id,
            },
        )
    return serialize_value(report)


def _ledger_evidence(db: Session | None, *, tenant: Tenant, session_day: str) -> dict[str, Any]:
    start_at, end_at = _session_bounds_for_day(session_day)
    pending = _session_rows([row for row in _safe_frame_records(sdm.read_pending_orders) if _is_live_row(row)], session_day)
    open_rows = _session_rows([row for row in _safe_frame_records(sdm.read_open_trades) if _is_live_row(row)], session_day)
    closed = _session_rows([row for row in _safe_frame_records(sdm.read_closed_trades) if _is_live_row(row)], session_day)
    events = _recent_order_events(db, tenant=tenant, start_at=start_at, end_at=end_at)
    limited_pending = [row for row in pending if _is_limited_live_row(row)]
    limited_open = [row for row in open_rows if _is_limited_live_row(row)]
    limited_closed = [row for row in closed if _is_limited_live_row(row)]
    uncontrolled_pending = [row for row in pending if _is_uncontrolled_live_row(row)]
    uncontrolled_open = [row for row in open_rows if _is_uncontrolled_live_row(row)]
    non_limit_rows = [
        row for row in [*limited_pending, *limited_open, *limited_closed] if _row_order_type(row) not in {"", "limit"}
    ]
    non_limit_events = [
        event for event in events if str(event.get("order_type") or "").strip().lower() not in {"", "limit"}
    ]
    failed_events = [
        event
        for event in events
        if str(event.get("status") or "").strip().lower() in {"failed", "error", "rejected", "blocked"}
    ]
    notional_values = [value for value in [_row_notional(row) for row in [*limited_pending, *limited_open, *limited_closed]] if value is not None]
    notional_values.extend(value for value in [_event_notional(event) for event in events] if value is not None)
    pnl_values = [value for value in [_realized_pnl(row) for row in [*limited_closed, *events]] if value is not None]
    slippage_values = [abs(value) for value in [_slippage_bps(row) for row in [*limited_closed, *events]] if value is not None]
    return serialize_value(
        {
            "session_day": session_day,
            "pending": limited_pending,
            "open": limited_open,
            "closed": limited_closed,
            "events": events,
            "uncontrolled_pending_count": len(uncontrolled_pending),
            "uncontrolled_open_count": len(uncontrolled_open),
            "non_limit_count": len(non_limit_rows) + len(non_limit_events),
            "failed_event_count": len(failed_events),
            "notional_values": notional_values,
            "pnl_values": pnl_values,
            "slippage_values": slippage_values,
            "counts": {
                "pending": len(limited_pending),
                "open": len(limited_open),
                "closed": len(limited_closed),
                "events": len(events),
            },
        }
    )


def _summaries_from_evidence(evidence_items: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    pnl_values = [float(value) for item in evidence_items for value in list(item.get("pnl_values") or [])]
    slippage_values = [abs(float(value)) for item in evidence_items for value in list(item.get("slippage_values") or [])]
    return (
        {"sample_count": len(pnl_values), "realized_pnl": float(sum(pnl_values)) if pnl_values else 0.0},
        {
            "sample_count": len(slippage_values),
            "average_abs_bps": float(sum(slippage_values) / len(slippage_values)) if slippage_values else None,
            "worst_abs_bps": float(max(slippage_values)) if slippage_values else None,
        },
    )


def _broker_gate_status(report: dict[str, Any], live_state: dict[str, Any] | None) -> str:
    status = str(report.get("broker_live_gate_status") or report.get("broker_gate_status") or "").strip().lower()
    if status:
        return status
    live_settings = dict((live_state or {}).get("settings") or {})
    if _coerce_bool(live_settings.get("kill_switch"), False):
        return "locked"
    return "unknown"


def _safety_lock_status(paper_state: dict[str, Any], live_state: dict[str, Any] | None) -> str:
    paper_settings = dict(paper_state.get("settings") or {})
    live_settings = dict((live_state or {}).get("settings") or {})
    if _coerce_bool(paper_settings.get("kill_switch"), False) or _coerce_bool(live_settings.get("kill_switch"), False):
        return "active"
    runtime = dict(paper_state.get("runtime") or {})
    if str(runtime.get("state_control_state") or "").strip().lower() == "halt":
        return "active"
    return "clear"


def _state_control_status(paper_state: dict[str, Any]) -> str:
    runtime = dict(paper_state.get("runtime") or {})
    return str(runtime.get("state_control_state") or dict(runtime.get("state_control_last_evaluation") or {}).get("state") or "unknown").strip().lower()


def _order_cap_for_current_ladder(state: dict[str, Any]) -> float:
    settings = dict(state.get("settings") or {})
    return _coerce_float(
        settings.get("limited_live_next_tier_cap_max_notional")
        or settings.get("limited_live_cap_expansion_max_notional")
        or settings.get("limited_live_rollout_max_notional"),
        500.0,
    )


def run_limited_live_broker_reconciliation(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    profile_key: str = LIMITED_LIVE_LADDER_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    del linked_account
    now = now or _utc_now()
    session_day, _ = limited_live_ladder_session_day_for(now, forced=str(run_source).strip().lower() != "scheduled")
    evidence = _ledger_evidence(db, tenant=tenant, session_day=session_day)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if profile_key != LIMITED_LIVE_LADDER_PAPER_PROFILE:
        blockers.append(_issue("paper_profile_required", "Limited-live reconciliation is reviewed from the personal paper automation profile."))
    if evidence["uncontrolled_pending_count"] or evidence["uncontrolled_open_count"]:
        blockers.append(_issue("uncontrolled_live_exposure", "Uncontrolled live order or position evidence is present.", component="ledger"))
    if evidence["non_limit_count"]:
        blockers.append(_issue("non_limit_order_evidence", "Limited-live evidence includes non-limit routing.", component="ledger"))
    if evidence["failed_event_count"]:
        blockers.append(_issue("order_event_failed", "Limited-live order events include failed, rejected, blocked, or error status.", component="order_events"))
    max_cap = _order_cap_for_current_ladder(paper_state)
    if evidence["notional_values"] and max(evidence["notional_values"]) > max_cap + 0.01:
        blockers.append(_issue("cap_breach", "Limited-live evidence exceeds the active ladder cap.", component="cap_policy"))
    warnings.append(
        _issue(
            "broker_snapshot_read_only",
            "V1 reconciliation uses local ledger and order-event evidence only; broker live state is not mutated or fetched with order APIs.",
            component="broker_live",
            severity="warning",
        )
    )
    pnl_summary, slippage_summary = _summaries_from_evidence([evidence])
    status = "blocked" if blockers else "warning" if warnings else "clean"
    report = {
        "status": status,
        "label": "Limited-live broker reconciliation blocked" if blockers else "Limited-live broker reconciliation warning" if warnings else "Limited-live broker reconciliation clean",
        "profile_key": profile_key,
        "live_profile_key": LIMITED_LIVE_LADDER_LIVE_PROFILE,
        "session_day": session_day,
        "checked_at": _serialize_datetime(now),
        "evaluated_at": _serialize_datetime(now),
        "run_source": str(run_source or "manual").strip().lower(),
        "matched_count": int(evidence["counts"]["events"] + evidence["counts"]["closed"]),
        "orphan_broker_order_count": 0,
        "orphan_local_order_count": int(evidence["counts"]["pending"]),
        "position_mismatch_count": int(evidence["uncontrolled_open_count"]),
        "fill_mismatch_count": int(evidence["failed_event_count"]),
        "ledger_consistency": "blocked" if blockers else "warning" if warnings else "clean",
        "reconciliation_status": status,
        "broker_gate_status": _broker_gate_status({}, live_state),
        "safety_lock_status": _safety_lock_status(paper_state, live_state),
        "state_control_status": _state_control_status(paper_state),
        "evidence": evidence,
        "pnl_summary": pnl_summary,
        "slippage_summary": slippage_summary,
        "blockers": blockers,
        "warnings": warnings,
        "manual_action_required": bool(blockers),
    }
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=profile_key,
        actor=actor,
        report=report,
        prefix="limited_live_broker_reconciliation",
        note_tag="limited-live-broker-reconciliation",
        note_title="Limited-live broker reconciliation",
        audit_event="trade_automation.limited_live_broker_reconciled",
        extra_note_tags=["reconciliation"],
    )


def run_limited_live_session_closeout(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    profile_key: str = LIMITED_LIVE_LADDER_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    del linked_account
    now = now or _utc_now()
    session_day, _ = limited_live_ladder_session_day_for(now, forced=str(run_source).strip().lower() != "scheduled")
    runtime = paper_state.setdefault("runtime", {})
    reconciliation = _latest_reconciliation(runtime)
    evidence = _ledger_evidence(db, tenant=tenant, session_day=session_day)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if profile_key != LIMITED_LIVE_LADDER_PAPER_PROFILE:
        blockers.append(_issue("paper_profile_required", "Limited-live closeout is reviewed from the personal paper automation profile."))
    rec_status = str(reconciliation.get("status") or "missing").strip().lower()
    if rec_status in {"blocked", "failed", "mismatch", "mismatched"}:
        blockers.append(_issue("reconciliation_blocked", "Limited-live broker reconciliation has unresolved mismatch evidence.", component="reconciliation"))
    elif rec_status in {"missing", "not_run"}:
        warnings.append(_issue("reconciliation_missing", "Limited-live broker reconciliation has not run for this session.", component="reconciliation", severity="warning"))
    if evidence["counts"]["pending"] or evidence["counts"]["open"]:
        blockers.append(_issue("terminal_state_incomplete", "Limited-live session has pending/open evidence requiring terminal handling.", component="ledger"))
    if evidence["failed_event_count"]:
        blockers.append(_issue("order_event_failed", "Limited-live order events include failure evidence.", component="order_events"))
    if evidence["uncontrolled_pending_count"] or evidence["uncontrolled_open_count"]:
        blockers.append(_issue("uncontrolled_live_exposure", "Uncontrolled live order or position evidence is present.", component="ledger"))
    if _safety_lock_status(paper_state, live_state) != "clear":
        blockers.append(_issue("safety_lock_active", "A paper or live safety lock is active.", component="safety_lock"))
    if _state_control_status(paper_state) == "halt":
        blockers.append(_issue("state_control_halt", "State-control is halted.", component="state_control"))
    pnl_summary, slippage_summary = _summaries_from_evidence([evidence])
    worst_slippage = slippage_summary.get("worst_abs_bps")
    if worst_slippage is not None and _coerce_float(worst_slippage, 0.0) > LIMITED_LIVE_BLOCK_SLIPPAGE_BPS:
        blockers.append(_issue("slippage_blocked", "Limited-live worst slippage exceeded the block threshold.", component="slippage"))
    elif worst_slippage is not None and _coerce_float(worst_slippage, 0.0) > LIMITED_LIVE_WARN_SLIPPAGE_BPS:
        warnings.append(_issue("slippage_warning", "Limited-live worst slippage exceeded the warning threshold.", component="slippage", severity="warning"))
    status = "blocked" if blockers else "warning" if warnings else "clean"
    report = {
        "status": status,
        "label": "Limited-live session closeout blocked" if blockers else "Limited-live session closeout warning" if warnings else "Limited-live session closeout clean",
        "profile_key": profile_key,
        "live_profile_key": LIMITED_LIVE_LADDER_LIVE_PROFILE,
        "session_day": session_day,
        "evaluated_at": _serialize_datetime(now),
        "run_source": str(run_source or "manual").strip().lower(),
        "terminal_state": "blocked" if blockers else "complete",
        "order_count": int(evidence["counts"]["events"] + evidence["counts"]["pending"] + evidence["counts"]["open"] + evidence["counts"]["closed"]),
        "consumed_order_count": int(evidence["counts"]["events"]),
        "reconciliation_status": rec_status,
        "broker_gate_status": _broker_gate_status(reconciliation, live_state),
        "safety_lock_status": _safety_lock_status(paper_state, live_state),
        "state_control_status": _state_control_status(paper_state),
        "evidence": evidence,
        "pnl_summary": pnl_summary,
        "slippage_summary": slippage_summary,
        "blockers": blockers,
        "warnings": warnings,
        "manual_action_required": bool(blockers),
    }
    finalized = _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=profile_key,
        actor=actor,
        report=report,
        prefix="limited_live_session_closeout",
        note_tag="limited-live-session-closeout",
        note_title="Limited-live session closeout",
        audit_event="trade_automation.limited_live_session_closed_out",
        extra_note_tags=["session-closeout"],
    )
    if blockers:
        disable_limited_live_allowances_for_fault(
            paper_state,
            reason="limited_live_session_closeout_blocked",
            now=now,
            actor=actor,
        )
    return finalized


def _note_lookup(profile_key: str) -> dict[str, set[str]]:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIMITED_LIVE_LADDER_NOTE_OWNER,
            limit=LIMITED_LIVE_LADDER_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return {}
    by_day: dict[str, set[str]] = {}
    profile = _profile_tag(profile_key)
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if "automation-ai" not in tags or profile not in tags:
            continue
        session_tags = [tag for tag in tags if tag.startswith("session-")]
        if not session_tags:
            continue
        session_day = session_tags[0].replace("session-", "", 1)
        by_day.setdefault(session_day, set()).update(tags)
    return by_day


def _history_report_by_day(runtime: dict[str, Any], prefix: str, session_day: str) -> dict[str, Any]:
    last = runtime.get(f"{prefix}_last_report")
    if isinstance(last, dict) and str(last.get("session_day") or "").strip() == session_day:
        return dict(last)
    for item in list(runtime.get(f"{prefix}_history") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("session_day") or "").strip() == session_day:
            return dict(item)
    return {}


def _next_tier_session_report(
    *,
    session_day: str,
    gate: dict[str, Any],
    reconciliation: dict[str, Any],
    closeout: dict[str, Any],
    note_tags: set[str],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    gate_status = str(gate.get("status") or "missing").strip().lower()
    consumed = _coerce_int(gate.get("consumed_order_count"), 0, minimum=0)
    if gate_status in {"missing", "not_run"}:
        blockers.append(_issue("next_tier_gate_missing", "No next-tier cap gate evidence exists for this session.", component="limited_live_next_tier_cap_gate"))
    elif gate_status == "blocked" or gate.get("blockers"):
        blockers.append(_issue("next_tier_gate_blocked", "Next-tier cap gate has unresolved blockers.", component="limited_live_next_tier_cap_gate"))
    elif gate.get("warnings"):
        warnings.append(_issue("next_tier_gate_warning", "Next-tier cap gate has warnings.", component="limited_live_next_tier_cap_gate", severity="warning"))
    if consumed <= 0:
        blockers.append(_issue("no_next_tier_order_evidence", "No next-tier capped order was consumed during this session.", component="order_events"))
    if evidence["counts"]["pending"] or evidence["counts"]["open"]:
        blockers.append(_issue("terminal_state_incomplete", "Next-tier evidence has pending or open ledger state.", component="ledger"))
    if evidence["non_limit_count"]:
        blockers.append(_issue("non_limit_order_evidence", "Next-tier evidence includes non-limit routing.", component="order_events"))
    if evidence["failed_event_count"]:
        blockers.append(_issue("order_event_failed", "Next-tier order events include failure evidence.", component="order_events"))
    rec_status = str(reconciliation.get("status") or "missing").strip().lower()
    if rec_status in {"blocked", "failed", "mismatch", "mismatched"}:
        blockers.append(_issue("reconciliation_blocked", "Limited-live reconciliation is blocked or mismatched.", component="reconciliation"))
    elif rec_status in {"missing", "not_run"}:
        warnings.append(_issue("reconciliation_missing", "Limited-live reconciliation evidence is missing.", component="reconciliation", severity="warning"))
    closeout_status = str(closeout.get("status") or "missing").strip().lower()
    if closeout_status in {"blocked", "failed"}:
        blockers.append(_issue("session_closeout_blocked", "Limited-live session closeout is blocked.", component="session_closeout"))
    elif closeout_status in {"missing", "not_run", "warning"}:
        warnings.append(_issue("session_closeout_warning", "Limited-live session closeout is missing or warning.", component="session_closeout", severity="warning"))
    required_note_tags = {
        "limited-live-next-tier-cap-gate",
        "limited-live-broker-reconciliation",
        "limited-live-session-closeout",
    }
    missing_note_tags = sorted(required_note_tags - note_tags)
    if missing_note_tags:
        blockers.append(_issue("notes_missing", "Session is missing required automation-ai Notes coverage.", component="notes"))
    pnl_summary, slippage_summary = _summaries_from_evidence([evidence])
    if _coerce_float(pnl_summary.get("realized_pnl"), 0.0) < 0:
        blockers.append(_issue("negative_pnl", "Next-tier session realized PnL is negative.", component="pnl"))
    worst_slippage = slippage_summary.get("worst_abs_bps")
    if worst_slippage is not None and _coerce_float(worst_slippage, 0.0) > LIMITED_LIVE_BLOCK_SLIPPAGE_BPS:
        blockers.append(_issue("slippage_blocked", "Next-tier worst slippage exceeded the block threshold.", component="slippage"))
    elif worst_slippage is not None and _coerce_float(worst_slippage, 0.0) > LIMITED_LIVE_WARN_SLIPPAGE_BPS:
        warnings.append(_issue("slippage_warning", "Next-tier worst slippage exceeded the warning threshold.", component="slippage", severity="warning"))
    status = "clean" if not blockers and not warnings else "warning" if not blockers else "blocked"
    return {
        "session_day": session_day,
        "status": status,
        "clean": status == "clean",
        "limited_live_next_tier_cap_gate": gate,
        "reconciliation": reconciliation,
        "session_closeout": closeout,
        "note_tags": sorted(note_tags),
        "missing_note_tags": missing_note_tags,
        "consumed_order_count": consumed,
        "terminal_state": closeout.get("terminal_state") or ("complete" if status == "clean" else None),
        "reconciliation_status": rec_status,
        "pnl_summary": pnl_summary,
        "slippage_summary": slippage_summary,
        "evidence": evidence,
        "blockers": blockers,
        "warnings": warnings,
    }


def run_limited_live_next_tier_cap_canary_review(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    profile_key: str = LIMITED_LIVE_LADDER_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    del linked_account
    now = now or _utc_now()
    settings = normalize_limited_live_ladder_settings(paper_state.get("settings"))
    session_day, _ = limited_live_ladder_session_day_for(now, forced=str(run_source).strip().lower() != "scheduled")
    runtime = paper_state.setdefault("runtime", {})
    window = settings["limited_live_next_tier_cap_canary_window_sessions"]
    required = settings["limited_live_next_tier_cap_canary_required_clean_sessions"]
    days = _recent_trading_days(now, count=window)
    notes = _note_lookup(profile_key)
    sessions: list[dict[str, Any]] = []
    for day in days:
        evidence = _ledger_evidence(db, tenant=tenant, session_day=day)
        gate = _history_report_by_day(runtime, "limited_live_next_tier_cap_gate", day)
        reconciliation = _history_report_by_day(runtime, "limited_live_broker_reconciliation", day)
        closeout = _history_report_by_day(runtime, "limited_live_session_closeout", day)
        if gate or reconciliation or closeout or evidence["counts"]["events"] or evidence["counts"]["closed"]:
            sessions.append(
                _next_tier_session_report(
                    session_day=day,
                    gate=gate,
                    reconciliation=reconciliation,
                    closeout=closeout,
                    note_tags=notes.get(day, set()),
                    evidence=evidence,
                )
            )
    blockers = [
        {"session_day": item.get("session_day"), **dict(blocker)}
        for item in sessions
        for blocker in list(item.get("blockers") or [])
        if isinstance(blocker, dict)
    ]
    warnings = [
        {"session_day": item.get("session_day"), **dict(warning)}
        for item in sessions
        for warning in list(item.get("warnings") or [])
        if isinstance(warning, dict)
    ]
    clean_count = sum(1 for item in sessions if item.get("clean"))
    if profile_key != LIMITED_LIVE_LADDER_PAPER_PROFILE:
        blockers.insert(0, _issue("paper_profile_required", "V1 next-tier canary is reviewed from the personal paper automation profile."))
    if not sessions:
        blockers.append(_issue("no_next_tier_sessions", "No next-tier capped live evidence was found in the configured session window."))
    if clean_count < required:
        blockers.append(_issue("clean_sessions_low", f"Next-tier canary has {clean_count}/{required} required clean sessions."))
    rec = _latest_reconciliation(runtime)
    if str(rec.get("status") or "").strip().lower() in {"blocked", "failed", "mismatch", "mismatched"}:
        blockers.append(_issue("latest_reconciliation_blocked", "Latest limited-live reconciliation is blocked.", component="reconciliation"))
    if _safety_lock_status(paper_state, live_state) != "clear":
        blockers.append(_issue("safety_lock_active", "A paper or live safety lock is active.", component="safety_lock"))
    if _state_control_status(paper_state) == "halt":
        blockers.append(_issue("state_control_halt", "State-control is halted.", component="state_control"))
    pnl_summary, slippage_summary = _summaries_from_evidence([item.get("evidence", {}) for item in sessions])
    status = "blocked" if blockers else "warning" if warnings else "ready_for_operator_review"
    report = {
        "status": status,
        "label": "Limited-live next-tier canary blocked" if blockers else "Limited-live next-tier canary needs review" if warnings else "Next-tier cap evidence ready for operator review",
        "profile_key": profile_key,
        "live_profile_key": LIMITED_LIVE_LADDER_LIVE_PROFILE,
        "session_day": session_day,
        "evaluated_at": _serialize_datetime(now),
        "run_source": str(run_source or "manual").strip().lower(),
        "clean_session_count": clean_count,
        "required_clean_sessions": required,
        "window_session_count": len(sessions),
        "window_days": window,
        "latest_gate_state": str(dict(runtime.get("limited_live_next_tier_cap_gate_last_report") or {}).get("status") or "missing"),
        "current_max_notional": _coerce_float(dict(paper_state.get("settings") or {}).get("limited_live_next_tier_cap_max_notional"), 500.0),
        "consumed_order_count": sum(_coerce_int(item.get("consumed_order_count"), 0) for item in sessions),
        "latest_reconciliation_status": str(rec.get("status") or "missing").strip().lower(),
        "broker_gate_status": _broker_gate_status(rec, live_state),
        "safety_lock_status": _safety_lock_status(paper_state, live_state),
        "state_control_status": _state_control_status(paper_state),
        "pnl_summary": pnl_summary,
        "slippage_summary": slippage_summary,
        "note_coverage": {
            "covered": sum(1 for item in sessions if not item.get("missing_note_tags")),
            "required": len(sessions),
            "ratio": (sum(1 for item in sessions if not item.get("missing_note_tags")) / len(sessions)) if sessions else 0.0,
        },
        "sessions": serialize_value(sessions),
        "blockers": blockers[:30],
        "warnings": warnings[:30],
        "manual_action_required": bool(blockers or warnings),
        "scheduled_status": "reviewed" if str(run_source).strip().lower() == "scheduled" else "manual",
    }
    if str(run_source).strip().lower() == "scheduled":
        report["last_scheduled_run_at"] = report["evaluated_at"]
        report["last_scheduled_session_day"] = session_day
        report["next_eligible_run_at"] = _serialize_datetime(next_eligible_limited_live_ladder_review_after_session(session_day))
    else:
        report["next_eligible_run_at"] = _serialize_datetime(next_eligible_limited_live_ladder_review_at(now))
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=profile_key,
        actor=actor,
        report=report,
        prefix="limited_live_next_tier_cap_canary",
        note_tag="limited-live-next-tier-cap-canary",
        note_title="Limited-live next-tier cap canary",
        audit_event="trade_automation.limited_live_next_tier_cap_canary_reviewed",
        extra_note_tags=["limited-live-next-tier-cap-gate"],
    )


def run_limited_live_higher_cap_report(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    profile_key: str = LIMITED_LIVE_LADDER_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    del linked_account
    now = now or _utc_now()
    settings = normalize_limited_live_ladder_settings(paper_state.get("settings"))
    session_day, _ = limited_live_ladder_session_day_for(now, forced=str(run_source).strip().lower() != "scheduled")
    runtime = paper_state.setdefault("runtime", {})
    canary = dict(runtime.get("limited_live_next_tier_cap_canary_last_report") or {})
    reconciliation = _latest_reconciliation(runtime)
    closeout = _latest_closeout(runtime)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if profile_key != LIMITED_LIVE_LADDER_PAPER_PROFILE:
        blockers.append(_issue("paper_profile_required", "Higher-cap report is reviewed from the personal paper automation profile."))
    canary_status = str(canary.get("status") or "missing").strip().lower()
    required = settings["limited_live_higher_cap_required_clean_sessions"]
    clean_count = _coerce_int(canary.get("clean_session_count"), 0)
    if canary_status in {"missing", "not_run"}:
        blockers.append(_issue("next_tier_canary_missing", "Run the next-tier cap canary before higher-cap review.", component="limited_live_next_tier_cap_canary"))
    elif canary_status != "ready_for_operator_review":
        blockers.append(_issue("next_tier_canary_not_ready", "Next-tier cap canary is not ready.", component="limited_live_next_tier_cap_canary"))
    if clean_count < required:
        blockers.append(_issue("clean_sessions_low", f"Next-tier canary has {clean_count}/{required} required clean sessions.", component="limited_live_next_tier_cap_canary"))
    if _is_stale(canary, now, settings["limited_live_higher_cap_stale_after_days"]):
        blockers.append(_issue("next_tier_canary_stale", "Next-tier cap canary evidence is stale.", component="limited_live_next_tier_cap_canary"))
    if not _report_note_id(canary):
        blockers.append(_issue("next_tier_canary_note_missing", "Next-tier cap canary is missing a linked automation-ai note.", component="notes"))
    rec_status = str(reconciliation.get("status") or "missing").strip().lower()
    if rec_status in {"blocked", "failed", "mismatch", "mismatched"}:
        blockers.append(_issue("reconciliation_blocked", "Limited-live reconciliation is blocked or mismatched.", component="reconciliation"))
    elif rec_status in {"missing", "not_run", "warning"}:
        warnings.append(_issue("reconciliation_warning", "Latest limited-live reconciliation is missing or warning.", component="reconciliation", severity="warning"))
    if str(closeout.get("status") or "missing").strip().lower() in {"blocked", "failed"}:
        blockers.append(_issue("session_closeout_blocked", "Limited-live session closeout is blocked.", component="session_closeout"))
    if _safety_lock_status(paper_state, live_state) != "clear":
        blockers.append(_issue("safety_lock_active", "A paper or live safety lock is active.", component="safety_lock"))
    if _state_control_status(paper_state) == "halt":
        blockers.append(_issue("state_control_halt", "State-control is halted.", component="state_control"))
    if settings["limited_live_operator_checklist_required"] and not _operator_checklist_current(runtime, session_day):
        blockers.append(_issue("operator_checklist_missing", "Submit the limited-live operator checklist before requesting a higher cap.", component="operator_checklist"))
    pnl_summary = dict(canary.get("pnl_summary") or {"sample_count": 0, "realized_pnl": 0.0})
    slippage_summary = dict(canary.get("slippage_summary") or {"sample_count": 0, "average_abs_bps": None, "worst_abs_bps": None})
    if _coerce_float(pnl_summary.get("realized_pnl"), 0.0) < 0:
        blockers.append(_issue("negative_pnl", "Next-tier realized PnL is negative.", component="pnl"))
    worst_slippage = slippage_summary.get("worst_abs_bps")
    if worst_slippage is not None and _coerce_float(worst_slippage, 0.0) > LIMITED_LIVE_BLOCK_SLIPPAGE_BPS:
        blockers.append(_issue("slippage_blocked", "Next-tier worst slippage exceeded the block threshold.", component="slippage"))
    elif warnings:
        pass
    status = "blocked" if blockers else "needs_operator_review" if warnings else "ready_to_request_higher_cap"
    report = {
        "status": status,
        "label": "Higher-cap request blocked" if blockers else "Higher-cap request needs operator review" if warnings else "Ready to request $1000 limited-live cap",
        "profile_key": profile_key,
        "live_profile_key": LIMITED_LIVE_LADDER_LIVE_PROFILE,
        "session_day": session_day,
        "evaluated_at": _serialize_datetime(now),
        "run_source": str(run_source or "manual").strip().lower(),
        "current_max_notional": _coerce_float(dict(paper_state.get("settings") or {}).get("limited_live_next_tier_cap_max_notional"), 500.0),
        "recommended_next_max_notional": settings["limited_live_higher_cap_target_max_notional"],
        "target_max_notional": settings["limited_live_higher_cap_target_max_notional"],
        "required_clean_sessions": required,
        "clean_session_progress": {"clean": clean_count, "required": required},
        "next_tier_cap_canary_status": canary_status,
        "latest_reconciliation_status": rec_status,
        "broker_gate_status": _broker_gate_status(reconciliation, live_state),
        "safety_lock_status": _safety_lock_status(paper_state, live_state),
        "state_control_status": _state_control_status(paper_state),
        "operator_checklist_status": "submitted" if _operator_checklist_current(runtime, session_day) else "missing",
        "pnl_summary": pnl_summary,
        "slippage_summary": slippage_summary,
        "blockers": blockers[:30],
        "warnings": warnings[:30],
        "required_operator_actions": [
            {
                "key": "request_higher_cap_approval" if not blockers else "clear_higher_cap_blockers",
                "detail": "Evidence is clean. Request separate operator approval for a $1000 cap gate."
                if not blockers
                else "Clear blockers before requesting a higher limited-live cap.",
            }
        ],
        "manual_action_required": bool(blockers or warnings),
        "scheduled_status": "reviewed" if str(run_source).strip().lower() == "scheduled" else "manual",
    }
    if str(run_source).strip().lower() == "scheduled":
        report["last_scheduled_run_at"] = report["evaluated_at"]
        report["last_scheduled_session_day"] = session_day
        report["next_eligible_run_at"] = _serialize_datetime(next_eligible_limited_live_ladder_review_after_session(session_day))
    else:
        report["next_eligible_run_at"] = _serialize_datetime(next_eligible_limited_live_ladder_review_at(now))
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=profile_key,
        actor=actor,
        report=report,
        prefix="limited_live_higher_cap_report",
        note_tag="limited-live-higher-cap-report",
        note_title="Limited-live higher-cap report",
        audit_event="trade_automation.limited_live_higher_cap_reported",
        extra_note_tags=["limited-live-next-tier-cap-canary"],
    )


def submit_limited_live_operator_checklist(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    profile_key: str = LIMITED_LIVE_LADDER_PAPER_PROFILE,
    actor: Any = None,
    now: datetime | None = None,
    checklist: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    session_day = _session_day_for(now)
    runtime = paper_state.setdefault("runtime", {})
    payload = serialize_value(checklist or {})
    required_items = {
        "account": True,
        "symbol_scope": True,
        "max_notional": True,
        "session_order_cap": True,
        "rollback_readiness": True,
        "live_gate_status": True,
        "current_positions": True,
        "kill_switch_state": True,
        "acknowledgement": True,
    }
    submitted_items = {**required_items, **{str(k): bool(v) for k, v in payload.items() if k in required_items}}
    missing = [key for key, value in submitted_items.items() if not value]
    blockers = [
        _issue("checklist_item_missing", f"Checklist item is not acknowledged: {key}.", component="operator_checklist")
        for key in missing
    ]
    if _safety_lock_status(paper_state, live_state) != "clear":
        blockers.append(_issue("safety_lock_active", "A paper or live safety lock is active.", component="safety_lock"))
    report = {
        "status": "blocked" if blockers else "submitted",
        "label": "Limited-live operator checklist blocked" if blockers else "Limited-live operator checklist submitted",
        "profile_key": profile_key,
        "live_profile_key": LIMITED_LIVE_LADDER_LIVE_PROFILE,
        "session_day": session_day,
        "evaluated_at": _serialize_datetime(now),
        "run_source": "manual",
        "operator_checklist": submitted_items,
        "acknowledgement_text": str(payload.get("acknowledgement_text") or "I acknowledge the limited-live cap raise safety checklist.").strip(),
        "broker_gate_status": _broker_gate_status({}, live_state),
        "safety_lock_status": _safety_lock_status(paper_state, live_state),
        "state_control_status": _state_control_status(paper_state),
        "blockers": blockers,
        "warnings": [],
        "manual_action_required": bool(blockers),
    }
    runtime["limited_live_operator_checklist"] = serialize_value(report)
    append_limited_live_approval_ledger_event(
        paper_state,
        event_type="operator_checklist_submitted",
        detail="Operator checklist submitted for limited-live cap ladder.",
        actor=actor,
        now=now,
        status=report["status"],
    )
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=profile_key,
        actor=actor,
        report=report,
        prefix="limited_live_operator_checklist",
        note_tag="limited-live-operator-checklist",
        note_title="Limited-live operator checklist",
        audit_event="trade_automation.limited_live_operator_checklist_submitted",
        extra_note_tags=["operator-checklist"],
    )


def disable_limited_live_allowances_for_fault(
    state: dict[str, Any],
    *,
    reason: str,
    now: datetime | None = None,
    actor: Any = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    before = deepcopy(dict(state.get("runtime") or {}))
    disabled: list[str] = []
    rollout = automation_limited_live_rollout_gate_service.disable_limited_live_rollout_allowance_for_fault(
        state,
        now=now,
        reason=reason,
    )
    if rollout and str(dict(rollout).get("status") or "").strip().lower() == "disabled":
        disabled.append("limited_live_rollout")
    cap = automation_limited_live_cap_expansion_gate_service.disable_limited_live_cap_expansion_allowance_for_fault(
        state,
        now=now,
        reason=reason,
    )
    if cap and str(dict(cap).get("status") or "").strip().lower() == "disabled":
        disabled.append("limited_live_cap_expansion")
    next_tier = automation_limited_live_next_tier_cap_gate_service.disable_limited_live_next_tier_cap_allowance_for_fault(
        state,
        now=now,
        reason=reason,
    )
    if next_tier and str(dict(next_tier).get("status") or "").strip().lower() == "disabled":
        disabled.append("limited_live_next_tier_cap")
    after = dict(state.get("runtime") or {})
    event = append_limited_live_approval_ledger_event(
        state,
        event_type="hard_fault_auto_rollback",
        detail="Runtime-only limited-live allowances disabled after hard safety fault.",
        actor=actor,
        now=now,
        reason=reason,
        disabled_allowances=disabled,
    )
    return serialize_value({"disabled_allowances": disabled, "event": event, "runtime_changed": before != after})
