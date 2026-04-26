from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.models.saas import BrokerageLinkedAccount, Tenant
from backend.services import automation_state_control_service, notes_service, risk_control_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

SHADOW_NOTE_OWNER = "automation-ai"
SHADOW_SCENARIO_LIMIT = 12
SHADOW_REPORT_HISTORY_LIMIT = 8

SHADOW_SCENARIOS = (
    "healthy_baseline",
    "loss_drawdown_weakness",
    "slippage_spread_weakness",
    "hard_operational_fault",
    "recovery_hysteresis",
    "live_profile_safety",
)


def _serialize_datetime(value: datetime | None) -> str | None:
    return automation_state_control_service._serialize_datetime(value)


def _session_day_for(value: datetime | None = None) -> str:
    return automation_state_control_service._session_day_for(value)


def _profile_tag(profile_key: str) -> str:
    return automation_state_control_service._profile_tag(profile_key)


def _state_severity(state: Any) -> int:
    normalized = automation_state_control_service._normalize_state(state)
    return automation_state_control_service.STATE_SEVERITY.get(normalized, 0)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    return automation_state_control_service._coerce_float(value, default)


def _coerce_int(value: Any, default: int = 0) -> int:
    return automation_state_control_service._coerce_int(value, default)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    return automation_state_control_service._coerce_bool(value, default)


def normalize_shadow_validation_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("state_control_shadow_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("state_control_shadow_report_history") or [])[:SHADOW_REPORT_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "state_control_shadow_last_report": serialize_value(last_report),
        "state_control_shadow_last_note_id": str(runtime.get("state_control_shadow_last_note_id") or "").strip() or None,
        "state_control_shadow_note_session_day": str(runtime.get("state_control_shadow_note_session_day") or "").strip() or None,
        "state_control_shadow_last_run_at": _serialize_datetime(
            automation_state_control_service._parse_datetime(runtime.get("state_control_shadow_last_run_at"))
        ),
        "state_control_shadow_last_error": str(runtime.get("state_control_shadow_last_error") or "").strip() or None,
        "state_control_shadow_report_history": history,
    }


def build_shadow_validation_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = normalize_shadow_validation_runtime((state or {}).get("runtime"))
    report = dict(runtime.get("state_control_shadow_last_report") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "scenario_count": 0,
            "passed_count": 0,
            "failed_count": 0,
            "worst_state": "healthy",
            "expected_overlay_count": 0,
            "safety_lock_expected": False,
            "note_id": runtime.get("state_control_shadow_last_note_id"),
            "last_run_at": runtime.get("state_control_shadow_last_run_at"),
            "last_error": runtime.get("state_control_shadow_last_error"),
            "scenarios": [],
        }
    report.setdefault("note_id", runtime.get("state_control_shadow_last_note_id"))
    report.setdefault("last_run_at", runtime.get("state_control_shadow_last_run_at"))
    report.setdefault("last_error", runtime.get("state_control_shadow_last_error"))
    return serialize_value(report)


