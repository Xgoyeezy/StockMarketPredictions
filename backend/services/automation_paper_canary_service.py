from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.services import automation_ai_review_service, automation_state_control_service, notes_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

PAPER_CANARY_NOTE_OWNER = "automation-ai"
PAPER_CANARY_WINDOW_SESSIONS = 5
PAPER_CANARY_REQUIRED_CLEAN_SESSIONS = 3
PAPER_CANARY_HISTORY_LIMIT = 8
PAPER_CANARY_NOTE_LIMIT = 250
PAPER_CANARY_MAX_AVG_SLIPPAGE_BPS = 20.0
PAPER_CANARY_MAX_WORST_SLIPPAGE_BPS = 40.0
PAPER_CANARY_PERSONAL_PAPER_PROFILE = "personal_paper"

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE
PAPER_CANARY_SETTINGS_DEFAULTS: dict[str, Any] = {
    "paper_canary_enabled": True,
    "paper_canary_auto_review_enabled": True,
    "paper_canary_window_sessions": PAPER_CANARY_WINDOW_SESSIONS,
    "paper_canary_required_clean_sessions": PAPER_CANARY_REQUIRED_CLEAN_SESSIONS,
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


def normalize_paper_canary_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    window_sessions = _clamp_int(
        state.get("paper_canary_window_sessions"),
        int(PAPER_CANARY_SETTINGS_DEFAULTS["paper_canary_window_sessions"]),
        minimum=1,
        maximum=20,
    )
    required_clean_sessions = _clamp_int(
        state.get("paper_canary_required_clean_sessions"),
        int(PAPER_CANARY_SETTINGS_DEFAULTS["paper_canary_required_clean_sessions"]),
        minimum=1,
        maximum=window_sessions,
    )
    return {
        "paper_canary_enabled": _coerce_bool(
            state.get("paper_canary_enabled"),
            bool(PAPER_CANARY_SETTINGS_DEFAULTS["paper_canary_enabled"]),
        ),
        "paper_canary_auto_review_enabled": _coerce_bool(
            state.get("paper_canary_auto_review_enabled"),
            bool(PAPER_CANARY_SETTINGS_DEFAULTS["paper_canary_auto_review_enabled"]),
        ),
        "paper_canary_window_sessions": window_sessions,
        "paper_canary_required_clean_sessions": required_clean_sessions,
    }


def _profile_tag(profile_key: str) -> str:
    return automation_state_control_service._profile_tag(profile_key)


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def _session_bounds_for_day(session_day: str) -> tuple[datetime, datetime]:
    local_day = date.fromisoformat(session_day)
    local_start = datetime.combine(local_day, time.min, tzinfo=MARKET_TIMEZONE)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def canary_review_session_day_for(value: datetime | None = None, *, forced: bool = False) -> tuple[str, bool]:
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


def next_eligible_canary_review_at(value: datetime | None = None) -> datetime:
    now = value or _utc_now()
    session_day, review_window_open = canary_review_session_day_for(now, forced=False)
    if review_window_open:
        return now
    return _review_time_for_session_day(session_day)


def next_eligible_canary_review_after_session(session_day: str) -> datetime:
    return _review_time_for_session_day(_next_trading_day_after(session_day))


def _recent_trading_days(now: datetime, *, count: int = PAPER_CANARY_WINDOW_SESSIONS) -> list[str]:
    cursor = now.astimezone(MARKET_TIMEZONE).date()
    days: list[str] = []
    while len(days) < max(1, count):
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return days


def normalize_paper_canary_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("paper_canary_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("paper_canary_history") or [])[:PAPER_CANARY_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "paper_canary_last_report": serialize_value(last_report),
        "paper_canary_last_note_id": str(runtime.get("paper_canary_last_note_id") or "").strip() or None,
        "paper_canary_note_session_day": str(runtime.get("paper_canary_note_session_day") or "").strip() or None,
        "paper_canary_last_run_at": _serialize_datetime(_parse_datetime(runtime.get("paper_canary_last_run_at"))),
        "paper_canary_last_scheduled_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("paper_canary_last_scheduled_run_at"))
        ),
        "paper_canary_last_scheduled_session_day": str(
            runtime.get("paper_canary_last_scheduled_session_day") or ""
        ).strip() or None,
        "paper_canary_next_eligible_run_at": (
            _serialize_datetime(_parse_datetime(runtime.get("paper_canary_next_eligible_run_at")))
            or _serialize_datetime(next_eligible_canary_review_at())
        ),
        "paper_canary_last_skipped_reason": str(runtime.get("paper_canary_last_skipped_reason") or "").strip() or None,
        "paper_canary_last_error": str(runtime.get("paper_canary_last_error") or "").strip() or None,
        "paper_canary_history": history,
    }


