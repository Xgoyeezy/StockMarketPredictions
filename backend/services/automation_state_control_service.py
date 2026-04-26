from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.services import automation_ai_review_service, notes_service, risk_control_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE
STATE_CONTROL_NOTE_OWNER = "automation-ai"
STATE_CONTROL_HISTORY_LIMIT = 20
STATE_CONTROL_SIGNAL_LIMIT = 16
STATE_CONTROL_PERSONAL_LIVE_PROFILE = "personal_live"

STATE_CONTROL_SETTINGS_DEFAULTS: dict[str, Any] = {
    "state_control_enabled": True,
    "state_control_auto_throttle_enabled": True,
    "state_control_auto_halt_enabled": True,
    "state_control_watch_score": 75.0,
    "state_control_derisk_score": 55.0,
    "state_control_halt_score": 30.0,
    "state_control_recovery_cycles": 2,
}

STATE_SEVERITY = {"healthy": 0, "watch": 1, "de_risk": 2, "halt": 3}
STATE_LABELS = {
    "healthy": "Healthy",
    "watch": "Watch",
    "de_risk": "De-risk",
    "halt": "Halt",
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


def _state_label(state: str) -> str:
    return STATE_LABELS.get(str(state or "").strip().lower(), "Unknown")


def _profile_tag(profile_key: str) -> str:
    return "profile-" + str(profile_key or "personal_paper").strip().lower().replace(":", "-")


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def _normalize_state(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in STATE_SEVERITY else "healthy"


def normalize_state_control_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    return {
        "state_control_enabled": _coerce_bool(
            state.get("state_control_enabled"),
            bool(STATE_CONTROL_SETTINGS_DEFAULTS["state_control_enabled"]),
        ),
        "state_control_auto_throttle_enabled": _coerce_bool(
            state.get("state_control_auto_throttle_enabled"),
            bool(STATE_CONTROL_SETTINGS_DEFAULTS["state_control_auto_throttle_enabled"]),
        ),
        "state_control_auto_halt_enabled": _coerce_bool(
            state.get("state_control_auto_halt_enabled"),
            bool(STATE_CONTROL_SETTINGS_DEFAULTS["state_control_auto_halt_enabled"]),
        ),
        "state_control_watch_score": _clamp_float(
            state.get("state_control_watch_score"),
            float(STATE_CONTROL_SETTINGS_DEFAULTS["state_control_watch_score"]),
            minimum=1.0,
            maximum=100.0,
        ),
        "state_control_derisk_score": _clamp_float(
            state.get("state_control_derisk_score"),
            float(STATE_CONTROL_SETTINGS_DEFAULTS["state_control_derisk_score"]),
            minimum=1.0,
            maximum=100.0,
        ),
        "state_control_halt_score": _clamp_float(
            state.get("state_control_halt_score"),
            float(STATE_CONTROL_SETTINGS_DEFAULTS["state_control_halt_score"]),
            minimum=0.0,
            maximum=99.0,
        ),
        "state_control_recovery_cycles": _clamp_int(
            state.get("state_control_recovery_cycles"),
            int(STATE_CONTROL_SETTINGS_DEFAULTS["state_control_recovery_cycles"]),
            minimum=1,
            maximum=20,
        ),
    }


def normalize_state_control_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_evaluation = runtime.get("state_control_last_evaluation")
    if not isinstance(last_evaluation, dict):
        last_evaluation = {}
    transition_history = [
        serialize_value(item)
        for item in list(runtime.get("state_control_transition_history") or [])[:STATE_CONTROL_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    active_overrides = [
        serialize_value(item)
        for item in list(runtime.get("state_control_active_overrides") or [])
        if isinstance(item, dict)
    ]
    effective_overrides = [
        serialize_value(item)
        for item in list(runtime.get("state_control_effective_overrides") or [])
        if isinstance(item, dict)
    ]
    state = _normalize_state(runtime.get("state_control_state") or last_evaluation.get("state"))
    return {
        "state_control_state": state,
        "state_control_score": _clamp_float(runtime.get("state_control_score"), 100.0, minimum=0.0, maximum=100.0),
        "state_control_active_overrides": active_overrides,
        "state_control_effective_overrides": effective_overrides,
        "state_control_last_evaluation": serialize_value(last_evaluation),
        "state_control_last_transition": serialize_value(runtime.get("state_control_last_transition")),
        "state_control_transition_history": transition_history,
        "state_control_last_note_id": str(runtime.get("state_control_last_note_id") or "").strip() or None,
        "state_control_note_session_day": str(runtime.get("state_control_note_session_day") or "").strip() or None,
        "state_control_last_review_at": _serialize_datetime(_parse_datetime(runtime.get("state_control_last_review_at"))),
        "state_control_last_error": str(runtime.get("state_control_last_error") or "").strip() or None,
        "state_control_clean_cycle_count": _clamp_int(
            runtime.get("state_control_clean_cycle_count"),
            0,
            minimum=0,
            maximum=1_000_000,
        ),
        "state_control_halt_active": _coerce_bool(runtime.get("state_control_halt_active"), state == "halt"),
    }


def _signal(
    signals: list[dict[str, Any]],
    *,
    component: str,
    signal: str,
    severity: str,
    detail: str,
    penalty: float,
    hard_fault: bool = False,
    metrics: dict[str, Any] | None = None,
) -> float:
    signals.append(
        {
            "component": component,
            "signal": signal,
            "severity": severity,
            "detail": detail,
            "penalty": float(max(0.0, penalty)),
            "hard_fault": bool(hard_fault),
            "metrics": serialize_value(metrics or {}),
        }
    )
    return float(max(0.0, penalty))


def _recent_order_events(
    db: Session | None,
    *,
    tenant: Tenant,
    profile_key: str,
    session_day: str,
) -> list[dict[str, Any]]:
    if db is None:
        return []
    start_at, end_at = automation_ai_review_service._session_bounds_for_day(session_day)
    statement = (
        select(OrderEventRecord)
        .where(OrderEventRecord.tenant_id == tenant.id)
        .order_by(OrderEventRecord.created_at.desc())
        .limit(250)
    )
    rows = list(db.execute(statement).scalars().all())
    events: list[dict[str, Any]] = []
    normalized_profile = str(profile_key or "personal_paper").strip().lower()
    for row in rows:
        created_at = row.created_at
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at and created_at < start_at:
            continue
        if created_at and created_at >= end_at:
            continue
        payload = dict(row.payload_json or {})
        payload_trade = dict(payload.get("trade") or {})
        row_profile = str(
            payload.get("automation_profile_key")
            or payload_trade.get("automation_profile_key")
            or ""
        ).strip().lower()
        if row_profile and row_profile != normalized_profile:
            continue
        if not (
            str(payload.get("automation_cycle_id") or "").strip()
            or str(payload_trade.get("automation_origin") or "").strip().lower() == "trade_automation"
            or row_profile
        ):
            continue
        events.append(
            {
                "event_key": row.event_key,
                "status": row.status,
                "ticker": row.ticker,
                "detail": row.detail,
                "created_at": _serialize_datetime(created_at),
                "slippage_bps": _coerce_float(payload.get("slippage_bps"), 0.0)
                if payload.get("slippage_bps") is not None
                else None,
                "payload": serialize_value(payload),
            }
        )
    return events[:50]


def _safe_frame(reader: Any) -> pd.DataFrame:
    try:
        value = reader()
    except Exception:
        return pd.DataFrame()
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _collect_evidence(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    session_day: str,
    now: datetime,
) -> dict[str, Any]:
    settings_state = dict(state.get("settings") or {})
    runtime = dict(state.get("runtime") or {})
    owned_closed = automation_ai_review_service._owned_automation_rows(
        _safe_frame(sdm.read_closed_trades),
        tenant_id=str(tenant.id),
        profile_key=profile_key,
    )
    day_closed = automation_ai_review_service._closed_rows_for_session(owned_closed, session_day=session_day)
    owned_open = automation_ai_review_service._owned_automation_rows(
        _safe_frame(sdm.read_open_trades),
        tenant_id=str(tenant.id),
        profile_key=profile_key,
    )
    owned_pending = automation_ai_review_service._owned_automation_rows(
        _safe_frame(sdm.read_pending_orders),
        tenant_id=str(tenant.id),
        profile_key=profile_key,
    )
    analytics = sdm.performance_analytics(day_closed)
    events = _recent_order_events(db, tenant=tenant, profile_key=profile_key, session_day=session_day)
    slippage_values = [
        abs(_coerce_float(item.get("slippage_bps"), 0.0))
        for item in events
        if item.get("slippage_bps") is not None
    ]
    statuses = Counter(str(item.get("status") or "").strip().lower() for item in events)
    event_keys = Counter(str(item.get("event_key") or "").strip().lower() for item in events)
    last_cycle_at = _parse_datetime(runtime.get("last_cycle_at"))
    last_success_at = _parse_datetime(runtime.get("last_success_at"))
    cycle_interval = max(15, _coerce_int(settings_state.get("cycle_interval_seconds"), 60))
    last_cycle_age_seconds = (now - last_cycle_at).total_seconds() if last_cycle_at else None
    last_success_age_seconds = (now - last_success_at).total_seconds() if last_success_at else None
    calibration: dict[str, Any] = {}
    try:
        calibration = dict(sdm.journal_probability_calibration_summary() or {})
    except Exception:
        calibration = {"calibration_scope": "unavailable"}
    closed_pnl = (
        float(pd.to_numeric(day_closed.get("realized_pnl"), errors="coerce").fillna(0.0).sum())
        if not day_closed.empty and "realized_pnl" in day_closed.columns
        else 0.0
    )
    equity_base = max(
        _coerce_float(state.get("__actual_funds"), 0.0)
        or _coerce_float(state.get("__effective_funds"), 0.0)
        or _coerce_float(settings_state.get("account_size"), 10000.0),
        1.0,
    )
    last_candidate = dict(runtime.get("last_candidate") or {})
    last_rejection = dict(runtime.get("last_rejection") or {})
    last_guardrail = dict(runtime.get("last_guardrail") or {})
    path_evaluations = [item for item in list(runtime.get("last_path_evaluations") or []) if isinstance(item, dict)]
    current_collection_audit = dict(runtime.get("current_collection_audit") or {})
    last_collection_audit = dict(runtime.get("last_collection_audit") or {})
    return {
        "session_day": session_day,
        "settings": serialize_value(settings_state),
        "runtime": serialize_value(runtime),
        "closed_trade_count": int(len(day_closed)),
        "open_position_count": int(len(owned_open)),
        "pending_order_count": int(len(owned_pending)),
        "realized_pnl": closed_pnl,
        "equity_base": equity_base,
        "daily_loss_pct": max(0.0, (-closed_pnl / equity_base) * 100.0),
        "analytics": serialize_value(analytics),
        "loss_streak": automation_ai_review_service._count_recent_loss_streak(day_closed),
        "recent_order_events": serialize_value(events[:20]),
        "event_status_counts": dict(statuses),
        "event_key_counts": dict(event_keys),
        "slippage": {
            "sample_count": len(slippage_values),
            "average_abs_bps": float(sum(slippage_values) / len(slippage_values)) if slippage_values else None,
            "worst_abs_bps": float(max(slippage_values)) if slippage_values else None,
        },
        "last_candidate": serialize_value(last_candidate),
        "last_rejection": serialize_value(last_rejection),
        "last_guardrail": serialize_value(last_guardrail),
        "path_evaluations": serialize_value(path_evaluations[:20]),
        "calibration": serialize_value(calibration),
        "accuracy_calibration": serialize_value(runtime.get("accuracy_calibration_last_report") or {}),
        "loss_containment": serialize_value(runtime.get("loss_containment_last_report") or {}),
        "exit_watchdog": serialize_value(runtime.get("exit_watchdog_last_report") or {}),
        "last_cycle_age_seconds": last_cycle_age_seconds,
        "last_success_age_seconds": last_success_age_seconds,
        "cycle_interval_seconds": cycle_interval,
        "current_collection_audit": serialize_value(current_collection_audit),
        "last_collection_audit": serialize_value(last_collection_audit),
    }


def _score_data_integrity(evidence: dict[str, Any], signals: list[dict[str, Any]]) -> float:
    runtime = dict(evidence.get("runtime") or {})
    settings_state = dict(evidence.get("settings") or {})
    score = 100.0
    blocker = str(runtime.get("last_collection_blocker") or "").strip().lower()
    reconciliation = str(runtime.get("current_route_reconciliation_status") or "").strip().lower()
    consistency = str(runtime.get("ledger_snapshot_consistency") or "").strip().lower()
    coverage = str(runtime.get("mark_to_market_coverage_status") or "").strip().lower()
    orphan_count = _coerce_int(runtime.get("current_route_orphan_order_event_count"), 0)
    sample_status = str(runtime.get("current_route_sample_status") or "").strip().lower()
    if "persistence" in blocker and ("fail" in blocker or "error" in blocker):
        score -= _signal(
            signals,
            component="data_integrity",
            signal="ledger_persistence_failed",
            severity="halt",
            detail="Ledger persistence failed during the current-route collection path.",
            penalty=85,
            hard_fault=True,
        )
    if consistency == "inconsistent":
        score -= _signal(
            signals,
            component="data_integrity",
            signal="ledger_snapshot_inconsistent",
            severity="halt",
            detail="Ledger and snapshot accounting are inconsistent.",
            penalty=80,
            hard_fault=True,
        )
    elif consistency in {"missing", "unavailable", "pending"}:
        score -= _signal(
            signals,
            component="data_integrity",
            signal="ledger_snapshot_pending",
            severity="watch",
            detail="Ledger and snapshot consistency is not fully available.",
            penalty=12,
        )
    if reconciliation in {"failed", "issues_present", "orphaned", "inconsistent"} or orphan_count > 0:
        score -= _signal(
            signals,
            component="data_integrity",
            signal="route_reconciliation_failed",
            severity="halt",
            detail="Current-route reconciliation found unresolved order-event issues.",
            penalty=80,
            hard_fault=True,
            metrics={"status": reconciliation, "orphan_order_event_count": orphan_count},
        )
    if sample_status == "sufficient" and coverage and coverage not in {"complete", "ledger_backed"}:
        score -= _signal(
            signals,
            component="data_integrity",
            signal="mark_to_market_coverage_incomplete",
            severity="watch",
            detail="Current-route evidence is large enough, but mark-to-market coverage is incomplete.",
            penalty=14,
            metrics={"coverage_status": coverage},
        )
    if _coerce_bool(settings_state.get("enabled"), False) and _coerce_bool(settings_state.get("armed"), False):
        age = evidence.get("last_success_age_seconds")
        interval = max(15, _coerce_int(evidence.get("cycle_interval_seconds"), 60))
        if age is not None and _coerce_float(age, 0.0) > max(interval * 8, 900):
            score -= _signal(
                signals,
                component="data_integrity",
                signal="automation_success_stale",
                severity="watch",
                detail="No successful automation cycle has completed within the freshness window.",
                penalty=18,
                metrics={"age_seconds": age, "cycle_interval_seconds": interval},
            )
    return max(0.0, min(100.0, score))


def _score_alpha_efficacy(evidence: dict[str, Any], signals: list[dict[str, Any]]) -> float:
    settings_state = dict(evidence.get("settings") or {})
    analytics = dict(evidence.get("analytics") or {})
    calibration = dict(evidence.get("calibration") or {})
    accuracy_calibration = dict(evidence.get("accuracy_calibration") or {})
    loss_containment = dict(evidence.get("loss_containment") or {})
    exit_watchdog = dict(evidence.get("exit_watchdog") or {})
    last_rejection = dict(evidence.get("last_rejection") or {})
    last_guardrail = dict(evidence.get("last_guardrail") or {})
    closed_count = _coerce_int(evidence.get("closed_trade_count"), 0)
    realized_pnl = _coerce_float(evidence.get("realized_pnl"), 0.0)
    loss_streak = _coerce_int(evidence.get("loss_streak"), 0)
    win_rate = _coerce_float(analytics.get("win_rate"), 0.0)
    profit_factor = _coerce_float(analytics.get("profit_factor"), 0.0)
    max_losses = max(1, _coerce_int(settings_state.get("max_consecutive_losses"), 3))
    score = 96.0 if closed_count == 0 and not last_rejection else 100.0
    if closed_count > 0 and realized_pnl < 0:
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal="negative_realized_pnl",
            severity="watch",
            detail=f"Same-day realized PnL is negative at {realized_pnl:.2f}.",
            penalty=min(35.0, 12.0 + abs(realized_pnl) / max(_coerce_float(evidence.get("equity_base"), 1.0), 1.0) * 2500.0),
        )
    if loss_streak >= max_losses:
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal="loss_streak_limit",
            severity="de_risk",
            detail=f"Loss streak reached {loss_streak}, at or above the configured {max_losses} limit.",
            penalty=38,
            metrics={"loss_streak": loss_streak, "max_consecutive_losses": max_losses},
        )
    elif loss_streak >= 2:
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal="loss_streak_warning",
            severity="watch",
            detail=f"Loss streak reached {loss_streak}.",
            penalty=18,
        )
    if closed_count >= 3 and win_rate < 0.4:
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal="low_win_rate",
            severity="watch",
            detail=f"Win rate is {win_rate * 100:.0f}% across {closed_count} same-day closes.",
            penalty=18,
        )
    if closed_count >= 4 and profit_factor and profit_factor < 0.8:
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal="weak_profit_factor",
            severity="watch",
            detail=f"Profit factor is weak at {profit_factor:.2f}.",
            penalty=14,
        )
    rejection_reason = str(last_rejection.get("reason") or "").strip().lower()
    guardrail_reason = str(last_guardrail.get("reason") or "").strip().lower()
    if rejection_reason in {"edge_cost_ratio_too_low", "missing_edge", "no_auto_entry_eligible"}:
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal=rejection_reason,
            severity="watch",
            detail=str(last_rejection.get("detail") or "Candidate quality was weak on the last cycle."),
            penalty=8 if rejection_reason == "no_auto_entry_eligible" else 12,
        )
    if guardrail_reason in {"daily_loss_lock", "max_consecutive_losses"}:
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal=guardrail_reason,
            severity="halt" if guardrail_reason == "daily_loss_lock" else "de_risk",
            detail=str(last_guardrail.get("detail") or "Capital guardrails blocked new risk."),
            penalty=55,
            hard_fault=guardrail_reason == "daily_loss_lock",
        )
    avg_error = calibration.get("average_error")
    if avg_error is not None and _coerce_float(avg_error, 0.0) > 0.30:
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal="forecast_calibration_drift",
            severity="watch",
            detail=f"Forecast calibration error is elevated at {_coerce_float(avg_error):.2f}.",
            penalty=14,
            metrics={"average_error": avg_error},
        )
    decision_pnl_accuracy = accuracy_calibration.get("decision_pnl_accuracy")
    confidence_error = accuracy_calibration.get("confidence_error")
    if decision_pnl_accuracy is not None and _coerce_float(decision_pnl_accuracy, 50.0) < 45.0:
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal="decision_pnl_accuracy_weak",
            severity="watch",
            detail=f"Decision-PnL accuracy is weak at {_coerce_float(decision_pnl_accuracy):.0f}.",
            penalty=18,
            metrics={"decision_pnl_accuracy": decision_pnl_accuracy},
        )
    if confidence_error is not None and _coerce_float(confidence_error, 0.0) > 0.45:
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal="decision_confidence_miscalibrated",
            severity="watch",
            detail=f"Decision confidence error is elevated at {_coerce_float(confidence_error):.2f}.",
            penalty=14,
            metrics={"confidence_error": confidence_error},
        )
    loss_status = str(loss_containment.get("status") or "").strip().lower()
    open_heat_pct = loss_containment.get("open_heat_pct")
    defensive_action_count = len(loss_containment.get("defensive_actions") or [])
    if loss_status in {"blocked", "action_required"} or bool(loss_containment.get("entries_blocked")):
        hard_fault = loss_status == "action_required" or defensive_action_count > 0
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal="loss_containment_breach",
            severity="halt" if hard_fault else "de_risk",
            detail="Loss containment found open-position heat, stale quote, or defensive-exit risk.",
            penalty=65 if hard_fault else 34,
            hard_fault=hard_fault,
            metrics={
                "status": loss_status,
                "open_heat_pct": open_heat_pct,
                "defensive_action_count": defensive_action_count,
            },
        )
    elif open_heat_pct is not None and _coerce_float(open_heat_pct, 0.0) > 0:
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal="open_heat_present",
            severity="watch",
            detail=f"Open heat is {_coerce_float(open_heat_pct):.2f}% of equity.",
            penalty=min(18.0, 4.0 + _coerce_float(open_heat_pct, 0.0) * 20.0),
            metrics={"open_heat_pct": open_heat_pct},
        )
    watchdog_status = str(exit_watchdog.get("status") or "").strip().lower()
    if watchdog_status in {"halt", "blocked"} or bool(exit_watchdog.get("entries_blocked")):
        hard_fault = watchdog_status == "halt" or _coerce_int(exit_watchdog.get("failed_exit_count"), 0) > 0
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal="exit_watchdog_unconfirmed_exit",
            severity="halt" if hard_fault else "de_risk",
            detail="Exit watchdog found missing, stuck, or failed defensive-exit confirmation.",
            penalty=75 if hard_fault else 42,
            hard_fault=hard_fault,
            metrics={
                "status": watchdog_status,
                "pending_exit_count": exit_watchdog.get("pending_exit_count"),
                "stuck_exit_count": exit_watchdog.get("stuck_exit_count"),
                "failed_exit_count": exit_watchdog.get("failed_exit_count"),
            },
        )
    elif watchdog_status == "watch":
        score -= _signal(
            signals,
            component="alpha_efficacy",
            signal="exit_watchdog_pending_exit",
            severity="watch",
            detail="Exit watchdog is waiting for defensive-exit confirmation.",
            penalty=12,
            metrics={
                "pending_exit_count": exit_watchdog.get("pending_exit_count"),
                "worst_delay_seconds": exit_watchdog.get("worst_delay_seconds"),
            },
        )
    return max(0.0, min(100.0, score))


