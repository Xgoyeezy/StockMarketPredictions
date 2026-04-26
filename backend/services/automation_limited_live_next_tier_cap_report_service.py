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

LIMITED_LIVE_NEXT_TIER_CAP_NOTE_OWNER = "automation-ai"
LIMITED_LIVE_NEXT_TIER_CAP_HISTORY_LIMIT = 8
LIMITED_LIVE_NEXT_TIER_CAP_NOTE_LIMIT = 250
LIMITED_LIVE_NEXT_TIER_CAP_REQUIRED_CLEAN_SESSIONS = 3
LIMITED_LIVE_NEXT_TIER_CAP_STALE_AFTER_DAYS = 2
LIMITED_LIVE_NEXT_TIER_CAP_TARGET_MAX_NOTIONAL = 500.0
LIMITED_LIVE_NEXT_TIER_CAP_PAPER_PROFILE = "personal_paper"
LIMITED_LIVE_NEXT_TIER_CAP_LIVE_PROFILE = "personal_live"
LIMITED_LIVE_NEXT_TIER_CAP_BLOCK_SLIPPAGE_BPS = 100.0
LIMITED_LIVE_NEXT_TIER_CAP_WARN_SLIPPAGE_BPS = 50.0
LIMITED_LIVE_NEXT_TIER_CAP_TARGET_NOTIONAL_CEILING = 10_000.0

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE

LIMITED_LIVE_NEXT_TIER_CAP_SETTINGS_DEFAULTS: dict[str, Any] = {
    "limited_live_next_tier_cap_report_enabled": True,
    "limited_live_next_tier_cap_report_auto_review_enabled": True,
    "limited_live_next_tier_cap_required_clean_sessions": LIMITED_LIVE_NEXT_TIER_CAP_REQUIRED_CLEAN_SESSIONS,
    "limited_live_next_tier_cap_stale_after_days": LIMITED_LIVE_NEXT_TIER_CAP_STALE_AFTER_DAYS,
    "limited_live_next_tier_cap_target_max_notional": LIMITED_LIVE_NEXT_TIER_CAP_TARGET_MAX_NOTIONAL,
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


def _clamp_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    return max(float(minimum), min(float(maximum), _coerce_float(value, default)))


def _profile_tag(profile_key: str) -> str:
    return "profile-" + str(profile_key or LIMITED_LIVE_NEXT_TIER_CAP_PAPER_PROFILE).strip().lower().replace(":", "-")


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def limited_live_next_tier_cap_session_day_for(
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


def next_eligible_limited_live_next_tier_cap_review_at(value: datetime | None = None) -> datetime:
    now = value or _utc_now()
    session_day, review_window_open = limited_live_next_tier_cap_session_day_for(now, forced=False)
    if review_window_open:
        return now
    return _review_time_for_session_day(session_day)


def next_eligible_limited_live_next_tier_cap_review_after_session(session_day: str) -> datetime:
    return _review_time_for_session_day(_next_trading_day_after(session_day))


def normalize_limited_live_next_tier_cap_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    return {
        "limited_live_next_tier_cap_report_enabled": _coerce_bool(
            state.get("limited_live_next_tier_cap_report_enabled"),
            bool(LIMITED_LIVE_NEXT_TIER_CAP_SETTINGS_DEFAULTS["limited_live_next_tier_cap_report_enabled"]),
        ),
        "limited_live_next_tier_cap_report_auto_review_enabled": _coerce_bool(
            state.get("limited_live_next_tier_cap_report_auto_review_enabled"),
            bool(LIMITED_LIVE_NEXT_TIER_CAP_SETTINGS_DEFAULTS["limited_live_next_tier_cap_report_auto_review_enabled"]),
        ),
        "limited_live_next_tier_cap_required_clean_sessions": _clamp_int(
            state.get("limited_live_next_tier_cap_required_clean_sessions"),
            int(LIMITED_LIVE_NEXT_TIER_CAP_SETTINGS_DEFAULTS["limited_live_next_tier_cap_required_clean_sessions"]),
            minimum=1,
            maximum=20,
        ),
        "limited_live_next_tier_cap_stale_after_days": _clamp_int(
            state.get("limited_live_next_tier_cap_stale_after_days"),
            int(LIMITED_LIVE_NEXT_TIER_CAP_SETTINGS_DEFAULTS["limited_live_next_tier_cap_stale_after_days"]),
            minimum=1,
            maximum=30,
        ),
        "limited_live_next_tier_cap_target_max_notional": _clamp_float(
            state.get("limited_live_next_tier_cap_target_max_notional"),
            float(LIMITED_LIVE_NEXT_TIER_CAP_SETTINGS_DEFAULTS["limited_live_next_tier_cap_target_max_notional"]),
            minimum=1.0,
            maximum=LIMITED_LIVE_NEXT_TIER_CAP_TARGET_NOTIONAL_CEILING,
        ),
    }


def normalize_limited_live_next_tier_cap_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("limited_live_next_tier_cap_report_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    history = [
        item
        for item in list(runtime.get("limited_live_next_tier_cap_report_history") or [])[
            :LIMITED_LIVE_NEXT_TIER_CAP_HISTORY_LIMIT
        ]
        if isinstance(item, dict)
    ]
    return {
        "limited_live_next_tier_cap_report_last_report": serialize_value(last_report),
        "limited_live_next_tier_cap_report_last_note_id": str(
            runtime.get("limited_live_next_tier_cap_report_last_note_id") or ""
        ).strip()
        or None,
        "limited_live_next_tier_cap_report_note_session_day": str(
            runtime.get("limited_live_next_tier_cap_report_note_session_day") or ""
        ).strip()
        or None,
        "limited_live_next_tier_cap_report_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("limited_live_next_tier_cap_report_last_run_at"))
        ),
        "limited_live_next_tier_cap_report_last_scheduled_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("limited_live_next_tier_cap_report_last_scheduled_run_at"))
        ),
        "limited_live_next_tier_cap_report_last_scheduled_session_day": str(
            runtime.get("limited_live_next_tier_cap_report_last_scheduled_session_day") or ""
        ).strip()
        or None,
        "limited_live_next_tier_cap_report_next_eligible_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("limited_live_next_tier_cap_report_next_eligible_run_at"))
        ),
        "limited_live_next_tier_cap_report_last_skipped_reason": str(
            runtime.get("limited_live_next_tier_cap_report_last_skipped_reason") or ""
        ).strip()
        or None,
        "limited_live_next_tier_cap_report_last_error": str(
            runtime.get("limited_live_next_tier_cap_report_last_error") or ""
        ).strip()
        or None,
        "limited_live_next_tier_cap_report_history": serialize_value(history),
    }


