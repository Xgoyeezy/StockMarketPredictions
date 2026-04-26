from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.services import automation_ai_review_service, notes_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

LIMITED_LIVE_CAP_EXPANSION_CANARY_NOTE_OWNER = "automation-ai"
LIMITED_LIVE_CAP_EXPANSION_CANARY_WINDOW_SESSIONS = 5
LIMITED_LIVE_CAP_EXPANSION_CANARY_REQUIRED_CLEAN_SESSIONS = 3
LIMITED_LIVE_CAP_EXPANSION_CANARY_STALE_AFTER_DAYS = 2
LIMITED_LIVE_CAP_EXPANSION_CANARY_HISTORY_LIMIT = 8
LIMITED_LIVE_CAP_EXPANSION_CANARY_NOTE_LIMIT = 250
LIMITED_LIVE_CAP_EXPANSION_CANARY_PAPER_PROFILE = "personal_paper"
LIMITED_LIVE_CAP_EXPANSION_CANARY_LIVE_PROFILE = "personal_live"
LIMITED_LIVE_CAP_EXPANSION_CANARY_BLOCK_SLIPPAGE_BPS = 100.0
LIMITED_LIVE_CAP_EXPANSION_CANARY_WARN_SLIPPAGE_BPS = 50.0

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE

LIMITED_LIVE_CAP_EXPANSION_CANARY_SETTINGS_DEFAULTS: dict[str, Any] = {
    "limited_live_cap_expansion_canary_enabled": True,
    "limited_live_cap_expansion_canary_auto_review_enabled": True,
    "limited_live_cap_expansion_canary_window_sessions": LIMITED_LIVE_CAP_EXPANSION_CANARY_WINDOW_SESSIONS,
    "limited_live_cap_expansion_canary_required_clean_sessions": LIMITED_LIVE_CAP_EXPANSION_CANARY_REQUIRED_CLEAN_SESSIONS,
    "limited_live_cap_expansion_canary_stale_after_days": LIMITED_LIVE_CAP_EXPANSION_CANARY_STALE_AFTER_DAYS,
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


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    return max(int(minimum), min(int(maximum), _coerce_int(value, default)))


def _profile_tag(profile_key: str) -> str:
    return "profile-" + str(profile_key or LIMITED_LIVE_CAP_EXPANSION_CANARY_PAPER_PROFILE).strip().lower().replace(":", "-")


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def _session_bounds_for_day(session_day: str) -> tuple[datetime, datetime]:
    local_day = date.fromisoformat(session_day)
    local_start = datetime.combine(local_day, time.min, tzinfo=MARKET_TIMEZONE)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def limited_live_cap_expansion_canary_session_day_for(
    value: datetime | None = None,
    *,
    forced: bool = False,
) -> tuple[str, bool]:
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


def next_eligible_limited_live_cap_expansion_canary_review_at(value: datetime | None = None) -> datetime:
    now = value or _utc_now()
    session_day, review_window_open = limited_live_cap_expansion_canary_session_day_for(now, forced=False)
    if review_window_open:
        return now
    return _review_time_for_session_day(session_day)


def next_eligible_limited_live_cap_expansion_canary_review_after_session(session_day: str) -> datetime:
    return _review_time_for_session_day(_next_trading_day_after(session_day))


def _recent_trading_days(now: datetime, *, count: int) -> list[str]:
    cursor = now.astimezone(MARKET_TIMEZONE).date()
    days: list[str] = []
    while len(days) < max(1, count):
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return days


def normalize_limited_live_cap_expansion_canary_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    window_sessions = _clamp_int(
        state.get("limited_live_cap_expansion_canary_window_sessions"),
        int(LIMITED_LIVE_CAP_EXPANSION_CANARY_SETTINGS_DEFAULTS["limited_live_cap_expansion_canary_window_sessions"]),
        minimum=1,
        maximum=20,
    )
    required_clean_sessions = _clamp_int(
        state.get("limited_live_cap_expansion_canary_required_clean_sessions"),
        int(LIMITED_LIVE_CAP_EXPANSION_CANARY_SETTINGS_DEFAULTS["limited_live_cap_expansion_canary_required_clean_sessions"]),
        minimum=1,
        maximum=window_sessions,
    )
    return {
        "limited_live_cap_expansion_canary_enabled": _coerce_bool(
            state.get("limited_live_cap_expansion_canary_enabled"),
            bool(LIMITED_LIVE_CAP_EXPANSION_CANARY_SETTINGS_DEFAULTS["limited_live_cap_expansion_canary_enabled"]),
        ),
        "limited_live_cap_expansion_canary_auto_review_enabled": _coerce_bool(
            state.get("limited_live_cap_expansion_canary_auto_review_enabled"),
            bool(LIMITED_LIVE_CAP_EXPANSION_CANARY_SETTINGS_DEFAULTS["limited_live_cap_expansion_canary_auto_review_enabled"]),
        ),
        "limited_live_cap_expansion_canary_window_sessions": window_sessions,
        "limited_live_cap_expansion_canary_required_clean_sessions": required_clean_sessions,
        "limited_live_cap_expansion_canary_stale_after_days": _clamp_int(
            state.get("limited_live_cap_expansion_canary_stale_after_days"),
            int(LIMITED_LIVE_CAP_EXPANSION_CANARY_SETTINGS_DEFAULTS["limited_live_cap_expansion_canary_stale_after_days"]),
            minimum=1,
            maximum=30,
        ),
    }


def normalize_limited_live_cap_expansion_canary_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("limited_live_cap_expansion_canary_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("limited_live_cap_expansion_canary_history") or [])[
            :LIMITED_LIVE_CAP_EXPANSION_CANARY_HISTORY_LIMIT
        ]
        if isinstance(item, dict)
    ]
    return {
        "limited_live_cap_expansion_canary_last_report": serialize_value(last_report),
        "limited_live_cap_expansion_canary_last_note_id": str(
            runtime.get("limited_live_cap_expansion_canary_last_note_id") or ""
        ).strip()
        or None,
        "limited_live_cap_expansion_canary_note_session_day": str(
            runtime.get("limited_live_cap_expansion_canary_note_session_day") or ""
        ).strip()
        or None,
        "limited_live_cap_expansion_canary_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("limited_live_cap_expansion_canary_last_run_at"))
        ),
        "limited_live_cap_expansion_canary_last_scheduled_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("limited_live_cap_expansion_canary_last_scheduled_run_at"))
        ),
        "limited_live_cap_expansion_canary_last_scheduled_session_day": str(
            runtime.get("limited_live_cap_expansion_canary_last_scheduled_session_day") or ""
        ).strip()
        or None,
        "limited_live_cap_expansion_canary_next_eligible_run_at": (
            _serialize_datetime(
                _parse_datetime(runtime.get("limited_live_cap_expansion_canary_next_eligible_run_at"))
            )
            or _serialize_datetime(next_eligible_limited_live_cap_expansion_canary_review_at())
        ),
        "limited_live_cap_expansion_canary_last_skipped_reason": str(
            runtime.get("limited_live_cap_expansion_canary_last_skipped_reason") or ""
        ).strip()
        or None,
        "limited_live_cap_expansion_canary_last_error": str(
            runtime.get("limited_live_cap_expansion_canary_last_error") or ""
        ).strip()
        or None,
        "limited_live_cap_expansion_canary_history": history,
    }