def _score_execution_quality(evidence: dict[str, Any], signals: list[dict[str, Any]]) -> float:
    settings_state = dict(evidence.get("settings") or {})
    runtime = dict(evidence.get("runtime") or {})
    slippage = dict(evidence.get("slippage") or {})
    statuses = dict(evidence.get("event_status_counts") or {})
    event_keys = dict(evidence.get("event_key_counts") or {})
    avg_slippage = slippage.get("average_abs_bps")
    worst_slippage = slippage.get("worst_abs_bps")
    error_streak = _coerce_int(runtime.get("error_streak"), 0)
    max_error_streak = max(1, _coerce_int(settings_state.get("max_error_streak"), 3))
    score = 100.0
    if avg_slippage is not None and _coerce_float(avg_slippage) > 20.0:
        severe = _coerce_float(avg_slippage) >= max(60.0, _coerce_float(settings_state.get("market_slippage_bps"), 20.0) * 3.0)
        score -= _signal(
            signals,
            component="execution_quality",
            signal="average_slippage_drift",
            severity="halt" if severe else "de_risk",
            detail=f"Average absolute slippage is {_coerce_float(avg_slippage):.1f} bps.",
            penalty=70 if severe else 28,
            hard_fault=severe,
            metrics={"average_abs_bps": avg_slippage},
        )
    if worst_slippage is not None and _coerce_float(worst_slippage) > 40.0:
        severe = _coerce_float(worst_slippage) >= max(100.0, _coerce_float(settings_state.get("market_slippage_bps"), 20.0) * 5.0)
        score -= _signal(
            signals,
            component="execution_quality",
            signal="worst_slippage_spike",
            severity="halt" if severe else "watch",
            detail=f"Worst absolute slippage is {_coerce_float(worst_slippage):.1f} bps.",
            penalty=72 if severe else 22,
            hard_fault=severe,
            metrics={"worst_abs_bps": worst_slippage},
        )
    if error_streak >= max_error_streak:
        score -= _signal(
            signals,
            component="execution_quality",
            signal="worker_error_streak",
            severity="halt",
            detail=f"Worker error streak reached {error_streak}, at or above the configured {max_error_streak} limit.",
            penalty=85,
            hard_fault=True,
            metrics={"error_streak": error_streak, "max_error_streak": max_error_streak},
        )
    elif error_streak > 0:
        score -= _signal(
            signals,
            component="execution_quality",
            signal="worker_errors",
            severity="watch",
            detail=f"Worker error streak is {error_streak}.",
            penalty=min(30.0, 10.0 * error_streak),
        )
    reject_count = sum(_coerce_int(statuses.get(key), 0) for key in {"rejected", "failed", "error"})
    cancel_fail_count = sum(_coerce_int(event_keys.get(key), 0) for key in {"order.cancel_failed", "order.rejected", "order.failed"})
    if reject_count or cancel_fail_count:
        score -= _signal(
            signals,
            component="execution_quality",
            signal="order_event_errors",
            severity="watch",
            detail="Recent automation order events include rejects or execution errors.",
            penalty=min(35.0, 10.0 + 5.0 * (reject_count + cancel_fail_count)),
            metrics={"rejected_status_count": reject_count, "execution_error_count": cancel_fail_count},
        )
    return max(0.0, min(100.0, score))