def build_limited_live_next_tier_cap_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = normalize_limited_live_next_tier_cap_runtime((state or {}).get("runtime"))
    settings = normalize_limited_live_next_tier_cap_settings((state or {}).get("settings"))
    report = dict(runtime.get("limited_live_next_tier_cap_report_last_report") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Limited-live next-tier cap report not run",
            "enabled": settings["limited_live_next_tier_cap_report_enabled"],
            "auto_review_enabled": settings["limited_live_next_tier_cap_report_auto_review_enabled"],
            "required_clean_sessions": settings["limited_live_next_tier_cap_required_clean_sessions"],
            "stale_after_days": settings["limited_live_next_tier_cap_stale_after_days"],
            "target_max_notional": settings["limited_live_next_tier_cap_target_max_notional"],
            "related_note_id": runtime.get("limited_live_next_tier_cap_report_last_note_id"),
            "note_id": runtime.get("limited_live_next_tier_cap_report_last_note_id"),
            "last_run_at": runtime.get("limited_live_next_tier_cap_report_last_run_at"),
            "last_scheduled_run_at": runtime.get("limited_live_next_tier_cap_report_last_scheduled_run_at"),
            "last_scheduled_session_day": runtime.get("limited_live_next_tier_cap_report_last_scheduled_session_day"),
            "next_eligible_run_at": runtime.get("limited_live_next_tier_cap_report_next_eligible_run_at"),
            "skipped_reason": runtime.get("limited_live_next_tier_cap_report_last_skipped_reason"),
            "last_error": runtime.get("limited_live_next_tier_cap_report_last_error"),
            "history": runtime.get("limited_live_next_tier_cap_report_history") or [],
            "blockers": [],
            "warnings": [],
        }
    report.setdefault("enabled", settings["limited_live_next_tier_cap_report_enabled"])
    report.setdefault("auto_review_enabled", settings["limited_live_next_tier_cap_report_auto_review_enabled"])
    report.setdefault("target_max_notional", settings["limited_live_next_tier_cap_target_max_notional"])
    report.setdefault("related_note_id", runtime.get("limited_live_next_tier_cap_report_last_note_id"))
    report.setdefault("note_id", runtime.get("limited_live_next_tier_cap_report_last_note_id"))
    report.setdefault("last_run_at", runtime.get("limited_live_next_tier_cap_report_last_run_at"))
    report.setdefault("last_scheduled_run_at", runtime.get("limited_live_next_tier_cap_report_last_scheduled_run_at"))
    report.setdefault(
        "last_scheduled_session_day",
        runtime.get("limited_live_next_tier_cap_report_last_scheduled_session_day"),
    )
    report.setdefault("next_eligible_run_at", runtime.get("limited_live_next_tier_cap_report_next_eligible_run_at"))
    report.setdefault("skipped_reason", runtime.get("limited_live_next_tier_cap_report_last_skipped_reason"))
    report.setdefault("last_error", runtime.get("limited_live_next_tier_cap_report_last_error"))
    report["history"] = runtime.get("limited_live_next_tier_cap_report_history") or []
    return serialize_value(report)


def _issue(key: str, detail: str, *, component: str = "next_tier_cap", severity: str = "blocker") -> dict[str, Any]:
    return {"key": key, "component": component, "severity": severity, "detail": detail}


def _report_status(report: dict[str, Any] | None) -> str:
    if not isinstance(report, dict) or not report:
        return "missing"
    return str(report.get("status") or "missing").strip().lower() or "missing"


def _report_note_id(report: dict[str, Any] | None) -> str | None:
    if not isinstance(report, dict):
        return None
    return str(report.get("related_note_id") or report.get("note_id") or "").strip() or None


