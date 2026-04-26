from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.services import automation_ai_review_service, notes_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

LIVE_PILOT_WINDOW_CANARY_NOTE_OWNER = "automation-ai"
LIVE_PILOT_WINDOW_CANARY_WINDOW_SESSIONS = 5
LIVE_PILOT_WINDOW_CANARY_REQUIRED_CLEAN_SESSIONS = 3
LIVE_PILOT_WINDOW_CANARY_HISTORY_LIMIT = 8
LIVE_PILOT_WINDOW_CANARY_NOTE_LIMIT = 250
LIVE_PILOT_WINDOW_CANARY_PAPER_PROFILE = "personal_paper"
LIVE_PILOT_WINDOW_CANARY_LIVE_PROFILE = "personal_live"
LIVE_PILOT_WINDOW_CANARY_BLOCK_SLIPPAGE_BPS = 100.0
LIVE_PILOT_WINDOW_CANARY_WARN_SLIPPAGE_BPS = 50.0

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE

LIVE_PILOT_WINDOW_CANARY_SETTINGS_DEFAULTS: dict[str, Any] = {
    "live_pilot_window_canary_enabled": True,
    "live_pilot_window_canary_auto_review_enabled": True,
    "live_pilot_window_canary_window_sessions": LIVE_PILOT_WINDOW_CANARY_WINDOW_SESSIONS,
    "live_pilot_window_canary_required_clean_sessions": LIVE_PILOT_WINDOW_CANARY_REQUIRED_CLEAN_SESSIONS,
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


def _clamp_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    return max(int(minimum), min(int(maximum), _coerce_int(value, default)))


def _profile_tag(profile_key: str) -> str:
    return "profile-" + str(profile_key or LIVE_PILOT_WINDOW_CANARY_PAPER_PROFILE).strip().lower().replace(":", "-")


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def _session_bounds_for_day(session_day: str) -> tuple[datetime, datetime]:
    local_day = date.fromisoformat(session_day)
    local_start = datetime.combine(local_day, time.min, tzinfo=MARKET_TIMEZONE)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def live_pilot_window_canary_session_day_for(
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


def next_eligible_live_pilot_window_canary_review_at(value: datetime | None = None) -> datetime:
    now = value or _utc_now()
    session_day, review_window_open = live_pilot_window_canary_session_day_for(now, forced=False)
    if review_window_open:
        return now
    return _review_time_for_session_day(session_day)


def next_eligible_live_pilot_window_canary_review_after_session(session_day: str) -> datetime:
    return _review_time_for_session_day(_next_trading_day_after(session_day))


def _recent_trading_days(now: datetime, *, count: int) -> list[str]:
    cursor = now.astimezone(MARKET_TIMEZONE).date()
    days: list[str] = []
    while len(days) < max(1, count):
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return days


def normalize_live_pilot_window_canary_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    window_sessions = _clamp_int(
        state.get("live_pilot_window_canary_window_sessions"),
        int(LIVE_PILOT_WINDOW_CANARY_SETTINGS_DEFAULTS["live_pilot_window_canary_window_sessions"]),
        minimum=1,
        maximum=20,
    )
    required_clean_sessions = _clamp_int(
        state.get("live_pilot_window_canary_required_clean_sessions"),
        int(LIVE_PILOT_WINDOW_CANARY_SETTINGS_DEFAULTS["live_pilot_window_canary_required_clean_sessions"]),
        minimum=1,
        maximum=window_sessions,
    )
    return {
        "live_pilot_window_canary_enabled": _coerce_bool(
            state.get("live_pilot_window_canary_enabled"),
            bool(LIVE_PILOT_WINDOW_CANARY_SETTINGS_DEFAULTS["live_pilot_window_canary_enabled"]),
        ),
        "live_pilot_window_canary_auto_review_enabled": _coerce_bool(
            state.get("live_pilot_window_canary_auto_review_enabled"),
            bool(LIVE_PILOT_WINDOW_CANARY_SETTINGS_DEFAULTS["live_pilot_window_canary_auto_review_enabled"]),
        ),
        "live_pilot_window_canary_window_sessions": window_sessions,
        "live_pilot_window_canary_required_clean_sessions": required_clean_sessions,
    }


def normalize_live_pilot_window_canary_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("live_pilot_window_canary_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("live_pilot_window_canary_history") or [])[:LIVE_PILOT_WINDOW_CANARY_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "live_pilot_window_canary_last_report": serialize_value(last_report),
        "live_pilot_window_canary_last_note_id": str(
            runtime.get("live_pilot_window_canary_last_note_id") or ""
        ).strip()
        or None,
        "live_pilot_window_canary_note_session_day": str(
            runtime.get("live_pilot_window_canary_note_session_day") or ""
        ).strip()
        or None,
        "live_pilot_window_canary_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("live_pilot_window_canary_last_run_at"))
        ),
        "live_pilot_window_canary_last_scheduled_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("live_pilot_window_canary_last_scheduled_run_at"))
        ),
        "live_pilot_window_canary_last_scheduled_session_day": str(
            runtime.get("live_pilot_window_canary_last_scheduled_session_day") or ""
        ).strip()
        or None,
        "live_pilot_window_canary_next_eligible_run_at": (
            _serialize_datetime(_parse_datetime(runtime.get("live_pilot_window_canary_next_eligible_run_at")))
            or _serialize_datetime(next_eligible_live_pilot_window_canary_review_at())
        ),
        "live_pilot_window_canary_last_skipped_reason": str(
            runtime.get("live_pilot_window_canary_last_skipped_reason") or ""
        ).strip()
        or None,
        "live_pilot_window_canary_last_error": str(
            runtime.get("live_pilot_window_canary_last_error") or ""
        ).strip()
        or None,
        "live_pilot_window_canary_history": history,
    }