def build_paper_canary_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = normalize_paper_canary_runtime((state or {}).get("runtime"))
    settings = normalize_paper_canary_settings((state or {}).get("settings"))
    report = dict(runtime.get("paper_canary_last_report") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "enabled": settings["paper_canary_enabled"],
            "auto_review_enabled": settings["paper_canary_auto_review_enabled"],
            "clean_session_count": 0,
            "required_clean_sessions": settings["paper_canary_required_clean_sessions"],
            "window_session_count": 0,
            "window_days": settings["paper_canary_window_sessions"],
            "worst_state": "healthy",
            "shadow_pass_rate": 0.0,
            "ai_review_coverage": {"covered": 0, "required": 0, "ratio": 0.0},
            "note_coverage": {"covered": 0, "required": 0, "ratio": 0.0},
            "pnl_summary": {"closed_trade_count": 0, "realized_pnl": 0.0},
            "slippage_summary": {"sample_count": 0, "average_abs_bps": None, "worst_abs_bps": None},
            "blockers": [],
            "warnings": [],
            "manual_action_required": False,
            "related_note_id": runtime.get("paper_canary_last_note_id"),
            "last_run_at": runtime.get("paper_canary_last_run_at"),
            "last_scheduled_run_at": runtime.get("paper_canary_last_scheduled_run_at"),
            "last_scheduled_session_day": runtime.get("paper_canary_last_scheduled_session_day"),
            "next_eligible_run_at": runtime.get("paper_canary_next_eligible_run_at"),
            "skipped_reason": runtime.get("paper_canary_last_skipped_reason"),
            "run_source": None,
            "last_error": runtime.get("paper_canary_last_error"),
            "sessions": [],
        }
    report.setdefault("enabled", settings["paper_canary_enabled"])
    report.setdefault("auto_review_enabled", settings["paper_canary_auto_review_enabled"])
    report.setdefault("window_days", settings["paper_canary_window_sessions"])
    report.setdefault("required_clean_sessions", settings["paper_canary_required_clean_sessions"])
    report.setdefault("related_note_id", runtime.get("paper_canary_last_note_id"))
    report.setdefault("note_id", runtime.get("paper_canary_last_note_id"))
    report.setdefault("last_run_at", runtime.get("paper_canary_last_run_at"))
    report.setdefault("last_scheduled_run_at", runtime.get("paper_canary_last_scheduled_run_at"))
    report.setdefault("last_scheduled_session_day", runtime.get("paper_canary_last_scheduled_session_day"))
    report.setdefault("next_eligible_run_at", runtime.get("paper_canary_next_eligible_run_at"))
    report.setdefault("skipped_reason", runtime.get("paper_canary_last_skipped_reason"))
    report.setdefault("last_error", runtime.get("paper_canary_last_error"))
    return serialize_value(report)


def _safe_frame(reader: Any) -> pd.DataFrame:
    try:
        frame = reader()
    except Exception:
        return pd.DataFrame()
    return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()


def _note_lookup(profile_key: str) -> dict[str, dict[str, Any]]:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=PAPER_CANARY_NOTE_OWNER,
            limit=PAPER_CANARY_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return {}
    profile = _profile_tag(profile_key)
    by_day: dict[str, dict[str, Any]] = {}
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if "automation-ai" not in tags or profile not in tags:
            continue
        session_tags = [tag for tag in tags if tag.startswith("session-")]
        if not session_tags:
            continue
        session_day = session_tags[0].removeprefix("session-")
        bucket = by_day.setdefault(session_day, {})
        if "daily-review" in tags:
            bucket["daily_review"] = item
        if "state-control" in tags and "shadow-validation" not in tags and "paper-canary" not in tags:
            bucket["state_control"] = item
        if "shadow-validation" in tags:
            bucket["shadow_validation"] = item
        if "paper-canary" in tags:
            bucket["paper_canary"] = item
        if "paper-broker" in tags and "reconciliation" in tags:
            bucket["paper_broker_reconciliation"] = item
        if "paper-broker" in tags and "order-lifecycle-soak" in tags:
            bucket["paper_order_lifecycle_soak"] = item
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
        db.execute(
            select(OrderEventRecord)
            .where(OrderEventRecord.tenant_id == tenant.id)
            .where(OrderEventRecord.created_at >= start_at)
            .where(OrderEventRecord.created_at < end_at)
            .order_by(OrderEventRecord.created_at.desc())
            .limit(1000)
        )
        .scalars()
        .all()
    )
    events: list[dict[str, Any]] = []
    normalized_profile = str(profile_key or PAPER_CANARY_PERSONAL_PAPER_PROFILE).strip().lower()
    for row in rows:
        payload = dict(row.payload_json or {})
        payload_trade = dict(payload.get("trade") or {})
        row_profile = str(
            payload.get("automation_profile_key")
            or payload_trade.get("automation_profile_key")
            or ""
        ).strip().lower()
        if row_profile and row_profile != normalized_profile:
            continue
        if not row_profile and normalized_profile != PAPER_CANARY_PERSONAL_PAPER_PROFILE:
            continue
        if not (
            str(payload.get("automation_cycle_id") or "").strip()
            or str(payload_trade.get("automation_origin") or "").strip().lower() == "trade_automation"
            or row_profile
        ):
            continue
        slippage = None
        if payload.get("slippage_bps") is not None:
            slippage = _coerce_float(payload.get("slippage_bps"), 0.0)
        events.append(
            {
                "event_key": row.event_key,
                "status": row.status,
                "ticker": row.ticker,
                "detail": row.detail,
                "created_at": _serialize_datetime(row.created_at),
                "slippage_bps": slippage,
            }
        )
    return events