def _report_timestamp(report: dict[str, Any] | None) -> datetime | None:
    if not isinstance(report, dict):
        return None
    for key in ("evaluated_at", "last_run_at", "checked_at", "created_at"):
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
    age_days = _age_days(now, timestamp)
    stale = age_days is None or age_days > stale_after_days
    blocker_count = len([item for item in list(report.get("blockers") or []) if isinstance(item, dict)])
    warning_count = len([item for item in list(report.get("warnings") or []) if isinstance(item, dict)])
    note_id = _report_note_id(report)
    return {
        "key": key,
        "label": label,
        "status": status,
        "ready": status in ready_statuses and not stale and blocker_count == 0 and bool(note_id),
        "stale": stale,
        "age_days": age_days,
        "note_id": note_id,
        "blocker_count": blocker_count,
        "warning_count": warning_count,
        "evaluated_at": _serialize_datetime(timestamp),
    }


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    if hasattr(row, "get"):
        try:
            return row.get(key)
        except Exception:
            return None
    return getattr(row, key, None)


def _row_is_uncontrolled_live(row: Any) -> bool:
    profile_key = str(
        _row_value(row, "automation_profile_key") or _row_value(row, "profile_key") or ""
    ).strip().lower()
    route_family = str(_row_value(row, "route_family") or _row_value(row, "source") or "").strip().lower()
    if profile_key and profile_key != LIMITED_LIVE_NEXT_TIER_CAP_LIVE_PROFILE:
        return False
    if route_family in {"limited_live_rollout", "limited_live_cap_expansion", "limited_live_next_tier_cap"}:
        return False
    return profile_key == LIMITED_LIVE_NEXT_TIER_CAP_LIVE_PROFILE or route_family == "broker_live"


def _uncontrolled_live_ledger_counts() -> dict[str, int]:
    counts = {"pending": 0, "open": 0}
    try:
        pending = sdm.read_pending_orders()
        counts["pending"] = sum(1 for _, row in pending.iterrows() if _row_is_uncontrolled_live(row))
    except Exception:
        counts["pending"] = 0
    try:
        open_trades = sdm.read_open_trades()
        counts["open"] = sum(1 for _, row in open_trades.iterrows() if _row_is_uncontrolled_live(row))
    except Exception:
        counts["open"] = 0
    return counts


def _event_notional(event: dict[str, Any]) -> float | None:
    payload = dict(event.get("payload") or {})
    for key in ("notional", "submitted_notional", "order_notional", "estimated_notional"):
        if payload.get(key) is not None:
            value = _coerce_float(payload.get(key), 0.0)
            if value > 0:
                return value
    qty = _coerce_float(payload.get("qty") or payload.get("quantity"), 0.0)
    price = _coerce_float(payload.get("limit_price") or payload.get("submitted_limit_price") or payload.get("price"), 0.0)
    if qty > 0 and price > 0:
        return abs(qty * price)
    return None


def _recent_cap_expansion_order_event_summary(
    db: Session | None,
    *,
    tenant: Tenant,
    now: datetime,
    stale_after_days: int,
    current_cap: float,
) -> dict[str, Any]:
    if db is None:
        return {"count": 0, "submitted_count": 0, "terminal_count": 0, "failed_count": 0, "cap_breach_count": 0, "non_limit_count": 0}
    start_at = now - timedelta(days=max(1, stale_after_days + 5))
    rows = (
        db.query(OrderEventRecord)
        .filter(OrderEventRecord.tenant_id == tenant.id)
        .filter(OrderEventRecord.created_at >= start_at)
        .filter(OrderEventRecord.created_at <= now)
        .order_by(OrderEventRecord.created_at.asc())
        .all()
    )
    matched: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row.payload_json or {})
        row_profile = str(payload.get("automation_profile_key") or payload.get("profile_key") or "").strip().lower()
        route_family = str(payload.get("route_family") or payload.get("source") or "").strip().lower()
        entry_reason = str(payload.get("automation_entry_reason") or "").strip().lower()
        expansion_id = str(payload.get("limited_live_cap_expansion_id") or "").strip()
        next_tier_cap_id = str(payload.get("limited_live_next_tier_cap_id") or "").strip()
        if row_profile and row_profile != LIMITED_LIVE_NEXT_TIER_CAP_LIVE_PROFILE:
            continue
        if (
            not expansion_id
            and not next_tier_cap_id
            and route_family not in {"limited_live_cap_expansion", "limited_live_next_tier_cap"}
            and entry_reason not in {"limited_live_cap_expansion", "limited_live_next_tier_cap"}
        ):
            continue
        matched.append(
            {
                "event_key": row.event_key,
                "status": row.status,
                "order_type": row.order_type,
                "created_at": _serialize_datetime(row.created_at),
                "payload": payload,
            }
        )
    failed = [
        item
        for item in matched
        if str(item.get("status") or "").strip().lower() in {"failed", "error", "rejected", "blocked"}
    ]
    non_limit = [
        item
        for item in matched
        if str(item.get("order_type") or "").strip().lower() not in {"", "limit"}
    ]
    notionals = [value for value in (_event_notional(item) for item in matched) if value is not None]
    cap_breaches = [value for value in notionals if value > float(current_cap) + 0.01]
    return {
        "count": len(matched),
        "submitted_count": sum(1 for item in matched if str(item.get("event_key") or "").strip().lower() == "order.submitted"),
        "terminal_count": sum(
            1
            for item in matched
            if str(item.get("event_key") or "").strip().lower() in {"order.canceled", "order.cancelled", "order.closed", "trade.closed"}
        ),
        "failed_count": len(failed),
        "non_limit_count": len(non_limit),
        "cap_breach_count": len(cap_breaches),
        "max_notional_seen": float(max(notionals)) if notionals else None,
        "latest_event_at": matched[-1]["created_at"] if matched else None,
    }