def _score_market_state(evidence: dict[str, Any], signals: list[dict[str, Any]]) -> float:
    settings_state = dict(evidence.get("settings") or {})
    last_candidate = dict(evidence.get("last_candidate") or {})
    last_rejection = dict(evidence.get("last_rejection") or {})
    path_evaluations = [item for item in list(evidence.get("path_evaluations") or []) if isinstance(item, dict)]
    max_spread = _coerce_float(
        settings_state.get("max_spread_bps"),
        risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["max_spread_bps"],
    )
    min_adv = _coerce_float(
        settings_state.get("min_average_dollar_volume"),
        risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["min_average_dollar_volume"],
    )
    spread = None
    for key in ("spread_bps", "bid_ask_spread_bps", "quote_spread_bps", "live_spread_bps"):
        if last_candidate.get(key) is not None:
            spread = _coerce_float(last_candidate.get(key), 0.0)
            break
    adv = None
    for key in ("average_dollar_volume", "avg_dollar_volume", "average_daily_dollar_volume", "dollar_volume"):
        if last_candidate.get(key) is not None:
            adv = _coerce_float(last_candidate.get(key), 0.0)
            break
    score = 100.0
    if spread is not None and max_spread > 0 and spread > max_spread:
        score -= _signal(
            signals,
            component="market_state",
            signal="spread_above_limit",
            severity="de_risk",
            detail=f"Candidate spread is {spread:.1f} bps, above the {max_spread:.1f} bps limit.",
            penalty=28,
            metrics={"spread_bps": spread, "max_spread_bps": max_spread},
        )
    elif spread is not None and max_spread > 0 and spread > max_spread * 0.75:
        score -= _signal(
            signals,
            component="market_state",
            signal="spread_near_limit",
            severity="watch",
            detail=f"Candidate spread is nearing the configured limit at {spread:.1f} bps.",
            penalty=12,
            metrics={"spread_bps": spread, "max_spread_bps": max_spread},
        )
    if adv is not None and min_adv > 0 and adv < min_adv:
        score -= _signal(
            signals,
            component="market_state",
            signal="liquidity_below_floor",
            severity="watch",
            detail=f"Candidate average dollar volume is below the configured floor.",
            penalty=18,
            metrics={"average_dollar_volume": adv, "min_average_dollar_volume": min_adv},
        )
    rejection_reason = str(last_rejection.get("reason") or "").strip().lower()
    if rejection_reason in {"spread_too_wide", "liquidity_too_low", "missing_spread", "missing_average_dollar_volume"}:
        score -= _signal(
            signals,
            component="market_state",
            signal=rejection_reason,
            severity="watch",
            detail=str(last_rejection.get("detail") or "Market quality blocked the last candidate."),
            penalty=16,
        )
    weak_paths = [
        item
        for item in path_evaluations
        if str(item.get("status") or "").strip().lower() in {"blocked", "rejected"}
        and str(item.get("reason") or item.get("blocked_reason") or item.get("detail") or "").strip()
    ]
    if len(weak_paths) >= 2:
        score -= _signal(
            signals,
            component="market_state",
            signal="multi_path_market_weakness",
            severity="watch",
            detail="Multiple candidate paths were blocked or rejected in the current regime.",
            penalty=min(24.0, 8.0 + 4.0 * len(weak_paths)),
            metrics={"weak_path_count": len(weak_paths)},
        )
    return max(0.0, min(100.0, score))