def build_limited_live_cap_expansion_canary_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = normalize_limited_live_cap_expansion_canary_runtime((state or {}).get("runtime"))
    settings = normalize_limited_live_cap_expansion_canary_settings((state or {}).get("settings"))
    report = dict(runtime.get("limited_live_cap_expansion_canary_last_report") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "enabled": settings["limited_live_cap_expansion_canary_enabled"],
            "auto_review_enabled": settings["limited_live_cap_expansion_canary_auto_review_enabled"],
            "clean_session_count": 0,
            "required_clean_sessions": settings["limited_live_cap_expansion_canary_required_clean_sessions"],
            "window_session_count": 0,
            "window_days": settings["limited_live_cap_expansion_canary_window_sessions"],
            "latest_gate_status": "missing",
            "latest_terminal_state": None,
            "current_max_notional": None,
            "expanded_max_notional": None,
            "consumed_order_count": 0,
            "broker_gate_status": "unknown",
            "broker_live_gate_status": "unknown",
            "safety_lock_status": "unknown",
            "state_control_status": "unknown",
            "pnl_summary": {"sample_count": 0, "realized_pnl": 0.0},
            "slippage_summary": {"sample_count": 0, "average_abs_bps": None, "worst_abs_bps": None},
            "note_coverage": {"covered": 0, "required": 0, "ratio": 0.0},
            "blockers": [],
            "warnings": [],
            "manual_action_required": False,
            "scheduled_status": "waiting",
            "related_note_id": runtime.get("limited_live_cap_expansion_canary_last_note_id"),
            "note_id": runtime.get("limited_live_cap_expansion_canary_last_note_id"),
            "last_run_at": runtime.get("limited_live_cap_expansion_canary_last_run_at"),
            "last_scheduled_run_at": runtime.get("limited_live_cap_expansion_canary_last_scheduled_run_at"),
            "last_scheduled_session_day": runtime.get("limited_live_cap_expansion_canary_last_scheduled_session_day"),
            "next_eligible_run_at": runtime.get("limited_live_cap_expansion_canary_next_eligible_run_at"),
            "skipped_reason": runtime.get("limited_live_cap_expansion_canary_last_skipped_reason"),
            "last_error": runtime.get("limited_live_cap_expansion_canary_last_error"),
            "sessions": [],
        }
    report.setdefault("enabled", settings["limited_live_cap_expansion_canary_enabled"])
    report.setdefault("auto_review_enabled", settings["limited_live_cap_expansion_canary_auto_review_enabled"])
    report.setdefault("window_days", settings["limited_live_cap_expansion_canary_window_sessions"])
    report.setdefault("required_clean_sessions", settings["limited_live_cap_expansion_canary_required_clean_sessions"])
    report.setdefault("stale_after_days", settings["limited_live_cap_expansion_canary_stale_after_days"])
    report.setdefault("related_note_id", runtime.get("limited_live_cap_expansion_canary_last_note_id"))
    report.setdefault("note_id", runtime.get("limited_live_cap_expansion_canary_last_note_id"))
    report.setdefault("last_run_at", runtime.get("limited_live_cap_expansion_canary_last_run_at"))
    report.setdefault("last_scheduled_run_at", runtime.get("limited_live_cap_expansion_canary_last_scheduled_run_at"))
    report.setdefault(
        "last_scheduled_session_day",
        runtime.get("limited_live_cap_expansion_canary_last_scheduled_session_day"),
    )
    report.setdefault("next_eligible_run_at", runtime.get("limited_live_cap_expansion_canary_next_eligible_run_at"))
    report.setdefault("skipped_reason", runtime.get("limited_live_cap_expansion_canary_last_skipped_reason"))
    report.setdefault("last_error", runtime.get("limited_live_cap_expansion_canary_last_error"))
    return serialize_value(report)


def _issue(
    key: str,
    detail: str,
    *,
    component: str = "limited_live_cap_expansion_canary",
    severity: str = "blocker",
) -> dict[str, Any]:
    return {"key": key, "component": component, "severity": severity, "detail": detail}


def _timestamp_in_session(row: dict[str, Any], session_day: str) -> bool:
    start_at, end_at = _session_bounds_for_day(session_day)
    for key in ("evaluated_at", "checked_at", "at", "created_at", "updated_at", "last_order_at"):
        parsed = _parse_datetime(row.get(key))
        if parsed is not None:
            return start_at <= parsed < end_at
    return False


