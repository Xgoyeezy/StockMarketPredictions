"""Deterministic readiness scoring for the Trade Automation surface."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


READY_STATUS = "ready"
WARNING_STATUS = "warning"
BLOCKED_STATUS = "blocked"


REQUIRED_MODULES: tuple[tuple[str, str], ...] = (
    ("ai_review", "Daily AI review"),
    ("control_plane", "State control"),
    ("state_control_shadow_validation", "State-control shadow validation"),
    ("paper_canary", "Paper canary"),
    ("paper_broker_reconciliation", "Paper broker reconciliation"),
    ("paper_order_lifecycle_soak", "Paper order lifecycle soak"),
    ("paper_order_lifecycle_canary", "Paper lifecycle canary"),
    ("daily_objective", "Daily objective"),
    ("accuracy_calibration", "Decision-PnL accuracy calibration"),
    ("paper_evidence_quality", "Paper evidence quality"),
    ("replay_lab", "Paper replay lab"),
    ("transaction_cost_calibration", "Transaction cost calibration"),
    ("loss_containment", "Loss containment"),
    ("exit_execution_watchdog", "Exit execution watchdog"),
    ("live_pilot_readiness", "Live pilot readiness"),
    ("live_pilot_soak", "Live pilot soak"),
    ("live_pilot_canary", "Live pilot canary"),
    ("live_pilot_expansion", "Live pilot expansion"),
    ("live_pilot_expansion_canary", "Live expansion canary"),
    ("live_pilot_window", "Supervised live pilot window"),
    ("live_pilot_window_canary", "Supervised window canary"),
    ("live_pilot_promotion_report", "Live pilot promotion report"),
    ("limited_live_rollout_gate", "Limited-live rollout gate"),
    ("limited_live_rollout_canary", "Limited-live rollout canary"),
    ("limited_live_cap_expansion_report", "Limited-live cap expansion report"),
    ("limited_live_cap_expansion_gate", "Limited-live cap expansion gate"),
    ("limited_live_cap_expansion_canary", "Limited-live cap expansion canary"),
    ("limited_live_next_tier_cap_report", "Next-tier cap report"),
    ("limited_live_next_tier_cap_gate", "Next-tier cap gate"),
    ("limited_live_next_tier_cap_canary", "Next-tier cap canary"),
    ("limited_live_broker_reconciliation", "Limited-live broker reconciliation"),
    ("limited_live_session_closeout", "Limited-live session closeout"),
    ("limited_live_cap_ladder", "Limited-live cap ladder"),
    ("limited_live_approval_ledger", "Limited-live approval ledger"),
    ("limited_live_higher_cap_report", "Higher-cap report"),
)


EXPECTED_ACTION_FLAGS: tuple[str, ...] = (
    "arm",
    "disarm",
    "kill_switch",
    "clear_kill_switch",
    "run_cycle",
    "reset_from_template",
    "run_ai_review",
    "run_state_control_review",
    "run_state_control_shadow_validation",
    "run_paper_canary_review",
    "run_paper_broker_reconciliation",
    "run_paper_order_lifecycle_soak",
    "run_paper_order_lifecycle_canary_review",
    "run_live_pilot_readiness_review",
    "prepare_live_pilot_soak",
    "run_live_pilot_soak",
    "run_live_pilot_canary_review",
    "prepare_live_pilot_expansion",
    "run_live_pilot_expansion",
    "run_live_pilot_expansion_canary_review",
    "prepare_live_pilot_window",
    "run_live_pilot_window_entry",
    "run_live_pilot_window_exit",
    "run_live_pilot_window_canary_review",
    "run_live_pilot_promotion_report",
    "prepare_limited_live_rollout",
    "activate_limited_live_rollout",
    "rollback_limited_live_rollout",
    "run_limited_live_rollout_canary_review",
    "run_limited_live_cap_expansion_report",
    "prepare_limited_live_cap_expansion",
    "activate_limited_live_cap_expansion",
    "rollback_limited_live_cap_expansion",
    "run_limited_live_cap_expansion_canary_review",
    "run_limited_live_next_tier_cap_report",
    "prepare_limited_live_next_tier_cap",
    "activate_limited_live_next_tier_cap",
    "rollback_limited_live_next_tier_cap",
    "run_limited_live_next_tier_cap_canary_review",
    "run_limited_live_broker_reconciliation",
    "run_limited_live_session_closeout",
    "run_limited_live_higher_cap_report",
    "submit_limited_live_operator_checklist",
    "run_daily_objective_review",
    "run_accuracy_calibration_review",
    "run_paper_evidence_review",
    "run_replay_lab_review",
    "run_transaction_cost_calibration_review",
    "run_loss_containment_review",
    "run_exit_watchdog_review",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    return [value]


def _status_rank(status: str | None) -> int:
    normalized = str(status or "").lower()
    if normalized in {"blocked", "error", "halt", "failed", "stale"}:
        return 2
    if normalized in {"warning", "warn", "needs_operator_review", "attention", "advisory"}:
        return 1
    return 0


def _merge_status(statuses: Iterable[str | None]) -> str:
    worst = max((_status_rank(status) for status in statuses), default=0)
    if worst >= 2:
        return BLOCKED_STATUS
    if worst == 1:
        return WARNING_STATUS
    return READY_STATUS


def _score_from_counts(total: int, failed: int, warned: int = 0) -> int:
    if total <= 0:
        return 0
    good = max(0, total - failed - warned)
    score = (good + warned * 0.5) / total
    return int(round(max(0.0, min(1.0, score)) * 100))


def _module_status(value: Any) -> str:
    if not isinstance(value, Mapping):
        return BLOCKED_STATUS
    return _merge_status(
        [
            str(value.get("status") or value.get("state") or value.get("readiness") or READY_STATUS),
        ]
    )


def _collect_module_coverage(snapshot: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    coverage: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    for key, label in REQUIRED_MODULES:
        value = snapshot.get(key)
        present = isinstance(value, Mapping)
        runtime_status = _module_status(value) if present else BLOCKED_STATUS
        status = READY_STATUS if present else BLOCKED_STATUS
        entry = {
            "key": key,
            "label": label,
            "present": present,
            "status": status,
            "runtime_status": runtime_status,
        }
        coverage.append(entry)
        if not present:
            blockers.append(f"{label} snapshot is missing.")
    return coverage, blockers, warnings


def _collect_action_schema(snapshot: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    actions = _as_mapping(snapshot.get("available_actions"))
    missing = []
    action_aliases = {
        "kill_switch": ("can_kill",),
        "clear_kill_switch": ("can_clear_kill",),
    }
    for flag in EXPECTED_ACTION_FLAGS:
        available_keys = {f"can_{flag}", *action_aliases.get(flag, ())}
        if flag in actions or any(key in actions for key in available_keys):
            continue
        missing.append(flag)
    return list(EXPECTED_ACTION_FLAGS), missing


def _deployment_status(deployment_readiness: Mapping[str, Any] | None) -> tuple[str, list[str], list[str]]:
    readiness = _as_mapping(deployment_readiness)
    if not readiness:
        return WARNING_STATUS, [], ["Deployment readiness snapshot was not available."]
    summary = _as_mapping(readiness.get("summary"))
    blockers = [str(item) for item in _as_list(readiness.get("blockers")) if item]
    blockers.extend(str(item) for item in _as_list(summary.get("blockers")) if item)
    warnings = [str(item) for item in _as_list(readiness.get("warnings")) if item]
    warnings.extend(str(item) for item in _as_list(summary.get("warnings")) if item)
    status = _merge_status([readiness.get("status") or readiness.get("overall_status") or summary.get("status")])
    if blockers:
        status = BLOCKED_STATUS
    elif warnings and status == READY_STATUS:
        status = WARNING_STATUS
    return status, blockers, warnings


def _route_status(route_health: Mapping[str, Any] | None) -> tuple[str, list[str], list[str]]:
    health = _as_mapping(route_health)
    if not health:
        return WARNING_STATUS, [], ["Trade Automation route health was not measured for this snapshot."]
    blockers = [str(item) for item in _as_list(health.get("blockers")) if item]
    warnings = [str(item) for item in _as_list(health.get("warnings")) if item]
    status = _merge_status([health.get("status")])
    if blockers:
        status = BLOCKED_STATUS
    elif warnings and status == READY_STATUS:
        status = WARNING_STATUS
    return status, blockers, warnings


def _hard_fault_status(snapshot: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    hard_faults = _as_mapping(snapshot.get("limited_live_hard_faults"))
    blockers = [str(item) for item in _as_list(hard_faults.get("blockers")) if item]
    warnings = [str(item) for item in _as_list(hard_faults.get("warnings")) if item]
    status = _merge_status([hard_faults.get("status") or READY_STATUS])

    control = _as_mapping(snapshot.get("control_plane"))
    control_state = str(control.get("state") or "").lower()
    if control_state == "halt":
        blockers.append("State control is halted.")
        status = BLOCKED_STATUS

    exit_watchdog = _as_mapping(snapshot.get("exit_execution_watchdog"))
    if exit_watchdog.get("entries_blocked") or int(exit_watchdog.get("stuck_exit_count") or 0) > 0:
        blockers.append("Exit execution watchdog has unconfirmed or stuck defensive exits.")
        status = BLOCKED_STATUS

    settings = _as_mapping(snapshot.get("settings"))
    if settings.get("kill_switch"):
        blockers.append("Profile kill switch is active.")
        status = BLOCKED_STATUS

    if blockers:
        status = BLOCKED_STATUS
    elif warnings and status == READY_STATUS:
        status = WARNING_STATUS
    return status, blockers, warnings


def _category(
    key: str,
    label: str,
    percent: int,
    status: str,
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    next_action: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    blockers = list(blockers or [])
    warnings = list(warnings or [])
    status = _merge_status([status])
    if blockers:
        status = BLOCKED_STATUS
    elif warnings and status == READY_STATUS:
        status = WARNING_STATUS
    return {
        "key": key,
        "label": label,
        "percent": int(max(0, min(100, percent))),
        "status": status,
        "blockers": blockers,
        "warnings": warnings,
        "next_action": next_action or ("No action required." if status == READY_STATUS else "Resolve listed blockers or warnings."),
        "details": dict(details or {}),
    }


def build_trade_automation_readiness_snapshot(
    snapshot: Mapping[str, Any],
    *,
    route_health: Mapping[str, Any] | None = None,
    deployment_readiness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the top-level readiness snapshot shown by Trade Automation.

    The snapshot is intentionally deterministic and read-only. It grades whether the
    automation system is verifiably wired, visible, and operationally checkable; it
    does not claim real-market proof or profit guarantees.
    """

    module_coverage, module_blockers, module_warnings = _collect_module_coverage(snapshot)
    expected_actions, missing_actions = _collect_action_schema(snapshot)
    route_status, route_blockers, route_warnings = _route_status(route_health)
    deploy_status, deploy_blockers, deploy_warnings = _deployment_status(deployment_readiness)
    hard_fault_status, hard_fault_blockers, hard_fault_warnings = _hard_fault_status(snapshot)

    backend_total = len(module_coverage) + len(expected_actions) + 1
    backend_failed = sum(1 for item in module_coverage if item["status"] == BLOCKED_STATUS)
    backend_warned = sum(1 for item in module_coverage if item["status"] == WARNING_STATUS)
    backend_failed += len(missing_actions)
    if route_status == BLOCKED_STATUS:
        backend_failed += 1
    elif route_status == WARNING_STATUS:
        backend_warned += 1
    backend_blockers = module_blockers + [f"Trade Automation action is unavailable: {flag}" for flag in missing_actions] + route_blockers
    backend_warnings = module_warnings + route_warnings

    safety_checks = [
        hard_fault_status,
        _module_status(snapshot.get("control_plane")),
        _module_status(snapshot.get("loss_containment")),
        _module_status(snapshot.get("exit_execution_watchdog")),
        _module_status(snapshot.get("limited_live_cap_ladder")),
        _module_status(snapshot.get("limited_live_approval_ledger")),
    ]
    safety_source_fields = (
        "control_plane",
        "loss_containment",
        "exit_execution_watchdog",
        "limited_live_cap_ladder",
        "limited_live_approval_ledger",
        "limited_live_hard_faults",
    )
    missing_safety_sources = [key for key in safety_source_fields if not isinstance(snapshot.get(key), Mapping)]

    smoke_status = _as_mapping(_as_mapping(deployment_readiness).get("trade_automation_route_status"))
    smoke_state = _merge_status([smoke_status.get("status")]) if smoke_status else WARNING_STATUS
    test_statuses = [smoke_state]
    tests_failed = sum(1 for status in test_statuses if status == BLOCKED_STATUS)
    tests_warned = sum(1 for status in test_statuses if status == WARNING_STATUS)
    tests_blockers = []
    tests_warnings = []
    if not smoke_status:
        tests_warnings.append("Readiness smoke status has not been recorded yet.")
    else:
        tests_blockers.extend(str(item) for item in _as_list(smoke_status.get("blockers")) if item)
        tests_warnings.extend(str(item) for item in _as_list(smoke_status.get("warnings")) if item)
        if smoke_state == BLOCKED_STATUS and not tests_blockers:
            tests_blockers.append("Readiness smoke status is blocked.")
        elif smoke_state == WARNING_STATUS and not tests_warnings:
            tests_warnings.append("Readiness smoke status has warnings.")

    frontend_fields = [
        snapshot.get("available_actions"),
        snapshot.get("limited_live_cap_ladder"),
        snapshot.get("limited_live_approval_ledger"),
        snapshot.get("exit_execution_watchdog"),
        snapshot.get("loss_containment"),
    ]
    frontend_missing = sum(1 for value in frontend_fields if not isinstance(value, Mapping))
    frontend_warnings = []
    if route_status != READY_STATUS:
        frontend_warnings.extend(route_warnings or ["Trade Automation route health is not clean."])
    frontend_blockers = []
    if frontend_missing:
        frontend_blockers.append("Trade Automation UI is missing readiness source fields.")

    operational_statuses = [deploy_status, route_status]
    operational_failed = sum(1 for status in operational_statuses if status == BLOCKED_STATUS)
    operational_warned = sum(1 for status in operational_statuses if status == WARNING_STATUS)

    categories = [
        _category(
            "backend_feature_coverage",
            "Backend Feature Coverage",
            _score_from_counts(backend_total, backend_failed, backend_warned),
            _merge_status([*([BLOCKED_STATUS] if backend_blockers else []), *([WARNING_STATUS] if backend_warnings else []), READY_STATUS]),
            backend_blockers,
            backend_warnings,
            "Expose every module and action in the Trade Automation snapshot.",
            {
                "module_count": len(module_coverage),
                "expected_action_count": len(expected_actions),
                "missing_action_count": len(missing_actions),
            },
        ),
        _category(
            "safety_risk_ladder",
            "Safety/Risk Ladder",
            100 if not missing_safety_sources else _score_from_counts(len(safety_source_fields), len(missing_safety_sources), 0),
            _merge_status(safety_checks),
            hard_fault_blockers + [f"{key} source is missing." for key in missing_safety_sources],
            hard_fault_warnings,
            "Clear hard faults and keep runtime allowances disabled until a fresh approval cycle.",
            {"check_count": len(safety_checks)},
        ),
        _category(
            "tests_build_coverage",
            "Tests/Build Coverage",
            _score_from_counts(max(1, len(test_statuses)), tests_failed, tests_warned),
            smoke_state,
            tests_blockers,
            tests_warnings,
            "Run scripts/smoke-trade-automation-readiness.ps1 and resolve failures.",
            {"smoke_status": smoke_status},
        ),
        _category(
            "frontend_visibility",
            "Frontend Visibility",
            _score_from_counts(len(frontend_fields) + 1, frontend_missing, 1 if route_status == WARNING_STATUS else 0),
            BLOCKED_STATUS if frontend_missing else (WARNING_STATUS if route_status != READY_STATUS else READY_STATUS),
            frontend_blockers,
            frontend_warnings,
            "Use the Trade Automation readiness panel and visible failure states.",
            {"source_field_count": len(frontend_fields)},
        ),
        _category(
            "operational_readiness",
            "Operational Readiness",
            _score_from_counts(len(operational_statuses), operational_failed, operational_warned),
            _merge_status(operational_statuses),
            deploy_blockers + route_blockers,
            deploy_warnings + route_warnings,
            "Make /api/healthz, /api/readyz, backup, backlog, and snapshot latency green.",
            {"deployment_status": _as_mapping(deployment_readiness), "route_health": _as_mapping(route_health)},
        ),
    ]

    overall_percent = int(round(sum(item["percent"] for item in categories) / len(categories))) if categories else 0
    overall_status = _merge_status(item["status"] for item in categories)
    blockers: list[str] = []
    warnings: list[str] = []
    for item in categories:
        blockers.extend(item["blockers"])
        warnings.extend(item["warnings"])

    if not blockers and not warnings:
        next_action = "System is ready for evidence collection; real paper/live proof still requires market sessions."
    elif blockers:
        next_action = "Resolve blockers, rerun the readiness smoke script, then refresh Trade Automation."
    else:
        next_action = "Review warnings and rerun the readiness smoke script after fixes."

    return {
        "status": overall_status,
        "overall_percent": overall_percent,
        "categories": categories,
        "module_coverage": module_coverage,
        "action_schema": {
            "expected_actions": expected_actions,
            "missing_actions": missing_actions,
            "status": BLOCKED_STATUS if missing_actions else READY_STATUS,
        },
        "hard_faults": _as_mapping(snapshot.get("limited_live_hard_faults")),
        "route_health": _as_mapping(route_health),
        "blockers": blockers,
        "warnings": warnings,
        "next_action": next_action,
        "real_market_evidence_status": "system_ready_for_evidence_collection"
        if overall_status == READY_STATUS
        else "system_not_ready_for_evidence_collection",
        "evaluated_at": _now_iso(),
    }
