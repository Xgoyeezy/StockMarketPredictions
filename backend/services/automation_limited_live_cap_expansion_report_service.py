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

LIMITED_LIVE_CAP_EXPANSION_NOTE_OWNER = "automation-ai"
LIMITED_LIVE_CAP_EXPANSION_HISTORY_LIMIT = 8
LIMITED_LIVE_CAP_EXPANSION_NOTE_LIMIT = 250
LIMITED_LIVE_CAP_EXPANSION_REQUIRED_CLEAN_SESSIONS = 3
LIMITED_LIVE_CAP_EXPANSION_STALE_AFTER_DAYS = 2
LIMITED_LIVE_CAP_EXPANSION_TARGET_MAX_NOTIONAL = 250.0
LIMITED_LIVE_CAP_EXPANSION_PAPER_PROFILE = "personal_paper"
LIMITED_LIVE_CAP_EXPANSION_LIVE_PROFILE = "personal_live"
LIMITED_LIVE_CAP_EXPANSION_BLOCK_SLIPPAGE_BPS = 100.0
LIMITED_LIVE_CAP_EXPANSION_WARN_SLIPPAGE_BPS = 50.0
LIMITED_LIVE_CAP_EXPANSION_TARGET_NOTIONAL_CEILING = 5_000.0

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE

LIMITED_LIVE_CAP_EXPANSION_SETTINGS_DEFAULTS: dict[str, Any] = {
    "limited_live_cap_expansion_report_enabled": True,
    "limited_live_cap_expansion_report_auto_review_enabled": True,
    "limited_live_cap_expansion_required_clean_sessions": LIMITED_LIVE_CAP_EXPANSION_REQUIRED_CLEAN_SESSIONS,
    "limited_live_cap_expansion_stale_after_days": LIMITED_LIVE_CAP_EXPANSION_STALE_AFTER_DAYS,
    "limited_live_cap_expansion_target_max_notional": LIMITED_LIVE_CAP_EXPANSION_TARGET_MAX_NOTIONAL,
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
    return "profile-" + str(profile_key or LIMITED_LIVE_CAP_EXPANSION_PAPER_PROFILE).strip().lower().replace(":", "-")


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def limited_live_cap_expansion_session_day_for(
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


def next_eligible_limited_live_cap_expansion_review_at(value: datetime | None = None) -> datetime:
    now = value or _utc_now()
    session_day, review_window_open = limited_live_cap_expansion_session_day_for(now, forced=False)
    if review_window_open:
        return now
    return _review_time_for_session_day(session_day)


def next_eligible_limited_live_cap_expansion_review_after_session(session_day: str) -> datetime:
    return _review_time_for_session_day(_next_trading_day_after(session_day))


def normalize_limited_live_cap_expansion_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    return {
        "limited_live_cap_expansion_report_enabled": _coerce_bool(
            state.get("limited_live_cap_expansion_report_enabled"),
            bool(LIMITED_LIVE_CAP_EXPANSION_SETTINGS_DEFAULTS["limited_live_cap_expansion_report_enabled"]),
        ),
        "limited_live_cap_expansion_report_auto_review_enabled": _coerce_bool(
            state.get("limited_live_cap_expansion_report_auto_review_enabled"),
            bool(LIMITED_LIVE_CAP_EXPANSION_SETTINGS_DEFAULTS["limited_live_cap_expansion_report_auto_review_enabled"]),
        ),
        "limited_live_cap_expansion_required_clean_sessions": _clamp_int(
            state.get("limited_live_cap_expansion_required_clean_sessions"),
            int(LIMITED_LIVE_CAP_EXPANSION_SETTINGS_DEFAULTS["limited_live_cap_expansion_required_clean_sessions"]),
            minimum=1,
            maximum=20,
        ),
        "limited_live_cap_expansion_stale_after_days": _clamp_int(
            state.get("limited_live_cap_expansion_stale_after_days"),
            int(LIMITED_LIVE_CAP_EXPANSION_SETTINGS_DEFAULTS["limited_live_cap_expansion_stale_after_days"]),
            minimum=1,
            maximum=30,
        ),
        "limited_live_cap_expansion_target_max_notional": _clamp_float(
            state.get("limited_live_cap_expansion_target_max_notional"),
            float(LIMITED_LIVE_CAP_EXPANSION_SETTINGS_DEFAULTS["limited_live_cap_expansion_target_max_notional"]),
            minimum=1.0,
            maximum=LIMITED_LIVE_CAP_EXPANSION_TARGET_NOTIONAL_CEILING,
        ),
    }


def normalize_limited_live_cap_expansion_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("limited_live_cap_expansion_report_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("limited_live_cap_expansion_report_history") or [])[
            :LIMITED_LIVE_CAP_EXPANSION_HISTORY_LIMIT
        ]
        if isinstance(item, dict)
    ]
    return {
        "limited_live_cap_expansion_report_last_report": serialize_value(last_report),
        "limited_live_cap_expansion_report_last_note_id": str(
            runtime.get("limited_live_cap_expansion_report_last_note_id") or ""
        ).strip()
        or None,
        "limited_live_cap_expansion_report_note_session_day": str(
            runtime.get("limited_live_cap_expansion_report_note_session_day") or ""
        ).strip()
        or None,
        "limited_live_cap_expansion_report_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("limited_live_cap_expansion_report_last_run_at"))
        ),
        "limited_live_cap_expansion_report_last_scheduled_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("limited_live_cap_expansion_report_last_scheduled_run_at"))
        ),
        "limited_live_cap_expansion_report_last_scheduled_session_day": str(
            runtime.get("limited_live_cap_expansion_report_last_scheduled_session_day") or ""
        ).strip()
        or None,
        "limited_live_cap_expansion_report_next_eligible_run_at": (
            _serialize_datetime(
                _parse_datetime(runtime.get("limited_live_cap_expansion_report_next_eligible_run_at"))
            )
            or _serialize_datetime(next_eligible_limited_live_cap_expansion_review_at())
        ),
        "limited_live_cap_expansion_report_last_skipped_reason": str(
            runtime.get("limited_live_cap_expansion_report_last_skipped_reason") or ""
        ).strip()
        or None,
        "limited_live_cap_expansion_report_last_error": str(
            runtime.get("limited_live_cap_expansion_report_last_error") or ""
        ).strip()
        or None,
        "limited_live_cap_expansion_report_history": history,
    }