def _runtime_gate_report_by_day(runtime: dict[str, Any], session_day: str) -> dict[str, Any]:
    last = runtime.get("limited_live_cap_expansion_gate_last_report")
    if isinstance(last, dict) and str(last.get("session_day") or "").strip() == session_day:
        return dict(last)
    for item in list(runtime.get("limited_live_cap_expansion_gate_history") or []):
        if not isinstance(item, dict):
            continue
        item_day = str(item.get("session_day") or "").strip()
        if not item_day and _timestamp_in_session(item, session_day):
            item_day = session_day
        if item_day == session_day:
            return dict(item)
    return {}


def _note_lookup(profile_key: str) -> dict[str, dict[str, Any]]:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIMITED_LIVE_CAP_EXPANSION_CANARY_NOTE_OWNER,
            limit=LIMITED_LIVE_CAP_EXPANSION_CANARY_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return {}
    by_day: dict[str, dict[str, Any]] = {}
    profile = _profile_tag(profile_key)
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if "automation-ai" not in tags or profile not in tags:
            continue
        session_tags = [tag for tag in tags if tag.startswith("session-")]
        if not session_tags:
            continue
        session_day = session_tags[0].replace("session-", "", 1)
        bucket = by_day.setdefault(session_day, {})
        if "limited-live-cap-expansion-canary" in tags:
            bucket["limited_live_cap_expansion_canary"] = item
        if "limited-live-cap-expansion-gate" in tags:
            bucket["limited_live_cap_expansion_gate"] = item
        if "limited-live-cap-expansion" in tags:
            bucket["limited_live_cap_expansion_report"] = item
        if "limited-live-rollout" in tags:
            bucket["limited_live_rollout"] = item
    return by_day