def _current_cap_from(runtime: dict[str, Any], settings: dict[str, Any]) -> float:
    canary = dict(runtime.get("limited_live_cap_expansion_canary_last_report") or {})
    gate = dict(runtime.get("limited_live_cap_expansion_gate_last_report") or {})
    gate_caps = dict(gate.get("caps") or {})
    report = dict(runtime.get("limited_live_cap_expansion_report_last_report") or {})
    values = [
        canary.get("expanded_max_notional"),
        gate_caps.get("expanded_max_notional"),
        gate.get("expanded_max_notional"),
        report.get("recommended_next_max_notional"),
        report.get("target_max_notional"),
        settings.get("limited_live_cap_expansion_max_notional"),
    ]
    for value in values:
        cap = _coerce_float(value, 0.0)
        if cap > 0:
            return cap
    return 250.0


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
    settings = normalize_limited_live_next_tier_cap_settings(paper_state.get("settings"))
    paper_settings = dict(paper_state.get("settings") or {})
    live_state = live_state if isinstance(live_state, dict) else {}
    live_settings = dict(live_state.get("settings") or {})
    current_day = str(review_session_day or "").strip() or _session_day_for(now)
    required_clean_sessions = int(settings["limited_live_next_tier_cap_required_clean_sessions"])
    stale_after_days = int(settings["limited_live_next_tier_cap_stale_after_days"])
    target_cap = float(settings["limited_live_next_tier_cap_target_max_notional"])
    current_cap = _current_cap_from(runtime, paper_settings)
    recommended_cap = max(current_cap, target_cap)

    cap_canary = dict(runtime.get("limited_live_cap_expansion_canary_last_report") or {})
    cap_report = dict(runtime.get("limited_live_cap_expansion_report_last_report") or {})
    cap_gate = dict(runtime.get("limited_live_cap_expansion_gate_last_report") or {})
    rollout_canary = dict(runtime.get("limited_live_rollout_canary_last_report") or {})
    rollout_gate = dict(runtime.get("limited_live_rollout_gate_last_report") or {})
    state_control = dict(runtime.get("state_control_last_evaluation") or {})
    state_control_status = str(runtime.get("state_control_state") or state_control.get("state") or "unknown").strip().lower()

    evidence_summaries = {
        "limited_live_cap_expansion_canary": _evidence_summary(
            now=now,
            key="limited_live_cap_expansion_canary",
            label="Limited-live cap expansion canary",
            report=cap_canary,
            ready_statuses={"ready_for_operator_review"},
            stale_after_days=stale_after_days,
        ),
        "limited_live_cap_expansion_report": _evidence_summary(
            now=now,
            key="limited_live_cap_expansion_report",
            label="Limited-live cap expansion report",
            report=cap_report,
            ready_statuses={"ready_to_request_cap_expansion"},
            stale_after_days=stale_after_days,
        ),
        "limited_live_cap_expansion_gate": _evidence_summary(
            now=now,
            key="limited_live_cap_expansion_gate",
            label="Limited-live cap expansion gate",
            report=cap_gate,
            ready_statuses={"active", "rolled_back", "completed", "warning"},
            stale_after_days=stale_after_days,
        ),
    }

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if profile_key != LIMITED_LIVE_NEXT_TIER_CAP_PAPER_PROFILE:
        blockers.append(_issue("paper_profile_required", "V1 next-tier cap reports are reviewed from the personal paper automation profile.", component="profile"))

    cap_canary_status = _report_status(cap_canary)
    cap_report_status = _report_status(cap_report)
    cap_gate_status = _report_status(cap_gate)
    rollout_canary_status = _report_status(rollout_canary)
    rollout_gate_status = _report_status(rollout_gate)
    cap_clean_count = _coerce_int(cap_canary.get("clean_session_count"), 0)
    cap_canary_required = _coerce_int(cap_canary.get("required_clean_sessions"), required_clean_sessions)
    clean_required = max(required_clean_sessions, min(cap_canary_required or required_clean_sessions, 20))
    cap_report_broker_gate_status = str(
        cap_report.get("broker_live_gate_status") or cap_report.get("broker_gate_status") or ""
    ).strip().lower()
    canary_broker_gate_status = str(
        cap_canary.get("broker_live_gate_status") or cap_canary.get("broker_gate_status") or ""
    ).strip().lower()
    broker_gate_status = (
        cap_report_broker_gate_status
        if cap_report_broker_gate_status and cap_report_broker_gate_status != "open"
        else canary_broker_gate_status
        if canary_broker_gate_status and canary_broker_gate_status != "open"
        else cap_report_broker_gate_status
        or canary_broker_gate_status
        or "unknown"
    )
    cap_report_safety_lock_status = str(cap_report.get("safety_lock_status") or "").strip().lower()
    canary_safety_lock_status = str(cap_canary.get("safety_lock_status") or "").strip().lower()
    safety_lock_status = (
        cap_report_safety_lock_status
        if cap_report_safety_lock_status and cap_report_safety_lock_status not in {"clear", "none"}
        else canary_safety_lock_status
        if canary_safety_lock_status and canary_safety_lock_status not in {"clear", "none"}
        else cap_report_safety_lock_status
        or canary_safety_lock_status
        or "unknown"
    )

    if cap_canary_status in {"missing", "not_run"}:
        blockers.append(_issue("cap_expansion_canary_missing", "Run the limited-live cap expansion canary before next-tier cap review.", component="limited_live_cap_expansion_canary"))
    elif cap_canary_status in {"blocked", "failed", "fail", "collecting"}:
        blockers.append(_issue("cap_expansion_canary_not_ready", "Limited-live cap expansion canary is not ready.", component="limited_live_cap_expansion_canary"))
    elif cap_canary_status == "warning" or cap_canary.get("warnings"):
        warnings.append(_issue("cap_expansion_canary_warning", "Limited-live cap expansion canary has advisory warnings.", component="limited_live_cap_expansion_canary", severity="warning"))
    if cap_clean_count < clean_required:
        blockers.append(
            _issue(
                "cap_expansion_clean_sessions_low",
                f"Limited-live cap expansion canary has {cap_clean_count}/{clean_required} clean sessions.",
                component="limited_live_cap_expansion_canary",
            )
        )
    if evidence_summaries["limited_live_cap_expansion_canary"].get("stale"):
        blockers.append(_issue("cap_expansion_canary_stale", "Limited-live cap expansion canary evidence is stale.", component="limited_live_cap_expansion_canary"))
    if not _report_note_id(cap_canary):
        blockers.append(_issue("cap_expansion_canary_note_missing", "Limited-live cap expansion canary is missing a linked automation-ai note.", component="notes"))
    for item in list(cap_canary.get("blockers") or []):
        if isinstance(item, dict):
            blockers.append(
                _issue(
                    str(item.get("key") or "cap_expansion_canary_blocker"),
                    str(item.get("detail") or "Cap expansion canary has unresolved blocker evidence."),
                    component=str(item.get("component") or "limited_live_cap_expansion_canary"),
                )
            )
    for item in list(cap_canary.get("warnings") or []):
        if isinstance(item, dict):
            warnings.append(
                _issue(
                    str(item.get("key") or "cap_expansion_canary_warning"),
                    str(item.get("detail") or "Cap expansion canary has advisory warning evidence."),
                    component=str(item.get("component") or "limited_live_cap_expansion_canary"),
                    severity="warning",
                )
            )

    if cap_report_status in {"missing", "not_run"}:
        blockers.append(_issue("cap_expansion_report_missing", "Run the limited-live cap expansion report before next-tier cap review.", component="limited_live_cap_expansion_report"))
    elif cap_report_status != "ready_to_request_cap_expansion":
        blockers.append(_issue("cap_expansion_report_not_ready", "Limited-live cap expansion report is not ready.", component="limited_live_cap_expansion_report"))
    if evidence_summaries["limited_live_cap_expansion_report"].get("stale"):
        blockers.append(_issue("cap_expansion_report_stale", "Limited-live cap expansion report evidence is stale.", component="limited_live_cap_expansion_report"))
    if cap_report.get("blockers"):
        blockers.append(_issue("cap_expansion_report_blocked", "Limited-live cap expansion report has unresolved blockers.", component="limited_live_cap_expansion_report"))
    elif cap_report.get("warnings"):
        warnings.append(_issue("cap_expansion_report_warning", "Limited-live cap expansion report has advisory warnings.", component="limited_live_cap_expansion_report", severity="warning"))
    if not _report_note_id(cap_report):
        blockers.append(_issue("cap_expansion_report_note_missing", "Limited-live cap expansion report is missing a linked automation-ai note.", component="notes"))

    if cap_gate_status == "blocked" or cap_gate.get("blockers"):
        blockers.append(_issue("cap_expansion_gate_blocked", "Limited-live cap expansion gate has unresolved blocker evidence.", component="limited_live_cap_expansion_gate"))
    if str(rollout_canary.get("status") or "").strip().lower() == "blocked" or rollout_canary.get("blockers"):
        blockers.append(_issue("rollout_canary_blocked", "Limited-live rollout canary has unresolved blocker evidence.", component="limited_live_rollout_canary"))
    if str(rollout_gate.get("status") or "").strip().lower() == "blocked" or rollout_gate.get("blockers"):
        blockers.append(_issue("rollout_gate_blocked", "Base limited-live rollout gate has unresolved blocker evidence.", component="limited_live_rollout"))

    if broker_gate_status != "open":
        blockers.append(_issue("broker_live_gate_not_open", "Broker-live gate is not open.", component="broker_live_gate"))
    if safety_lock_status not in {"clear", "none"}:
        blockers.append(_issue("safety_lock_active", "A paper or live safety lock is active.", component="safety_lock"))
    if _coerce_bool(paper_settings.get("kill_switch"), False) or _coerce_bool(live_settings.get("kill_switch"), False):
        blockers.append(_issue("kill_switch_active", "A paper or live kill switch is active.", component="safety_lock"))
    if state_control_status == "halt" or _coerce_bool(runtime.get("state_control_halt_active"), False):
        blockers.append(_issue("state_control_halt", "State-control is halted.", component="state_control"))

    latest_reconciliation = str(cap_canary.get("latest_reconciliation_status") or "missing").strip().lower()
    if latest_reconciliation in {"blocked", "failed", "fail", "mismatch", "mismatched"}:
        blockers.append(_issue("reconciliation_blocked", "Expanded-cap broker/local reconciliation is blocked or mismatched.", component="reconciliation"))

    note_coverage = dict(cap_canary.get("note_coverage") or {})
    note_required = _coerce_int(note_coverage.get("required"), 0)
    note_covered = _coerce_int(note_coverage.get("covered"), 0)
    if note_required and note_covered < note_required:
        blockers.append(_issue("cap_expansion_notes_missing", "Cap expansion canary has missing session Notes coverage.", component="notes"))

    pnl_summary = dict(cap_canary.get("pnl_summary") or {"sample_count": 0, "realized_pnl": 0.0})
    slippage_summary = dict(
        cap_canary.get("slippage_summary") or {"sample_count": 0, "average_abs_bps": None, "worst_abs_bps": None}
    )
    worst_slippage = slippage_summary.get("worst_abs_bps")
    average_slippage = slippage_summary.get("average_abs_bps")
    if worst_slippage is not None and _coerce_float(worst_slippage, 0.0) > LIMITED_LIVE_NEXT_TIER_CAP_BLOCK_SLIPPAGE_BPS:
        blockers.append(_issue("slippage_blocked", f"Expanded-cap worst slippage was {_coerce_float(worst_slippage):.1f} bps.", component="slippage"))
    elif average_slippage is not None and _coerce_float(average_slippage, 0.0) > LIMITED_LIVE_NEXT_TIER_CAP_WARN_SLIPPAGE_BPS:
        warnings.append(_issue("slippage_warning", f"Expanded-cap average slippage was {_coerce_float(average_slippage):.1f} bps.", component="slippage", severity="warning"))
    if _coerce_float(pnl_summary.get("realized_pnl"), 0.0) < 0:
        blockers.append(_issue("negative_expanded_cap_pnl", "Expanded-cap realized PnL is negative.", component="pnl"))
    if recommended_cap <= current_cap + 0.01:
        warnings.append(_issue("target_cap_not_higher", "Configured next-tier cap is not above the current expanded cap.", component="cap_policy", severity="warning"))

    ledger_counts = _uncontrolled_live_ledger_counts()
    if ledger_counts["pending"] or ledger_counts["open"]:
        blockers.append(_issue("uncontrolled_live_exposure", "Existing uncontrolled live order or position evidence is present.", component="broker_live_gate"))

    order_event_summary = _recent_cap_expansion_order_event_summary(
        db,
        tenant=tenant,
        now=now,
        stale_after_days=stale_after_days,
        current_cap=current_cap,
    )
    if _coerce_int(order_event_summary.get("failed_count"), 0) > 0:
        blockers.append(_issue("recent_cap_expansion_order_event_failed", "Recent cap expansion order events include failure evidence.", component="order_events"))
    if _coerce_int(order_event_summary.get("non_limit_count"), 0) > 0:
        blockers.append(_issue("non_limit_order_evidence", "Recent cap expansion order event evidence includes non-limit routing.", component="order_events"))
    if _coerce_int(order_event_summary.get("cap_breach_count"), 0) > 0:
        blockers.append(_issue("expanded_notional_cap_breached", "Recent cap expansion order event evidence exceeds the expanded cap.", component="order_events"))

    required_actions: list[dict[str, Any]] = []
    if blockers:
        required_actions.append({"key": "clear_next_tier_cap_blockers", "detail": "Clear all next-tier cap blockers before requesting a higher limited-live cap."})
    elif warnings:
        required_actions.append({"key": "operator_review_required", "detail": "Review warnings and confirm the next-tier cap before building a separate runtime gate."})
    else:
        required_actions.append({"key": "request_next_tier_cap_approval", "detail": "Evidence is clean. Request operator approval for a separate next-tier cap gate."})

    status = "blocked" if blockers else "needs_operator_review" if warnings else "ready_to_request_next_tier_cap"
    label = {
        "blocked": "Limited-live next-tier cap request blocked",
        "needs_operator_review": "Limited-live next-tier cap needs operator review",
        "ready_to_request_next_tier_cap": "Ready to request next-tier limited-live cap",
    }[status]
    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": profile_key,
            "live_profile_key": LIMITED_LIVE_NEXT_TIER_CAP_LIVE_PROFILE,
            "linked_account_id": getattr(linked_account, "id", None),
            "session_day": current_day,
            "evaluated_at": _serialize_datetime(now),
            "current_max_notional": current_cap,
            "recommended_next_max_notional": recommended_cap,
            "target_max_notional": target_cap,
            "required_clean_sessions": clean_required,
            "stale_after_days": stale_after_days,
            "evidence_summaries": evidence_summaries,
            "clean_session_progress": {
                "clean": cap_clean_count,
                "required": clean_required,
                "reported_required": cap_canary_required,
            },
            "latest_gate_status": cap_canary.get("latest_gate_status") or cap_gate_status,
            "latest_terminal_state": cap_canary.get("latest_terminal_state"),
            "latest_reconciliation_status": latest_reconciliation,
            "consumed_order_count": _coerce_int(cap_canary.get("consumed_order_count"), 0),
            "broker_live_gate_status": broker_gate_status,
            "broker_gate_status": broker_gate_status,
            "safety_lock_status": safety_lock_status,
            "state_control_status": state_control_status,
            "cap_expansion_canary_status": cap_canary_status,
            "cap_expansion_report_status": cap_report_status,
            "cap_expansion_gate_status": cap_gate_status,
            "rollout_canary_status": rollout_canary_status,
            "rollout_gate_status": rollout_gate_status,
            "uncontrolled_live_ledger_counts": ledger_counts,
            "pnl_summary": pnl_summary,
            "slippage_summary": slippage_summary,
            "note_coverage": note_coverage,
            "order_event_summary": order_event_summary,
            "blockers": blockers[:24],
            "warnings": warnings[:24],
            "required_operator_actions": required_actions,
            "manual_action_required": bool(blockers or warnings),
        }
    )