def _score_components(evidence: dict[str, Any]) -> tuple[dict[str, float], list[dict[str, Any]], list[dict[str, Any]], float]:
    signals: list[dict[str, Any]] = []
    component_scores = {
        "data_integrity": _score_data_integrity(evidence, signals),
        "alpha_efficacy": _score_alpha_efficacy(evidence, signals),
        "execution_quality": _score_execution_quality(evidence, signals),
        "market_state": _score_market_state(evidence, signals),
    }
    score = (
        component_scores["data_integrity"] * 0.25
        + component_scores["alpha_efficacy"] * 0.25
        + component_scores["execution_quality"] * 0.30
        + component_scores["market_state"] * 0.20
    )
    hard_faults = [item for item in signals if bool(item.get("hard_fault"))]
    signals = sorted(signals, key=lambda item: (STATE_SEVERITY.get(str(item.get("severity") or ""), 0), item.get("penalty", 0)), reverse=True)
    return component_scores, signals[:STATE_CONTROL_SIGNAL_LIMIT], hard_faults, max(0.0, min(100.0, score))


def _candidate_state(score: float, settings_state: dict[str, Any], hard_faults: list[dict[str, Any]]) -> str:
    control_settings = normalize_state_control_settings(settings_state)
    if hard_faults:
        return "halt"
    halt_score = float(control_settings["state_control_halt_score"])
    derisk_score = float(control_settings["state_control_derisk_score"])
    watch_score = float(control_settings["state_control_watch_score"])
    if score < halt_score:
        return "halt"
    if score < derisk_score:
        return "de_risk"
    if score < watch_score:
        return "watch"
    return "healthy"


