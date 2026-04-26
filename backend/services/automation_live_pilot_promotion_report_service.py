from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.services import automation_ai_review_service, notes_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

LIVE_PILOT_PROMOTION_NOTE_OWNER = "automation-ai"
LIVE_PILOT_PROMOTION_HISTORY_LIMIT = 8
LIVE_PILOT_PROMOTION_NOTE_LIMIT = 250
LIVE_PILOT_PROMOTION_REQUIRED_WINDOW_CLEAN_SESSIONS = 3
LIVE_PILOT_PROMOTION_STALE_AFTER_DAYS = 2
LIVE_PILOT_PROMOTION_PAPER_PROFILE = "personal_paper"
LIVE_PILOT_PROMOTION_LIVE_PROFILE = "personal_live"
LIVE_PILOT_PROMOTION_BLOCK_SLIPPAGE_BPS = 100.0
LIVE_PILOT_PROMOTION_WARN_SLIPPAGE_BPS = 50.0

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE

LIVE_PILOT_PROMOTION_SETTINGS_DEFAULTS: dict[str, Any] = {
    "live_pilot_promotion_report_enabled": True,
    "live_pilot_promotion_report_auto_review_enabled": True,
    "live_pilot_promotion_required_window_clean_sessions": LIVE_PILOT_PROMOTION_REQUIRED_WINDOW_CLEAN_SESSIONS,
    "live_pilot_promotion_stale_after_days": LIVE_PILOT_PROMOTION_STALE_AFTER_DAYS,
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
    return "profile-" + str(profile_key or LIVE_PILOT_PROMOTION_PAPER_PROFILE).strip().lower().replace(":", "-")


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def live_pilot_promotion_session_day_for(
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


def next_eligible_live_pilot_promotion_review_at(value: datetime | None = None) -> datetime:
    now = value or _utc_now()
    session_day, review_window_open = live_pilot_promotion_session_day_for(now, forced=False)
    if review_window_open:
        return now
    return _review_time_for_session_day(session_day)


def next_eligible_live_pilot_promotion_review_after_session(session_day: str) -> datetime:
    return _review_time_for_session_day(_next_trading_day_after(session_day))


def normalize_live_pilot_promotion_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    return {
        "live_pilot_promotion_report_enabled": _coerce_bool(
            state.get("live_pilot_promotion_report_enabled"),
            bool(LIVE_PILOT_PROMOTION_SETTINGS_DEFAULTS["live_pilot_promotion_report_enabled"]),
        ),
        "live_pilot_promotion_report_auto_review_enabled": _coerce_bool(
            state.get("live_pilot_promotion_report_auto_review_enabled"),
            bool(LIVE_PILOT_PROMOTION_SETTINGS_DEFAULTS["live_pilot_promotion_report_auto_review_enabled"]),
        ),
        "live_pilot_promotion_required_window_clean_sessions": _clamp_int(
            state.get("live_pilot_promotion_required_window_clean_sessions"),
            int(LIVE_PILOT_PROMOTION_SETTINGS_DEFAULTS["live_pilot_promotion_required_window_clean_sessions"]),
            minimum=1,
            maximum=20,
        ),
        "live_pilot_promotion_stale_after_days": _clamp_int(
            state.get("live_pilot_promotion_stale_after_days"),
            int(LIVE_PILOT_PROMOTION_SETTINGS_DEFAULTS["live_pilot_promotion_stale_after_days"]),
            minimum=1,
            maximum=30,
        ),
    }


def normalize_live_pilot_promotion_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("live_pilot_promotion_report_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("live_pilot_promotion_report_history") or [])[
            :LIVE_PILOT_PROMOTION_HISTORY_LIMIT
        ]
        if isinstance(item, dict)
    ]
    return {
        "live_pilot_promotion_report_last_report": serialize_value(last_report),
        "live_pilot_promotion_report_last_note_id": str(
            runtime.get("live_pilot_promotion_report_last_note_id") or ""
        ).strip()
        or None,
        "live_pilot_promotion_report_note_session_day": str(
            runtime.get("live_pilot_promotion_report_note_session_day") or ""
        ).strip()
        or None,
        "live_pilot_promotion_report_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("live_pilot_promotion_report_last_run_at"))
        ),
        "live_pilot_promotion_report_last_scheduled_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("live_pilot_promotion_report_last_scheduled_run_at"))
        ),
        "live_pilot_promotion_report_last_scheduled_session_day": str(
            runtime.get("live_pilot_promotion_report_last_scheduled_session_day") or ""
        ).strip()
        or None,
        "live_pilot_promotion_report_next_eligible_run_at": (
            _serialize_datetime(_parse_datetime(runtime.get("live_pilot_promotion_report_next_eligible_run_at")))
            or _serialize_datetime(next_eligible_live_pilot_promotion_review_at())
        ),
        "live_pilot_promotion_report_last_skipped_reason": str(
            runtime.get("live_pilot_promotion_report_last_skipped_reason") or ""
        ).strip()
        or None,
        "live_pilot_promotion_report_last_error": str(
            runtime.get("live_pilot_promotion_report_last_error") or ""
        ).strip()
        or None,
        "live_pilot_promotion_report_history": history,
    }