def build_limited_live_cap_expansion_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = normalize_limited_live_cap_expansion_runtime((state or {}).get("runtime"))
    settings = normalize_limited_live_cap_expansion_settings((state or {}).get("settings"))
    raw_settings = dict((state or {}).get("settings") or {})
    current_cap = _coerce_float(raw_settings.get("limited_live_rollout_max_notional"), 100.0)
    report = dict(runtime.get("limited_live_cap_expansion_report_last_report") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "enabled": settings["limited_live_cap_expansion_report_enabled"],
            "auto_review_enabled": settings["limited_live_cap_expansion_report_auto_review_enabled"],
            "current_max_notional": current_cap,
            "recommended_next_max_notional": settings["limited_live_cap_expansion_target_max_notional"],
            "target_max_notional": settings["limited_live_cap_expansion_target_max_notional"],
            "required_clean_sessions": settings["limited_live_cap_expansion_required_clean_sessions"],
            "stale_after_days": settings["limited_live_cap_expansion_stale_after_days"],
            "evidence_summaries": {},
            "clean_session_progress": {
                "clean": 0,
                "required": settings["limited_live_cap_expansion_required_clean_sessions"],
            },
            "broker_live_gate_status": "unknown",
            "safety_lock_status": "unknown",
            "state_control_status": "unknown",
            "pnl_summary": {"sample_count": 0, "realized_pnl": 0.0},
            "slippage_summary": {"sample_count": 0, "average_abs_bps": None, "worst_abs_bps": None},
            "blockers": [],
            "warnings": [],
            "required_operator_actions": [],
            "manual_action_required": False,
            "scheduled_status": "waiting",
            "related_note_id": runtime.get("limited_live_cap_expansion_report_last_note_id"),
            "note_id": runtime.get("limited_live_cap_expansion_report_last_note_id"),
            "last_run_at": runtime.get("limited_live_cap_expansion_report_last_run_at"),
            "last_scheduled_run_at": runtime.get("limited_live_cap_expansion_report_last_scheduled_run_at"),
            "last_scheduled_session_day": runtime.get("limited_live_cap_expansion_report_last_scheduled_session_day"),
            "next_eligible_run_at": runtime.get("limited_live_cap_expansion_report_next_eligible_run_at"),
            "skipped_reason": runtime.get("limited_live_cap_expansion_report_last_skipped_reason"),
            "last_error": runtime.get("limited_live_cap_expansion_report_last_error"),
        }
    report.setdefault("enabled", settings["limited_live_cap_expansion_report_enabled"])
    report.setdefault("auto_review_enabled", settings["limited_live_cap_expansion_report_auto_review_enabled"])
    report.setdefault("current_max_notional", current_cap)
    report.setdefault("recommended_next_max_notional", settings["limited_live_cap_expansion_target_max_notional"])
    report.setdefault("target_max_notional", settings["limited_live_cap_expansion_target_max_notional"])
    report.setdefault("required_clean_sessions", settings["limited_live_cap_expansion_required_clean_sessions"])
    report.setdefault("stale_after_days", settings["limited_live_cap_expansion_stale_after_days"])
    report.setdefault("related_note_id", runtime.get("limited_live_cap_expansion_report_last_note_id"))
    report.setdefault("note_id", runtime.get("limited_live_cap_expansion_report_last_note_id"))
    report.setdefault("last_run_at", runtime.get("limited_live_cap_expansion_report_last_run_at"))
    report.setdefault("last_scheduled_run_at", runtime.get("limited_live_cap_expansion_report_last_scheduled_run_at"))
    report.setdefault(
        "last_scheduled_session_day",
        runtime.get("limited_live_cap_expansion_report_last_scheduled_session_day"),
    )
    report.setdefault("next_eligible_run_at", runtime.get("limited_live_cap_expansion_report_next_eligible_run_at"))
    report.setdefault("skipped_reason", runtime.get("limited_live_cap_expansion_report_last_skipped_reason"))
    report.setdefault("last_error", runtime.get("limited_live_cap_expansion_report_last_error"))
    return serialize_value(report)


