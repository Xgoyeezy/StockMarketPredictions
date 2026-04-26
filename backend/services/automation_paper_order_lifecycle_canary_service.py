from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.services import automation_ai_review_service, notes_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

PAPER_ORDER_LIFECYCLE_CANARY_NOTE_OWNER = "automation-ai"
PAPER_ORDER_LIFECYCLE_CANARY_WINDOW_SESSIONS = 5
PAPER_ORDER_LIFECYCLE_CANARY_REQUIRED_CLEAN_SESSIONS = 3
PAPER_ORDER_LIFECYCLE_CANARY_HISTORY_LIMIT = 8
PAPER_ORDER_LIFECYCLE_CANARY_NOTE_LIMIT = 250
PAPER_ORDER_LIFECYCLE_CANARY_PERSONAL_PAPER_PROFILE = "personal_paper"

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE

PAPER_ORDER_LIFECYCLE_CANARY_SETTINGS_DEFAULTS: dict[str, Any] = {
    "paper_order_lifecycle_canary_enabled": True,
    "paper_order_lifecycle_auto_submit_enabled": False,
    "paper_order_lifecycle_window_sessions": PAPER_ORDER_LIFECYCLE_CANARY_WINDOW_SESSIONS,
    "paper_order_lifecycle_required_clean_sessions": PAPER_ORDER_LIFECYCLE_CANARY_REQUIRED_CLEAN_SESSIONS,
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
    return "profile-" + str(profile_key or PAPER_ORDER_LIFECYCLE_CANARY_PERSONAL_PAPER_PROFILE).strip().lower().replace(":", "-")


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def _session_bounds_for_day(session_day: str) -> tuple[datetime, datetime]:
    local_day = date.fromisoformat(session_day)
    local_start = datetime.combine(local_day, time.min, tzinfo=MARKET_TIMEZONE)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def lifecycle_canary_session_day_for(value: datetime | None = None, *, forced: bool = False) -> tuple[str, bool]:
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


def next_eligible_lifecycle_canary_review_at(value: datetime | None = None) -> datetime:
    now = value or _utc_now()
    session_day, review_window_open = lifecycle_canary_session_day_for(now, forced=False)
    if review_window_open:
        return now
    return _review_time_for_session_day(session_day)


def next_eligible_lifecycle_canary_review_after_session(session_day: str) -> datetime:
    return _review_time_for_session_day(_next_trading_day_after(session_day))


def _recent_trading_days(now: datetime, *, count: int) -> list[str]:
    cursor = now.astimezone(MARKET_TIMEZONE).date()
    days: list[str] = []
    while len(days) < max(1, count):
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return days


def normalize_paper_order_lifecycle_canary_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    window_sessions = _clamp_int(
        state.get("paper_order_lifecycle_window_sessions"),
        int(PAPER_ORDER_LIFECYCLE_CANARY_SETTINGS_DEFAULTS["paper_order_lifecycle_window_sessions"]),
        minimum=1,
        maximum=20,
    )
    required_clean_sessions = _clamp_int(
        state.get("paper_order_lifecycle_required_clean_sessions"),
        int(PAPER_ORDER_LIFECYCLE_CANARY_SETTINGS_DEFAULTS["paper_order_lifecycle_required_clean_sessions"]),
        minimum=1,
        maximum=window_sessions,
    )
    return {
        "paper_order_lifecycle_canary_enabled": _coerce_bool(
            state.get("paper_order_lifecycle_canary_enabled"),
            bool(PAPER_ORDER_LIFECYCLE_CANARY_SETTINGS_DEFAULTS["paper_order_lifecycle_canary_enabled"]),
        ),
        "paper_order_lifecycle_auto_submit_enabled": _coerce_bool(
            state.get("paper_order_lifecycle_auto_submit_enabled"),
            bool(PAPER_ORDER_LIFECYCLE_CANARY_SETTINGS_DEFAULTS["paper_order_lifecycle_auto_submit_enabled"]),
        ),
        "paper_order_lifecycle_window_sessions": window_sessions,
        "paper_order_lifecycle_required_clean_sessions": required_clean_sessions,
    }


def normalize_paper_order_lifecycle_canary_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("paper_order_lifecycle_canary_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("paper_order_lifecycle_canary_history") or [])[:PAPER_ORDER_LIFECYCLE_CANARY_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "paper_order_lifecycle_canary_last_report": serialize_value(last_report),
        "paper_order_lifecycle_canary_last_note_id": str(runtime.get("paper_order_lifecycle_canary_last_note_id") or "").strip() or None,
        "paper_order_lifecycle_canary_note_session_day": str(runtime.get("paper_order_lifecycle_canary_note_session_day") or "").strip() or None,
        "paper_order_lifecycle_canary_last_run_at": _serialize_datetime(_parse_datetime(runtime.get("paper_order_lifecycle_canary_last_run_at"))),
        "paper_order_lifecycle_canary_last_scheduled_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("paper_order_lifecycle_canary_last_scheduled_run_at"))
        ),
        "paper_order_lifecycle_canary_last_scheduled_session_day": str(
            runtime.get("paper_order_lifecycle_canary_last_scheduled_session_day") or ""
        ).strip() or None,
        "paper_order_lifecycle_canary_next_eligible_run_at": (
            _serialize_datetime(_parse_datetime(runtime.get("paper_order_lifecycle_canary_next_eligible_run_at")))
            or _serialize_datetime(next_eligible_lifecycle_canary_review_at())
        ),
        "paper_order_lifecycle_canary_last_auto_submit_at": _serialize_datetime(
            _parse_datetime(runtime.get("paper_order_lifecycle_canary_last_auto_submit_at"))
        ),
        "paper_order_lifecycle_canary_last_auto_submit_session_day": str(
            runtime.get("paper_order_lifecycle_canary_last_auto_submit_session_day") or ""
        ).strip() or None,
        "paper_order_lifecycle_canary_last_skipped_reason": str(
            runtime.get("paper_order_lifecycle_canary_last_skipped_reason") or ""
        ).strip() or None,
        "paper_order_lifecycle_canary_last_error": str(runtime.get("paper_order_lifecycle_canary_last_error") or "").strip() or None,
        "paper_order_lifecycle_canary_history": history,
    }