def _base_clean_evidence(
    *,
    base_evidence: dict[str, Any],
    scenario_state: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    settings_state = dict(scenario_state.get("settings") or {})
    runtime = dict(scenario_state.get("runtime") or {})
    max_spread = _coerce_float(
        settings_state.get("max_spread_bps"),
        risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["max_spread_bps"],
    )
    min_adv = _coerce_float(
        settings_state.get("min_average_dollar_volume"),
        risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["min_average_dollar_volume"],
    )
    clean_runtime = {
        **runtime,
        "last_cycle_at": _serialize_datetime(now),
        "last_success_at": _serialize_datetime(now),
        "last_error": None,
        "last_error_at": None,
        "error_streak": 0,
        "ledger_snapshot_consistency": "consistent",
        "current_route_reconciliation_status": "clean",
        "current_route_orphan_order_event_count": 0,
        "mark_to_market_coverage_status": "complete",
        "current_route_sample_status": "sufficient",
        "last_collection_blocker": None,
        "last_rejection": None,
        "last_guardrail": None,
        "last_path_evaluations": [],
    }
    scenario_state["runtime"] = clean_runtime
    return {
        **deepcopy(base_evidence),
        "settings": serialize_value(settings_state),
        "runtime": serialize_value(clean_runtime),
        "closed_trade_count": 0,
        "open_position_count": 0,
        "pending_order_count": 0,
        "realized_pnl": 0.0,
        "daily_loss_pct": 0.0,
        "analytics": {"win_rate": None, "profit_factor": None},
        "loss_streak": 0,
        "recent_order_events": [],
        "event_status_counts": {},
        "event_key_counts": {},
        "slippage": {"sample_count": 0, "average_abs_bps": None, "worst_abs_bps": None},
        "last_candidate": {
            "ticker": "SPY",
            "spread_bps": max(0.1, max_spread * 0.25),
            "average_dollar_volume": max(min_adv * 3.0, min_adv + 1.0, 1_000_000.0),
        },
        "last_rejection": {},
        "last_guardrail": {},
        "path_evaluations": [],
        "calibration": {"resolved_count": 0, "average_error": None},
        "last_cycle_age_seconds": 0.0,
        "last_success_age_seconds": 0.0,
        "cycle_interval_seconds": max(15, _coerce_int(settings_state.get("cycle_interval_seconds"), 60)),
        "current_collection_audit": {},
        "last_collection_audit": {},
    }


def _prepare_scenario(
    *,
    scenario_id: str,
    state: dict[str, Any],
    base_evidence: dict[str, Any],
    profile_key: str,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    scenario_state = deepcopy(state)
    settings_state = scenario_state.setdefault("settings", {})
    runtime = scenario_state.setdefault("runtime", {})
    settings_state.setdefault("state_control_enabled", True)
    settings_state.setdefault("state_control_auto_throttle_enabled", True)
    settings_state.setdefault("state_control_auto_halt_enabled", True)
    settings_state.setdefault("risk_percent", 0.5)
    settings_state.setdefault("max_notional_per_trade", 2500.0)
    settings_state.setdefault("max_total_open_notional", 5000.0)
    settings_state.setdefault("cycle_entry_rank_limit", 3)
    settings_state.setdefault("allow_pyramiding", True)
    settings_state.setdefault("order_type", "market")
    settings_state.setdefault("max_consecutive_losses", 3)
    settings_state.setdefault("max_error_streak", 3)

    if scenario_id == "healthy_baseline":
        evidence = deepcopy(base_evidence)
        evidence["settings"] = serialize_value(settings_state)
        evidence["runtime"] = serialize_value(runtime)
        return scenario_state, evidence, "Current telemetry replay should remain healthy with no runtime overlay."

    evidence = _base_clean_evidence(base_evidence=base_evidence, scenario_state=scenario_state, now=now)
    runtime = scenario_state.setdefault("runtime", {})
    settings_state = scenario_state.setdefault("settings", {})
    equity_base = max(_coerce_float(evidence.get("equity_base"), 10000.0), 1.0)
    max_losses = max(1, _coerce_int(settings_state.get("max_consecutive_losses"), 3))
    max_spread = _coerce_float(
        settings_state.get("max_spread_bps"),
        risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["max_spread_bps"],
    )
    min_adv = _coerce_float(
        settings_state.get("min_average_dollar_volume"),
        risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["min_average_dollar_volume"],
    )

    if scenario_id == "loss_drawdown_weakness":
        evidence.update(
            {
                "closed_trade_count": max(4, max_losses + 1),
                "realized_pnl": -equity_base * 0.03,
                "daily_loss_pct": 3.0,
                "analytics": {"win_rate": 0.25, "profit_factor": 0.55},
                "loss_streak": max_losses,
                "last_rejection": {
                    "reason": "no_auto_entry_eligible",
                    "detail": "Synthetic drawdown test found weak same-day candidate quality.",
                },
                "path_evaluations": [
                    {"status": "blocked", "reason": "edge_cost_ratio_too_low"},
                    {"status": "rejected", "reason": "no_auto_entry_eligible"},
                ],
                "calibration": {"resolved_count": 8, "average_error": 0.36},
            }
        )
        return scenario_state, evidence, "Loss streak and weak expectancy should throttle new risk."

    if scenario_id == "slippage_spread_weakness":
        runtime["mark_to_market_coverage_status"] = "partial"
        runtime["error_streak"] = 1
        evidence["runtime"] = serialize_value(runtime)
        evidence.update(
            {
                "event_status_counts": {"rejected": 4, "failed": 2},
                "event_key_counts": {"order.rejected": 2, "order.failed": 1},
                "slippage": {"sample_count": 6, "average_abs_bps": 55.0, "worst_abs_bps": 95.0},
                "last_candidate": {
                    "ticker": "SPY",
                    "spread_bps": max(max_spread * 1.35, max_spread + 5.0),
                    "average_dollar_volume": max(min_adv * 0.5, 1.0),
                },
                "last_rejection": {
                    "reason": "spread_too_wide",
                    "detail": "Synthetic execution stress widened spread and slippage.",
                },
                "path_evaluations": [
                    {"status": "blocked", "reason": "spread_too_wide"},
                    {"status": "blocked", "reason": "liquidity_too_low"},
                    {"status": "rejected", "reason": "missing_spread"},
                ],
            }
        )
        return scenario_state, evidence, "Execution drift should force limit routing and tighter entry constraints."

    if scenario_id == "hard_operational_fault":
        runtime["ledger_snapshot_consistency"] = "inconsistent"
        evidence["runtime"] = serialize_value(runtime)
        evidence["last_collection_audit"] = {"status": "failed", "reason": "ledger_snapshot_inconsistent"}
        return scenario_state, evidence, "Ledger inconsistency should halt in shadow without setting the real kill switch."

    if scenario_id == "recovery_hysteresis":
        runtime["state_control_state"] = "de_risk"
        runtime["state_control_halt_active"] = False
        runtime["state_control_clean_cycle_count"] = 0
        runtime["state_control_last_evaluation"] = {
            "state": "de_risk",
            "score": 52.0,
            "evaluated_at": _serialize_datetime(now),
        }
        evidence["runtime"] = serialize_value(runtime)
        return scenario_state, evidence, "Clean telemetry should stay throttled until the configured recovery count clears."

    if scenario_id == "live_profile_safety":
        settings_state["execution_intent"] = "broker_live"
        settings_state["enabled"] = False
        settings_state["armed"] = False
        settings_state["kill_switch"] = True
        runtime["ledger_snapshot_consistency"] = "inconsistent"
        evidence["settings"] = serialize_value(settings_state)
        evidence["runtime"] = serialize_value(runtime)
        return scenario_state, evidence, "Live shadow can recommend reduction or halt, but cannot enable, arm, bypass gates, or clear locks."

    return scenario_state, evidence, "Unknown scenario."


def _preview_control_review(
    *,
    state: dict[str, Any],
    evidence: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None,
    now: datetime,
) -> dict[str, Any]:
    scenario_state = deepcopy(state)
    settings_state = scenario_state.setdefault("settings", {})
    runtime = scenario_state.setdefault("runtime", {})
    control_settings = automation_state_control_service.normalize_state_control_settings(settings_state)
    component_scores, triggered_signals, hard_faults, score = automation_state_control_service._score_components(evidence)
    candidate = automation_state_control_service._candidate_state(score, settings_state, hard_faults)
    active_state, clean_cycles, transition = automation_state_control_service._transition_state(
        runtime=runtime,
        candidate=candidate,
        hard_faults=hard_faults,
        settings_state=settings_state,
        now=now,
        signals=triggered_signals,
    )
    review: dict[str, Any] = {
        "status": "shadow_reviewed",
        "profile_key": profile_key,
        "linked_account_id": getattr(linked_account, "id", None),
        "session_day": evidence.get("session_day") or _session_day_for(now),
        "evaluated_at": _serialize_datetime(now),
        "state": active_state,
        "candidate_state": candidate,
        "score": round(float(score), 4),
        "component_scores": serialize_value(component_scores),
        "triggered_signals": serialize_value(
            list(triggered_signals or [])[: automation_state_control_service.STATE_CONTROL_SIGNAL_LIMIT]
        ),
        "hard_faults": serialize_value(hard_faults),
        "manual_action_required": active_state == "halt",
        "block_new_entries": active_state == "halt",
        "clean_cycle_count": clean_cycles,
        "skipped_actions": [],
    }
    if transition:
        review["last_transition"] = serialize_value(transition)
    review["active_overrides"] = serialize_value(
        automation_state_control_service.build_policy_overrides(settings_state, review)
    )
    review["effective_settings"] = serialize_value(
        {
            str(item.get("field")): item.get("effective")
            for item in list(review.get("active_overrides") or [])
            if item.get("field")
        }
    )
    auto_halt_would_apply = (
        active_state == "halt"
        and bool(control_settings["state_control_auto_halt_enabled"])
        and not _coerce_bool(settings_state.get("kill_switch"), False)
    )
    review["auto_halt_would_apply"] = bool(auto_halt_would_apply)
    review["safety_lock_expected"] = active_state == "halt"
    if active_state == "halt":
        review["skipped_actions"].append(
            {
                "reason": "shadow_no_write",
                "detail": "Shadow validation would require manual review or safety locking, but it does not mutate kill switches.",
            }
        )
    return serialize_value(review)


def _scenario_passed(scenario_id: str, review: dict[str, Any]) -> tuple[bool, str]:
    state = automation_state_control_service._normalize_state(review.get("state"))
    overrides = list(review.get("active_overrides") or [])
    signals = {
        str(item.get("signal") or "").strip().lower()
        for item in list(review.get("triggered_signals") or [])
        if isinstance(item, dict)
    }
    fields = {str(item.get("field") or "").strip() for item in overrides if isinstance(item, dict)}
    if scenario_id == "healthy_baseline":
        if state == "healthy" and not overrides:
            return True, "Healthy baseline stayed unthrottled."
        return False, "Baseline telemetry was not healthy or produced runtime overlays."
    if scenario_id == "loss_drawdown_weakness":
        if state in {"watch", "de_risk"} and {"risk_percent", "max_notional_per_trade"} & fields:
            return True, "Loss weakness produced a risk-reducing overlay."
        return False, "Loss weakness did not produce the expected watch/de-risk risk throttle."
    if scenario_id == "slippage_spread_weakness":
        has_execution_signal = bool({"average_slippage_drift", "spread_above_limit"} & signals)
        has_routing_or_throttle = bool(
            "order_type" in fields
            or {"risk_percent", "max_notional_per_trade", "max_spread_bps", "cycle_entry_rank_limit"} & fields
        )
        if state in {"watch", "de_risk"} and has_execution_signal and has_routing_or_throttle:
            return True, "Execution weakness forced limit routing and throttled entries."
        return False, "Execution weakness did not force the expected routing and spread response."
    if scenario_id == "hard_operational_fault":
        if state == "halt" and review.get("safety_lock_expected"):
            return True, "Hard operational fault produced a shadow halt only."
        return False, "Hard operational fault did not produce a halt expectation."
    if scenario_id == "recovery_hysteresis":
        if state in {"watch", "de_risk"} and "recovery_hold" in signals:
            return True, "Recovery stayed throttled until enough clean cycles accrue."
        return False, "Recovery hysteresis did not hold the prior throttle state."
    if scenario_id == "live_profile_safety":
        forbidden = set(review.get("forbidden_actions_allowed") or [])
        if not forbidden:
            return True, "Live safety scenario did not permit gate bypass, enablement, arming, or lock clearing."
        return False, "Live safety scenario reported forbidden live-profile actions as allowed."
    return False, "Unknown shadow scenario."


def _compact_scenario(
    *,
    scenario_id: str,
    description: str,
    review: dict[str, Any],
    baseline_settings: dict[str, Any],
    baseline_runtime: dict[str, Any],
    profile_key: str,
) -> dict[str, Any]:
    passed, detail = _scenario_passed(scenario_id, review)
    forbidden_actions_allowed: list[str] = []
    if scenario_id == "live_profile_safety":
        if review.get("effective_settings", {}).get("enabled") is True:
            forbidden_actions_allowed.append("enable_live_trading")
        if review.get("effective_settings", {}).get("armed") is True:
            forbidden_actions_allowed.append("arm_live_trading")
        if review.get("effective_settings", {}).get("kill_switch") is False:
            forbidden_actions_allowed.append("clear_live_kill_switch")
        review["forbidden_actions_allowed"] = forbidden_actions_allowed
        passed, detail = _scenario_passed(scenario_id, review)
    after_settings = baseline_settings
    after_runtime = baseline_runtime
    mutated_forbidden_settings = any(
        after_settings.get(key) != baseline_settings.get(key)
        for key in ("enabled", "armed", "kill_switch", "execution_intent")
    )
    return serialize_value(
        {
            "id": scenario_id,
            "label": scenario_id.replace("_", " ").title(),
            "description": description,
            "status": "pass" if passed else "fail",
            "passed": bool(passed),
            "detail": detail,
            "profile_key": profile_key,
            "state": review.get("state"),
            "score": review.get("score"),
            "component_scores": review.get("component_scores") or {},
            "signals": list(review.get("triggered_signals") or [])[:6],
            "active_overrides": list(review.get("active_overrides") or [])[:8],
            "skipped_actions": list(review.get("skipped_actions") or [])[:6],
            "safety_lock_expected": bool(review.get("safety_lock_expected")),
            "auto_halt_would_apply": bool(review.get("auto_halt_would_apply")),
            "manual_action_required": bool(review.get("manual_action_required")),
            "forbidden_actions_allowed": forbidden_actions_allowed,
            "baseline_mutation_detected": bool(mutated_forbidden_settings),
        }
    )


def _find_existing_shadow_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=SHADOW_NOTE_OWNER,
            limit=250,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "state-control",
        "shadow-validation",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _format_override_line(item: dict[str, Any]) -> str:
    field = str(item.get("field") or "").replace("_", " ")
    before = item.get("before")
    effective = item.get("effective")
    reason = str(item.get("reason") or "Shadow runtime overlay would apply.").strip()
    return f"  - {field}: {before} -> {effective}. {reason}"


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    lines = [
        f"Tenant: {tenant.name or tenant.slug}",
        f"Profile: {profile_key}",
        f"Session: {report.get('session_day')}",
        f"Run: {report.get('evaluated_at')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Worst state: {str(report.get('worst_state') or 'healthy').replace('_', ' ')}",
        f"Scenarios: {report.get('passed_count', 0)} pass / {report.get('failed_count', 0)} fail / {report.get('scenario_count', 0)} total",
        f"Safety lock expected: {'yes' if report.get('safety_lock_expected') else 'no'}",
        "",
        "Shadow validation does not place orders, cancel orders, clear locks, enable automation, arm automation, or persist baseline setting changes.",
        "",
        "Scenarios",
    ]
    for scenario in list(report.get("scenarios") or [])[:SHADOW_SCENARIO_LIMIT]:
        lines.append(
            f"- {scenario.get('label')}: {str(scenario.get('status') or '').upper()} | "
            f"state {scenario.get('state')} | score {float(scenario.get('score') or 0.0):.0f}. {scenario.get('detail')}"
        )
        overrides = list(scenario.get("active_overrides") or [])
        if overrides:
            lines.append("  Expected setting/effective overrides:")
            for item in overrides[:6]:
                lines.append(_format_override_line(item))
        skipped = list(scenario.get("skipped_actions") or [])
        if skipped:
            lines.append("  Skipped/no-write actions:")
            for item in skipped[:4]:
                lines.append(f"  - {item.get('reason')}: {item.get('detail')}")
        signals = list(scenario.get("signals") or [])
        if signals:
            lines.append("  Trigger reasons:")
            for item in signals[:4]:
                lines.append(f"  - {item.get('component')}: {item.get('signal')}. {item.get('detail')}")
    return "\n".join(lines)


def _sync_shadow_note(*, tenant: Tenant, profile_key: str, session_day: str, report: dict[str, Any]) -> str | None:
    tags = ["automation-ai", "state-control", "shadow-validation", _profile_tag(profile_key), f"session-{session_day}"]
    title = f"Automation state-control shadow validation - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = str(report.get("note_id") or "").strip() or _find_existing_shadow_note_id(profile_key, session_day)
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": SHADOW_NOTE_OWNER,
        "note_type": "risk_review",
        "priority": "high" if report.get("failed_count") else "medium",
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


def run_state_control_shadow_validation(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or automation_state_control_service._utc_now()
    session_day = _session_day_for(now)
    original_settings = deepcopy(dict(state.get("settings") or {}))
    original_runtime = deepcopy(dict(state.get("runtime") or {}))
    base_state = deepcopy(state)
    base_evidence = automation_state_control_service._collect_evidence(
        db,
        tenant=tenant,
        state=base_state,
        profile_key=profile_key,
        session_day=session_day,
        now=now,
    )
    scenarios: list[dict[str, Any]] = []
    for scenario_id in SHADOW_SCENARIOS:
        scenario_state, evidence, description = _prepare_scenario(
            scenario_id=scenario_id,
            state=base_state,
            base_evidence=base_evidence,
            profile_key=profile_key,
            now=now,
        )
        review = _preview_control_review(
            state=scenario_state,
            evidence=evidence,
            profile_key=profile_key,
            linked_account=linked_account,
            now=now,
        )
        scenarios.append(
            _compact_scenario(
                scenario_id=scenario_id,
                description=description,
                review=review,
                baseline_settings=original_settings,
                baseline_runtime=original_runtime,
                profile_key=profile_key,
            )
        )

    failed = [item for item in scenarios if not item.get("passed")]
    worst_state = max(
        (str(item.get("state") or "healthy") for item in scenarios),
        key=_state_severity,
        default="healthy",
    )
    expected_overlay_count = sum(len(item.get("active_overrides") or []) for item in scenarios)
    safety_lock_expected = any(bool(item.get("safety_lock_expected")) for item in scenarios)
    report: dict[str, Any] = {
        "status": "pass" if not failed else "fail",
        "label": "Pass" if not failed else "Needs review",
        "profile_key": profile_key,
        "linked_account_id": getattr(linked_account, "id", None),
        "session_day": session_day,
        "evaluated_at": _serialize_datetime(now),
        "scenario_count": len(scenarios),
        "passed_count": len(scenarios) - len(failed),
        "failed_count": len(failed),
        "worst_state": worst_state,
        "expected_overlay_count": expected_overlay_count,
        "safety_lock_expected": safety_lock_expected,
        "scenarios": scenarios,
        "forbidden_live_actions": [],
        "baseline_settings_mutated": False,
        "baseline_runtime_mutated": False,
    }
    note_id = _sync_shadow_note(tenant=tenant, profile_key=profile_key, session_day=session_day, report=report)
    if note_id:
        report["note_id"] = note_id

    runtime = state.setdefault("runtime", {})
    summary = {
        "status": report["status"],
        "label": report["label"],
        "profile_key": profile_key,
        "session_day": session_day,
        "evaluated_at": report["evaluated_at"],
        "scenario_count": report["scenario_count"],
        "passed_count": report["passed_count"],
        "failed_count": report["failed_count"],
        "worst_state": report["worst_state"],
        "expected_overlay_count": report["expected_overlay_count"],
        "safety_lock_expected": report["safety_lock_expected"],
        "note_id": note_id,
        "scenarios": scenarios,
    }
    runtime["state_control_shadow_last_report"] = serialize_value(summary)
    runtime["state_control_shadow_last_note_id"] = note_id
    runtime["state_control_shadow_note_session_day"] = session_day
    runtime["state_control_shadow_last_run_at"] = report["evaluated_at"]
    runtime["state_control_shadow_last_error"] = None
    history = list(runtime.get("state_control_shadow_report_history") or [])
    history.insert(
        0,
        {
            "at": report["evaluated_at"],
            "status": report["status"],
            "scenario_count": report["scenario_count"],
            "failed_count": report["failed_count"],
            "worst_state": report["worst_state"],
            "note_id": note_id,
        },
    )
    runtime["state_control_shadow_report_history"] = serialize_value(history[:SHADOW_REPORT_HISTORY_LIMIT])
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.state_shadow_validated",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
                "status": report["status"],
                "scenario_count": report["scenario_count"],
                "failed_count": report["failed_count"],
                "worst_state": report["worst_state"],
                "note_id": note_id,
            },
        )
    return serialize_value(report)