def build_live_pilot_promotion_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = normalize_live_pilot_promotion_runtime((state or {}).get("runtime"))
    settings = normalize_live_pilot_promotion_settings((state or {}).get("settings"))
    report = dict(runtime.get("live_pilot_promotion_report_last_report") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "enabled": settings["live_pilot_promotion_report_enabled"],
            "auto_review_enabled": settings["live_pilot_promotion_report_auto_review_enabled"],
            "required_window_clean_sessions": settings["live_pilot_promotion_required_window_clean_sessions"],
            "stale_after_days": settings["live_pilot_promotion_stale_after_days"],
            "evidence_summaries": {},
            "clean_session_progress": {"clean": 0, "required": settings["live_pilot_promotion_required_window_clean_sessions"]},
            "broker_live_gate_status": "unknown",
            "safety_lock_status": "unknown",
            "pnl_summary": {"sample_count": 0, "realized_pnl": 0.0},
            "slippage_summary": {"sample_count": 0, "average_abs_bps": None, "worst_abs_bps": None},
            "blockers": [],
            "warnings": [],
            "required_operator_actions": [],
            "manual_action_required": False,
            "related_note_id": runtime.get("live_pilot_promotion_report_last_note_id"),
            "note_id": runtime.get("live_pilot_promotion_report_last_note_id"),
            "last_run_at": runtime.get("live_pilot_promotion_report_last_run_at"),
            "last_scheduled_run_at": runtime.get("live_pilot_promotion_report_last_scheduled_run_at"),
            "last_scheduled_session_day": runtime.get("live_pilot_promotion_report_last_scheduled_session_day"),
            "next_eligible_run_at": runtime.get("live_pilot_promotion_report_next_eligible_run_at"),
            "skipped_reason": runtime.get("live_pilot_promotion_report_last_skipped_reason"),
            "last_error": runtime.get("live_pilot_promotion_report_last_error"),
        }
    report.setdefault("enabled", settings["live_pilot_promotion_report_enabled"])
    report.setdefault("auto_review_enabled", settings["live_pilot_promotion_report_auto_review_enabled"])
    report.setdefault("required_window_clean_sessions", settings["live_pilot_promotion_required_window_clean_sessions"])
    report.setdefault("stale_after_days", settings["live_pilot_promotion_stale_after_days"])
    report.setdefault("related_note_id", runtime.get("live_pilot_promotion_report_last_note_id"))
    report.setdefault("note_id", runtime.get("live_pilot_promotion_report_last_note_id"))
    report.setdefault("last_run_at", runtime.get("live_pilot_promotion_report_last_run_at"))
    report.setdefault("last_scheduled_run_at", runtime.get("live_pilot_promotion_report_last_scheduled_run_at"))
    report.setdefault("last_scheduled_session_day", runtime.get("live_pilot_promotion_report_last_scheduled_session_day"))
    report.setdefault("next_eligible_run_at", runtime.get("live_pilot_promotion_report_next_eligible_run_at"))
    report.setdefault("skipped_reason", runtime.get("live_pilot_promotion_report_last_skipped_reason"))
    report.setdefault("last_error", runtime.get("live_pilot_promotion_report_last_error"))
    return serialize_value(report)