def build_paper_order_lifecycle_canary_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = normalize_paper_order_lifecycle_canary_runtime((state or {}).get("runtime"))
    settings = normalize_paper_order_lifecycle_canary_settings((state or {}).get("settings"))
    report = dict(runtime.get("paper_order_lifecycle_canary_last_report") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "enabled": settings["paper_order_lifecycle_canary_enabled"],
            "auto_submit_enabled": settings["paper_order_lifecycle_auto_submit_enabled"],
            "clean_session_count": 0,
            "required_clean_sessions": settings["paper_order_lifecycle_required_clean_sessions"],
            "window_session_count": 0,
            "window_days": settings["paper_order_lifecycle_window_sessions"],
            "latest_soak_status": "missing",
            "latest_terminal_state": None,
            "latest_reconciliation_status": "missing",
            "blockers": [],
            "warnings": [],
            "manual_action_required": False,
            "related_note_id": runtime.get("paper_order_lifecycle_canary_last_note_id"),
            "last_run_at": runtime.get("paper_order_lifecycle_canary_last_run_at"),
            "last_scheduled_run_at": runtime.get("paper_order_lifecycle_canary_last_scheduled_run_at"),
            "last_scheduled_session_day": runtime.get("paper_order_lifecycle_canary_last_scheduled_session_day"),
            "next_eligible_run_at": runtime.get("paper_order_lifecycle_canary_next_eligible_run_at"),
            "last_auto_submit_at": runtime.get("paper_order_lifecycle_canary_last_auto_submit_at"),
            "last_auto_submit_session_day": runtime.get("paper_order_lifecycle_canary_last_auto_submit_session_day"),
            "skipped_reason": runtime.get("paper_order_lifecycle_canary_last_skipped_reason"),
            "last_error": runtime.get("paper_order_lifecycle_canary_last_error"),
            "sessions": [],
        }
    report.setdefault("enabled", settings["paper_order_lifecycle_canary_enabled"])
    report.setdefault("auto_submit_enabled", settings["paper_order_lifecycle_auto_submit_enabled"])
    report.setdefault("window_days", settings["paper_order_lifecycle_window_sessions"])
    report.setdefault("required_clean_sessions", settings["paper_order_lifecycle_required_clean_sessions"])
    report.setdefault("related_note_id", runtime.get("paper_order_lifecycle_canary_last_note_id"))
    report.setdefault("note_id", runtime.get("paper_order_lifecycle_canary_last_note_id"))
    report.setdefault("last_run_at", runtime.get("paper_order_lifecycle_canary_last_run_at"))
    report.setdefault("last_scheduled_run_at", runtime.get("paper_order_lifecycle_canary_last_scheduled_run_at"))
    report.setdefault("last_scheduled_session_day", runtime.get("paper_order_lifecycle_canary_last_scheduled_session_day"))
    report.setdefault("next_eligible_run_at", runtime.get("paper_order_lifecycle_canary_next_eligible_run_at"))
    report.setdefault("last_auto_submit_at", runtime.get("paper_order_lifecycle_canary_last_auto_submit_at"))
    report.setdefault("last_auto_submit_session_day", runtime.get("paper_order_lifecycle_canary_last_auto_submit_session_day"))
    report.setdefault("skipped_reason", runtime.get("paper_order_lifecycle_canary_last_skipped_reason"))
    report.setdefault("last_error", runtime.get("paper_order_lifecycle_canary_last_error"))
    return serialize_value(report)