def _transition_state(
    *,
    runtime: dict[str, Any],
    candidate: str,
    hard_faults: list[dict[str, Any]],
    settings_state: dict[str, Any],
    now: datetime,
    signals: list[dict[str, Any]],
) -> tuple[str, int, dict[str, Any] | None]:
    last_evaluation = runtime.get("state_control_last_evaluation")
    if not isinstance(last_evaluation, dict):
        last_evaluation = {}
    previous = _normalize_state(runtime.get("state_control_state") or last_evaluation.get("state"))
    if _coerce_bool(runtime.get("state_control_halt_active"), previous == "halt"):
        previous = "halt"
    recovery_cycles = int(normalize_state_control_settings(settings_state)["state_control_recovery_cycles"])
    clean_cycles = _coerce_int(runtime.get("state_control_clean_cycle_count"), 0)
    previous_severity = STATE_SEVERITY[previous]
    candidate_severity = STATE_SEVERITY[candidate]

    if previous == "halt" and candidate != "halt":
        signals.insert(
            0,
            {
                "component": "policy",
                "signal": "manual_halt_recovery_required",
                "severity": "halt",
                "detail": "Previous halt state remains active until the operator manually clears the safety lock.",
                "penalty": 0.0,
                "hard_fault": False,
                "metrics": {},
            },
        )
        return "halt", 0, None

    if candidate_severity < previous_severity and previous in {"watch", "de_risk"} and not hard_faults:
        clean_cycles += 1
        if clean_cycles < recovery_cycles:
            signals.insert(
                0,
                {
                    "component": "policy",
                    "signal": "recovery_hold",
                    "severity": previous,
                    "detail": f"{previous.replace('_', ' ')} recovery needs {recovery_cycles} clean evaluation(s); {clean_cycles} recorded.",
                    "penalty": 0.0,
                    "hard_fault": False,
                    "metrics": {"clean_cycles": clean_cycles, "required_clean_cycles": recovery_cycles},
                },
            )
            return previous, clean_cycles, None
        clean_cycles = 0
    elif candidate_severity > previous_severity or hard_faults:
        clean_cycles = 0
    elif candidate == "healthy":
        clean_cycles = min(clean_cycles + 1, recovery_cycles)
    else:
        clean_cycles = 0

    if candidate != previous:
        transition = {
            "from": previous,
            "to": candidate,
            "at": _serialize_datetime(now),
            "reason": str(signals[0].get("signal") if signals else "score_change"),
            "detail": str(signals[0].get("detail") if signals else "State-control score crossed a threshold."),
        }
        return candidate, clean_cycles, transition
    return candidate, clean_cycles, None