def _find_existing_next_tier_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIMITED_LIVE_NEXT_TIER_CAP_NOTE_OWNER,
            limit=LIMITED_LIVE_NEXT_TIER_CAP_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "limited-live-next-tier-cap",
        "limited-live-cap-expansion-canary",
        "limited-live-cap-expansion-gate",
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
    progress = dict(report.get("clean_session_progress") or {})
    lines = [
        f"Automation limited-live next-tier cap report for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Run source: {str(report.get('run_source') or 'manual').replace('_', ' ')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Clean expanded-cap sessions: {progress.get('clean', 0)} / {progress.get('required', report.get('required_clean_sessions', 0))} required",
        f"Current expanded cap: ${float(report.get('current_max_notional') or 0.0):.2f}",
        f"Recommended next cap: ${float(report.get('recommended_next_max_notional') or 0.0):.2f}",
        f"Target cap setting: ${float(report.get('target_max_notional') or 0.0):.2f}",
        f"Cap expansion canary: {report.get('cap_expansion_canary_status') or 'missing'}",
        f"Cap expansion report: {report.get('cap_expansion_report_status') or 'missing'}",
        f"Cap expansion gate: {report.get('cap_expansion_gate_status') or 'missing'}",
        f"Broker-live gate: {report.get('broker_gate_status') or report.get('broker_live_gate_status') or 'unknown'}",
        f"Safety locks: {report.get('safety_lock_status') or 'unknown'}",
        f"State control: {report.get('state_control_status') or 'unknown'}",
        f"Latest reconciliation: {report.get('latest_reconciliation_status') or 'missing'}",
        f"Consumed expanded-cap orders: {report.get('consumed_order_count') or 0}",
        f"Realized PnL samples: {pnl.get('sample_count', 0)} | total ${float(pnl.get('realized_pnl') or 0.0):.2f}",
        f"Slippage samples: {slippage.get('sample_count', 0)} | worst {slippage.get('worst_abs_bps') if slippage.get('worst_abs_bps') is not None else '--'} bps",
        (
            f"Next scheduled review: {report.get('next_eligible_run_at')}"
            if report.get("next_eligible_run_at")
            else "Next scheduled review: not available"
        ),
        "",
        "This report is advisory. It does not place, cancel, or close orders, enable live trading, arm automation, clear locks, tune baseline settings, activate or rollback allowances, increase caps, or change broker-live gates.",
        "",
        "Blockers",
    ]
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    if blockers:
        lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in blockers[:16])
    else:
        lines.append("- None.")
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    lines.extend(["", "Warnings"])
    if warnings:
        lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in warnings[:16])
    else:
        lines.append("- None.")
    actions = [item for item in list(report.get("required_operator_actions") or []) if isinstance(item, dict)]
    lines.extend(["", "Required operator actions"])
    if actions:
        lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in actions[:8])
    else:
        lines.append("- None.")
    return "\n".join(lines).strip()