def _safe_frame(reader: Any) -> pd.DataFrame:
    try:
        frame = reader() if callable(reader) else reader
    except Exception:
        return pd.DataFrame()
    if isinstance(frame, pd.DataFrame):
        return frame.copy()
    return pd.DataFrame()


def _timestamp_in_session(row: dict[str, Any], session_day: str) -> bool:
    start_at, end_at = _session_bounds_for_day(session_day)
    for key in ("checked_at", "at", "created_at", "updated_at", "opened_at", "closed_at"):
        parsed = _parse_datetime(row.get(key))
        if parsed is not None:
            return start_at <= parsed < end_at
    return False


def _runtime_soak_by_day(runtime: dict[str, Any], session_day: str) -> dict[str, Any]:
    last = runtime.get("paper_order_lifecycle_soak_last_report")
    if isinstance(last, dict) and str(last.get("session_day") or "").strip() == session_day:
        return dict(last)
    for item in list(runtime.get("paper_order_lifecycle_soak_history") or []):
        if not isinstance(item, dict):
            continue
        item_day = str(item.get("session_day") or "").strip()
        if not item_day and _timestamp_in_session(item, session_day):
            item_day = session_day
        if item_day == session_day:
            return dict(item)
    return {}


def _runtime_broker_reconciliation_by_day(runtime: dict[str, Any], session_day: str) -> dict[str, Any]:
    last = runtime.get("paper_broker_reconciliation_last_report")
    if isinstance(last, dict) and str(last.get("session_day") or "").strip() == session_day:
        return dict(last)
    for item in list(runtime.get("paper_broker_reconciliation_history") or []):
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
            owner=PAPER_ORDER_LIFECYCLE_CANARY_NOTE_OWNER,
            limit=PAPER_ORDER_LIFECYCLE_CANARY_NOTE_LIMIT,
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
        if "paper-broker" in tags and "order-lifecycle-soak" in tags:
            bucket["lifecycle_soak"] = item
        if "paper-broker" in tags and "order-lifecycle-canary" in tags:
            bucket["lifecycle_canary"] = item
        if "paper-broker" in tags and "reconciliation" in tags:
            bucket["paper_broker_reconciliation"] = item
    return by_day