def build_live_pilot_window_canary_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = normalize_live_pilot_window_canary_runtime((state or {}).get("runtime"))
    settings = normalize_live_pilot_window_canary_settings((state or {}).get("settings"))
    report = dict(runtime.get("live_pilot_window_canary_last_report") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "enabled": settings["live_pilot_window_canary_enabled"],
            "auto_review_enabled": settings["live_pilot_window_canary_auto_review_enabled"],
            "clean_session_count": 0,
            "required_clean_sessions": settings["live_pilot_window_canary_required_clean_sessions"],
            "window_session_count": 0,
            "window_days": settings["live_pilot_window_canary_window_sessions"],
            "latest_window_status": "missing",
            "latest_terminal_state": None,
            "latest_reconciliation_status": "missing",
            "live_readiness_status": "missing",
            "broker_live_gate_status": "unknown",
            "safety_lock_status": "unknown",
            "candidate_evidence": {},
            "pnl_summary": {"sample_count": 0, "realized_pnl": 0.0},
            "slippage_summary": {"sample_count": 0, "average_abs_bps": None, "worst_abs_bps": None},
            "note_coverage": {"covered": 0, "required": 0, "ratio": 0.0},
            "blockers": [],
            "warnings": [],
            "manual_action_required": False,
            "related_note_id": runtime.get("live_pilot_window_canary_last_note_id"),
            "note_id": runtime.get("live_pilot_window_canary_last_note_id"),
            "last_run_at": runtime.get("live_pilot_window_canary_last_run_at"),
            "last_scheduled_run_at": runtime.get("live_pilot_window_canary_last_scheduled_run_at"),
            "last_scheduled_session_day": runtime.get("live_pilot_window_canary_last_scheduled_session_day"),
            "next_eligible_run_at": runtime.get("live_pilot_window_canary_next_eligible_run_at"),
            "skipped_reason": runtime.get("live_pilot_window_canary_last_skipped_reason"),
            "last_error": runtime.get("live_pilot_window_canary_last_error"),
            "sessions": [],
        }
    report.setdefault("enabled", settings["live_pilot_window_canary_enabled"])
    report.setdefault("auto_review_enabled", settings["live_pilot_window_canary_auto_review_enabled"])
    report.setdefault("window_days", settings["live_pilot_window_canary_window_sessions"])
    report.setdefault("required_clean_sessions", settings["live_pilot_window_canary_required_clean_sessions"])
    report.setdefault("related_note_id", runtime.get("live_pilot_window_canary_last_note_id"))
    report.setdefault("note_id", runtime.get("live_pilot_window_canary_last_note_id"))
    report.setdefault("last_run_at", runtime.get("live_pilot_window_canary_last_run_at"))
    report.setdefault("last_scheduled_run_at", runtime.get("live_pilot_window_canary_last_scheduled_run_at"))
    report.setdefault("last_scheduled_session_day", runtime.get("live_pilot_window_canary_last_scheduled_session_day"))
    report.setdefault("next_eligible_run_at", runtime.get("live_pilot_window_canary_next_eligible_run_at"))
    report.setdefault("skipped_reason", runtime.get("live_pilot_window_canary_last_skipped_reason"))
    report.setdefault("last_error", runtime.get("live_pilot_window_canary_last_error"))
    return serialize_value(report)