def _recent_order_events(
    db: Session | None,
    *,
    tenant: Tenant,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
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
        row_profile = str(payload.get("automation_profile_key") or payload.get("profile_key") or "").strip().lower()
        route_family = str(payload.get("route_family") or payload.get("source") or "").strip().lower()
        rollout_id = str(payload.get("limited_live_rollout_id") or "").strip()
        expansion_id = str(payload.get("limited_live_cap_expansion_id") or "").strip()
        next_tier_cap_id = str(payload.get("limited_live_next_tier_cap_id") or "").strip()
        entry_reason = str(payload.get("automation_entry_reason") or "").strip().lower()
        if row_profile and row_profile != LIMITED_LIVE_CAP_EXPANSION_CANARY_LIVE_PROFILE:
            continue
        if next_tier_cap_id or route_family == "limited_live_next_tier_cap" or entry_reason == "limited_live_next_tier_cap":
            continue
        if (
            not expansion_id
            and route_family != "limited_live_cap_expansion"
            and entry_reason != "limited_live_cap_expansion"
            and not rollout_id
        ):
            continue
        events.append(
            {
                "event_key": row.event_key,
                "status": row.status,
                "detail": row.detail,
                "order_type": row.order_type,
                "created_at": _serialize_datetime(row.created_at),
                "payload": payload,
            }
        )
    return events


def _row_value(row: Any, key: str) -> Any:
    try:
        return row.get(key)
    except AttributeError:
        return None


def _row_is_uncontrolled_live(row: Any) -> bool:
    profile = str(_row_value(row, "automation_profile_key") or "").strip().lower()
    intent = str(_row_value(row, "automation_execution_intent") or _row_value(row, "execution_intent") or "").strip().lower()
    if profile != LIMITED_LIVE_CAP_EXPANSION_CANARY_LIVE_PROFILE and intent != "broker_live":
        return False
    known_markers = (
        "live_pilot_soak_id",
        "live_pilot_expansion_id",
        "live_pilot_window_id",
        "limited_live_rollout_id",
        "limited_live_cap_expansion_id",
        "limited_live_next_tier_cap_id",
    )
    return not any(str(_row_value(row, key) or "").strip() for key in known_markers)


def _uncontrolled_live_ledger_counts() -> dict[str, int]:
    counts = {"pending": 0, "open": 0}
    try:
        pending = sdm.read_pending_orders()
        if pending is not None and not getattr(pending, "empty", True):
            counts["pending"] = sum(1 for _, row in pending.iterrows() if _row_is_uncontrolled_live(row))
    except Exception:
        counts["pending"] = 0
    try:
        open_rows = sdm.read_open_trades()
        if open_rows is not None and not getattr(open_rows, "empty", True):
            counts["open"] = sum(1 for _, row in open_rows.iterrows() if _row_is_uncontrolled_live(row))
    except Exception:
        counts["open"] = 0
    return counts


def _order_notional(order: dict[str, Any]) -> float | None:
    for key in ("notional", "estimated_notional", "submitted_notional", "order_notional"):
        if order.get(key) is not None:
            return abs(_coerce_float(order.get(key), 0.0))
    qty = order.get("qty") or order.get("quantity")
    price = order.get("limit_price") or order.get("submitted_price") or order.get("price")
    if qty is not None and price is not None:
        return abs(_coerce_float(qty, 0.0) * _coerce_float(price, 0.0))
    return None


def _event_notional(event: dict[str, Any]) -> float | None:
    payload = dict(event.get("payload") or {})
    request_payload = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    for source in (payload, request_payload):
        for key in ("notional", "estimated_notional", "submitted_notional"):
            if source.get(key) is not None:
                return abs(_coerce_float(source.get(key), 0.0))
        qty = source.get("qty") or source.get("quantity")
        price = source.get("limit_price") or source.get("price")
        if qty is not None and price is not None:
            return abs(_coerce_float(qty, 0.0) * _coerce_float(price, 0.0))
    return None


def _slippage_bps(item: dict[str, Any]) -> float | None:
    for key in ("slippage_bps", "slippage_bp", "average_slippage_bps"):
        if item.get(key) is not None:
            return _coerce_float(item.get(key), 0.0)
    payload = item.get("payload")
    if isinstance(payload, dict):
        for key in ("slippage_bps", "slippage_bp", "average_slippage_bps"):
            if payload.get(key) is not None:
                return _coerce_float(payload.get(key), 0.0)
    return None


def _realized_pnl(item: dict[str, Any]) -> float | None:
    for key in ("realized_pnl", "pnl", "realized_pnl_dollars"):
        if item.get(key) is not None:
            return _coerce_float(item.get(key), 0.0)
    payload = item.get("payload")
    if isinstance(payload, dict):
        for key in ("realized_pnl", "pnl", "realized_pnl_dollars"):
            if payload.get(key) is not None:
                return _coerce_float(payload.get(key), 0.0)
    return None


def _report_stale(report: dict[str, Any], now: datetime, stale_after_days: int) -> bool:
    evaluated_at = _parse_datetime(report.get("evaluated_at") or report.get("last_run_at"))
    if evaluated_at is None:
        return True
    return (now - evaluated_at).total_seconds() > max(1, stale_after_days) * 86400


def _terminal_state_from(order: dict[str, Any], events: list[dict[str, Any]]) -> str | None:
    status = str(order.get("broker_status") or order.get("status") or "").strip().lower()
    if status in {"filled", "canceled", "cancelled", "closed", "rejected", "expired"}:
        return "canceled" if status == "cancelled" else status
    for event in reversed(events):
        key = str(event.get("event_key") or "").strip().lower()
        if key in {"order.canceled", "order.cancelled"}:
            return "canceled"
        if key in {"order.closed", "trade.closed"}:
            return "closed"
        if key in {"order.filled", "trade.opened"}:
            return "filled"
    if _coerce_bool(order.get("position_opened"), False):
        return "filled"
    return None


def _session_report(
    *,
    session_day: str,
    gate_item: dict[str, Any],
    cap_report: dict[str, Any],
    notes: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    gate_status = str(gate_item.get("status") or "").strip().lower()
    evidence = dict(gate_item.get("candidate_order_evidence") or {})
    orders = [item for item in list(evidence.get("orders") or []) if isinstance(item, dict)]
    consumed_count = max(_coerce_int(gate_item.get("consumed_order_count"), 0), len(orders))
    caps = dict(gate_item.get("caps") or {})
    max_orders = _coerce_int(caps.get("max_session_orders") or gate_item.get("max_session_orders"), 1)
    expanded_cap = _coerce_float(
        caps.get("expanded_max_notional")
        or gate_item.get("expanded_max_notional")
        or gate_item.get("max_notional"),
        250.0,
    )
    current_cap = _coerce_float(caps.get("current_max_notional") or gate_item.get("current_max_notional"), 100.0)
    require_limit = _coerce_bool(caps.get("require_limit"), True)
    order_events = [item for item in events if str(item.get("event_key") or "").strip().lower().startswith("order.")]
    failed_events = [
        item
        for item in events
        if str(item.get("status") or "").strip().lower() in {"failed", "error", "rejected", "blocked"}
    ]
    non_limit_orders = [item for item in orders if str(item.get("order_type") or "").strip().lower() not in {"", "limit"}]
    non_limit_events = [
        item
        for item in order_events
        if str(item.get("order_type") or "").strip().lower() not in {"", "limit"}
    ]
    order_notionals = [value for value in [_order_notional(item) for item in orders] if value is not None]
    order_notionals.extend(value for value in [_event_notional(item) for item in order_events] if value is not None)
    terminal_state = None
    for order in orders:
        terminal_state = terminal_state or _terminal_state_from(order, order_events)
    terminal_state = terminal_state or _terminal_state_from({}, order_events)
    reconciliation_status = str(
        gate_item.get("reconciliation_status")
        or evidence.get("reconciliation_status")
        or ("clean" if consumed_count and not failed_events else "missing")
    ).strip().lower()
    note_id = (
        str(gate_item.get("related_note_id") or gate_item.get("note_id") or "").strip()
        or str((notes.get("limited_live_cap_expansion_gate") or {}).get("id") or "").strip()
        or None
    )

    if not gate_status:
        blockers.append(_issue("cap_expansion_gate_missing", "No limited-live cap expansion gate evidence exists for this session."))
    elif gate_status in {"blocked", "failed", "fail"} or gate_item.get("blockers"):
        blockers.append(_issue("cap_expansion_gate_blocked", "Limited-live cap expansion gate evidence has unresolved blockers."))
    elif gate_status == "warning" or gate_item.get("warnings"):
        warnings.append(_issue("cap_expansion_gate_warning", "Limited-live cap expansion gate evidence has warnings.", severity="warning"))
    if consumed_count <= 0:
        blockers.append(_issue("no_expanded_cap_order_evidence", "No expanded-cap live order was consumed during this session."))
    if max_orders > 0 and consumed_count > max_orders:
        blockers.append(_issue("session_order_cap_breached", "Consumed expanded-cap order count exceeded the configured session cap."))
    if require_limit and (non_limit_orders or non_limit_events):
        blockers.append(_issue("non_limit_order_evidence", "Expanded-cap evidence includes a non-limit order."))
    if order_notionals and max(order_notionals) > expanded_cap + 0.01:
        blockers.append(_issue("expanded_notional_cap_breached", "Expanded-cap evidence exceeds the configured notional cap."))
    if consumed_count > 0 and not terminal_state:
        blockers.append(_issue("terminal_evidence_missing", "Consumed expanded-cap live order evidence is missing terminal broker/local state."))
    if failed_events:
        blockers.append(_issue("order_event_failed", "Expanded-cap order events include failed, blocked, or rejected status.", component="order_events"))
    expires_at = _parse_datetime(gate_item.get("expansion_expires_at"))
    if expires_at is not None:
        late_events = [
            item
            for item in order_events
            if (_parse_datetime(item.get("created_at")) or expires_at) > expires_at
        ]
        if late_events:
            blockers.append(_issue("expired_allowance_use", "Expanded-cap order evidence appears after the allowance expiry.", component="order_events"))
    if reconciliation_status in {"blocked", "failed", "fail", "mismatch", "mismatched"}:
        blockers.append(_issue("reconciliation_blocked", "Broker/local reconciliation evidence is blocked or mismatched.", component="reconciliation"))
    elif reconciliation_status in {"missing", "unknown", "unavailable"}:
        warnings.append(_issue("reconciliation_unavailable", "Clean broker/local reconciliation evidence was not available.", component="reconciliation", severity="warning"))
    if not note_id:
        blockers.append(_issue("cap_expansion_note_missing", "Expanded-cap session is missing a linked automation-ai note.", component="notes"))

    slippage_values = []
    pnl_values = []
    for item in orders + events:
        slip = _slippage_bps(item)
        pnl = _realized_pnl(item)
        if slip is not None:
            slippage_values.append(abs(float(slip)))
        if pnl is not None:
            pnl_values.append(float(pnl))
    worst_slippage = max(slippage_values) if slippage_values else None
    if worst_slippage is not None and worst_slippage > LIMITED_LIVE_CAP_EXPANSION_CANARY_BLOCK_SLIPPAGE_BPS:
        blockers.append(
            _issue(
                "slippage_blocked",
                f"Expanded-cap worst slippage was {worst_slippage:.1f} bps.",
                component="slippage",
            )
        )
    elif worst_slippage is not None and worst_slippage > LIMITED_LIVE_CAP_EXPANSION_CANARY_WARN_SLIPPAGE_BPS:
        warnings.append(
            _issue(
                "slippage_warning",
                f"Expanded-cap worst slippage was {worst_slippage:.1f} bps.",
                component="slippage",
                severity="warning",
            )
        )
    if sum(pnl_values) < 0:
        blockers.append(_issue("negative_pnl", "Expanded-cap realized PnL is negative.", component="pnl"))

    cap_report_status = str(cap_report.get("status") or "").strip().lower() or "missing"
    if cap_report_status != "ready_to_request_cap_expansion":
        blockers.append(_issue("cap_expansion_report_not_ready", "Limited-live cap expansion report is not ready.", component="limited_live_cap_expansion_report"))

    status = "clean" if not blockers and not warnings else "warning" if not blockers else "blocked"
    return serialize_value(
        {
            "session_day": session_day,
            "status": status,
            "clean": status == "clean",
            "limited_live_cap_expansion_gate": gate_item,
            "limited_live_cap_expansion_report": cap_report,
            "notes": notes,
            "note_id": note_id,
            "consumed_order_count": consumed_count,
            "max_session_orders": max_orders,
            "current_max_notional": current_cap,
            "expanded_max_notional": expanded_cap,
            "terminal_state": terminal_state,
            "reconciliation_status": reconciliation_status,
            "order_events": {
                "count": len(order_events),
                "failed_count": len(failed_events),
                "non_limit_count": len(non_limit_events),
            },
            "orders": serialize_value(orders),
            "pnl_values": pnl_values,
            "slippage_values": slippage_values,
            "blockers": blockers,
            "warnings": warnings,
        }
    )


def _summary_from_sessions(sessions: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    pnl_values = [float(value) for item in sessions for value in list(item.get("pnl_values") or [])]
    slippage_values = [abs(float(value)) for item in sessions for value in list(item.get("slippage_values") or [])]
    pnl_summary = {
        "sample_count": len(pnl_values),
        "realized_pnl": float(sum(pnl_values)) if pnl_values else 0.0,
    }
    slippage_summary = {
        "sample_count": len(slippage_values),
        "average_abs_bps": float(sum(slippage_values) / len(slippage_values)) if slippage_values else None,
        "worst_abs_bps": float(max(slippage_values)) if slippage_values else None,
    }
    return pnl_summary, slippage_summary


def _aggregate_report(
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None,
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None,
    db: Session | None,
    now: datetime,
    review_session_day: str | None = None,
) -> dict[str, Any]:
    runtime = dict(paper_state.get("runtime") or {})
    settings = normalize_limited_live_cap_expansion_canary_settings(paper_state.get("settings"))
    live_state = live_state if isinstance(live_state, dict) else {}
    live_settings = dict(live_state.get("settings") or {})
    current_day = str(review_session_day or "").strip() or _session_day_for(now)
    window_days = int(settings["limited_live_cap_expansion_canary_window_sessions"])
    required_clean_sessions = int(settings["limited_live_cap_expansion_canary_required_clean_sessions"])
    stale_after_days = int(settings["limited_live_cap_expansion_canary_stale_after_days"])
    session_days = _recent_trading_days(now, count=window_days)
    notes_by_day = _note_lookup(profile_key)
    cap_report = dict(runtime.get("limited_live_cap_expansion_report_last_report") or {})
    rollout_canary = dict(runtime.get("limited_live_rollout_canary_last_report") or {})
    rollout_gate = dict(runtime.get("limited_live_rollout_gate_last_report") or {})
    state_control = dict(runtime.get("state_control_last_evaluation") or {})
    state_control_status = str(runtime.get("state_control_state") or state_control.get("state") or "unknown").strip().lower()

    sessions: list[dict[str, Any]] = []
    for session_day in session_days:
        gate_item = _runtime_gate_report_by_day(runtime, session_day)
        notes = notes_by_day.get(session_day, {})
        start_at, end_at = _session_bounds_for_day(session_day)
        events = _recent_order_events(db, tenant=tenant, start_at=start_at, end_at=end_at)
        if gate_item or events or notes:
            sessions.append(
                _session_report(
                    session_day=session_day,
                    gate_item=gate_item,
                    cap_report=cap_report,
                    notes=notes,
                    events=events,
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
    if profile_key != LIMITED_LIVE_CAP_EXPANSION_CANARY_PAPER_PROFILE:
        blockers.insert(0, _issue("paper_profile_required", "V1 expanded-cap canary is reviewed from the personal paper automation profile."))
    if not sessions:
        blockers.append(_issue("no_cap_expansion_sessions", "No limited-live cap expansion evidence was found in the configured trading-day window."))

    cap_report_status = str(cap_report.get("status") or "").strip().lower() or "missing"
    broker_gate_status = str(cap_report.get("broker_live_gate_status") or cap_report.get("broker_gate_status") or "unknown").strip().lower()
    safety_lock_status = str(cap_report.get("safety_lock_status") or "unknown").strip().lower()
    if cap_report_status in {"missing", "not_run"}:
        blockers.append(_issue("cap_expansion_report_missing", "Run the limited-live cap expansion report before the expanded-cap canary.", component="limited_live_cap_expansion_report"))
    elif cap_report_status != "ready_to_request_cap_expansion":
        blockers.append(_issue("cap_expansion_report_not_ready", "Limited-live cap expansion report is not ready.", component="limited_live_cap_expansion_report"))
    elif _report_stale(cap_report, now, stale_after_days):
        blockers.append(_issue("cap_expansion_report_stale", "Limited-live cap expansion report evidence is stale.", component="limited_live_cap_expansion_report"))
    if cap_report.get("blockers"):
        blockers.append(_issue("cap_expansion_report_blocked", "Limited-live cap expansion report has unresolved blockers.", component="limited_live_cap_expansion_report"))
    if not str(cap_report.get("related_note_id") or cap_report.get("note_id") or "").strip():
        blockers.append(_issue("cap_expansion_report_note_missing", "Limited-live cap expansion report is missing a linked automation-ai note.", component="notes"))
    if broker_gate_status != "open":
        blockers.append(_issue("broker_live_gate_not_open", "Broker-live gate is not open.", component="broker_live_gate"))
    if safety_lock_status not in {"clear", "none"}:
        blockers.append(_issue("safety_lock_active", "A paper or live safety lock is active.", component="safety_lock"))
    if _coerce_bool(dict(paper_state.get("settings") or {}).get("kill_switch"), False) or _coerce_bool(live_settings.get("kill_switch"), False):
        blockers.append(_issue("kill_switch_active", "A paper or live kill switch is active.", component="safety_lock"))
    if state_control_status == "halt" or _coerce_bool(runtime.get("state_control_halt_active"), False):
        blockers.append(_issue("state_control_halt", "State-control is halted.", component="state_control"))

    if str(rollout_canary.get("status") or "").strip().lower() == "blocked" or rollout_canary.get("blockers"):
        blockers.append(_issue("rollout_canary_blocked", "Limited-live rollout canary has unresolved blocker evidence.", component="limited_live_rollout_canary"))
    if str(rollout_gate.get("status") or "").strip().lower() == "blocked" or rollout_gate.get("blockers"):
        blockers.append(_issue("rollout_gate_blocked", "Base limited-live rollout gate has unresolved blocker evidence.", component="limited_live_rollout"))

    ledger_counts = _uncontrolled_live_ledger_counts()
    if ledger_counts["pending"] or ledger_counts["open"]:
        blockers.append(
            _issue(
                "uncontrolled_live_exposure",
                "Existing uncontrolled live order or position evidence is present.",
                component="broker_live_gate",
            )
        )

    clean_count = sum(1 for item in sessions if item.get("clean"))
    latest_session = sessions[0] if sessions else {}
    latest_gate = dict(latest_session.get("limited_live_cap_expansion_gate") or {})
    pnl_summary, slippage_summary = _summary_from_sessions(sessions)
    note_covered = sum(1 for item in sessions if item.get("note_id"))
    total_consumed = sum(_coerce_int(item.get("consumed_order_count"), 0) for item in sessions)
    status = "ready_for_operator_review" if clean_count >= required_clean_sessions and not blockers else "collecting"
    if blockers:
        status = "blocked"
    elif warnings:
        status = "warning"
    label = {
        "ready_for_operator_review": "Limited-live cap expansion canary ready for operator review",
        "collecting": "Collecting limited-live cap expansion canary evidence",
        "warning": "Limited-live cap expansion canary warning",
        "blocked": "Limited-live cap expansion canary blocked",
    }.get(status, "Limited-live cap expansion canary")
    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": profile_key,
            "live_profile_key": LIMITED_LIVE_CAP_EXPANSION_CANARY_LIVE_PROFILE,
            "linked_account_id": getattr(linked_account, "id", None),
            "session_day": current_day,
            "evaluated_at": _serialize_datetime(now),
            "window_days": window_days,
            "window_sessions": session_days,
            "window_session_count": len(sessions),
            "clean_session_count": clean_count,
            "required_clean_sessions": required_clean_sessions,
            "stale_after_days": stale_after_days,
            "evidence_window": {
                "start_session_day": session_days[-1] if session_days else current_day,
                "end_session_day": session_days[0] if session_days else current_day,
                "session_days": session_days,
                "configured_session_count": window_days,
                "evidence_session_count": len(sessions),
                "required_clean_sessions": required_clean_sessions,
            },
            "latest_gate_status": latest_gate.get("status") or "missing",
            "latest_rollout_status": latest_gate.get("status") or "missing",
            "latest_terminal_state": latest_session.get("terminal_state"),
            "latest_reconciliation_status": latest_session.get("reconciliation_status") or "missing",
            "latest_broker_order_id": (latest_session.get("orders") or [{}])[0].get("broker_order_id") if latest_session.get("orders") else None,
            "latest_local_order_id": (latest_session.get("orders") or [{}])[0].get("order_id") if latest_session.get("orders") else None,
            "current_max_notional": latest_session.get("current_max_notional") or cap_report.get("current_max_notional"),
            "expanded_max_notional": latest_session.get("expanded_max_notional")
            or cap_report.get("recommended_next_max_notional")
            or cap_report.get("target_max_notional"),
            "consumed_order_count": total_consumed,
            "broker_gate_status": broker_gate_status,
            "broker_live_gate_status": broker_gate_status,
            "safety_lock_status": safety_lock_status,
            "state_control_status": state_control_status,
            "cap_expansion_report_status": cap_report_status,
            "rollout_canary_status": str(rollout_canary.get("status") or "missing").strip().lower() or "missing",
            "uncontrolled_live_ledger_counts": ledger_counts,
            "pnl_summary": pnl_summary,
            "slippage_summary": slippage_summary,
            "note_coverage": {
                "covered": note_covered,
                "required": len(sessions),
                "ratio": float(note_covered / len(sessions)) if sessions else 0.0,
            },
            "blockers": blockers[:24],
            "warnings": warnings[:24],
            "manual_action_required": bool(blockers or warnings),
            "sessions": sessions,
        }
    )


def _find_existing_canary_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIMITED_LIVE_CAP_EXPANSION_CANARY_NOTE_OWNER,
            limit=LIMITED_LIVE_CAP_EXPANSION_CANARY_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "limited-live-cap-expansion-canary",
        "limited-live-cap-expansion-gate",
        "limited-live-rollout",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    pnl = dict(report.get("pnl_summary") or {})
    slippage = dict(report.get("slippage_summary") or {})
    evidence_window = dict(report.get("evidence_window") or {})
    lines = [
        f"Automation limited-live cap expansion canary for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Run source: {str(report.get('run_source') or 'manual').replace('_', ' ')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Clean sessions: {report.get('clean_session_count', 0)} / {report.get('required_clean_sessions', 0)} required",
        f"Window sessions with evidence: {report.get('window_session_count', 0)}",
        (
            f"Evidence window: {evidence_window.get('start_session_day')} through {evidence_window.get('end_session_day')}"
            if evidence_window
            else f"Evidence window: {report.get('window_days', LIMITED_LIVE_CAP_EXPANSION_CANARY_WINDOW_SESSIONS)} trading session(s)"
        ),
        f"Latest gate: {report.get('latest_gate_status') or 'missing'}",
        f"Latest terminal state: {report.get('latest_terminal_state') or '--'}",
        f"Latest reconciliation: {report.get('latest_reconciliation_status') or 'missing'}",
        f"Current cap: ${float(report.get('current_max_notional') or 0.0):.2f}",
        f"Expanded cap: ${float(report.get('expanded_max_notional') or 0.0):.2f}",
        f"Consumed orders: {report.get('consumed_order_count') or 0}",
        f"Broker-live gate: {report.get('broker_gate_status') or report.get('broker_live_gate_status') or 'unknown'}",
        f"Safety locks: {report.get('safety_lock_status') or 'unknown'}",
        f"State control: {report.get('state_control_status') or 'unknown'}",
        f"Realized PnL samples: {pnl.get('sample_count', 0)} | total ${float(pnl.get('realized_pnl') or 0.0):.2f}",
        f"Slippage samples: {slippage.get('sample_count', 0)} | worst {slippage.get('worst_abs_bps') if slippage.get('worst_abs_bps') is not None else '--'} bps",
        (
            f"Next scheduled review: {report.get('next_eligible_run_at')}"
            if report.get("next_eligible_run_at")
            else "Next scheduled review: not available"
        ),
        "",
        "This canary is advisory. It does not place, cancel, or close orders, enable live trading, arm automation, clear locks, tune baseline settings, activate or rollback allowances, increase caps, or change broker-live gates.",
        "",
        "Blockers",
    ]
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    if blockers:
        lines.extend(f"- {item.get('session_day', report.get('session_day'))}: {item.get('key')}. {item.get('detail')}" for item in blockers[:16])
    else:
        lines.append("- None.")
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    lines.extend(["", "Warnings"])
    if warnings:
        lines.extend(f"- {item.get('session_day', report.get('session_day'))}: {item.get('key')}. {item.get('detail')}" for item in warnings[:16])
    else:
        lines.append("- None.")
    lines.extend(["", "Sessions"])
    for item in list(report.get("sessions") or [])[: _coerce_int(report.get("window_days"), LIMITED_LIVE_CAP_EXPANSION_CANARY_WINDOW_SESSIONS)]:
        gate = dict(item.get("limited_live_cap_expansion_gate") or {})
        events = dict(item.get("order_events") or {})
        lines.append(
            f"- {item.get('session_day')}: {str(item.get('status') or '').upper()} | "
            f"gate {gate.get('status') or 'missing'} | "
            f"terminal {item.get('terminal_state') or '--'} | "
            f"reconcile {item.get('reconciliation_status') or 'missing'} | "
            f"orders {item.get('consumed_order_count') or 0} | "
            f"events {events.get('count', 0)}"
        )
    return "\n".join(lines).strip()


def _sync_canary_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "limited-live-cap-expansion-canary",
        "limited-live-cap-expansion-gate",
        "limited-live-rollout",
        _profile_tag(profile_key),
        _profile_tag(LIMITED_LIVE_CAP_EXPANSION_CANARY_LIVE_PROFILE),
        f"session-{session_day}",
    ]
    title = f"Automation limited-live cap expansion canary - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = (
        str(report.get("related_note_id") or report.get("note_id") or "").strip()
        or _find_existing_canary_note_id(profile_key, session_day)
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": LIMITED_LIVE_CAP_EXPANSION_CANARY_NOTE_OWNER,
        "note_type": "risk_review",
        "priority": "high" if report.get("blockers") else "medium",
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


def run_limited_live_cap_expansion_canary_review(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    profile_key: str = LIMITED_LIVE_CAP_EXPANSION_CANARY_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_run_source = str(run_source or "manual").strip().lower().replace(" ", "_") or "manual"
    review_session_day, _review_window_open = limited_live_cap_expansion_canary_session_day_for(
        now,
        forced=normalized_run_source != "scheduled",
    )
    original_settings = deepcopy(dict(paper_state.get("settings") or {}))
    original_live_settings = deepcopy(dict((live_state or {}).get("settings") or {}))
    protected_before = {
        "paper": {key: original_settings.get(key) for key in ("enabled", "armed", "kill_switch", "execution_intent")},
        "live": {key: original_live_settings.get(key) for key in ("enabled", "armed", "kill_switch", "execution_intent")},
        "approval": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_cap_expansion_gate_approval") or {})),
        "allowance": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_cap_expansion_gate_allowance") or {})),
    }
    report = _aggregate_report(
        tenant=tenant,
        paper_state=paper_state,
        live_state=live_state,
        profile_key=profile_key,
        linked_account=linked_account,
        db=db,
        now=now,
        review_session_day=review_session_day,
    )
    runtime_before = dict(paper_state.get("runtime") or {})
    report["run_source"] = normalized_run_source
    report["skipped_reason"] = None
    report["last_scheduled_run_at"] = (
        report.get("evaluated_at")
        if normalized_run_source == "scheduled"
        else _serialize_datetime(
            _parse_datetime(runtime_before.get("limited_live_cap_expansion_canary_last_scheduled_run_at"))
        )
    )
    report["last_scheduled_session_day"] = (
        review_session_day
        if normalized_run_source == "scheduled"
        else str(runtime_before.get("limited_live_cap_expansion_canary_last_scheduled_session_day") or "").strip() or None
    )
    next_eligible = (
        next_eligible_limited_live_cap_expansion_canary_review_after_session(review_session_day)
        if normalized_run_source == "scheduled"
        else next_eligible_limited_live_cap_expansion_canary_review_at(now)
    )
    report["next_eligible_run_at"] = _serialize_datetime(next_eligible)
    report["scheduled_status"] = "reviewed" if normalized_run_source == "scheduled" else "manual"
    note_id = _sync_canary_note(tenant=tenant, profile_key=profile_key, report=report)
    if note_id:
        report["note_id"] = note_id
        report["related_note_id"] = note_id
    protected_after = {
        "paper": {
            key: dict(paper_state.get("settings") or {}).get(key)
            for key in ("enabled", "armed", "kill_switch", "execution_intent")
        },
        "live": {
            key: dict((live_state or {}).get("settings") or {}).get(key)
            for key in ("enabled", "armed", "kill_switch", "execution_intent")
        },
        "approval": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_cap_expansion_gate_approval") or {})),
        "allowance": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_cap_expansion_gate_allowance") or {})),
    }
    report["baseline_settings_mutated"] = protected_before["paper"] != protected_after["paper"] or protected_before["live"] != protected_after["live"]
    report["allowance_mutated"] = protected_before["approval"] != protected_after["approval"] or protected_before["allowance"] != protected_after["allowance"]

    runtime = paper_state.setdefault("runtime", {})
    summary_keys = {
        "status",
        "label",
        "profile_key",
        "live_profile_key",
        "linked_account_id",
        "session_day",
        "evaluated_at",
        "window_days",
        "window_sessions",
        "window_session_count",
        "clean_session_count",
        "required_clean_sessions",
        "stale_after_days",
        "evidence_window",
        "latest_gate_status",
        "latest_rollout_status",
        "latest_terminal_state",
        "latest_reconciliation_status",
        "latest_broker_order_id",
        "latest_local_order_id",
        "current_max_notional",
        "expanded_max_notional",
        "consumed_order_count",
        "broker_gate_status",
        "broker_live_gate_status",
        "safety_lock_status",
        "state_control_status",
        "cap_expansion_report_status",
        "rollout_canary_status",
        "uncontrolled_live_ledger_counts",
        "pnl_summary",
        "slippage_summary",
        "note_coverage",
        "blockers",
        "warnings",
        "manual_action_required",
        "sessions",
        "note_id",
        "related_note_id",
        "baseline_settings_mutated",
        "allowance_mutated",
        "run_source",
        "skipped_reason",
        "scheduled_status",
        "last_scheduled_run_at",
        "last_scheduled_session_day",
        "next_eligible_run_at",
    }
    summary = {key: report.get(key) for key in summary_keys if key in report}
    runtime["limited_live_cap_expansion_canary_last_report"] = serialize_value(summary)
    runtime["limited_live_cap_expansion_canary_last_note_id"] = note_id
    runtime["limited_live_cap_expansion_canary_note_session_day"] = report.get("session_day")
    runtime["limited_live_cap_expansion_canary_last_run_at"] = report.get("evaluated_at")
    if normalized_run_source == "scheduled":
        runtime["limited_live_cap_expansion_canary_last_scheduled_run_at"] = report.get("evaluated_at")
        runtime["limited_live_cap_expansion_canary_last_scheduled_session_day"] = review_session_day
    runtime["limited_live_cap_expansion_canary_next_eligible_run_at"] = report.get("next_eligible_run_at")
    runtime["limited_live_cap_expansion_canary_last_skipped_reason"] = None
    runtime["limited_live_cap_expansion_canary_last_error"] = None
    history = list(runtime.get("limited_live_cap_expansion_canary_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "clean_session_count": report.get("clean_session_count"),
            "required_clean_sessions": report.get("required_clean_sessions"),
            "window_session_count": report.get("window_session_count"),
            "current_max_notional": report.get("current_max_notional"),
            "expanded_max_notional": report.get("expanded_max_notional"),
            "consumed_order_count": report.get("consumed_order_count"),
            "blocker_count": len(report.get("blockers") or []),
            "warning_count": len(report.get("warnings") or []),
            "note_id": note_id,
            "run_source": normalized_run_source,
        },
    )
    runtime["limited_live_cap_expansion_canary_history"] = serialize_value(
        history[:LIMITED_LIVE_CAP_EXPANSION_CANARY_HISTORY_LIMIT]
    )
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.limited_live_cap_expansion_canary_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "live_profile_key": LIMITED_LIVE_CAP_EXPANSION_CANARY_LIVE_PROFILE,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "clean_session_count": report.get("clean_session_count"),
                "required_clean_sessions": report.get("required_clean_sessions"),
                "window_session_count": report.get("window_session_count"),
                "consumed_order_count": report.get("consumed_order_count"),
                "current_max_notional": report.get("current_max_notional"),
                "expanded_max_notional": report.get("expanded_max_notional"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
                "note_id": note_id,
                "run_source": normalized_run_source,
                "baseline_settings_mutated": report.get("baseline_settings_mutated"),
                "allowance_mutated": report.get("allowance_mutated"),
            },
        )
    return serialize_value(report)