def _sync_next_tier_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "limited-live-next-tier-cap",
        "limited-live-cap-expansion-canary",
        "limited-live-cap-expansion-gate",
        _profile_tag(profile_key),
        _profile_tag(LIMITED_LIVE_NEXT_TIER_CAP_LIVE_PROFILE),
        f"session-{session_day}",
    ]
    title = f"Automation limited-live next-tier cap report - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = (
        str(report.get("related_note_id") or report.get("note_id") or "").strip()
        or _find_existing_next_tier_note_id(profile_key, session_day)
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": LIMITED_LIVE_NEXT_TIER_CAP_NOTE_OWNER,
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


def run_limited_live_next_tier_cap_report(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    profile_key: str = LIMITED_LIVE_NEXT_TIER_CAP_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_run_source = str(run_source or "manual").strip().lower().replace(" ", "_") or "manual"
    review_session_day, _review_window_open = limited_live_next_tier_cap_session_day_for(
        now,
        forced=normalized_run_source != "scheduled",
    )
    original_settings = deepcopy(dict(paper_state.get("settings") or {}))
    original_live_settings = deepcopy(dict((live_state or {}).get("settings") or {}))
    protected_before = {
        "paper": {key: original_settings.get(key) for key in ("enabled", "armed", "kill_switch", "execution_intent")},
        "live": {key: original_live_settings.get(key) for key in ("enabled", "armed", "kill_switch", "execution_intent")},
        "rollout_approval": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_rollout_gate_approval") or {})),
        "rollout_allowance": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_rollout_gate_allowance") or {})),
        "cap_approval": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_cap_expansion_gate_approval") or {})),
        "cap_allowance": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_cap_expansion_gate_allowance") or {})),
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
            _parse_datetime(runtime_before.get("limited_live_next_tier_cap_report_last_scheduled_run_at"))
        )
    )
    report["last_scheduled_session_day"] = (
        review_session_day
        if normalized_run_source == "scheduled"
        else str(runtime_before.get("limited_live_next_tier_cap_report_last_scheduled_session_day") or "").strip()
        or None
    )
    next_eligible = (
        next_eligible_limited_live_next_tier_cap_review_after_session(review_session_day)
        if normalized_run_source == "scheduled"
        else next_eligible_limited_live_next_tier_cap_review_at(now)
    )
    report["next_eligible_run_at"] = _serialize_datetime(next_eligible)
    report["scheduled_status"] = "reviewed" if normalized_run_source == "scheduled" else "manual"
    note_id = _sync_next_tier_note(tenant=tenant, profile_key=profile_key, report=report)
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
        "rollout_approval": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_rollout_gate_approval") or {})),
        "rollout_allowance": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_rollout_gate_allowance") or {})),
        "cap_approval": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_cap_expansion_gate_approval") or {})),
        "cap_allowance": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_cap_expansion_gate_allowance") or {})),
    }
    report["baseline_settings_mutated"] = protected_before["paper"] != protected_after["paper"] or protected_before["live"] != protected_after["live"]
    report["allowance_mutated"] = any(
        protected_before[key] != protected_after[key]
        for key in ("rollout_approval", "rollout_allowance", "cap_approval", "cap_allowance")
    )

    runtime = paper_state.setdefault("runtime", {})
    summary_keys = {
        "status",
        "label",
        "profile_key",
        "live_profile_key",
        "linked_account_id",
        "session_day",
        "evaluated_at",
        "current_max_notional",
        "recommended_next_max_notional",
        "target_max_notional",
        "required_clean_sessions",
        "stale_after_days",
        "evidence_summaries",
        "clean_session_progress",
        "latest_gate_status",
        "latest_terminal_state",
        "latest_reconciliation_status",
        "consumed_order_count",
        "broker_live_gate_status",
        "broker_gate_status",
        "safety_lock_status",
        "state_control_status",
        "cap_expansion_canary_status",
        "cap_expansion_report_status",
        "cap_expansion_gate_status",
        "rollout_canary_status",
        "rollout_gate_status",
        "uncontrolled_live_ledger_counts",
        "pnl_summary",
        "slippage_summary",
        "note_coverage",
        "order_event_summary",
        "blockers",
        "warnings",
        "required_operator_actions",
        "manual_action_required",
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
    runtime["limited_live_next_tier_cap_report_last_report"] = serialize_value(summary)
    runtime["limited_live_next_tier_cap_report_last_note_id"] = note_id
    runtime["limited_live_next_tier_cap_report_note_session_day"] = report.get("session_day")
    runtime["limited_live_next_tier_cap_report_last_run_at"] = report.get("evaluated_at")
    if normalized_run_source == "scheduled":
        runtime["limited_live_next_tier_cap_report_last_scheduled_run_at"] = report.get("evaluated_at")
        runtime["limited_live_next_tier_cap_report_last_scheduled_session_day"] = review_session_day
    runtime["limited_live_next_tier_cap_report_next_eligible_run_at"] = report.get("next_eligible_run_at")
    runtime["limited_live_next_tier_cap_report_last_skipped_reason"] = None
    runtime["limited_live_next_tier_cap_report_last_error"] = None
    history = list(runtime.get("limited_live_next_tier_cap_report_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "current_max_notional": report.get("current_max_notional"),
            "recommended_next_max_notional": report.get("recommended_next_max_notional"),
            "clean_session_count": dict(report.get("clean_session_progress") or {}).get("clean"),
            "required_clean_sessions": report.get("required_clean_sessions"),
            "blocker_count": len(report.get("blockers") or []),
            "warning_count": len(report.get("warnings") or []),
            "note_id": note_id,
            "run_source": normalized_run_source,
        },
    )
    runtime["limited_live_next_tier_cap_report_history"] = serialize_value(
        history[:LIMITED_LIVE_NEXT_TIER_CAP_HISTORY_LIMIT]
    )
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.limited_live_next_tier_cap_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "live_profile_key": LIMITED_LIVE_NEXT_TIER_CAP_LIVE_PROFILE,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "current_max_notional": report.get("current_max_notional"),
                "recommended_next_max_notional": report.get("recommended_next_max_notional"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
                "note_id": note_id,
                "run_source": normalized_run_source,
                "baseline_settings_mutated": report.get("baseline_settings_mutated"),
                "allowance_mutated": report.get("allowance_mutated"),
            },
        )
    return serialize_value(report)