def _runtime_shadow_by_day(runtime: dict[str, Any], session_day: str) -> dict[str, Any]:
    last_report = runtime.get("state_control_shadow_last_report")
    if isinstance(last_report, dict) and str(last_report.get("session_day") or "").strip() == session_day:
        return dict(last_report)
    for item in list(runtime.get("state_control_shadow_report_history") or []):
        if not isinstance(item, dict):
            continue
        item_day = str(item.get("session_day") or "").strip()
        if not item_day:
            parsed = _parse_datetime(item.get("at") or item.get("evaluated_at"))
            item_day = _session_day_for(parsed) if parsed else ""
        if item_day == session_day:
            return dict(item)
    return {}


def _runtime_ai_by_day(runtime: dict[str, Any], session_day: str) -> dict[str, Any]:
    journal = runtime.get("ai_daily_journal")
    if isinstance(journal, dict) and isinstance(journal.get(session_day), dict):
        item = dict(journal[session_day])
        review = item.get("review")
        if isinstance(review, dict):
            item["review"] = dict(review)
        return item
    for item in list(runtime.get("ai_review_history") or []):
        if isinstance(item, dict) and str(item.get("session_day") or "").strip() == session_day:
            return {"review": dict(item)}
    return {}


def _runtime_state_by_day(runtime: dict[str, Any], session_day: str) -> dict[str, Any]:
    last = runtime.get("state_control_last_evaluation")
    if isinstance(last, dict) and str(last.get("session_day") or "").strip() == session_day:
        return dict(last)
    transitions = [
        dict(item)
        for item in list(runtime.get("state_control_transition_history") or [])
        if isinstance(item, dict)
    ]
    for item in transitions:
        parsed = _parse_datetime(item.get("at"))
        if parsed and _session_day_for(parsed) == session_day:
            return {
                "state": automation_state_control_service._normalize_state(item.get("to")),
                "score": item.get("score"),
                "evaluated_at": item.get("at"),
                "last_transition": item,
            }
    return {}


def _runtime_paper_broker_by_day(runtime: dict[str, Any], session_day: str) -> dict[str, Any]:
    last = runtime.get("paper_broker_reconciliation_last_report")
    if isinstance(last, dict) and str(last.get("session_day") or "").strip() == session_day:
        return dict(last)
    for item in list(runtime.get("paper_broker_reconciliation_history") or []):
        if not isinstance(item, dict):
            continue
        item_day = str(item.get("session_day") or "").strip()
        if not item_day:
            parsed = _parse_datetime(item.get("at") or item.get("checked_at"))
            item_day = _session_day_for(parsed) if parsed else ""
        if item_day == session_day:
            return dict(item)
    return {}


def _runtime_order_lifecycle_by_day(runtime: dict[str, Any], session_day: str) -> dict[str, Any]:
    last = runtime.get("paper_order_lifecycle_soak_last_report")
    if isinstance(last, dict) and str(last.get("session_day") or "").strip() == session_day:
        return dict(last)
    for item in list(runtime.get("paper_order_lifecycle_soak_history") or []):
        if not isinstance(item, dict):
            continue
        item_day = str(item.get("session_day") or "").strip()
        if not item_day:
            parsed = _parse_datetime(item.get("at") or item.get("checked_at"))
            item_day = _session_day_for(parsed) if parsed else ""
        if item_day == session_day:
            return dict(item)
    return {}


def _state_severity(state: Any) -> int:
    return automation_state_control_service.STATE_SEVERITY.get(
        automation_state_control_service._normalize_state(state),
        0,
    )


def _slippage_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    values = [abs(_coerce_float(item.get("slippage_bps"), 0.0)) for item in events if item.get("slippage_bps") is not None]
    return {
        "sample_count": len(values),
        "average_abs_bps": float(sum(values) / len(values)) if values else None,
        "worst_abs_bps": float(max(values)) if values else None,
    }