def _issue(key: str, detail: str, *, component: str, severity: str = "blocker") -> dict[str, Any]:
    return {"key": key, "component": component, "severity": severity, "detail": detail}


def _report_status(report: dict[str, Any] | None) -> str:
    if not isinstance(report, dict) or not report:
        return "missing"
    return str(report.get("status") or "missing").strip().lower() or "missing"


def _report_note_id(report: dict[str, Any] | None) -> str | None:
    if not isinstance(report, dict):
        return None
    value = str(report.get("related_note_id") or report.get("note_id") or "").strip()
    return value or None


def _report_timestamp(report: dict[str, Any] | None) -> datetime | None:
    if not isinstance(report, dict):
        return None
    for key in ("evaluated_at", "checked_at", "last_run_at", "at"):
        parsed = _parse_datetime(report.get(key))
        if parsed is not None:
            return parsed
    return None


def _age_days(now: datetime, timestamp: datetime | None) -> float | None:
    if timestamp is None:
        return None
    return max(0.0, (now - timestamp).total_seconds() / 86400.0)


def _evidence_summary(
    *,
    now: datetime,
    key: str,
    label: str,
    report: dict[str, Any],
    ready_statuses: set[str],
    stale_after_days: int,
) -> dict[str, Any]:
    status = _report_status(report)
    timestamp = _report_timestamp(report)
    age = _age_days(now, timestamp)
    note_id = _report_note_id(report)
    return {
        "key": key,
        "label": label,
        "status": status,
        "ready": status in ready_statuses,
        "evaluated_at": _serialize_datetime(timestamp),
        "age_days": age,
        "stale": bool(age is None or age > stale_after_days),
        "note_id": note_id,
        "blocker_count": len([item for item in list(report.get("blockers") or []) if isinstance(item, dict)]),
        "warning_count": len([item for item in list(report.get("warnings") or []) if isinstance(item, dict)]),
        "clean_session_count": _coerce_int(report.get("clean_session_count"), 0),
        "required_clean_sessions": _coerce_int(report.get("required_clean_sessions"), 0),
    }