def _timestamp_in_session(row: dict[str, Any], session_day: str) -> bool:
    start_at, end_at = _session_bounds_for_day(session_day)
    for key in ("checked_at", "evaluated_at", "at", "created_at", "updated_at"):
        parsed = _parse_datetime(row.get(key))
        if parsed is not None:
            return start_at <= parsed < end_at
    return False


def _runtime_report_by_day(runtime: dict[str, Any], last_key: str, history_key: str, session_day: str) -> dict[str, Any]:
    last = runtime.get(last_key)
    if isinstance(last, dict) and str(last.get("session_day") or "").strip() == session_day:
        return dict(last)
    for item in list(runtime.get(history_key) or []):
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
            owner=LIVE_PILOT_WINDOW_CANARY_NOTE_OWNER,
            limit=LIVE_PILOT_WINDOW_CANARY_NOTE_LIMIT,
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
        if "supervised-live-pilot-canary" in tags:
            bucket["live_pilot_window_canary"] = item
            continue
        if "supervised-live-pilot" in tags:
            bucket["live_pilot_window"] = item
        if "live-pilot-readiness" in tags:
            bucket["live_pilot_readiness"] = item
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
        row_profile = str(payload.get("automation_profile_key") or payload.get("profile_key") or "").strip()
        window_id = str(payload.get("live_pilot_window_id") or "").strip()
        route_family = str(payload.get("route_family") or payload.get("source") or "").strip().lower()
        if row_profile and row_profile != LIVE_PILOT_WINDOW_CANARY_LIVE_PROFILE:
            continue
        if not window_id and route_family != "live_pilot_window":
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


def _issue(
    key: str,
    detail: str,
    *,
    component: str = "live_pilot_window_canary",
    severity: str = "blocker",
) -> dict[str, Any]:
    return {"key": key, "component": component, "severity": severity, "detail": detail}


def _slippage_bps(window_item: dict[str, Any]) -> float | None:
    for section_key in ("position_evidence", "exit_evidence", "cancel_evidence"):
        section = dict(window_item.get(section_key) or {})
        for key in ("slippage_bps", "slippage_bp", "average_slippage_bps"):
            if section.get(key) is not None:
                return _coerce_float(section.get(key), 0.0)
    return None


def _realized_pnl(window_item: dict[str, Any]) -> float | None:
    for section_key in ("exit_evidence", "position_evidence"):
        section = dict(window_item.get(section_key) or {})
        for key in ("realized_pnl", "pnl", "realized_pnl_dollars"):
            if section.get(key) is not None:
                return _coerce_float(section.get(key), 0.0)
    return None