def _closed_summary(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {"closed_trade_count": 0, "realized_pnl": 0.0, "win_rate": None, "loss_streak": 0}
    pnl = pd.to_numeric(rows.get("realized_pnl"), errors="coerce").fillna(0.0)
    wins = int((pnl > 0).sum())
    return {
        "closed_trade_count": int(len(rows)),
        "realized_pnl": float(pnl.sum()),
        "win_rate": float(wins / len(rows)) if len(rows) else None,
        "loss_streak": automation_ai_review_service._count_recent_loss_streak(rows),
    }


def _settings_changes_for_day(runtime: dict[str, Any], session_day: str) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for item in list(runtime.get("ai_review_history") or []):
        if not isinstance(item, dict) or str(item.get("session_day") or "").strip() != session_day:
            continue
        changes.extend([dict(change) for change in list(item.get("applied_changes") or []) if isinstance(change, dict)])
    adjustment = runtime.get("ai_last_adjustment")
    if isinstance(adjustment, dict) and str(adjustment.get("session_day") or "").strip() == session_day:
        changes.extend([dict(change) for change in list(adjustment.get("applied_changes") or []) if isinstance(change, dict)])
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for change in changes:
        key = (str(change.get("field")), str(change.get("before")), str(change.get("after")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(change)
    return deduped


def _has_session_evidence(
    *,
    notes: dict[str, Any],
    ai_item: dict[str, Any],
    state_item: dict[str, Any],
    shadow_item: dict[str, Any],
    paper_broker_item: dict[str, Any],
    lifecycle_item: dict[str, Any],
    closed: dict[str, Any],
    events: list[dict[str, Any]],
) -> bool:
    if any(notes.get(key) for key in ("daily_review", "state_control", "shadow_validation", "paper_broker_reconciliation", "paper_order_lifecycle_soak")):
        return True
    if ai_item or state_item or shadow_item or paper_broker_item or lifecycle_item:
        return True
    if int(closed.get("closed_trade_count") or 0) > 0 or events:
        return True
    return False


def _session_report(
    *,
    session_day: str,
    notes: dict[str, Any],
    ai_item: dict[str, Any],
    state_item: dict[str, Any],
    shadow_item: dict[str, Any],
    paper_broker_item: dict[str, Any],
    lifecycle_item: dict[str, Any],
    closed: dict[str, Any],
    events: list[dict[str, Any]],
    runtime: dict[str, Any],
    current_day: str,
    state: dict[str, Any],
    rollout_readiness: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    daily_note = notes.get("daily_review")
    state_note = notes.get("state_control")
    shadow_note = notes.get("shadow_validation")
    paper_broker_note = notes.get("paper_broker_reconciliation")
    lifecycle_note = notes.get("paper_order_lifecycle_soak")
    ai_review_present = bool(daily_note or ai_item.get("review") or ai_item.get("observations"))
    state_review_present = bool(state_note or state_item)
    shadow_status = str(shadow_item.get("status") or ("pass" if shadow_note else "")).strip().lower()
    shadow_failed = _coerce_int(shadow_item.get("failed_count"), 0)
    paper_broker_status = str(paper_broker_item.get("status") or ("clean" if paper_broker_note else "")).strip().lower()
    lifecycle_status = str(lifecycle_item.get("status") or ("completed" if lifecycle_note else "")).strip().lower()
    state_value = automation_state_control_service._normalize_state(state_item.get("state") or "healthy")
    status_counts = Counter(str(item.get("status") or "").strip().lower() for item in events)
    failed_events = int(status_counts.get("failed", 0) + status_counts.get("error", 0))
    rejected_events = int(status_counts.get("rejected", 0))
    slippage = _slippage_summary(events)
    settings_changes = _settings_changes_for_day(runtime, session_day)

    if not ai_review_present:
        blockers.append({"key": "ai_review_missing", "detail": "Daily AI review note or journal entry is missing."})
    if not state_review_present:
        blockers.append({"key": "state_control_missing", "detail": "State-control review note or runtime evaluation is missing."})
    if not shadow_status:
        blockers.append({"key": "shadow_validation_missing", "detail": "State-control shadow validation is missing."})
    elif shadow_status != "pass" or shadow_failed:
        blockers.append({"key": "shadow_validation_failed", "detail": "State-control shadow validation did not pass."})
    if state_value == "halt":
        blockers.append({"key": "state_control_halt", "detail": "State-control is still in halt and requires manual recovery."})
    if paper_broker_status in {"blocked", "fail", "failed"} or paper_broker_item.get("blockers"):
        blockers.append({"key": "paper_broker_reconciliation_blocked", "detail": "Paper broker reconciliation has unresolved broker/local mismatches."})
    elif paper_broker_status == "warning" or paper_broker_item.get("warnings"):
        warnings.append({"key": "paper_broker_reconciliation_warning", "detail": "Paper broker reconciliation has warnings that should be reviewed."})
    if lifecycle_status in {"blocked", "fail", "failed"} or lifecycle_item.get("blockers"):
        blockers.append({"key": "paper_order_lifecycle_soak_blocked", "detail": "Paper order lifecycle soak has unresolved submit, fill, cancel, close, or reconciliation blockers."})
    elif lifecycle_status == "warning" or lifecycle_item.get("warnings"):
        warnings.append({"key": "paper_order_lifecycle_soak_warning", "detail": "Paper order lifecycle soak completed with advisory warnings."})
    if failed_events:
        blockers.append({"key": "order_event_failed", "detail": f"{failed_events} automation order event failure(s) were recorded."})
    if rejected_events:
        warnings.append({"key": "order_event_rejected", "detail": f"{rejected_events} rejected automation order event(s) were recorded."})
    if slippage["sample_count"]:
        average = slippage["average_abs_bps"] or 0.0
        worst = slippage["worst_abs_bps"] or 0.0
        if average > PAPER_CANARY_MAX_AVG_SLIPPAGE_BPS or worst > PAPER_CANARY_MAX_WORST_SLIPPAGE_BPS:
            blockers.append(
                {
                    "key": "slippage_blocked",
                    "detail": f"Slippage exceeded canary limits: avg {average:.1f} bps, worst {worst:.1f} bps.",
                }
            )
    else:
        warnings.append({"key": "slippage_sample_missing", "detail": "No slippage sample was available for this session."})
    if int(closed.get("closed_trade_count") or 0) == 0:
        warnings.append({"key": "closed_trade_sample_missing", "detail": "No closed automation trade was available for this session."})
    if session_day == current_day:
        settings_state = dict(state.get("settings") or {})
        if _coerce_bool(settings_state.get("kill_switch"), False):
            blockers.append({"key": "kill_switch_active", "detail": "The profile kill switch is active."})
        if (
            str(settings_state.get("execution_intent") or "").strip().lower() == "broker_live"
            and not bool(rollout_readiness.get("allows_live_rollout"))
        ):
            blockers.append({"key": "live_gate_locked", "detail": "Broker-live routing remains locked by rollout readiness."})
        runtime_reconciliation = str(runtime.get("current_route_reconciliation_status") or "").strip().lower()
        runtime_consistency = str(runtime.get("ledger_snapshot_consistency") or "").strip().lower()
        if runtime_reconciliation in {"failed", "issues_present", "orphaned", "inconsistent"}:
            blockers.append({"key": "reconciliation_fault", "detail": "Current-route reconciliation has unresolved issues."})
        if runtime_consistency == "inconsistent":
            blockers.append({"key": "ledger_fault", "detail": "Ledger and snapshot accounting are inconsistent."})

    clean = not blockers
    return serialize_value(
        {
            "session_day": session_day,
            "status": "clean" if clean else "blocked",
            "clean": clean,
            "blockers": blockers,
            "warnings": warnings,
            "ai_review": {
                "covered": ai_review_present,
                "note_id": daily_note.get("id") if isinstance(daily_note, dict) else ai_item.get("note_id"),
                "observation_count": len(list(ai_item.get("observations") or [])),
                "reviewed": bool(ai_item.get("review")),
            },
            "state_control": {
                "covered": state_review_present,
                "state": state_value,
                "score": state_item.get("score"),
                "note_id": state_note.get("id") if isinstance(state_note, dict) else state_item.get("note_id"),
            },
            "shadow_validation": {
                "covered": bool(shadow_status),
                "status": shadow_status or "missing",
                "failed_count": shadow_failed,
                "note_id": shadow_note.get("id") if isinstance(shadow_note, dict) else shadow_item.get("note_id"),
            },
            "paper_broker_reconciliation": {
                "covered": bool(paper_broker_status),
                "status": paper_broker_status or "missing",
                "matched_count": _coerce_int(paper_broker_item.get("matched_count"), 0),
                "blocker_count": len(list(paper_broker_item.get("blockers") or [])),
                "note_id": (
                    paper_broker_note.get("id")
                    if isinstance(paper_broker_note, dict)
                    else paper_broker_item.get("related_note_id") or paper_broker_item.get("note_id")
                ),
            },
            "paper_order_lifecycle_soak": {
                "covered": bool(lifecycle_status),
                "status": lifecycle_status or "missing",
                "terminal_state": lifecycle_item.get("terminal_state"),
                "broker_order_id": lifecycle_item.get("broker_order_id"),
                "blocker_count": len(list(lifecycle_item.get("blockers") or [])),
                "note_id": (
                    lifecycle_note.get("id")
                    if isinstance(lifecycle_note, dict)
                    else lifecycle_item.get("related_note_id") or lifecycle_item.get("note_id")
                ),
            },
            "order_events": {
                "count": len(events),
                "failed_count": failed_events,
                "rejected_count": rejected_events,
            },
            "pnl": closed,
            "slippage": slippage,
            "settings_changed": bool(settings_changes),
            "setting_changes": settings_changes[:8],
        }
    )


def _aggregate_report(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None,
    rollout_readiness: dict[str, Any] | None,
    db: Session | None,
    now: datetime,
    review_session_day: str | None = None,
) -> dict[str, Any]:
    runtime = dict(state.get("runtime") or {})
    settings = normalize_paper_canary_settings(state.get("settings"))
    window_days = int(settings["paper_canary_window_sessions"])
    required_clean_sessions = int(settings["paper_canary_required_clean_sessions"])
    notes_by_day = _note_lookup(profile_key)
    current_day = str(review_session_day or "").strip() or _session_day_for(now)
    anchor = datetime.combine(date.fromisoformat(current_day), time(12), tzinfo=MARKET_TIMEZONE)
    session_days = _recent_trading_days(anchor, count=window_days)
    closed_all = automation_ai_review_service._owned_automation_rows(
        _safe_frame(sdm.read_closed_trades),
        tenant_id=str(tenant.id),
        profile_key=profile_key,
    )
    sessions: list[dict[str, Any]] = []
    for session_day in session_days:
        start_at, end_at = _session_bounds_for_day(session_day)
        day_closed = automation_ai_review_service._closed_rows_for_session(closed_all, session_day=session_day)
        events = _recent_order_events(db, tenant=tenant, profile_key=profile_key, start_at=start_at, end_at=end_at)
        notes = dict(notes_by_day.get(session_day) or {})
        ai_item = _runtime_ai_by_day(runtime, session_day)
        state_item = _runtime_state_by_day(runtime, session_day)
        shadow_item = _runtime_shadow_by_day(runtime, session_day)
        paper_broker_item = _runtime_paper_broker_by_day(runtime, session_day)
        lifecycle_item = _runtime_order_lifecycle_by_day(runtime, session_day)
        closed = _closed_summary(day_closed)
        if not _has_session_evidence(
            notes=notes,
            ai_item=ai_item,
            state_item=state_item,
            shadow_item=shadow_item,
            paper_broker_item=paper_broker_item,
            lifecycle_item=lifecycle_item,
            closed=closed,
            events=events,
        ):
            continue
        sessions.append(
            _session_report(
                session_day=session_day,
                notes=notes,
                ai_item=ai_item,
                state_item=state_item,
                shadow_item=shadow_item,
                paper_broker_item=paper_broker_item,
                lifecycle_item=lifecycle_item,
                closed=closed,
                events=events,
                runtime=runtime,
                current_day=current_day,
                state=state,
                rollout_readiness=dict(rollout_readiness or {}),
            )
        )

    clean_sessions = [item for item in sessions if item.get("clean")]
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
    if profile_key != PAPER_CANARY_PERSONAL_PAPER_PROFILE:
        blockers.insert(
            0,
            {
                "session_day": current_day,
                "key": "paper_profile_required",
                "detail": "V1 paper canary readiness only targets the personal paper automation profile.",
            },
        )
    if not sessions:
        warnings.append(
            {
                "session_day": current_day,
                "key": "no_paper_sessions",
                "detail": "No paper canary evidence was found in the recent trading-day window.",
            }
        )
    lifecycle_canary = dict(runtime.get("paper_order_lifecycle_canary_last_report") or {})
    lifecycle_canary_enabled = _coerce_bool(
        dict(state.get("settings") or {}).get("paper_order_lifecycle_canary_enabled"),
        True,
    )
    lifecycle_canary_status = str(lifecycle_canary.get("status") or "").strip().lower()
    if lifecycle_canary_enabled:
        if lifecycle_canary_status != "ready":
            blockers.append(
                {
                    "session_day": current_day,
                    "key": "paper_order_lifecycle_canary_not_ready",
                    "detail": "Multi-session paper order lifecycle canary evidence is not ready yet.",
                }
            )
        elif lifecycle_canary.get("blockers"):
            blockers.append(
                {
                    "session_day": current_day,
                    "key": "paper_order_lifecycle_canary_blocked",
                    "detail": "Multi-session paper order lifecycle canary has unresolved blockers.",
                }
            )

    state_values = [((item.get("state_control") or {}).get("state") or "healthy") for item in sessions]
    worst_state = max(state_values, key=_state_severity, default="healthy")
    shadow_sessions = [item for item in sessions if (item.get("shadow_validation") or {}).get("covered")]
    shadow_passed = [
        item
        for item in shadow_sessions
        if str((item.get("shadow_validation") or {}).get("status") or "").lower() == "pass"
        and _coerce_int((item.get("shadow_validation") or {}).get("failed_count"), 0) == 0
    ]
    ai_covered = sum(1 for item in sessions if (item.get("ai_review") or {}).get("covered"))
    note_covered = sum(
        1
        for item in sessions
        if (item.get("ai_review") or {}).get("note_id")
        and (item.get("state_control") or {}).get("note_id")
        and (item.get("shadow_validation") or {}).get("note_id")
        and (item.get("paper_broker_reconciliation") or {}).get("note_id")
        and (item.get("paper_order_lifecycle_soak") or {}).get("note_id")
    )
    closed_trade_count = sum(_coerce_int((item.get("pnl") or {}).get("closed_trade_count"), 0) for item in sessions)
    realized_pnl = sum(_coerce_float((item.get("pnl") or {}).get("realized_pnl"), 0.0) for item in sessions)
    positive_sessions = sum(1 for item in sessions if _coerce_float((item.get("pnl") or {}).get("realized_pnl"), 0.0) > 0)
    slippage_values: list[float] = []
    for item in sessions:
        slippage = dict(item.get("slippage") or {})
        sample_count = _coerce_int(slippage.get("sample_count"), 0)
        average = slippage.get("average_abs_bps")
        if sample_count and average is not None:
            slippage_values.extend([_coerce_float(average, 0.0)] * sample_count)
    worst_slippage_values = [
        _coerce_float((item.get("slippage") or {}).get("worst_abs_bps"), 0.0)
        for item in sessions
        if (item.get("slippage") or {}).get("worst_abs_bps") is not None
    ]
    clean_count = len(clean_sessions)
    status = "ready" if clean_count >= required_clean_sessions and not blockers else "collecting"
    if blockers:
        status = "blocked"
    label = {
        "ready": "Paper canary ready",
        "collecting": "Collecting paper canary",
        "blocked": "Paper canary blocked",
    }.get(status, "Paper canary")
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
            "worst_state": worst_state,
            "shadow_pass_rate": float(len(shadow_passed) / len(shadow_sessions)) if shadow_sessions else 0.0,
            "ai_review_coverage": {
                "covered": ai_covered,
                "required": len(sessions),
                "ratio": float(ai_covered / len(sessions)) if sessions else 0.0,
            },
            "note_coverage": {
                "covered": note_covered,
                "required": len(sessions),
                "ratio": float(note_covered / len(sessions)) if sessions else 0.0,
            },
            "pnl_summary": {
                "closed_trade_count": closed_trade_count,
                "realized_pnl": float(realized_pnl),
                "positive_session_count": positive_sessions,
            },
            "slippage_summary": {
                "sample_count": len(slippage_values),
                "average_abs_bps": float(sum(slippage_values) / len(slippage_values)) if slippage_values else None,
                "worst_abs_bps": float(max(worst_slippage_values)) if worst_slippage_values else None,
                "max_average_abs_bps": PAPER_CANARY_MAX_AVG_SLIPPAGE_BPS,
                "max_worst_abs_bps": PAPER_CANARY_MAX_WORST_SLIPPAGE_BPS,
            },
            "paper_order_lifecycle_canary": {
                "enabled": lifecycle_canary_enabled,
                "status": lifecycle_canary_status or "missing",
                "clean_session_count": _coerce_int(lifecycle_canary.get("clean_session_count"), 0),
                "required_clean_sessions": _coerce_int(lifecycle_canary.get("required_clean_sessions"), 0),
                "window_session_count": _coerce_int(lifecycle_canary.get("window_session_count"), 0),
                "latest_soak_status": lifecycle_canary.get("latest_soak_status") or "missing",
                "latest_terminal_state": lifecycle_canary.get("latest_terminal_state"),
                "latest_reconciliation_status": lifecycle_canary.get("latest_reconciliation_status") or "missing",
                "blocker_count": len(list(lifecycle_canary.get("blockers") or [])),
                "note_id": lifecycle_canary.get("related_note_id") or lifecycle_canary.get("note_id"),
            },
            "blockers": blockers[:20],
            "warnings": warnings[:20],
            "manual_action_required": bool(blockers),
            "settings_changed_during_window": any(bool(item.get("settings_changed")) for item in sessions),
            "sessions": sessions,
            "rollout_readiness": serialize_value(rollout_readiness or {}),
        }
    )


def _find_existing_canary_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=PAPER_CANARY_NOTE_OWNER,
            limit=PAPER_CANARY_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {"automation-ai", "paper-canary", "state-control", _profile_tag(profile_key), f"session-{session_day}"}
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
        f"Automation paper canary review for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Run source: {str(report.get('run_source') or 'manual').replace('_', ' ')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Clean sessions: {report.get('clean_session_count', 0)} / {report.get('required_clean_sessions', 0)} required",
        f"Window sessions with evidence: {report.get('window_session_count', 0)}",
        (
            f"Evidence window: {evidence_window.get('start_session_day')} through {evidence_window.get('end_session_day')}"
            if evidence_window
            else f"Evidence window: {report.get('window_days', PAPER_CANARY_WINDOW_SESSIONS)} trading session(s)"
        ),
        (
            f"Next scheduled review: {report.get('next_eligible_run_at')}"
            if report.get("next_eligible_run_at")
            else "Next scheduled review: not available"
        ),
        (
            "Lifecycle canary: "
            f"{str((report.get('paper_order_lifecycle_canary') or {}).get('status') or 'missing').upper()} | "
            f"{(report.get('paper_order_lifecycle_canary') or {}).get('clean_session_count', 0)} / "
            f"{(report.get('paper_order_lifecycle_canary') or {}).get('required_clean_sessions', 0)} clean"
        ),
        f"Worst state: {str(report.get('worst_state') or 'healthy').replace('_', ' ')}",
        f"Shadow pass rate: {_coerce_float(report.get('shadow_pass_rate'), 0.0) * 100:.0f}%",
        f"AI review coverage: {dict(report.get('ai_review_coverage') or {}).get('covered', 0)} / {dict(report.get('ai_review_coverage') or {}).get('required', 0)}",
        f"Note coverage: {dict(report.get('note_coverage') or {}).get('covered', 0)} / {dict(report.get('note_coverage') or {}).get('required', 0)}",
        f"PnL: {_coerce_float(pnl.get('realized_pnl'), 0.0):.2f} across {_coerce_int(pnl.get('closed_trade_count'), 0)} closed trade(s)",
        (
            "Slippage: no sample"
            if slippage.get("average_abs_bps") is None
            else f"Slippage: avg {_coerce_float(slippage.get('average_abs_bps'), 0.0):.1f} bps | worst {_coerce_float(slippage.get('worst_abs_bps'), 0.0):.1f} bps"
        ),
        f"Settings changed during window: {'yes' if report.get('settings_changed_during_window') else 'no'}",
        "",
        "This canary is advisory. It does not place orders, cancel orders, tune baseline settings, enable live trading, arm automation, or clear locks.",
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
    for item in list(report.get("sessions") or [])[: _coerce_int(report.get("window_days"), PAPER_CANARY_WINDOW_SESSIONS)]:
        pnl_item = dict(item.get("pnl") or {})
        slip_item = dict(item.get("slippage") or {})
        lifecycle_item = dict(item.get("paper_order_lifecycle_soak") or {})
        slip_label = (
            "--"
            if slip_item.get("average_abs_bps") is None
            else f"{_coerce_float(slip_item.get('average_abs_bps'), 0.0):.1f} bps"
        )
        lines.append(
            f"- {item.get('session_day')}: {str(item.get('status') or '').upper()} | "
            f"state {(item.get('state_control') or {}).get('state') or 'healthy'} | "
            f"shadow {(item.get('shadow_validation') or {}).get('status') or 'missing'} | "
            f"lifecycle {lifecycle_item.get('status') or 'missing'} | "
            f"PnL {_coerce_float(pnl_item.get('realized_pnl'), 0.0):.2f} | "
            f"slip {slip_label} | "
            f"settings changed {'yes' if item.get('settings_changed') else 'no'}"
        )
        changes = [change for change in list(item.get("setting_changes") or []) if isinstance(change, dict)]
        if changes:
            lines.append("  Setting changes")
            for change in changes[:5]:
                lines.append(
                    f"  - {str(change.get('field') or '').replace('_', ' ')}: "
                    f"{change.get('before')} -> {change.get('after')}. {change.get('reason') or 'AI review applied a bounded setting change.'}"
                )
    return "\n".join(lines).strip()


def _sync_canary_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = ["automation-ai", "paper-canary", "state-control", _profile_tag(profile_key), f"session-{session_day}"]
    title = f"Automation paper canary - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = str(report.get("related_note_id") or report.get("note_id") or "").strip() or _find_existing_canary_note_id(profile_key, session_day)
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": PAPER_CANARY_NOTE_OWNER,
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


def run_paper_canary_review(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    rollout_readiness: dict[str, Any] | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_run_source = str(run_source or "manual").strip().lower().replace(" ", "_") or "manual"
    review_session_day, _review_window_open = canary_review_session_day_for(
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
        rollout_readiness=rollout_readiness,
        db=db,
        now=now,
        review_session_day=review_session_day,
    )
    runtime_before = dict(state.get("runtime") or {})
    report["run_source"] = normalized_run_source
    report["skipped_reason"] = None
    report["last_scheduled_run_at"] = (
        report.get("evaluated_at")
        if normalized_run_source == "scheduled"
        else _serialize_datetime(_parse_datetime(runtime_before.get("paper_canary_last_scheduled_run_at")))
    )
    report["last_scheduled_session_day"] = (
        review_session_day
        if normalized_run_source == "scheduled"
        else str(runtime_before.get("paper_canary_last_scheduled_session_day") or "").strip() or None
    )
    next_eligible = (
        next_eligible_canary_review_after_session(review_session_day)
        if normalized_run_source == "scheduled"
        else next_eligible_canary_review_at(now)
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
        "worst_state",
        "shadow_pass_rate",
        "ai_review_coverage",
        "note_coverage",
        "pnl_summary",
        "slippage_summary",
        "paper_order_lifecycle_canary",
        "blockers",
        "warnings",
        "manual_action_required",
        "settings_changed_during_window",
        "sessions",
        "evidence_window",
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
    runtime["paper_canary_last_report"] = serialize_value(summary)
    runtime["paper_canary_last_note_id"] = note_id
    runtime["paper_canary_note_session_day"] = report.get("session_day")
    runtime["paper_canary_last_run_at"] = report.get("evaluated_at")
    if normalized_run_source == "scheduled":
        runtime["paper_canary_last_scheduled_run_at"] = report.get("evaluated_at")
        runtime["paper_canary_last_scheduled_session_day"] = review_session_day
    runtime["paper_canary_next_eligible_run_at"] = report.get("next_eligible_run_at")
    runtime["paper_canary_last_skipped_reason"] = None
    runtime["paper_canary_last_error"] = None
    history = list(runtime.get("paper_canary_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "status": report.get("status"),
            "clean_session_count": report.get("clean_session_count"),
            "required_clean_sessions": report.get("required_clean_sessions"),
            "window_session_count": report.get("window_session_count"),
            "blocker_count": len(report.get("blockers") or []),
            "note_id": note_id,
            "run_source": normalized_run_source,
        },
    )
    runtime["paper_canary_history"] = serialize_value(history[:PAPER_CANARY_HISTORY_LIMIT])
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.paper_canary_reviewed",
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