def _has_execution_weakness(snapshot: dict[str, Any]) -> bool:
    for item in list(snapshot.get("triggered_signals") or []):
        component = str(item.get("component") or "").strip().lower()
        signal = str(item.get("signal") or "").strip().lower()
        if component in {"execution_quality", "market_state"}:
            if any(token in signal for token in ("slippage", "spread", "liquidity", "order_event")):
                return True
    return False


def _override(
    overrides: list[dict[str, Any]],
    settings_state: dict[str, Any],
    field: str,
    effective: Any,
    *,
    reason: str,
) -> None:
    before = settings_state.get(field)
    if before == effective:
        return
    overrides.append(
        {
            "field": field,
            "before": serialize_value(before),
            "effective": serialize_value(effective),
            "reason": reason,
            "kind": "runtime_overlay",
        }
    )


def build_policy_overrides(settings_state: dict[str, Any], control_snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    settings = dict(settings_state or {})
    snapshot = dict(control_snapshot or {})
    control_settings = normalize_state_control_settings(settings)
    if not control_settings["state_control_enabled"] or not control_settings["state_control_auto_throttle_enabled"]:
        return []
    state = _normalize_state(snapshot.get("state"))
    if state == "healthy":
        return []
    overrides: list[dict[str, Any]] = []
    multiplier = 0.75 if state == "watch" else 0.50 if state in {"de_risk", "halt"} else 1.0
    if state in {"watch", "de_risk"}:
        _override(
            overrides,
            settings,
            "risk_percent",
            round(_coerce_float(settings.get("risk_percent"), 0.5) * multiplier, 6),
            reason=f"{_state_label(state)} state applies a {multiplier:.2f} new-risk multiplier.",
        )
        _override(
            overrides,
            settings,
            "max_notional_per_trade",
            round(max(1.0, _coerce_float(settings.get("max_notional_per_trade"), 1.0) * multiplier), 2),
            reason=f"{_state_label(state)} state caps new ticket notional.",
        )
        _override(
            overrides,
            settings,
            "max_total_open_notional",
            round(max(1.0, _coerce_float(settings.get("max_total_open_notional"), 1.0) * multiplier), 2),
            reason=f"{_state_label(state)} state caps total automation exposure.",
        )
    if state == "watch" and _has_execution_weakness(snapshot):
        _override(
            overrides,
            settings,
            "order_type",
            "limit",
            reason="Watch state requires limit routing while execution or liquidity is the active weakness.",
        )
    if state == "de_risk":
        _override(
            overrides,
            settings,
            "cycle_entry_rank_limit",
            min(1, _coerce_int(settings.get("cycle_entry_rank_limit"), 2)),
            reason="De-risk state only allows the top ranked entry per cycle.",
        )
        _override(
            overrides,
            settings,
            "allow_pyramiding",
            False,
            reason="De-risk state disables pyramiding until recovery clears.",
        )
        _override(
            overrides,
            settings,
            "max_spread_bps",
            round(_coerce_float(settings.get("max_spread_bps"), 25.0) * 0.75, 4),
            reason="De-risk state tightens max spread by 25%.",
        )
        _override(
            overrides,
            settings,
            "order_type",
            "limit",
            reason="De-risk state requires limit routing for new entries.",
        )
    return overrides


def apply_state_control_overlay(settings_state: dict[str, Any], control_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    effective = dict(settings_state or {})
    for item in build_policy_overrides(effective, control_snapshot):
        field = str(item.get("field") or "").strip()
        if field:
            effective[field] = item.get("effective")
    return effective


def should_block_new_entries(control_snapshot: dict[str, Any] | None) -> bool:
    snapshot = dict(control_snapshot or {})
    if not _coerce_bool(snapshot.get("enabled"), True):
        return False
    return _normalize_state(snapshot.get("state")) == "halt" or _coerce_bool(snapshot.get("block_new_entries"), False)


def build_control_plane_snapshot(state: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or _utc_now()
    settings_state = dict(state.get("settings") or {})
    runtime = normalize_state_control_runtime(state.get("runtime"))
    control_settings = normalize_state_control_settings(settings_state)
    last_evaluation = dict(runtime.get("state_control_last_evaluation") or {})
    state_value = _normalize_state(last_evaluation.get("state") or runtime.get("state_control_state"))
    score = _clamp_float(last_evaluation.get("score"), runtime.get("state_control_score") or 100.0, minimum=0.0, maximum=100.0)
    snapshot = {
        "enabled": control_settings["state_control_enabled"],
        "auto_throttle_enabled": control_settings["state_control_auto_throttle_enabled"],
        "auto_halt_enabled": control_settings["state_control_auto_halt_enabled"],
        "thresholds": {
            "watch": control_settings["state_control_watch_score"],
            "de_risk": control_settings["state_control_derisk_score"],
            "halt": control_settings["state_control_halt_score"],
            "recovery_cycles": control_settings["state_control_recovery_cycles"],
        },
        "state": state_value,
        "label": _state_label(state_value),
        "score": score,
        "component_scores": serialize_value(last_evaluation.get("component_scores") or {}),
        "triggered_signals": serialize_value(list(last_evaluation.get("triggered_signals") or [])[:STATE_CONTROL_SIGNAL_LIMIT]),
        "active_overrides": [],
        "active_runtime_overrides": serialize_value(runtime.get("state_control_effective_overrides") or []),
        "last_transition": serialize_value(runtime.get("state_control_last_transition")),
        "transition_history": serialize_value(runtime.get("state_control_transition_history") or []),
        "evaluated_at": last_evaluation.get("evaluated_at") or runtime.get("state_control_last_review_at"),
        "related_note_id": last_evaluation.get("note_id") or runtime.get("state_control_last_note_id"),
        "session_day": last_evaluation.get("session_day") or _session_day_for(now),
        "clean_cycle_count": runtime["state_control_clean_cycle_count"],
        "manual_action_required": state_value == "halt" or runtime["state_control_halt_active"],
        "last_error": runtime.get("state_control_last_error"),
        "block_new_entries": state_value == "halt" and control_settings["state_control_enabled"],
    }
    snapshot["active_overrides"] = serialize_value(build_policy_overrides(settings_state, snapshot))
    return serialize_value(snapshot)


def _format_score_line(component_scores: dict[str, Any]) -> str:
    if not component_scores:
        return "- Data -- | Alpha -- | Execution -- | Market --"
    return (
        f"- Data {_coerce_float(component_scores.get('data_integrity'), 0.0):.0f} | "
        f"Alpha {_coerce_float(component_scores.get('alpha_efficacy'), 0.0):.0f} | "
        f"Execution {_coerce_float(component_scores.get('execution_quality'), 0.0):.0f} | "
        f"Market {_coerce_float(component_scores.get('market_state'), 0.0):.0f}"
    )


def _format_signal_line(signal: dict[str, Any]) -> str:
    component = str(signal.get("component") or "policy").replace("_", " ")
    name = str(signal.get("signal") or "signal").replace("_", " ")
    detail = str(signal.get("detail") or "").strip()
    severity = str(signal.get("severity") or "watch").replace("_", " ")
    return f"- {component}: {name} ({severity}). {detail}"


def _format_override_line(override: dict[str, Any]) -> str:
    field = str(override.get("field") or "").replace("_", " ")
    before = override.get("before")
    after = override.get("effective")
    reason = str(override.get("reason") or "Runtime overlay active.").strip()
    return f"- {field}: {before} -> {after}. {reason}"


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=STATE_CONTROL_NOTE_OWNER,
            limit=250,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {"automation-ai", "state-control", _profile_tag(profile_key), f"session-{session_day}"}
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _build_note_body(
    *,
    tenant: Tenant,
    profile_key: str,
    session_day: str,
    review: dict[str, Any],
) -> str:
    state_value = _normalize_state(review.get("state"))
    component_scores = dict(review.get("component_scores") or {})
    signals = [item for item in list(review.get("triggered_signals") or []) if isinstance(item, dict)]
    overrides = [item for item in list(review.get("active_overrides") or []) if isinstance(item, dict)]
    skipped = [item for item in list(review.get("skipped_actions") or []) if isinstance(item, dict)]
    transition = dict(review.get("last_transition") or {})
    lines = [
        f"Automation state-control review for {tenant.name} / {profile_key}",
        f"Session day: {session_day}",
        f"State: {_state_label(state_value)}",
        f"Score: {_coerce_float(review.get('score'), 0.0):.0f}/100",
        f"Manual action required: {'Yes' if bool(review.get('manual_action_required')) else 'No'}",
        "",
        "Component scores",
        _format_score_line(component_scores),
        "",
        "Triggered signals",
    ]
    lines.extend(_format_signal_line(item) for item in signals[:8])
    if not signals:
        lines.append("- No active weakness crossed a state-control threshold.")
    lines.extend(["", "Runtime setting overrides"])
    lines.extend(_format_override_line(item) for item in overrides[:10])
    if not overrides:
        lines.append("- None. Baseline settings remain effective.")
    lines.extend(["", "Skipped or blocked actions"])
    if skipped:
        lines.extend(f"- {str(item.get('reason') or 'skipped')}: {str(item.get('detail') or '').strip()}" for item in skipped[:8])
    else:
        lines.append("- None.")
    if transition:
        lines.extend(
            [
                "",
                "Last transition",
                f"- {str(transition.get('from') or '--').replace('_', ' ')} -> {str(transition.get('to') or '--').replace('_', ' ')} at {transition.get('at') or '--'}",
                f"- Reason: {transition.get('detail') or transition.get('reason') or 'State changed.'}",
            ]
        )
    return "\n".join(lines).strip()


def _sync_state_control_note(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    session_day: str,
    review: dict[str, Any],
) -> str | None:
    runtime = state.setdefault("runtime", {})
    note_id = ""
    if str(runtime.get("state_control_note_session_day") or "").strip() == session_day:
        note_id = str(runtime.get("state_control_last_note_id") or "").strip()
    if not note_id:
        note_id = _find_existing_note_id(profile_key, session_day) or ""
    title = f"Automation state control - {profile_key} - {session_day}"
    tags = ["automation-ai", "state-control", _profile_tag(profile_key), f"session-{session_day}"]
    body = _build_note_body(tenant=tenant, profile_key=profile_key, session_day=session_day, review=review)
    priority = "high" if _normalize_state(review.get("state")) in {"de_risk", "halt"} else "medium"
    if note_id:
        try:
            updated = notes_service.update_note(
                note_id,
                {
                    "title": title,
                    "body": body,
                    "tags": tags,
                    "owner": STATE_CONTROL_NOTE_OWNER,
                    "priority": priority,
                    "note_type": "risk_review",
                    "completed": False,
                },
            )
            runtime["state_control_last_note_id"] = updated.get("id") or note_id
            runtime["state_control_note_session_day"] = session_day
            return str(runtime["state_control_last_note_id"] or "").strip() or None
        except Exception:
            note_id = ""
    try:
        created = notes_service.create_note(
            title=title,
            body=body,
            tags=tags,
            owner=STATE_CONTROL_NOTE_OWNER,
            priority=priority,
            note_type="risk_review",
            completed=False,
        )
    except Exception:
        return None
    runtime["state_control_last_note_id"] = created.get("id")
    runtime["state_control_note_session_day"] = session_day
    return str(created.get("id") or "").strip() or None


def evaluate_trade_automation_state_control(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    forced: bool = False,
    actor: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    settings_state = state.setdefault("settings", {})
    runtime = state.setdefault("runtime", {})
    control_settings = normalize_state_control_settings(settings_state)
    if not control_settings["state_control_enabled"]:
        snapshot = build_control_plane_snapshot(state, now=now)
        runtime["state_control_last_review_at"] = _serialize_datetime(now)
        return {"status": "skipped", "reason": "state_control_disabled", **snapshot}

    session_day = _session_day_for(now)
    evidence = _collect_evidence(
        db,
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        session_day=session_day,
        now=now,
    )
    component_scores, triggered_signals, hard_faults, score = _score_components(evidence)
    candidate = _candidate_state(score, settings_state, hard_faults)
    active_state, clean_cycles, transition = _transition_state(
        runtime=runtime,
        candidate=candidate,
        hard_faults=hard_faults,
        settings_state=settings_state,
        now=now,
        signals=triggered_signals,
    )
    review: dict[str, Any] = {
        "status": "reviewed",
        "profile_key": profile_key,
        "linked_account_id": getattr(linked_account, "id", None),
        "session_day": session_day,
        "evaluated_at": _serialize_datetime(now),
        "state": active_state,
        "candidate_state": candidate,
        "score": round(score, 4),
        "component_scores": serialize_value(component_scores),
        "triggered_signals": serialize_value(triggered_signals[:STATE_CONTROL_SIGNAL_LIMIT]),
        "hard_faults": serialize_value(hard_faults),
        "evidence": serialize_value(evidence),
        "manual_action_required": active_state == "halt",
        "skipped_actions": [],
    }
    review["active_overrides"] = serialize_value(build_policy_overrides(settings_state, review))
    review["block_new_entries"] = active_state == "halt"
    if transition:
        review["last_transition"] = serialize_value(transition)

    halted = active_state == "halt"
    if halted:
        runtime["state_control_halt_active"] = True
        if control_settings["state_control_auto_halt_enabled"]:
            if not _coerce_bool(settings_state.get("kill_switch"), False):
                settings_state["kill_switch"] = True
                settings_state["armed"] = False
                review["auto_halt_applied"] = True
            else:
                review["auto_halt_applied"] = False
        else:
            review["skipped_actions"].append(
                {
                    "reason": "auto_halt_disabled",
                    "detail": "Halt state was detected, but automatic safety-locking is disabled.",
                }
            )
    else:
        runtime["state_control_halt_active"] = False

    note_id = _sync_state_control_note(
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        session_day=session_day,
        review=review,
    )
    if note_id:
        review["note_id"] = note_id

    runtime["state_control_state"] = active_state
    runtime["state_control_score"] = round(score, 4)
    runtime["state_control_clean_cycle_count"] = clean_cycles
    runtime["state_control_active_overrides"] = serialize_value(review.get("active_overrides") or [])
    runtime["state_control_last_evaluation"] = serialize_value(review)
    runtime["state_control_last_review_at"] = _serialize_datetime(now)
    runtime["state_control_last_error"] = None
    if transition:
        runtime["state_control_last_transition"] = serialize_value(transition)
        history = list(runtime.get("state_control_transition_history") or [])
        history.insert(0, serialize_value(transition))
        runtime["state_control_transition_history"] = history[:STATE_CONTROL_HISTORY_LIMIT]

    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.state_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
                "state": active_state,
                "score": review["score"],
                "component_scores": review["component_scores"],
                "note_id": note_id,
                "forced": bool(forced),
            },
        )
        if transition:
            record_audit_event(
                db,
                event_type="trade_automation.state_transitioned",
                tenant=tenant,
                user=actor,
                payload={
                    "profile_key": profile_key,
                    "linked_account_id": getattr(linked_account, "id", None),
                    "transition": serialize_value(transition),
                    "score": review["score"],
                    "note_id": note_id,
                },
            )
        if review.get("active_overrides"):
            record_audit_event(
                db,
                event_type="trade_automation.state_throttled",
                tenant=tenant,
                user=actor,
                payload={
                    "profile_key": profile_key,
                    "linked_account_id": getattr(linked_account, "id", None),
                    "state": active_state,
                    "overrides": serialize_value(review.get("active_overrides") or []),
                    "score": review["score"],
                },
            )
        if halted and review.get("auto_halt_applied"):
            record_audit_event(
                db,
                event_type="trade_automation.state_halted",
                tenant=tenant,
                user=actor,
                payload={
                    "profile_key": profile_key,
                    "linked_account_id": getattr(linked_account, "id", None),
                    "state": active_state,
                    "score": review["score"],
                    "hard_faults": serialize_value(hard_faults),
                    "note_id": note_id,
                },
            )
    return serialize_value(review)


def clear_state_control_halt(state: dict[str, Any], *, now: datetime | None = None) -> None:
    now = now or _utc_now()
    runtime = state.setdefault("runtime", {})
    previous = _normalize_state(runtime.get("state_control_state"))
    runtime["state_control_halt_active"] = False
    runtime["state_control_state"] = "watch" if previous == "halt" else previous
    runtime["state_control_clean_cycle_count"] = 0
    transition = {
        "from": previous,
        "to": runtime["state_control_state"],
        "at": _serialize_datetime(now),
        "reason": "manual_clear",
        "detail": "Operator manually cleared the safety lock; the next review may halt again if faults remain.",
    }
    runtime["state_control_last_transition"] = transition
    history = list(runtime.get("state_control_transition_history") or [])
    history.insert(0, serialize_value(transition))
    runtime["state_control_transition_history"] = history[:STATE_CONTROL_HISTORY_LIMIT]
    last_evaluation = dict(runtime.get("state_control_last_evaluation") or {})
    if last_evaluation:
        last_evaluation["state"] = runtime["state_control_state"]
        last_evaluation["manual_action_required"] = False
        last_evaluation["block_new_entries"] = False
        last_evaluation["last_transition"] = transition
        runtime["state_control_last_evaluation"] = serialize_value(last_evaluation)