def _session_report(
    *,
    session_day: str,
    window_item: dict[str, Any],
    readiness_item: dict[str, Any],
    notes: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    window_note = notes.get("live_pilot_window")
    readiness_note = notes.get("live_pilot_readiness")
    window_status = str(window_item.get("status") or "").strip().lower()
    terminal_state = str(window_item.get("terminal_state") or "").strip().lower() or None
    reconciliation_status = str(window_item.get("reconciliation_status") or "").strip().lower()
    readiness_status = str(readiness_item.get("status") or ("noted" if readiness_note else "")).strip().lower()
    candidate = dict(window_item.get("selected_candidate") or {})
    broker_order_id = window_item.get("broker_order_id")
    local_order_id = window_item.get("local_order_id") or window_item.get("order_id")
    notional_cap = _coerce_float(window_item.get("notional_cap"), 0.0)
    estimated_notional = _coerce_float(window_item.get("estimated_notional"), 0.0)
    submitted_events = [item for item in events if str(item.get("event_key") or "").strip().lower() == "order.submitted"]
    entry_events = [
        item
        for item in events
        if str(item.get("event_key") or "").strip().lower() in {"order.submitted", "order.accepted", "order.filled"}
    ]
    terminal_events = [
        item
        for item in events
        if str(item.get("event_key") or "").strip().lower() in {"order.canceled", "order.closed"}
    ]
    failed_events = [
        item
        for item in events
        if str(item.get("status") or "").strip().lower() in {"failed", "error", "rejected", "blocked"}
    ]
    non_limit_entry_events: list[dict[str, Any]] = []
    for item in entry_events:
        payload = dict(item.get("payload") or {})
        request_payload = payload.get("request")
        if not isinstance(request_payload, dict):
            request_payload = {}
        order_type = str(request_payload.get("order_type") or item.get("order_type") or "").strip().lower()
        if order_type and order_type != "limit":
            non_limit_entry_events.append(item)

    if not window_status:
        blockers.append({"key": "live_window_missing", "detail": "No supervised live pilot window evidence exists for this session."})
    elif window_status in {"blocked", "fail", "failed"} or window_item.get("blockers"):
        blockers.append({"key": "live_window_blocked", "detail": "The supervised live pilot window has unresolved blockers."})
    elif window_status == "warning" or window_item.get("warnings"):
        warnings.append({"key": "live_window_warning", "detail": "The supervised live pilot window completed with advisory warnings."})
    if window_status and window_status != "completed":
        blockers.append({"key": "live_window_not_completed", "detail": "The supervised live pilot window did not reach completed status."})
    if window_status == "completed" and terminal_state not in {"canceled", "closed"}:
        blockers.append(
            {
                "key": "terminal_state_not_final",
                "detail": "The supervised live pilot window did not record canceled or safely closed terminal evidence.",
            }
        )
    if window_status == "completed" and (not broker_order_id or not local_order_id):
        blockers.append(
            {
                "key": "order_evidence_missing",
                "detail": "The supervised live pilot window is missing broker or local order identifiers.",
            }
        )
    if estimated_notional and notional_cap and estimated_notional > notional_cap:
        blockers.append({"key": "notional_cap_exceeded", "detail": "The supervised live pilot estimated notional exceeded the configured cap."})
    if window_status == "completed" and len(submitted_events) != 1:
        blockers.append({"key": "order_count_not_one", "detail": f"The session recorded {len(submitted_events)} supervised live submit events; exactly one is allowed."})
    if non_limit_entry_events:
        blockers.append({"key": "non_limit_entry_detected", "detail": "A supervised live pilot entry event reported non-limit routing."})
    if terminal_state in {"canceled", "closed"} and not terminal_events:
        warnings.append({"key": "terminal_event_missing", "detail": "Terminal report exists but no matching cancel or close order event was found."})
    if not reconciliation_status:
        blockers.append({"key": "live_reconciliation_missing", "detail": "No supervised live pilot reconciliation status was recorded."})
    elif reconciliation_status in {"blocked", "fail", "failed"}:
        blockers.append({"key": "live_reconciliation_blocked", "detail": "Supervised live pilot reconciliation has unresolved mismatches."})
    elif reconciliation_status != "clean":
        warnings.append({"key": "live_reconciliation_warning", "detail": "Supervised live pilot reconciliation is not clean for this session."})
    if not candidate:
        blockers.append({"key": "candidate_missing", "detail": "The supervised live pilot did not record selected candidate evidence."})
    else:
        if not str(candidate.get("ticker") or candidate.get("symbol") or "").strip():
            blockers.append({"key": "candidate_symbol_missing", "detail": "The selected candidate did not include a ticker."})
        if candidate.get("auto_entry_eligible") is False:
            blockers.append({"key": "candidate_not_auto_eligible", "detail": "The selected candidate was not auto-entry eligible."})
        if str(candidate.get("reject_reason") or "").strip():
            blockers.append({"key": "candidate_rejected", "detail": "The selected candidate carried a rejection reason."})
    slippage = _slippage_bps(window_item)
    if slippage is not None:
        abs_slippage = abs(float(slippage))
        if abs_slippage > LIVE_PILOT_WINDOW_CANARY_BLOCK_SLIPPAGE_BPS:
            blockers.append({"key": "slippage_blocked", "detail": f"Supervised live pilot slippage was {abs_slippage:.1f} bps."})
        elif abs_slippage > LIVE_PILOT_WINDOW_CANARY_WARN_SLIPPAGE_BPS:
            warnings.append({"key": "slippage_warning", "detail": f"Supervised live pilot slippage was {abs_slippage:.1f} bps."})
    if not window_note:
        blockers.append({"key": "live_window_note_missing", "detail": "The automation-ai supervised live pilot note is missing for this session."})
    if readiness_status == "blocked" or readiness_item.get("blockers"):
        blockers.append({"key": "readiness_blocked", "detail": "Live pilot readiness was blocked during this session."})
    elif readiness_status == "warning" or readiness_item.get("warnings"):
        warnings.append({"key": "readiness_warning", "detail": "Live pilot readiness carried warnings during this session."})
    elif not readiness_status:
        warnings.append({"key": "readiness_missing", "detail": "No same-session live pilot readiness evidence was found."})
    if failed_events:
        blockers.append({"key": "live_order_event_failed", "detail": f"{len(failed_events)} supervised live pilot order event failure(s) were recorded."})

    pnl = _realized_pnl(window_item)
    clean = not blockers
    return serialize_value(
        {
            "session_day": session_day,
            "status": "clean" if clean else "blocked",
            "clean": clean,
            "blockers": blockers,
            "warnings": warnings,
            "live_pilot_window": {
                "covered": bool(window_status),
                "status": window_status or "missing",
                "terminal_state": terminal_state,
                "broker_order_id": broker_order_id,
                "local_order_id": local_order_id,
                "local_trade_id": window_item.get("local_trade_id"),
                "reconciliation_status": reconciliation_status or "missing",
                "selected_candidate": candidate,
                "estimated_notional": estimated_notional or None,
                "notional_cap": notional_cap or None,
                "note_id": window_note.get("id") if isinstance(window_note, dict) else window_item.get("related_note_id") or window_item.get("note_id"),
            },
            "live_pilot_readiness": {
                "covered": bool(readiness_status),
                "status": readiness_status or "missing",
                "broker_live_gate_status": readiness_item.get("broker_live_gate_status") or "unknown",
                "safety_lock_status": readiness_item.get("safety_lock_status") or "unknown",
                "note_id": readiness_note.get("id") if isinstance(readiness_note, dict) else readiness_item.get("related_note_id") or readiness_item.get("note_id"),
            },
            "order_events": {
                "count": len(events),
                "submitted_count": len(submitted_events),
                "terminal_count": len(terminal_events),
                "failed_count": len(failed_events),
            },
            "slippage": {"bps": slippage, "abs_bps": abs(float(slippage)) if slippage is not None else None},
            "pnl": {"realized_pnl": pnl},
        }
    )


def _summary_from_sessions(sessions: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    slippages: list[float] = []
    pnl_values: list[float] = []
    latest_candidate: dict[str, Any] = {}
    for item in sessions:
        window = dict(item.get("live_pilot_window") or {})
        candidate = dict(window.get("selected_candidate") or {})
        if candidate and not latest_candidate:
            latest_candidate = candidate
        slippage = dict(item.get("slippage") or {}).get("bps")
        if slippage is not None:
            slippages.append(abs(float(slippage)))
        pnl = dict(item.get("pnl") or {}).get("realized_pnl")
        if pnl is not None:
            pnl_values.append(float(pnl))
    return (
        latest_candidate,
        {
            "sample_count": len(pnl_values),
            "realized_pnl": float(sum(pnl_values)) if pnl_values else 0.0,
        },
        {
            "sample_count": len(slippages),
            "average_abs_bps": float(sum(slippages) / len(slippages)) if slippages else None,
            "worst_abs_bps": float(max(slippages)) if slippages else None,
        },
    )


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
    settings = normalize_live_pilot_window_canary_settings(paper_state.get("settings"))
    live_state = live_state if isinstance(live_state, dict) else {}
    live_settings = dict(live_state.get("settings") or {})
    window_days = int(settings["live_pilot_window_canary_window_sessions"])
    required_clean_sessions = int(settings["live_pilot_window_canary_required_clean_sessions"])
    current_day = str(review_session_day or "").strip() or _session_day_for(now)
    anchor = datetime.combine(date.fromisoformat(current_day), time(12), tzinfo=MARKET_TIMEZONE)
    session_days = _recent_trading_days(anchor, count=window_days)
    notes_by_day = _note_lookup(profile_key)
    sessions: list[dict[str, Any]] = []

    for session_day in session_days:
        start_at, end_at = _session_bounds_for_day(session_day)
        notes = dict(notes_by_day.get(session_day) or {})
        window_item = _runtime_report_by_day(
            runtime,
            "live_pilot_window_last_report",
            "live_pilot_window_history",
            session_day,
        )
        readiness_item = _runtime_report_by_day(
            runtime,
            "live_pilot_readiness_last_report",
            "live_pilot_readiness_history",
            session_day,
        )
        events = _recent_order_events(db, tenant=tenant, start_at=start_at, end_at=end_at)
        if not (window_item or readiness_item or notes or events):
            continue
        sessions.append(
            _session_report(
                session_day=session_day,
                window_item=window_item,
                readiness_item=readiness_item,
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
    if profile_key != LIVE_PILOT_WINDOW_CANARY_PAPER_PROFILE:
        blockers.insert(
            0,
            _issue(
                "paper_profile_required",
                "V1 supervised live pilot window canary is reviewed from the personal paper automation profile.",
            ),
        )
    if not sessions:
        blockers.append(_issue("no_live_window_sessions", "No supervised live pilot window evidence was found in the configured trading-day window."))

    latest_readiness = dict(runtime.get("live_pilot_readiness_last_report") or {})
    latest_readiness_status = str(latest_readiness.get("status") or "").strip().lower() or "missing"
    broker_live_gate_status = str(latest_readiness.get("broker_live_gate_status") or "unknown").strip().lower()
    safety_lock_status = str(latest_readiness.get("safety_lock_status") or "unknown").strip().lower()
    live_enabled = _coerce_bool(live_settings.get("enabled"), False)
    live_armed = _coerce_bool(live_settings.get("armed"), False)
    live_kill = _coerce_bool(live_settings.get("kill_switch"), False)
    paper_kill = _coerce_bool(dict(paper_state.get("settings") or {}).get("kill_switch"), False)

    if latest_readiness_status in {"missing", "not_run"}:
        blockers.append(_issue("live_readiness_missing", "Run live pilot readiness before reviewing the supervised live pilot window canary."))
    elif latest_readiness_status == "blocked" or latest_readiness.get("blockers"):
        blockers.append(_issue("live_readiness_blocked", "Current live pilot readiness has unresolved blockers."))
    elif latest_readiness_status == "warning" or latest_readiness.get("warnings"):
        warnings.append(_issue("live_readiness_warning", "Current live pilot readiness has advisory warnings.", severity="warning"))
    if broker_live_gate_status != "open":
        blockers.append(_issue("broker_live_gate_not_open", "The broker-live readiness gate is not open."))
    if safety_lock_status not in {"clear", "none"}:
        blockers.append(_issue("safety_lock_active", "A paper or live safety lock is active."))
    if live_enabled or live_armed:
        blockers.append(_issue("live_profile_active", "The personal live automation profile must remain disabled and disarmed."))
    if live_kill or paper_kill:
        blockers.append(_issue("kill_switch_active", "A paper or live kill switch is active."))

    clean_sessions = [item for item in sessions if item.get("clean")]
    clean_count = len(clean_sessions)
    latest_session = sessions[0] if sessions else {}
    latest_window = dict(latest_session.get("live_pilot_window") or {})
    latest_ready = dict(latest_session.get("live_pilot_readiness") or {})
    note_covered = sum(1 for item in sessions if (item.get("live_pilot_window") or {}).get("note_id"))
    candidate_evidence, pnl_summary, slippage_summary = _summary_from_sessions(sessions)
    status = "ready" if clean_count >= required_clean_sessions and not blockers else "collecting"
    if blockers:
        status = "blocked"
    label = {
        "ready": "Supervised live pilot window canary ready",
        "collecting": "Collecting supervised live pilot window canary",
        "blocked": "Supervised live pilot window canary blocked",
    }.get(status, "Supervised live pilot window canary")
    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": profile_key,
            "live_profile_key": LIVE_PILOT_WINDOW_CANARY_LIVE_PROFILE,
            "linked_account_id": getattr(linked_account, "id", None),
            "session_day": current_day,
            "evaluated_at": _serialize_datetime(now),
            "window_days": window_days,
            "window_sessions": session_days,
            "window_session_count": len(sessions),
            "clean_session_count": clean_count,
            "required_clean_sessions": required_clean_sessions,
            "evidence_window": {
                "start_session_day": session_days[-1] if session_days else current_day,
                "end_session_day": session_days[0] if session_days else current_day,
                "session_days": session_days,
                "configured_session_count": window_days,
                "evidence_session_count": len(sessions),
                "required_clean_sessions": required_clean_sessions,
            },
            "latest_window_status": latest_window.get("status") or "missing",
            "latest_terminal_state": latest_window.get("terminal_state"),
            "latest_broker_order_id": latest_window.get("broker_order_id"),
            "latest_local_order_id": latest_window.get("local_order_id"),
            "latest_local_trade_id": latest_window.get("local_trade_id"),
            "latest_reconciliation_status": latest_window.get("reconciliation_status") or "missing",
            "live_readiness_status": latest_readiness_status,
            "latest_session_readiness_status": latest_ready.get("status") or "missing",
            "broker_live_gate_status": broker_live_gate_status,
            "safety_lock_status": safety_lock_status,
            "live_profile_enabled": live_enabled,
            "live_profile_armed": live_armed,
            "candidate_evidence": candidate_evidence,
            "pnl_summary": pnl_summary,
            "slippage_summary": slippage_summary,
            "note_coverage": {
                "covered": note_covered,
                "required": len(sessions),
                "ratio": float(note_covered / len(sessions)) if sessions else 0.0,
            },
            "blockers": blockers[:20],
            "warnings": warnings[:20],
            "manual_action_required": bool(blockers),
            "sessions": sessions,
        }
    )


def _find_existing_canary_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIVE_PILOT_WINDOW_CANARY_NOTE_OWNER,
            limit=LIVE_PILOT_WINDOW_CANARY_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "supervised-live-pilot-canary",
        "supervised-live-pilot",
        "live-pilot-readiness",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    evidence_window = dict(report.get("evidence_window") or {})
    candidate = dict(report.get("candidate_evidence") or {})
    slippage = dict(report.get("slippage_summary") or {})
    pnl = dict(report.get("pnl_summary") or {})
    lines = [
        f"Automation supervised live pilot window canary for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Run source: {str(report.get('run_source') or 'manual').replace('_', ' ')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Clean sessions: {report.get('clean_session_count', 0)} / {report.get('required_clean_sessions', 0)} required",
        f"Window sessions with evidence: {report.get('window_session_count', 0)}",
        (
            f"Evidence window: {evidence_window.get('start_session_day')} through {evidence_window.get('end_session_day')}"
            if evidence_window
            else f"Evidence window: {report.get('window_days', LIVE_PILOT_WINDOW_CANARY_WINDOW_SESSIONS)} trading session(s)"
        ),
        f"Latest window: {report.get('latest_window_status') or 'missing'}",
        f"Latest terminal state: {report.get('latest_terminal_state') or '--'}",
        f"Latest reconciliation status: {report.get('latest_reconciliation_status') or 'missing'}",
        f"Latest candidate: {candidate.get('ticker') or candidate.get('symbol') or '--'} rank {candidate.get('portfolio_rank') or '--'}",
        f"Live readiness: {report.get('live_readiness_status') or 'missing'}",
        f"Broker live gate: {report.get('broker_live_gate_status') or 'unknown'}",
        f"Safety locks: {report.get('safety_lock_status') or 'unknown'}",
        f"Slippage samples: {slippage.get('sample_count', 0)} | worst {slippage.get('worst_abs_bps') if slippage.get('worst_abs_bps') is not None else '--'} bps",
        f"Realized PnL samples: {pnl.get('sample_count', 0)} | total ${float(pnl.get('realized_pnl') or 0.0):.2f}",
        (
            f"Next scheduled review: {report.get('next_eligible_run_at')}"
            if report.get("next_eligible_run_at")
            else "Next scheduled review: not available"
        ),
        "",
        "This canary is advisory. It does not place, cancel, or close orders, enable live trading, arm automation, clear locks, tune baseline settings, or change broker-live gates.",
        "",
        "Blockers",
    ]
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    if blockers:
        lines.extend(f"- {item.get('session_day', report.get('session_day'))}: {item.get('key')}. {item.get('detail')}" for item in blockers[:12])
    else:
        lines.append("- None.")
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    lines.extend(["", "Warnings"])
    if warnings:
        lines.extend(f"- {item.get('session_day', report.get('session_day'))}: {item.get('key')}. {item.get('detail')}" for item in warnings[:12])
    else:
        lines.append("- None.")
    lines.extend(["", "Sessions"])
    for item in list(report.get("sessions") or [])[: _coerce_int(report.get("window_days"), LIVE_PILOT_WINDOW_CANARY_WINDOW_SESSIONS)]:
        window = dict(item.get("live_pilot_window") or {})
        readiness = dict(item.get("live_pilot_readiness") or {})
        order_events = dict(item.get("order_events") or {})
        session_candidate = dict(window.get("selected_candidate") or {})
        lines.append(
            f"- {item.get('session_day')}: {str(item.get('status') or '').upper()} | "
            f"window {window.get('status') or 'missing'} | "
            f"terminal {window.get('terminal_state') or '--'} | "
            f"reconcile {window.get('reconciliation_status') or 'missing'} | "
            f"candidate {session_candidate.get('ticker') or '--'} | "
            f"readiness {readiness.get('status') or 'missing'} | "
            f"events {order_events.get('count', 0)}"
        )
    return "\n".join(lines).strip()


def _sync_canary_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "supervised-live-pilot-canary",
        "supervised-live-pilot",
        "live-pilot-readiness",
        _profile_tag(profile_key),
        f"session-{session_day}",
    ]
    title = f"Automation supervised live pilot window canary - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = str(report.get("related_note_id") or report.get("note_id") or "").strip() or _find_existing_canary_note_id(profile_key, session_day)
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": LIVE_PILOT_WINDOW_CANARY_NOTE_OWNER,
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


def run_live_pilot_window_canary_review(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    profile_key: str = LIVE_PILOT_WINDOW_CANARY_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_run_source = str(run_source or "manual").strip().lower().replace(" ", "_") or "manual"
    review_session_day, _review_window_open = live_pilot_window_canary_session_day_for(
        now,
        forced=normalized_run_source != "scheduled",
    )
    original_settings = deepcopy(dict(paper_state.get("settings") or {}))
    original_live_settings = deepcopy(dict((live_state or {}).get("settings") or {}))
    protected_before = {
        "paper": {key: original_settings.get(key) for key in ("enabled", "armed", "kill_switch", "execution_intent")},
        "live": {key: original_live_settings.get(key) for key in ("enabled", "armed", "kill_switch", "execution_intent")},
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
        else _serialize_datetime(_parse_datetime(runtime_before.get("live_pilot_window_canary_last_scheduled_run_at")))
    )
    report["last_scheduled_session_day"] = (
        review_session_day
        if normalized_run_source == "scheduled"
        else str(runtime_before.get("live_pilot_window_canary_last_scheduled_session_day") or "").strip() or None
    )
    next_eligible = (
        next_eligible_live_pilot_window_canary_review_after_session(review_session_day)
        if normalized_run_source == "scheduled"
        else next_eligible_live_pilot_window_canary_review_at(now)
    )
    report["next_eligible_run_at"] = _serialize_datetime(next_eligible)
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
    }
    report["baseline_settings_mutated"] = protected_before != protected_after

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
        "evidence_window",
        "latest_window_status",
        "latest_terminal_state",
        "latest_broker_order_id",
        "latest_local_order_id",
        "latest_local_trade_id",
        "latest_reconciliation_status",
        "live_readiness_status",
        "latest_session_readiness_status",
        "broker_live_gate_status",
        "safety_lock_status",
        "live_profile_enabled",
        "live_profile_armed",
        "candidate_evidence",
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
        "run_source",
        "skipped_reason",
        "last_scheduled_run_at",
        "last_scheduled_session_day",
        "next_eligible_run_at",
    }
    summary = {key: report.get(key) for key in summary_keys if key in report}
    runtime["live_pilot_window_canary_last_report"] = serialize_value(summary)
    runtime["live_pilot_window_canary_last_note_id"] = note_id
    runtime["live_pilot_window_canary_note_session_day"] = report.get("session_day")
    runtime["live_pilot_window_canary_last_run_at"] = report.get("evaluated_at")
    if normalized_run_source == "scheduled":
        runtime["live_pilot_window_canary_last_scheduled_run_at"] = report.get("evaluated_at")
        runtime["live_pilot_window_canary_last_scheduled_session_day"] = review_session_day
    runtime["live_pilot_window_canary_next_eligible_run_at"] = report.get("next_eligible_run_at")
    runtime["live_pilot_window_canary_last_skipped_reason"] = None
    runtime["live_pilot_window_canary_last_error"] = None
    history = list(runtime.get("live_pilot_window_canary_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "clean_session_count": report.get("clean_session_count"),
            "required_clean_sessions": report.get("required_clean_sessions"),
            "window_session_count": report.get("window_session_count"),
            "blocker_count": len(report.get("blockers") or []),
            "note_id": note_id,
            "run_source": normalized_run_source,
        },
    )
    runtime["live_pilot_window_canary_history"] = serialize_value(history[:LIVE_PILOT_WINDOW_CANARY_HISTORY_LIMIT])
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.live_pilot_window_canary_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "live_profile_key": LIVE_PILOT_WINDOW_CANARY_LIVE_PROFILE,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "clean_session_count": report.get("clean_session_count"),
                "required_clean_sessions": report.get("required_clean_sessions"),
                "window_session_count": report.get("window_session_count"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
                "note_id": note_id,
                "run_source": normalized_run_source,
                "baseline_settings_mutated": report.get("baseline_settings_mutated"),
            },
        )
    return serialize_value(report)