def _recent_live_order_event_summary(
    db: Session | None,
    *,
    tenant: Tenant,
    now: datetime,
    stale_after_days: int,
) -> dict[str, Any]:
    if db is None:
        return {"count": 0, "submitted_count": 0, "terminal_count": 0, "failed_count": 0}
    start_at = now - timedelta(days=max(1, stale_after_days + 5))
    rows = (
        db.query(OrderEventRecord)
        .filter(OrderEventRecord.tenant_id == tenant.id)
        .filter(OrderEventRecord.created_at >= start_at)
        .filter(OrderEventRecord.created_at <= now)
        .order_by(OrderEventRecord.created_at.asc())
        .all()
    )
    matched: list[OrderEventRecord] = []
    for row in rows:
        payload = dict(row.payload_json or {})
        row_profile = str(payload.get("automation_profile_key") or payload.get("profile_key") or "").strip()
        has_live_pilot_key = any(str(payload.get(key) or "").strip() for key in (
            "live_pilot_soak_id",
            "live_pilot_expansion_id",
            "live_pilot_window_id",
        ))
        route_family = str(payload.get("route_family") or payload.get("source") or "").strip().lower()
        if row_profile == LIVE_PILOT_PROMOTION_LIVE_PROFILE or has_live_pilot_key or route_family.startswith("live_pilot"):
            matched.append(row)
    failed = [
        row
        for row in matched
        if str(row.status or "").strip().lower() in {"failed", "error", "rejected", "blocked"}
    ]
    return {
        "count": len(matched),
        "submitted_count": sum(1 for row in matched if str(row.event_key or "").strip().lower() == "order.submitted"),
        "terminal_count": sum(
            1
            for row in matched
            if str(row.event_key or "").strip().lower() in {"order.canceled", "order.closed"}
        ),
        "failed_count": len(failed),
        "latest_event_at": _serialize_datetime(matched[-1].created_at) if matched else None,
    }


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
    settings = normalize_live_pilot_promotion_settings(paper_state.get("settings"))
    live_state = live_state if isinstance(live_state, dict) else {}
    live_settings = dict(live_state.get("settings") or {})
    current_day = str(review_session_day or "").strip() or _session_day_for(now)
    stale_after_days = int(settings["live_pilot_promotion_stale_after_days"])
    required_window_clean = int(settings["live_pilot_promotion_required_window_clean_sessions"])

    evidence_specs = {
        "paper_canary": (
            "Paper canary",
            dict(runtime.get("paper_canary_last_report") or {}),
            {"ready"},
        ),
        "paper_broker_reconciliation": (
            "Paper broker reconciliation",
            dict(runtime.get("paper_broker_reconciliation_last_report") or {}),
            {"clean"},
        ),
        "live_pilot_readiness": (
            "Live pilot readiness",
            dict(runtime.get("live_pilot_readiness_last_report") or {}),
            {"ready_to_request_approval"},
        ),
        "live_pilot_canary": (
            "Tiny live pilot canary",
            dict(runtime.get("live_pilot_canary_last_report") or {}),
            {"ready"},
        ),
        "live_pilot_expansion_canary": (
            "Live expansion canary",
            dict(runtime.get("live_pilot_expansion_canary_last_report") or {}),
            {"ready"},
        ),
        "live_pilot_window_canary": (
            "Supervised live pilot canary",
            dict(runtime.get("live_pilot_window_canary_last_report") or {}),
            {"ready"},
        ),
    }
    evidence_summaries: dict[str, Any] = {}
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for key, (label, report, ready_statuses) in evidence_specs.items():
        summary = _evidence_summary(
            now=now,
            key=key,
            label=label,
            report=report,
            ready_statuses=ready_statuses,
            stale_after_days=stale_after_days,
        )
        evidence_summaries[key] = summary
        status = str(summary.get("status") or "missing")
        if status in {"missing", "not_run"}:
            blockers.append(_issue(f"{key}_missing", f"{label} evidence is missing.", component=key))
            continue
        if summary.get("stale"):
            blockers.append(_issue(f"{key}_stale", f"{label} evidence is stale.", component=key))
        if not summary.get("note_id"):
            blockers.append(_issue(f"{key}_note_missing", f"{label} is missing a linked automation-ai note.", component="notes"))
        if report.get("blockers"):
            blockers.append(_issue(f"{key}_blocked", f"{label} has unresolved blockers.", component=key))
        elif status == "warning" or report.get("warnings"):
            warnings.append(
                _issue(f"{key}_warning", f"{label} has advisory warnings.", component=key, severity="warning")
            )
        if not summary.get("ready"):
            if status in {"blocked", "failed", "fail", "collecting"}:
                blockers.append(_issue(f"{key}_not_ready", f"{label} is not ready.", component=key))
            elif status != "warning":
                warnings.append(
                    _issue(f"{key}_unexpected_status", f"{label} status is {status}.", component=key, severity="warning")
                )

    window_canary = dict(evidence_specs["live_pilot_window_canary"][1] or {})
    window_clean_count = _coerce_int(window_canary.get("clean_session_count"), 0)
    window_required_reported = _coerce_int(window_canary.get("required_clean_sessions"), 0)
    if window_clean_count < required_window_clean:
        blockers.append(
            _issue(
                "supervised_window_clean_sessions_low",
                f"Supervised live pilot window canary has {window_clean_count}/{required_window_clean} clean sessions.",
                component="live_pilot_window_canary",
            )
        )

    readiness = dict(evidence_specs["live_pilot_readiness"][1] or {})
    broker_live_gate_status = str(readiness.get("broker_live_gate_status") or "unknown").strip().lower()
    safety_lock_status = str(readiness.get("safety_lock_status") or "unknown").strip().lower()
    live_enabled = _coerce_bool(live_settings.get("enabled"), False)
    live_armed = _coerce_bool(live_settings.get("armed"), False)
    live_kill = _coerce_bool(live_settings.get("kill_switch"), False)
    paper_kill = _coerce_bool(dict(paper_state.get("settings") or {}).get("kill_switch"), False)

    if profile_key != LIVE_PILOT_PROMOTION_PAPER_PROFILE:
        blockers.append(
            _issue(
                "paper_profile_required",
                "V1 live pilot promotion reports are reviewed from the personal paper automation profile.",
                component="profile",
            )
        )
    if broker_live_gate_status != "open":
        blockers.append(_issue("broker_live_gate_not_open", "The broker-live gate is not open.", component="broker_live_gate"))
    if safety_lock_status not in {"clear", "none"}:
        blockers.append(_issue("safety_lock_active", "A paper or live safety lock is active.", component="safety_lock"))
    if live_enabled or live_armed:
        blockers.append(_issue("live_profile_active", "The live profile must remain disabled and disarmed before a limited-live rollout gate.", component="live_profile"))
    if live_kill or paper_kill:
        blockers.append(_issue("kill_switch_active", "A paper or live kill switch is active.", component="safety_lock"))

    latest_window = dict(runtime.get("live_pilot_window_last_report") or {})
    if latest_window:
        latest_window_status = _report_status(latest_window)
        latest_terminal = str(latest_window.get("terminal_state") or "").strip().lower()
        latest_reconciliation = str(latest_window.get("reconciliation_status") or "").strip().lower()
        if latest_window_status not in {"completed", "warning"}:
            blockers.append(_issue("latest_window_unresolved", "The latest supervised live pilot window is unresolved.", component="live_pilot_window"))
        if latest_window_status == "completed" and latest_terminal not in {"canceled", "closed"}:
            blockers.append(_issue("latest_window_terminal_missing", "The latest supervised live pilot window lacks terminal cancel or close evidence.", component="live_pilot_window"))
        if latest_reconciliation in {"blocked", "failed", "fail"}:
            blockers.append(_issue("latest_window_reconciliation_blocked", "The latest supervised live pilot reconciliation is blocked.", component="live_pilot_window"))

    order_event_summary = _recent_live_order_event_summary(db, tenant=tenant, now=now, stale_after_days=stale_after_days)
    if _coerce_int(order_event_summary.get("failed_count"), 0) > 0:
        blockers.append(_issue("recent_live_order_event_failed", "Recent live pilot order events include failure evidence.", component="order_events"))

    pnl_summary = dict(window_canary.get("pnl_summary") or {})
    slippage_summary = dict(window_canary.get("slippage_summary") or {})
    worst_slippage = slippage_summary.get("worst_abs_bps")
    average_slippage = slippage_summary.get("average_abs_bps")
    if worst_slippage is not None and _coerce_float(worst_slippage, 0.0) > LIVE_PILOT_PROMOTION_BLOCK_SLIPPAGE_BPS:
        blockers.append(
            _issue(
                "slippage_blocked",
                f"Supervised live pilot worst slippage was {_coerce_float(worst_slippage):.1f} bps.",
                component="slippage",
            )
        )
    elif average_slippage is not None and _coerce_float(average_slippage, 0.0) > LIVE_PILOT_PROMOTION_WARN_SLIPPAGE_BPS:
        warnings.append(
            _issue(
                "slippage_warning",
                f"Supervised live pilot average slippage was {_coerce_float(average_slippage):.1f} bps.",
                component="slippage",
                severity="warning",
            )
        )
    if _coerce_float(pnl_summary.get("realized_pnl"), 0.0) < 0:
        warnings.append(_issue("negative_live_pilot_pnl", "Supervised live pilot realized PnL is negative.", component="pnl", severity="warning"))

    required_actions: list[dict[str, Any]] = []
    if blockers:
        required_actions.append(
            {
                "key": "clear_promotion_blockers",
                "detail": "Clear all promotion blockers before requesting a limited-live rollout gate.",
            }
        )
    elif warnings:
        required_actions.append(
            {
                "key": "operator_review_required",
                "detail": "Review advisory warnings before requesting a limited-live rollout gate.",
            }
        )
    else:
        required_actions.append(
            {
                "key": "request_limited_live_rollout_approval",
                "detail": "Evidence is clean. Request operator approval for the separate limited-live rollout gate.",
            }
        )

    status = "blocked" if blockers else "needs_operator_review" if warnings else "ready_to_request_limited_live_rollout"
    label = {
        "blocked": "Live pilot promotion blocked",
        "needs_operator_review": "Live pilot promotion needs operator review",
        "ready_to_request_limited_live_rollout": "Ready to request limited-live rollout",
    }[status]
    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": profile_key,
            "live_profile_key": LIVE_PILOT_PROMOTION_LIVE_PROFILE,
            "linked_account_id": getattr(linked_account, "id", None),
            "session_day": current_day,
            "evaluated_at": _serialize_datetime(now),
            "required_window_clean_sessions": required_window_clean,
            "stale_after_days": stale_after_days,
            "evidence_summaries": evidence_summaries,
            "clean_session_progress": {
                "clean": window_clean_count,
                "required": required_window_clean,
                "reported_required": window_required_reported,
            },
            "broker_live_gate_status": broker_live_gate_status,
            "safety_lock_status": safety_lock_status,
            "live_profile_enabled": live_enabled,
            "live_profile_armed": live_armed,
            "paper_kill_switch": paper_kill,
            "live_kill_switch": live_kill,
            "latest_window_status": _report_status(latest_window) if latest_window else "missing",
            "latest_window_terminal_state": latest_window.get("terminal_state") if latest_window else None,
            "latest_window_reconciliation_status": latest_window.get("reconciliation_status") if latest_window else "missing",
            "latest_broker_order_id": latest_window.get("broker_order_id") if latest_window else None,
            "latest_local_order_id": latest_window.get("local_order_id") or latest_window.get("order_id") if latest_window else None,
            "pnl_summary": pnl_summary or {"sample_count": 0, "realized_pnl": 0.0},
            "slippage_summary": slippage_summary or {"sample_count": 0, "average_abs_bps": None, "worst_abs_bps": None},
            "order_event_summary": order_event_summary,
            "blockers": blockers[:24],
            "warnings": warnings[:24],
            "required_operator_actions": required_actions,
            "manual_action_required": bool(blockers or warnings),
        }
    )