def _issue(
    key: str,
    detail: str,
    *,
    component: str = "limited_live_cap_expansion",
    severity: str = "blocker",
) -> dict[str, Any]:
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


def _row_value(row: Any, key: str) -> Any:
    try:
        return row.get(key)
    except AttributeError:
        return None


def _row_is_uncontrolled_live(row: Any) -> bool:
    profile = str(_row_value(row, "automation_profile_key") or "").strip().lower()
    intent = str(_row_value(row, "automation_execution_intent") or _row_value(row, "execution_intent") or "").strip().lower()
    if profile != LIMITED_LIVE_CAP_EXPANSION_LIVE_PROFILE and intent != "broker_live":
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


def _recent_limited_live_order_event_summary(
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
        rollout_id = str(payload.get("limited_live_rollout_id") or "").strip()
        expansion_id = str(payload.get("limited_live_cap_expansion_id") or "").strip()
        next_tier_cap_id = str(payload.get("limited_live_next_tier_cap_id") or "").strip()
        if row_profile and row_profile != LIMITED_LIVE_CAP_EXPANSION_LIVE_PROFILE:
            continue
        if next_tier_cap_id or route_family == "limited_live_next_tier_cap" or entry_reason == "limited_live_next_tier_cap":
            continue
        if (
            not rollout_id
            and not expansion_id
            and route_family not in {"limited_live_rollout", "limited_live_cap_expansion"}
            and entry_reason not in {"limited_live_rollout", "limited_live_cap_expansion"}
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
    settings = normalize_limited_live_cap_expansion_settings(paper_state.get("settings"))
    paper_settings = dict(paper_state.get("settings") or {})
    live_state = live_state if isinstance(live_state, dict) else {}
    live_settings = dict(live_state.get("settings") or {})
    current_day = str(review_session_day or "").strip() or _session_day_for(now)
    required_clean_sessions = int(settings["limited_live_cap_expansion_required_clean_sessions"])
    stale_after_days = int(settings["limited_live_cap_expansion_stale_after_days"])
    target_cap = float(settings["limited_live_cap_expansion_target_max_notional"])
    current_cap = _coerce_float(paper_settings.get("limited_live_rollout_max_notional"), 100.0)
    recommended_cap = max(current_cap, target_cap)

    canary = dict(runtime.get("limited_live_rollout_canary_last_report") or {})
    gate = dict(runtime.get("limited_live_rollout_gate_last_report") or {})
    promotion = dict(runtime.get("live_pilot_promotion_report_last_report") or {})
    state_control = dict(runtime.get("state_control_last_evaluation") or {})
    state_control_status = str(runtime.get("state_control_state") or state_control.get("state") or "unknown").strip().lower()

    evidence_summaries = {
        "limited_live_rollout_canary": _evidence_summary(
            now=now,
            key="limited_live_rollout_canary",
            label="Limited-live rollout canary",
            report=canary,
            ready_statuses={"ready_for_operator_review"},
            stale_after_days=stale_after_days,
        ),
        "live_pilot_promotion_report": _evidence_summary(
            now=now,
            key="live_pilot_promotion_report",
            label="Live pilot promotion report",
            report=promotion,
            ready_statuses={"ready_to_request_limited_live_rollout"},
            stale_after_days=stale_after_days,
        ),
        "limited_live_rollout_gate": _evidence_summary(
            now=now,
            key="limited_live_rollout_gate",
            label="Limited-live rollout gate",
            report=gate,
            ready_statuses={"active", "rolled_back", "completed", "warning"},
            stale_after_days=stale_after_days,
        ),
    }

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if profile_key != LIMITED_LIVE_CAP_EXPANSION_PAPER_PROFILE:
        blockers.append(_issue("paper_profile_required", "V1 cap expansion reports are reviewed from the personal paper automation profile.", component="profile"))

    canary_status = _report_status(canary)
    promotion_status = _report_status(promotion)
    gate_status = _report_status(gate)
    canary_clean_count = _coerce_int(canary.get("clean_session_count"), 0)
    canary_required = _coerce_int(canary.get("required_clean_sessions"), required_clean_sessions)
    clean_required = max(required_clean_sessions, min(canary_required or required_clean_sessions, 20))
    promotion_broker_gate_status = str(promotion.get("broker_live_gate_status") or "").strip().lower()
    canary_broker_gate_status = str(
        canary.get("broker_live_gate_status") or canary.get("broker_gate_status") or ""
    ).strip().lower()
    broker_gate_status = (
        promotion_broker_gate_status
        if promotion_broker_gate_status and promotion_broker_gate_status != "open"
        else canary_broker_gate_status
        or promotion_broker_gate_status
        or "unknown"
    )
    promotion_safety_lock_status = str(promotion.get("safety_lock_status") or "").strip().lower()
    canary_safety_lock_status = str(canary.get("safety_lock_status") or "").strip().lower()
    safety_lock_status = (
        promotion_safety_lock_status
        if promotion_safety_lock_status and promotion_safety_lock_status not in {"clear", "none"}
        else canary_safety_lock_status
        or promotion_safety_lock_status
        or "unknown"
    )

    if canary_status in {"missing", "not_run"}:
        blockers.append(_issue("limited_live_rollout_canary_missing", "Run the limited-live rollout canary before requesting cap expansion.", component="limited_live_rollout_canary"))
    elif canary_status in {"blocked", "failed", "fail", "collecting"}:
        blockers.append(_issue("limited_live_rollout_canary_not_ready", "Limited-live rollout canary is not ready for cap expansion.", component="limited_live_rollout_canary"))
    elif canary_status == "warning" or canary.get("warnings"):
        warnings.append(_issue("limited_live_rollout_canary_warning", "Limited-live rollout canary has advisory warnings.", component="limited_live_rollout_canary", severity="warning"))
    if canary_clean_count < clean_required:
        blockers.append(
            _issue(
                "limited_live_clean_sessions_low",
                f"Limited-live rollout canary has {canary_clean_count}/{clean_required} clean sessions.",
                component="limited_live_rollout_canary",
            )
        )
    if evidence_summaries["limited_live_rollout_canary"].get("stale"):
        blockers.append(_issue("limited_live_rollout_canary_stale", "Limited-live rollout canary evidence is stale.", component="limited_live_rollout_canary"))
    if not _report_note_id(canary):
        blockers.append(_issue("limited_live_rollout_canary_note_missing", "Limited-live rollout canary is missing a linked automation-ai note.", component="notes"))

    for item in list(canary.get("blockers") or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "limited_live_rollout_canary_blocker")
        blockers.append(
            _issue(
                key,
                str(item.get("detail") or "Limited-live rollout canary has unresolved blocker evidence."),
                component=str(item.get("component") or "limited_live_rollout_canary"),
            )
        )
    for item in list(canary.get("warnings") or []):
        if not isinstance(item, dict):
            continue
        warnings.append(
            _issue(
                str(item.get("key") or "limited_live_rollout_canary_warning"),
                str(item.get("detail") or "Limited-live rollout canary has advisory warning evidence."),
                component=str(item.get("component") or "limited_live_rollout_canary"),
                severity="warning",
            )
        )

    if promotion_status in {"missing", "not_run"}:
        blockers.append(_issue("promotion_report_missing", "Run the live pilot promotion report before cap expansion review.", component="live_pilot_promotion"))
    elif promotion_status != "ready_to_request_limited_live_rollout":
        blockers.append(_issue("promotion_report_not_ready", "Live pilot promotion report is not ready.", component="live_pilot_promotion"))
    if evidence_summaries["live_pilot_promotion_report"].get("stale"):
        blockers.append(_issue("promotion_report_stale", "Live pilot promotion report evidence is stale.", component="live_pilot_promotion"))
    if promotion.get("blockers"):
        blockers.append(_issue("promotion_report_blocked", "Live pilot promotion report has unresolved blockers.", component="live_pilot_promotion"))
    elif promotion.get("warnings"):
        warnings.append(_issue("promotion_report_warning", "Live pilot promotion report has advisory warnings.", component="live_pilot_promotion", severity="warning"))
    if not _report_note_id(promotion):
        blockers.append(_issue("promotion_report_note_missing", "Live pilot promotion report is missing a linked automation-ai note.", component="notes"))

    if broker_gate_status != "open":
        blockers.append(_issue("broker_live_gate_not_open", "Broker-live gate is not open.", component="broker_live_gate"))
    if safety_lock_status not in {"clear", "none"}:
        blockers.append(_issue("safety_lock_active", "A paper or live safety lock is active.", component="safety_lock"))
    if _coerce_bool(paper_settings.get("kill_switch"), False) or _coerce_bool(live_settings.get("kill_switch"), False):
        blockers.append(_issue("kill_switch_active", "A paper or live kill switch is active.", component="safety_lock"))
    if state_control_status == "halt" or _coerce_bool(runtime.get("state_control_halt_active"), False):
        blockers.append(_issue("state_control_halt", "State-control is halted.", component="state_control"))

    latest_reconciliation = str(canary.get("latest_reconciliation_status") or "missing").strip().lower()
    if latest_reconciliation in {"blocked", "failed", "fail", "mismatch", "mismatched"}:
        blockers.append(_issue("reconciliation_blocked", "Limited-live broker/local reconciliation is blocked or mismatched.", component="reconciliation"))

    note_coverage = dict(canary.get("note_coverage") or {})
    note_required = _coerce_int(note_coverage.get("required"), 0)
    note_covered = _coerce_int(note_coverage.get("covered"), 0)
    if note_required and note_covered < note_required:
        blockers.append(_issue("limited_live_notes_missing", "Limited-live rollout canary has missing session Notes coverage.", component="notes"))

    pnl_summary = dict(canary.get("pnl_summary") or {"sample_count": 0, "realized_pnl": 0.0})
    slippage_summary = dict(
        canary.get("slippage_summary") or {"sample_count": 0, "average_abs_bps": None, "worst_abs_bps": None}
    )
    worst_slippage = slippage_summary.get("worst_abs_bps")
    average_slippage = slippage_summary.get("average_abs_bps")
    if worst_slippage is not None and _coerce_float(worst_slippage, 0.0) > LIMITED_LIVE_CAP_EXPANSION_BLOCK_SLIPPAGE_BPS:
        blockers.append(
            _issue(
                "slippage_blocked",
                f"Limited-live rollout worst slippage was {_coerce_float(worst_slippage):.1f} bps.",
                component="slippage",
            )
        )
    elif average_slippage is not None and _coerce_float(average_slippage, 0.0) > LIMITED_LIVE_CAP_EXPANSION_WARN_SLIPPAGE_BPS:
        warnings.append(
            _issue(
                "slippage_warning",
                f"Limited-live rollout average slippage was {_coerce_float(average_slippage):.1f} bps.",
                component="slippage",
                severity="warning",
            )
        )
    if _coerce_float(pnl_summary.get("realized_pnl"), 0.0) < 0:
        blockers.append(_issue("negative_limited_live_pnl", "Limited-live rollout realized PnL is negative.", component="pnl"))

    ledger_counts = _uncontrolled_live_ledger_counts()
    if ledger_counts["pending"] or ledger_counts["open"]:
        blockers.append(_issue("uncontrolled_live_exposure", "Existing uncontrolled live order or position evidence is present.", component="broker_live_gate"))

    order_event_summary = _recent_limited_live_order_event_summary(
        db,
        tenant=tenant,
        now=now,
        stale_after_days=stale_after_days,
        current_cap=current_cap,
    )
    if _coerce_int(order_event_summary.get("failed_count"), 0) > 0:
        blockers.append(_issue("recent_limited_live_order_event_failed", "Recent limited-live order events include failure evidence.", component="order_events"))
    if _coerce_int(order_event_summary.get("non_limit_count"), 0) > 0:
        blockers.append(_issue("non_limit_order_evidence", "Recent limited-live order event evidence includes non-limit routing.", component="order_events"))
    if _coerce_int(order_event_summary.get("cap_breach_count"), 0) > 0:
        blockers.append(_issue("notional_cap_breached", "Recent limited-live order event evidence exceeds the current cap.", component="order_events"))

    required_actions: list[dict[str, Any]] = []
    if blockers:
        required_actions.append(
            {
                "key": "clear_cap_expansion_blockers",
                "detail": "Clear all cap-expansion blockers before requesting a higher limited-live cap.",
            }
        )
    elif warnings:
        required_actions.append(
            {
                "key": "operator_review_required",
                "detail": "Review warnings and confirm the target cap before requesting the separate expansion gate.",
            }
        )
    else:
        required_actions.append(
            {
                "key": "request_limited_live_cap_expansion_approval",
                "detail": "Evidence is clean. Request operator approval for the separate limited-live cap expansion gate.",
            }
        )

    status = "blocked" if blockers else "needs_operator_review" if warnings else "ready_to_request_cap_expansion"
    label = {
        "blocked": "Limited-live cap expansion blocked",
        "needs_operator_review": "Limited-live cap expansion needs operator review",
        "ready_to_request_cap_expansion": "Ready to request limited-live cap expansion",
    }[status]
    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": profile_key,
            "live_profile_key": LIMITED_LIVE_CAP_EXPANSION_LIVE_PROFILE,
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
                "clean": canary_clean_count,
                "required": clean_required,
                "reported_required": canary_required,
            },
            "latest_rollout_status": canary.get("latest_rollout_status") or gate_status,
            "latest_terminal_state": canary.get("latest_terminal_state"),
            "latest_reconciliation_status": latest_reconciliation,
            "consumed_order_count": _coerce_int(canary.get("consumed_order_count"), 0),
            "broker_live_gate_status": broker_gate_status,
            "broker_gate_status": broker_gate_status,
            "safety_lock_status": safety_lock_status,
            "state_control_status": state_control_status,
            "promotion_status": promotion_status,
            "rollout_canary_status": canary_status,
            "limited_live_rollout_gate_status": gate_status,
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


def _find_existing_cap_expansion_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIMITED_LIVE_CAP_EXPANSION_NOTE_OWNER,
            limit=LIMITED_LIVE_CAP_EXPANSION_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "limited-live-cap-expansion",
        "limited-live-rollout-canary",
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
    progress = dict(report.get("clean_session_progress") or {})
    pnl = dict(report.get("pnl_summary") or {})
    slippage = dict(report.get("slippage_summary") or {})
    order_events = dict(report.get("order_event_summary") or {})
    lines = [
        f"Automation limited-live cap expansion report for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Run source: {str(report.get('run_source') or 'manual').replace('_', ' ')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Current cap: ${float(report.get('current_max_notional') or 0.0):.2f}",
        f"Recommended next cap: ${float(report.get('recommended_next_max_notional') or 0.0):.2f}",
        f"Clean limited-live sessions: {progress.get('clean', 0)} / {progress.get('required', 0)} required",
        f"Rollout canary: {report.get('rollout_canary_status') or 'missing'}",
        f"Promotion report: {report.get('promotion_status') or 'missing'}",
        f"Broker-live gate: {report.get('broker_live_gate_status') or report.get('broker_gate_status') or 'unknown'}",
        f"Safety locks: {report.get('safety_lock_status') or 'unknown'}",
        f"State control: {report.get('state_control_status') or 'unknown'}",
        f"Latest reconciliation: {report.get('latest_reconciliation_status') or 'missing'}",
        f"Consumed orders: {report.get('consumed_order_count') or 0}",
        f"Order events: {order_events.get('count', 0)} total | {order_events.get('failed_count', 0)} failed | {order_events.get('cap_breach_count', 0)} cap breaches",
        f"Realized PnL samples: {pnl.get('sample_count', 0)} | total ${float(pnl.get('realized_pnl') or 0.0):.2f}",
        f"Slippage samples: {slippage.get('sample_count', 0)} | worst {slippage.get('worst_abs_bps') if slippage.get('worst_abs_bps') is not None else '--'} bps",
        (
            f"Next scheduled review: {report.get('next_eligible_run_at')}"
            if report.get("next_eligible_run_at")
            else "Next scheduled review: not available"
        ),
        "",
        "This report is advisory. It does not place, cancel, or close orders, enable live trading, arm automation, clear locks, tune baseline settings, activate or rollback rollout allowances, increase caps, or change broker-live gates.",
        "",
        "Evidence",
    ]
    for key, item in dict(report.get("evidence_summaries") or {}).items():
        if not isinstance(item, dict):
            continue
        age = item.get("age_days")
        age_text = f"{float(age):.2f}d" if age is not None else "--"
        lines.append(
            f"- {item.get('label') or key}: {str(item.get('status') or 'missing').upper()} | "
            f"note {'linked' if item.get('note_id') else 'missing'} | age {age_text}"
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


def _sync_cap_expansion_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "limited-live-cap-expansion",
        "limited-live-rollout-canary",
        "limited-live-rollout",
        _profile_tag(profile_key),
        _profile_tag(LIMITED_LIVE_CAP_EXPANSION_LIVE_PROFILE),
        f"session-{session_day}",
    ]
    title = f"Automation limited-live cap expansion report - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = (
        str(report.get("related_note_id") or report.get("note_id") or "").strip()
        or _find_existing_cap_expansion_note_id(profile_key, session_day)
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": LIMITED_LIVE_CAP_EXPANSION_NOTE_OWNER,
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


def run_limited_live_cap_expansion_report(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    profile_key: str = LIMITED_LIVE_CAP_EXPANSION_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_run_source = str(run_source or "manual").strip().lower().replace(" ", "_") or "manual"
    review_session_day, _review_window_open = limited_live_cap_expansion_session_day_for(
        now,
        forced=normalized_run_source != "scheduled",
    )
    original_settings = deepcopy(dict(paper_state.get("settings") or {}))
    original_live_settings = deepcopy(dict((live_state or {}).get("settings") or {}))
    protected_before = {
        "paper": {key: original_settings.get(key) for key in ("enabled", "armed", "kill_switch", "execution_intent")},
        "live": {key: original_live_settings.get(key) for key in ("enabled", "armed", "kill_switch", "execution_intent")},
        "approval": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_rollout_gate_approval") or {})),
        "allowance": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_rollout_gate_allowance") or {})),
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
            _parse_datetime(runtime_before.get("limited_live_cap_expansion_report_last_scheduled_run_at"))
        )
    )
    report["last_scheduled_session_day"] = (
        review_session_day
        if normalized_run_source == "scheduled"
        else str(runtime_before.get("limited_live_cap_expansion_report_last_scheduled_session_day") or "").strip() or None
    )
    next_eligible = (
        next_eligible_limited_live_cap_expansion_review_after_session(review_session_day)
        if normalized_run_source == "scheduled"
        else next_eligible_limited_live_cap_expansion_review_at(now)
    )
    report["next_eligible_run_at"] = _serialize_datetime(next_eligible)
    report["scheduled_status"] = "reviewed" if normalized_run_source == "scheduled" else "manual"
    note_id = _sync_cap_expansion_note(tenant=tenant, profile_key=profile_key, report=report)
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
        "approval": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_rollout_gate_approval") or {})),
        "allowance": deepcopy(dict((paper_state.get("runtime") or {}).get("limited_live_rollout_gate_allowance") or {})),
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
        "current_max_notional",
        "recommended_next_max_notional",
        "target_max_notional",
        "required_clean_sessions",
        "stale_after_days",
        "evidence_summaries",
        "clean_session_progress",
        "latest_rollout_status",
        "latest_terminal_state",
        "latest_reconciliation_status",
        "consumed_order_count",
        "broker_gate_status",
        "broker_live_gate_status",
        "safety_lock_status",
        "state_control_status",
        "promotion_status",
        "rollout_canary_status",
        "limited_live_rollout_gate_status",
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
    runtime["limited_live_cap_expansion_report_last_report"] = serialize_value(summary)
    runtime["limited_live_cap_expansion_report_last_note_id"] = note_id
    runtime["limited_live_cap_expansion_report_note_session_day"] = report.get("session_day")
    runtime["limited_live_cap_expansion_report_last_run_at"] = report.get("evaluated_at")
    if normalized_run_source == "scheduled":
        runtime["limited_live_cap_expansion_report_last_scheduled_run_at"] = report.get("evaluated_at")
        runtime["limited_live_cap_expansion_report_last_scheduled_session_day"] = review_session_day
    runtime["limited_live_cap_expansion_report_next_eligible_run_at"] = report.get("next_eligible_run_at")
    runtime["limited_live_cap_expansion_report_last_skipped_reason"] = None
    runtime["limited_live_cap_expansion_report_last_error"] = None
    history = list(runtime.get("limited_live_cap_expansion_report_history") or [])
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
    runtime["limited_live_cap_expansion_report_history"] = serialize_value(
        history[:LIMITED_LIVE_CAP_EXPANSION_HISTORY_LIMIT]
    )
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.limited_live_cap_expansion_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "live_profile_key": LIMITED_LIVE_CAP_EXPANSION_LIVE_PROFILE,
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