def _recent_order_events(
    db: Session | None,
    *,
    tenant: Tenant,
    profile_key: str,
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
        lifecycle_id = str(payload.get("paper_order_lifecycle_soak_id") or "").strip()
        if row_profile and row_profile != profile_key:
            continue
        if not lifecycle_id and "lifecycle" not in str(row.event_key or "").lower() and row_profile != profile_key:
            continue
        events.append(
            {
                "event_key": row.event_key,
                "status": row.status,
                "detail": row.detail,
                "created_at": _serialize_datetime(row.created_at),
                "payload": payload,
            }
        )
    return events


def _ledger_summary_for_day(*, tenant: Tenant, profile_key: str, session_day: str) -> dict[str, Any]:
    summary = {
        "pending_count": 0,
        "open_count": 0,
        "closed_count": 0,
        "unresolved_count": 0,
    }
    for key, frame in (
        ("pending_count", _safe_frame(sdm.read_pending_orders)),
        ("open_count", _safe_frame(sdm.read_open_trades)),
        ("closed_count", _safe_frame(sdm.read_closed_trades)),
    ):
        if frame.empty:
            continue
        rows = []
        for row in frame.to_dict(orient="records"):
            if str(row.get("automation_profile_key") or "").strip() != profile_key:
                continue
            if str(row.get("automation_tenant_id") or "").strip() != str(tenant.id):
                continue
            is_lifecycle = bool(str(row.get("paper_order_lifecycle_soak_id") or "").strip()) or (
                str(row.get("automation_entry_reason") or "").strip() == "paper_order_lifecycle_soak"
            )
            if is_lifecycle and _timestamp_in_session(row, session_day):
                rows.append(row)
        summary[key] = len(rows)
    summary["unresolved_count"] = int(summary["pending_count"] or 0) + int(summary["open_count"] or 0)
    return summary


def _session_report(
    *,
    session_day: str,
    soak_item: dict[str, Any],
    broker_item: dict[str, Any],
    notes: dict[str, Any],
    events: list[dict[str, Any]],
    ledger: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    soak_note = notes.get("lifecycle_soak")
    broker_note = notes.get("paper_broker_reconciliation")
    soak_status = str(soak_item.get("status") or ("completed" if soak_note else "")).strip().lower()
    terminal_state = str(soak_item.get("terminal_state") or "").strip().lower() or None
    reconciliation_status = str(
        soak_item.get("reconciliation_status") or broker_item.get("status") or ("clean" if broker_note else "")
    ).strip().lower()
    failed_events = [
        item
        for item in events
        if str(item.get("status") or "").strip().lower() in {"failed", "error", "rejected"}
    ]

    if not soak_status:
        blockers.append({"key": "lifecycle_soak_missing", "detail": "No paper order lifecycle soak evidence exists for this session."})
    elif soak_status in {"blocked", "fail", "failed"} or soak_item.get("blockers"):
        blockers.append({"key": "lifecycle_soak_blocked", "detail": "The paper order lifecycle soak has unresolved blockers."})
    elif soak_status == "warning" or soak_item.get("warnings"):
        warnings.append({"key": "lifecycle_soak_warning", "detail": "The paper order lifecycle soak completed with advisory warnings."})
    if soak_status and soak_status != "completed":
        blockers.append({"key": "lifecycle_soak_not_completed", "detail": "The lifecycle soak did not reach completed status."})
    if soak_status == "completed" and terminal_state not in {"canceled", "closed", "checked"}:
        blockers.append({"key": "terminal_state_missing", "detail": "The lifecycle soak did not record cancel, close, or checked terminal evidence."})
    if not reconciliation_status:
        blockers.append({"key": "reconciliation_missing", "detail": "No paper-broker reconciliation evidence exists for the lifecycle session."})
    elif reconciliation_status in {"blocked", "fail", "failed"} or broker_item.get("blockers"):
        blockers.append({"key": "reconciliation_blocked", "detail": "Paper-broker reconciliation has unresolved lifecycle mismatches."})
    elif reconciliation_status != "clean":
        warnings.append({"key": "reconciliation_warning", "detail": "Paper-broker reconciliation is not clean for this lifecycle session."})
    if not soak_note:
        blockers.append({"key": "lifecycle_note_missing", "detail": "The automation-ai lifecycle soak note is missing for this session."})
    if failed_events:
        blockers.append({"key": "order_event_failed", "detail": f"{len(failed_events)} lifecycle order event failure(s) were recorded."})
    if int(ledger.get("unresolved_count") or 0) > 0:
        blockers.append({"key": "lifecycle_ledger_unresolved", "detail": "Lifecycle soak left pending or open local ledger rows unresolved."})

    clean = not blockers
    return serialize_value(
        {
            "session_day": session_day,
            "status": "clean" if clean else "blocked",
            "clean": clean,
            "blockers": blockers,
            "warnings": warnings,
            "lifecycle_soak": {
                "covered": bool(soak_status),
                "status": soak_status or "missing",
                "terminal_state": terminal_state,
                "broker_order_id": soak_item.get("broker_order_id"),
                "local_order_id": soak_item.get("local_order_id") or soak_item.get("order_id"),
                "note_id": soak_note.get("id") if isinstance(soak_note, dict) else soak_item.get("related_note_id") or soak_item.get("note_id"),
            },
            "paper_broker_reconciliation": {
                "covered": bool(reconciliation_status),
                "status": reconciliation_status or "missing",
                "note_id": broker_note.get("id") if isinstance(broker_note, dict) else broker_item.get("related_note_id") or broker_item.get("note_id"),
            },
            "order_events": {
                "count": len(events),
                "failed_count": len(failed_events),
            },
            "ledger": ledger,
        }
    )


def _aggregate_report(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None,
    db: Session | None,
    now: datetime,
    review_session_day: str | None = None,
) -> dict[str, Any]:
    runtime = dict(state.get("runtime") or {})
    settings = normalize_paper_order_lifecycle_canary_settings(state.get("settings"))
    window_days = int(settings["paper_order_lifecycle_window_sessions"])
    required_clean_sessions = int(settings["paper_order_lifecycle_required_clean_sessions"])
    current_day = str(review_session_day or "").strip() or _session_day_for(now)
    anchor = datetime.combine(date.fromisoformat(current_day), time(12), tzinfo=MARKET_TIMEZONE)
    session_days = _recent_trading_days(anchor, count=window_days)
    notes_by_day = _note_lookup(profile_key)
    sessions: list[dict[str, Any]] = []

    for session_day in session_days:
        start_at, end_at = _session_bounds_for_day(session_day)
        notes = dict(notes_by_day.get(session_day) or {})
        soak_item = _runtime_soak_by_day(runtime, session_day)
        broker_item = _runtime_broker_reconciliation_by_day(runtime, session_day)
        events = _recent_order_events(db, tenant=tenant, profile_key=profile_key, start_at=start_at, end_at=end_at)
        ledger = _ledger_summary_for_day(tenant=tenant, profile_key=profile_key, session_day=session_day)
        has_evidence = bool(soak_item or broker_item or notes or events or any(ledger.values()))
        if not has_evidence:
            continue
        sessions.append(
            _session_report(
                session_day=session_day,
                soak_item=soak_item,
                broker_item=broker_item,
                notes=notes,
                events=events,
                ledger=ledger,
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
    if profile_key != PAPER_ORDER_LIFECYCLE_CANARY_PERSONAL_PAPER_PROFILE:
        blockers.insert(
            0,
            {
                "session_day": current_day,
                "key": "paper_profile_required",
                "detail": "V1 lifecycle canary only targets the personal paper automation profile.",
            },
        )
    if not sessions:
        blockers.append(
            {
                "session_day": current_day,
                "key": "no_lifecycle_sessions",
                "detail": "No paper order lifecycle soak evidence was found in the configured trading-day window.",
            }
        )

    clean_sessions = [item for item in sessions if item.get("clean")]
    clean_count = len(clean_sessions)
    status = "ready" if clean_count >= required_clean_sessions and not blockers else "collecting"
    if blockers:
        status = "blocked"
    latest_session = sessions[0] if sessions else {}
    latest_soak = dict(latest_session.get("lifecycle_soak") or {})
    latest_reconciliation = dict(latest_session.get("paper_broker_reconciliation") or {})
    label = {
        "ready": "Paper lifecycle canary ready",
        "collecting": "Collecting paper lifecycle canary",
        "blocked": "Paper lifecycle canary blocked",
    }.get(status, "Paper lifecycle canary")
    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": profile_key,
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
            "latest_soak_status": latest_soak.get("status") or "missing",
            "latest_terminal_state": latest_soak.get("terminal_state"),
            "latest_broker_order_id": latest_soak.get("broker_order_id"),
            "latest_local_order_id": latest_soak.get("local_order_id"),
            "latest_reconciliation_status": latest_reconciliation.get("status") or "missing",
            "note_coverage": {
                "covered": sum(1 for item in sessions if (item.get("lifecycle_soak") or {}).get("note_id")),
                "required": len(sessions),
                "ratio": (
                    float(sum(1 for item in sessions if (item.get("lifecycle_soak") or {}).get("note_id")) / len(sessions))
                    if sessions
                    else 0.0
                ),
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
            owner=PAPER_ORDER_LIFECYCLE_CANARY_NOTE_OWNER,
            limit=PAPER_ORDER_LIFECYCLE_CANARY_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {"automation-ai", "paper-broker", "order-lifecycle-canary", _profile_tag(profile_key), f"session-{session_day}"}
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    evidence_window = dict(report.get("evidence_window") or {})
    lines = [
        f"Automation paper order lifecycle canary for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Run source: {str(report.get('run_source') or 'manual').replace('_', ' ')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Clean sessions: {report.get('clean_session_count', 0)} / {report.get('required_clean_sessions', 0)} required",
        f"Window sessions with evidence: {report.get('window_session_count', 0)}",
        (
            f"Evidence window: {evidence_window.get('start_session_day')} through {evidence_window.get('end_session_day')}"
            if evidence_window
            else f"Evidence window: {report.get('window_days', PAPER_ORDER_LIFECYCLE_CANARY_WINDOW_SESSIONS)} trading session(s)"
        ),
        f"Latest lifecycle status: {report.get('latest_soak_status') or 'missing'}",
        f"Latest terminal state: {report.get('latest_terminal_state') or '--'}",
        f"Latest reconciliation status: {report.get('latest_reconciliation_status') or 'missing'}",
        f"Auto submit enabled: {'yes' if report.get('auto_submit_enabled') else 'no'}",
        "",
        "This lifecycle canary is paper-only. It does not place live orders, clear locks, enable trading, arm automation, tune baseline settings, or change broker-live gates.",
        "",
        "Blockers",
    ]
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    if blockers:
        lines.extend(f"- {item.get('session_day')}: {item.get('key')}. {item.get('detail')}" for item in blockers[:12])
    else:
        lines.append("- None.")
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    lines.extend(["", "Warnings"])
    if warnings:
        lines.extend(f"- {item.get('session_day')}: {item.get('key')}. {item.get('detail')}" for item in warnings[:12])
    else:
        lines.append("- None.")
    lines.extend(["", "Sessions"])
    for item in list(report.get("sessions") or [])[: _coerce_int(report.get("window_days"), PAPER_ORDER_LIFECYCLE_CANARY_WINDOW_SESSIONS)]:
        soak = dict(item.get("lifecycle_soak") or {})
        reconciliation = dict(item.get("paper_broker_reconciliation") or {})
        ledger = dict(item.get("ledger") or {})
        lines.append(
            f"- {item.get('session_day')}: {str(item.get('status') or '').upper()} | "
            f"soak {soak.get('status') or 'missing'} | "
            f"terminal {soak.get('terminal_state') or '--'} | "
            f"reconcile {reconciliation.get('status') or 'missing'} | "
            f"ledger unresolved {ledger.get('unresolved_count', 0)}"
        )
    return "\n".join(lines).strip()


def _sync_canary_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "paper-broker",
        "order-lifecycle-canary",
        _profile_tag(profile_key),
        f"session-{session_day}",
    ]
    title = f"Automation paper order lifecycle canary - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = str(report.get("related_note_id") or report.get("note_id") or "").strip() or _find_existing_canary_note_id(profile_key, session_day)
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": PAPER_ORDER_LIFECYCLE_CANARY_NOTE_OWNER,
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


def run_paper_order_lifecycle_canary_review(
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
    normalized_run_source = str(run_source or "manual").strip().lower().replace(" ", "_") or "manual"
    review_session_day, _review_window_open = lifecycle_canary_session_day_for(
        now,
        forced=normalized_run_source != "scheduled",
    )
    original_settings = deepcopy(dict(state.get("settings") or {}))
    protected_before = {
        key: original_settings.get(key)
        for key in ("enabled", "armed", "kill_switch", "execution_intent")
    }
    report = _aggregate_report(
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        linked_account=linked_account,
        db=db,
        now=now,
        review_session_day=review_session_day,
    )
    runtime_before = dict(state.get("runtime") or {})
    settings = normalize_paper_order_lifecycle_canary_settings(state.get("settings"))
    report["run_source"] = normalized_run_source
    report["skipped_reason"] = None
    report["auto_submit_enabled"] = settings["paper_order_lifecycle_auto_submit_enabled"]
    report["last_scheduled_run_at"] = (
        report.get("evaluated_at")
        if normalized_run_source == "scheduled"
        else _serialize_datetime(_parse_datetime(runtime_before.get("paper_order_lifecycle_canary_last_scheduled_run_at")))
    )
    report["last_scheduled_session_day"] = (
        review_session_day
        if normalized_run_source == "scheduled"
        else str(runtime_before.get("paper_order_lifecycle_canary_last_scheduled_session_day") or "").strip() or None
    )
    next_eligible = (
        next_eligible_lifecycle_canary_review_after_session(review_session_day)
        if normalized_run_source == "scheduled"
        else next_eligible_lifecycle_canary_review_at(now)
    )
    report["next_eligible_run_at"] = _serialize_datetime(next_eligible)
    note_id = _sync_canary_note(tenant=tenant, profile_key=profile_key, report=report)
    if note_id:
        report["note_id"] = note_id
        report["related_note_id"] = note_id
    protected_after = {
        key: dict(state.get("settings") or {}).get(key)
        for key in ("enabled", "armed", "kill_switch", "execution_intent")
    }
    report["baseline_settings_mutated"] = protected_before != protected_after

    runtime = state.setdefault("runtime", {})
    summary_keys = {
        "status",
        "label",
        "profile_key",
        "linked_account_id",
        "session_day",
        "evaluated_at",
        "window_days",
        "window_sessions",
        "window_session_count",
        "clean_session_count",
        "required_clean_sessions",
        "evidence_window",
        "latest_soak_status",
        "latest_terminal_state",
        "latest_broker_order_id",
        "latest_local_order_id",
        "latest_reconciliation_status",
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
        "auto_submit_enabled",
        "last_scheduled_run_at",
        "last_scheduled_session_day",
        "next_eligible_run_at",
    }
    summary = {key: report.get(key) for key in summary_keys if key in report}
    runtime["paper_order_lifecycle_canary_last_report"] = serialize_value(summary)
    runtime["paper_order_lifecycle_canary_last_note_id"] = note_id
    runtime["paper_order_lifecycle_canary_note_session_day"] = report.get("session_day")
    runtime["paper_order_lifecycle_canary_last_run_at"] = report.get("evaluated_at")
    if normalized_run_source == "scheduled":
        runtime["paper_order_lifecycle_canary_last_scheduled_run_at"] = report.get("evaluated_at")
        runtime["paper_order_lifecycle_canary_last_scheduled_session_day"] = review_session_day
    runtime["paper_order_lifecycle_canary_next_eligible_run_at"] = report.get("next_eligible_run_at")
    runtime["paper_order_lifecycle_canary_last_skipped_reason"] = None
    runtime["paper_order_lifecycle_canary_last_error"] = None
    history = list(runtime.get("paper_order_lifecycle_canary_history") or [])
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
    runtime["paper_order_lifecycle_canary_history"] = serialize_value(history[:PAPER_ORDER_LIFECYCLE_CANARY_HISTORY_LIMIT])
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.paper_order_lifecycle_canary_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
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
    return serialize_value(report)