def _find_existing_promotion_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIVE_PILOT_PROMOTION_NOTE_OWNER,
            limit=LIVE_PILOT_PROMOTION_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "live-pilot-promotion",
        "supervised-live-pilot-canary",
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
    progress = dict(report.get("clean_session_progress") or {})
    pnl = dict(report.get("pnl_summary") or {})
    slippage = dict(report.get("slippage_summary") or {})
    order_events = dict(report.get("order_event_summary") or {})
    lines = [
        f"Automation live pilot promotion report for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Run source: {str(report.get('run_source') or 'manual').replace('_', ' ')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Supervised clean sessions: {progress.get('clean', 0)} / {progress.get('required', 0)} required",
        f"Broker-live gate: {report.get('broker_live_gate_status') or 'unknown'}",
        f"Safety locks: {report.get('safety_lock_status') or 'unknown'}",
        f"Latest supervised window: {report.get('latest_window_status') or 'missing'}",
        f"Latest terminal state: {report.get('latest_window_terminal_state') or '--'}",
        f"Latest reconciliation: {report.get('latest_window_reconciliation_status') or 'missing'}",
        f"Order events: {order_events.get('count', 0)} total | {order_events.get('failed_count', 0)} failed",
        f"Realized PnL samples: {pnl.get('sample_count', 0)} | total ${float(pnl.get('realized_pnl') or 0.0):.2f}",
        f"Slippage samples: {slippage.get('sample_count', 0)} | worst {slippage.get('worst_abs_bps') if slippage.get('worst_abs_bps') is not None else '--'} bps",
        (
            f"Next scheduled review: {report.get('next_eligible_run_at')}"
            if report.get("next_eligible_run_at")
            else "Next scheduled review: not available"
        ),
        "",
        "This report is advisory. It does not place, cancel, or close orders, enable live trading, arm automation, clear locks, tune baseline settings, or change broker-live gates.",
        "",
        "Evidence",
    ]
    for key, item in dict(report.get("evidence_summaries") or {}).items():
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- {item.get('label') or key}: {str(item.get('status') or 'missing').upper()} | "
            f"note {'linked' if item.get('note_id') else 'missing'} | "
            f"age {float(item.get('age_days')):.2f}d" if item.get("age_days") is not None else f"- {item.get('label') or key}: {str(item.get('status') or 'missing').upper()} | note {'linked' if item.get('note_id') else 'missing'} | age --"
        )
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    lines.extend(["", "Blockers"])
    if blockers:
        lines.extend(f"- {item.get('component')}: {item.get('key')}. {item.get('detail')}" for item in blockers[:16])
    else:
        lines.append("- None.")
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    lines.extend(["", "Warnings"])
    if warnings:
        lines.extend(f"- {item.get('component')}: {item.get('key')}. {item.get('detail')}" for item in warnings[:16])
    else:
        lines.append("- None.")
    actions = [item for item in list(report.get("required_operator_actions") or []) if isinstance(item, dict)]
    lines.extend(["", "Operator actions"])
    if actions:
        lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in actions[:8])
    else:
        lines.append("- None.")
    return "\n".join(lines).strip()


def _sync_promotion_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "live-pilot-promotion",
        "supervised-live-pilot-canary",
        "live-pilot-readiness",
        _profile_tag(profile_key),
        _profile_tag(LIVE_PILOT_PROMOTION_LIVE_PROFILE),
        f"session-{session_day}",
    ]
    title = f"Automation live pilot promotion report - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = (
        str(report.get("related_note_id") or report.get("note_id") or "").strip()
        or _find_existing_promotion_note_id(profile_key, session_day)
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": LIVE_PILOT_PROMOTION_NOTE_OWNER,
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


def run_live_pilot_promotion_report(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    profile_key: str = LIVE_PILOT_PROMOTION_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_run_source = str(run_source or "manual").strip().lower().replace(" ", "_") or "manual"
    review_session_day, _review_window_open = live_pilot_promotion_session_day_for(
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
        else _serialize_datetime(_parse_datetime(runtime_before.get("live_pilot_promotion_report_last_scheduled_run_at")))
    )
    report["last_scheduled_session_day"] = (
        review_session_day
        if normalized_run_source == "scheduled"
        else str(runtime_before.get("live_pilot_promotion_report_last_scheduled_session_day") or "").strip() or None
    )
    next_eligible = (
        next_eligible_live_pilot_promotion_review_after_session(review_session_day)
        if normalized_run_source == "scheduled"
        else next_eligible_live_pilot_promotion_review_at(now)
    )
    report["next_eligible_run_at"] = _serialize_datetime(next_eligible)
    note_id = _sync_promotion_note(tenant=tenant, profile_key=profile_key, report=report)
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
        "required_window_clean_sessions",
        "stale_after_days",
        "evidence_summaries",
        "clean_session_progress",
        "broker_live_gate_status",
        "safety_lock_status",
        "live_profile_enabled",
        "live_profile_armed",
        "paper_kill_switch",
        "live_kill_switch",
        "latest_window_status",
        "latest_window_terminal_state",
        "latest_window_reconciliation_status",
        "latest_broker_order_id",
        "latest_local_order_id",
        "pnl_summary",
        "slippage_summary",
        "order_event_summary",
        "blockers",
        "warnings",
        "required_operator_actions",
        "manual_action_required",
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
    runtime["live_pilot_promotion_report_last_report"] = serialize_value(summary)
    runtime["live_pilot_promotion_report_last_note_id"] = note_id
    runtime["live_pilot_promotion_report_note_session_day"] = report.get("session_day")
    runtime["live_pilot_promotion_report_last_run_at"] = report.get("evaluated_at")
    if normalized_run_source == "scheduled":
        runtime["live_pilot_promotion_report_last_scheduled_run_at"] = report.get("evaluated_at")
        runtime["live_pilot_promotion_report_last_scheduled_session_day"] = review_session_day
    runtime["live_pilot_promotion_report_next_eligible_run_at"] = report.get("next_eligible_run_at")
    runtime["live_pilot_promotion_report_last_skipped_reason"] = None
    runtime["live_pilot_promotion_report_last_error"] = None
    history = list(runtime.get("live_pilot_promotion_report_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "blocker_count": len(report.get("blockers") or []),
            "warning_count": len(report.get("warnings") or []),
            "note_id": note_id,
            "run_source": normalized_run_source,
        },
    )
    runtime["live_pilot_promotion_report_history"] = serialize_value(history[:LIVE_PILOT_PROMOTION_HISTORY_LIMIT])
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.live_pilot_promotion_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "live_profile_key": LIVE_PILOT_PROMOTION_LIVE_PROFILE,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
                "note_id": note_id,
                "run_source": normalized_run_source,
                "baseline_settings_mutated": report.get("baseline_settings_mutated"),
            },
        )
    return serialize_value(report)
