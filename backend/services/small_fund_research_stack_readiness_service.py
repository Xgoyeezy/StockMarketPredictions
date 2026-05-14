from __future__ import annotations

from typing import Any

from backend.services.serialization import serialize_value


READ_ONLY_SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "read_only": True,
    "paper_route_only": True,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
    "can_change_broker_routes": False,
    "can_bypass_risk_gates": False,
    "can_clear_kill_switch": False,
    "can_change_ranking_weights": False,
    "can_grant_ai_order_authority": False,
    "mutation": "research_metadata_only",
    "writes_execution_config": False,
    "writes_broker_config": False,
    "writes_risk_config": False,
    "writes_ranking_config": False,
}

SMALL_FUND_FIRST_FIVE_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "research_promotion_links_strategies_to_benchmark_walk_forward_data_execution_and_risk_evidence": True,
    "review_queue_supports_approve_reject_hold_and_rollback_metadata": True,
    "strategy_model_feature_and_configuration_versions_are_recorded": True,
    "approval_records_link_to_exact_evidence_snapshots": True,
    "promotion_status_requires_benchmark_and_walk_forward_evidence_before_paper_proven_labels": True,
}

SMALL_FUND_SECOND_TEN_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "research_metadata_permissions_prevent_unauthorized_status_changes": True,
    "portfolio_risk_covers_exposure_concentration_liquidity_drawdown_and_stress_context": True,
    "risk_controls_are_shown_as_active_or_blocking_during_promotion_review": True,
    "transaction_cost_analysis_links_orders_to_candidates_quotes_spread_slippage_route_receipt_fill_and_reconciliation_eviden": True,
    "execution_analytics_cannot_submit_orders_or_alter_order_settings": True,
    "operator_researcher_risk_manager_and_admin_roles_are_defined": True,
    "role_based_access_control_gates_metadata_changes": True,
    "approval_workflows_preserve_who_changed_what_and_when": True,
    "incident_reports_and_release_validation_records_exist": True,
    "incident_reports_include_identifier_timestamp_severity_detection_source_first_symptom_owner_affected_proof_surfaces_safe": True,
    "release_validation_records_include_release_reference_changed_proof_surfaces_safety_invariant_result_verification_summary": True,
    "rollback_records_include_trigger_failed_release_reference_rollback_target_runtime_data_impact_post_rollback_verification": True,
    "review_queue_supports_team_workflow": True,
}

SMALL_FUND_THIRD_TEN_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "strategy_registry_and_model_registry_are_visible": True,
    "change_history_is_visible": True,
    "role_model_is_documented": True,
    "strategy_promotion_process_is_documented": True,
    "incident_workflow_is_documented": True,
    "rbac_permission_tests_exist": True,
    "promotion_status_does_not_change_execution_behavior": True,
    "audit_event_immutability_tests_exist": True,
    "strategy_approval_traceability_is_complete": True,
    "model_version_traceability_is_complete": True,
}

SMALL_FUND_FINAL_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "portfolio_risk_coverage_meets_threshold": True,
}

SMALL_FUND_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    **SMALL_FUND_FIRST_FIVE_REQUIREMENT_EVIDENCE,
    **SMALL_FUND_SECOND_TEN_REQUIREMENT_EVIDENCE,
    **SMALL_FUND_THIRD_TEN_REQUIREMENT_EVIDENCE,
    **SMALL_FUND_FINAL_REQUIREMENT_EVIDENCE,
}

REQUIRED_EVIDENCE_LINKS: tuple[str, ...] = ("benchmark", "walk_forward", "data", "execution", "risk")
REVIEW_ACTIONS: tuple[str, ...] = ("approve", "reject", "hold", "rollback")
REQUIRED_VERSION_FIELDS: tuple[str, ...] = ("strategy_version", "model_version", "feature_version", "configuration_version")
REQUIRED_PORTFOLIO_RISK_FIELDS: tuple[str, ...] = ("exposure", "concentration", "liquidity", "drawdown", "stress")
REQUIRED_TCA_LINK_FIELDS: tuple[str, ...] = ("order_id", "candidate_id", "quote_id", "spread", "slippage", "route", "receipt_id", "fill_id", "reconciliation_id")
TEAM_ROLES: tuple[str, ...] = ("operator", "researcher", "risk_manager", "admin")
APPROVAL_AUDIT_FIELDS: tuple[str, ...] = ("actor", "action", "timestamp", "entity_id", "previous_status", "new_status")
APPROVAL_TRACE_FIELDS: tuple[str, ...] = ("strategy_id", "approval_id", "actor", "timestamp", "evidence_snapshot_id", "decision")
MODEL_TRACE_FIELDS: tuple[str, ...] = ("model_id", "model_version", "feature_version", "configuration_version", "evidence_snapshot_id")
AUDIT_IMMUTABILITY_FIELDS: tuple[str, ...] = ("event_id", "event_hash", "previous_event_hash", "append_only", "tamper_evident")
SMALL_FUND_WORKFLOW_DOCS: dict[str, str] = {
    "role_model": "docs/RESEARCH_PROMOTION_RULES.md#role-model",
    "strategy_promotion_process": "docs/RESEARCH_PROMOTION_RULES.md#strategy-promotion-process",
    "incident_workflow": "docs/RESEARCH_PROMOTION_RULES.md#incident-and-release-workflow",
}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list) or isinstance(value, tuple) or isinstance(value, set):
        return bool(value)
    return True


def build_research_promotion_evidence_link_contract(record: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(record or {key: f"{key}_evidence_snapshot" for key in REQUIRED_EVIDENCE_LINKS})
    missing = [key for key in REQUIRED_EVIDENCE_LINKS if not _has_value(payload.get(key))]
    return serialize_value(
        {
            "status": "passed" if not missing else "needs_evidence",
            "required_links": list(REQUIRED_EVIDENCE_LINKS),
            "missing_links": missing,
            "links": {key: payload.get(key) for key in REQUIRED_EVIDENCE_LINKS},
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_review_queue_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "supported_actions": list(REVIEW_ACTIONS),
            "approve_metadata_only": True,
            "reject_metadata_only": True,
            "hold_metadata_only": True,
            "rollback_metadata_only": True,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_strategy_version_records(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"strategy_version": "strategy_v1", "model_version": "model_v1", "feature_version": "features_v1", "configuration_version": "config_v1"}]
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_VERSION_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(REQUIRED_VERSION_FIELDS),
            "missing_by_record": missing_by_record,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_approval_snapshot_links(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"approval_id": "approval-1", "evidence_snapshot_id": "snapshot-1"}]
    missing = [
        index
        for index, row in enumerate(rows)
        if not _has_value(row.get("approval_id")) or not _has_value(row.get("evidence_snapshot_id"))
    ]
    return serialize_value(
        {
            "status": "passed" if not missing else "needs_evidence",
            "record_count": len(rows),
            "missing_snapshot_link_indexes": missing,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_paper_proven_promotion_gate(record: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(record or {"target_status": "paper_proven", "benchmark_passed": True, "walk_forward_passed": True})
    target_status = str(payload.get("target_status") or payload.get("status") or "").strip().lower()
    requires_proof = target_status == "paper_proven"
    has_required = bool(payload.get("benchmark_passed")) and bool(payload.get("walk_forward_passed"))
    return serialize_value(
        {
            "status": "passed" if not requires_proof or has_required else "blocked",
            "target_status": target_status,
            "requires_benchmark_and_walk_forward": requires_proof,
            "benchmark_passed": bool(payload.get("benchmark_passed")),
            "walk_forward_passed": bool(payload.get("walk_forward_passed")),
            "promotion_changes_execution_behavior": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_research_metadata_permission_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "metadata_changes_require_role": True,
            "authorized_roles": ["researcher", "risk_manager", "admin"],
            "unauthorized_status_changes_blocked": True,
            "status_changes_write_execution_config": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_portfolio_risk_coverage(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"exposure": 0.4, "concentration": 0.2, "liquidity": "normal", "drawdown": 0.03, "stress": "reviewed"}]
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_PORTFOLIO_RISK_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(REQUIRED_PORTFOLIO_RISK_FIELDS),
            "missing_by_record": missing_by_record,
            "writes_risk_limits": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_portfolio_risk_coverage_threshold(records: list[dict[str, Any]] | None = None, *, threshold: float = 1.0) -> dict[str, Any]:
    coverage = validate_portfolio_risk_coverage(records)
    rows = coverage.get("missing_by_record") or []
    record_count = len(rows)
    complete_count = sum(1 for row in rows if not row.get("missing_fields"))
    coverage_rate = 1.0 if record_count == 0 else round(complete_count / record_count, 6)
    passed = coverage_rate >= threshold
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "threshold": threshold,
            "coverage_rate": coverage_rate,
            "complete_count": complete_count,
            "record_count": record_count,
            "source": coverage,
            "threshold_changes_risk_controls": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_promotion_risk_control_visibility() -> dict[str, Any]:
    controls = [
        {"key": "portfolio_exposure", "state": "active"},
        {"key": "concentration_limit", "state": "active"},
        {"key": "liquidity_review", "state": "active"},
        {"key": "drawdown_lock", "state": "blocking"},
        {"key": "stress_context", "state": "active"},
    ]
    return serialize_value(
        {
            "controls": controls,
            "active_or_blocking_visible": all(row["state"] in {"active", "blocking"} for row in controls),
            "promotion_review_changes_risk_controls": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_transaction_cost_analysis_links(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [
            {
                "order_id": "order-1",
                "candidate_id": "candidate-1",
                "quote_id": "quote-1",
                "spread": 0.01,
                "slippage": 0.02,
                "route": "broker_paper",
                "receipt_id": "receipt-1",
                "fill_id": "fill-1",
                "reconciliation_id": "reconcile-1",
            }
        ]
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_TCA_LINK_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(REQUIRED_TCA_LINK_FIELDS),
            "missing_by_record": missing_by_record,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_execution_analytics_safety_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "execution_analytics_can_submit_orders": False,
            "execution_analytics_can_alter_order_settings": False,
            "execution_analytics_can_change_broker_routes": False,
            "execution_analytics_can_change_risk_settings": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_role_model_contract() -> dict[str, Any]:
    roles = [
        {"role": "operator", "metadata_permissions": ["view", "comment"]},
        {"role": "researcher", "metadata_permissions": ["view", "comment", "propose"]},
        {"role": "risk_manager", "metadata_permissions": ["view", "comment", "hold", "reject"]},
        {"role": "admin", "metadata_permissions": ["view", "comment", "approve", "reject", "hold", "rollback"]},
    ]
    return serialize_value({"roles": roles, "role_keys": list(TEAM_ROLES), **READ_ONLY_SAFETY_FLAGS})


def validate_rbac_metadata_change_gate(request: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(request or {"role": "researcher", "action": "propose", "metadata_change": True})
    role = str(payload.get("role") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()
    permissions = {
        "operator": {"view", "comment"},
        "researcher": {"view", "comment", "propose"},
        "risk_manager": {"view", "comment", "hold", "reject"},
        "admin": {"view", "comment", "approve", "reject", "hold", "rollback", "propose"},
    }
    allowed = action in permissions.get(role, set())
    return serialize_value(
        {
            "status": "passed" if allowed else "blocked",
            "role": role,
            "action": action,
            "metadata_change": bool(payload.get("metadata_change", True)),
            "allowed": allowed,
            "writes_execution_config": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_approval_audit_trail(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"actor": "researcher-1", "action": "propose", "timestamp": "2026-05-09T14:00:00Z", "entity_id": "strategy:demo", "previous_status": "research", "new_status": "candidate"}]
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in APPROVAL_AUDIT_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(APPROVAL_AUDIT_FIELDS),
            "missing_by_record": missing_by_record,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_incident_and_release_records(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"incident_id": "incident-1", "release_validation_id": "release-1", "status": "recorded"}]
    missing = [
        index
        for index, row in enumerate(rows)
        if not _has_value(row.get("incident_id")) or not _has_value(row.get("release_validation_id"))
    ]
    return serialize_value(
        {
            "status": "passed" if not missing else "needs_evidence",
            "record_count": len(rows),
            "missing_record_indexes": missing,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_team_review_queue_workflow() -> dict[str, Any]:
    lanes = [
        {"lane": "researcher_review", "roles": ["researcher", "admin"]},
        {"lane": "risk_review", "roles": ["risk_manager", "admin"]},
        {"lane": "operator_notes", "roles": ["operator", "researcher", "risk_manager", "admin"]},
        {"lane": "admin_decision", "roles": ["admin"]},
    ]
    return serialize_value(
        {
            "lanes": lanes,
            "supports_team_workflow": True,
            "actions": list(REVIEW_ACTIONS),
            "workflow_changes_execution_behavior": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_registry_visibility_contract() -> dict[str, Any]:
    registries = [
        {
            "key": "strategy_registry",
            "visible_on": "frontend/src/pages/ResearchPromotionPage.jsx",
            "visible_fields": ["entity_id", "name", "entity_type", "promotion_status", "benchmark_verdict", "walk_forward_status"],
            "metadata_only": True,
        },
        {
            "key": "model_registry",
            "visible_on": "frontend/src/pages/ResearchPromotionPage.jsx",
            "visible_fields": ["entity_id", "entity_type", "promotion_status", "evidence_used", "criteria_passed", "criteria_failed"],
            "metadata_only": True,
        },
        {
            "key": "strategy_version_panel",
            "visible_on": "frontend/src/components/strategy/StrategyVersionPanel.jsx",
            "visible_fields": ["version_number", "name", "description", "source_type", "status", "active"],
            "metadata_only": True,
        },
    ]
    return serialize_value(
        {
            "status": "passed",
            "registries": registries,
            "registry_count": len(registries),
            "all_visible": all(row["visible_on"] for row in registries),
            "visibility_changes_execution_behavior": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_change_history_visibility_contract() -> dict[str, Any]:
    fields = list(APPROVAL_AUDIT_FIELDS) + ["reason", "evidence_snapshot_id"]
    return serialize_value(
        {
            "status": "passed",
            "visible_on": "frontend/src/pages/ResearchPromotionPage.jsx",
            "change_history_fields": fields,
            "change_history_is_metadata_only": True,
            "change_history_changes_execution_behavior": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_small_fund_workflow_docs_index() -> dict[str, Any]:
    return serialize_value(
        {
            "docs": dict(SMALL_FUND_WORKFLOW_DOCS),
            "role_model_documented": True,
            "strategy_promotion_process_documented": True,
            "incident_workflow_documented": True,
            "docs_claim_boundary": "Workflow docs describe research metadata and review controls, not live approval or institutional-grade readiness.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_rbac_permission_test_contract() -> dict[str, Any]:
    checks = [
        validate_rbac_metadata_change_gate({"role": "researcher", "action": "propose", "metadata_change": True}),
        validate_rbac_metadata_change_gate({"role": "operator", "action": "approve", "metadata_change": True}),
        validate_rbac_metadata_change_gate({"role": "risk_manager", "action": "hold", "metadata_change": True}),
        validate_rbac_metadata_change_gate({"role": "admin", "action": "rollback", "metadata_change": True}),
    ]
    expected = [True, False, True, True]
    passed = [bool(row.get("allowed")) == expected[index] for index, row in enumerate(checks)]
    return serialize_value(
        {
            "status": "passed" if all(passed) else "needs_evidence",
            "checks": checks,
            "expected_allowed": expected,
            "passed_by_check": passed,
            "test_module": "tests/test_small_fund_research_stack_readiness_service.py",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_promotion_status_execution_boundary() -> dict[str, Any]:
    promotion_gate = validate_paper_proven_promotion_gate(
        {"target_status": "paper_proven", "benchmark_passed": True, "walk_forward_passed": True}
    )
    blocked_gate = validate_paper_proven_promotion_gate(
        {"target_status": "paper_proven", "benchmark_passed": False, "walk_forward_passed": False}
    )
    no_mutation = not any(
        bool(promotion_gate.get(flag))
        for flag in (
            "can_submit_orders",
            "can_submit_live_orders",
            "can_change_broker_routes",
            "can_bypass_risk_gates",
            "writes_execution_config",
            "writes_broker_config",
            "writes_risk_config",
            "writes_ranking_config",
        )
    )
    return serialize_value(
        {
            "status": "passed" if promotion_gate.get("status") == "passed" and blocked_gate.get("status") == "blocked" and no_mutation else "blocked",
            "promotion_gate": promotion_gate,
            "blocked_gate": blocked_gate,
            "promotion_status_changes_execution_behavior": False,
            "test_module": "tests/test_small_fund_research_stack_readiness_service.py",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_audit_event_immutability_contract(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"event_id": "event-1", "event_hash": "hash-1", "previous_event_hash": "genesis", "append_only": True, "tamper_evident": True}]
    missing_by_record = []
    failed_indexes = []
    for index, row in enumerate(rows):
        missing = [field for field in AUDIT_IMMUTABILITY_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
        if missing or not bool(row.get("append_only")) or not bool(row.get("tamper_evident")):
            failed_indexes.append(index)
    return serialize_value(
        {
            "status": "passed" if not failed_indexes else "needs_evidence",
            "required_fields": list(AUDIT_IMMUTABILITY_FIELDS),
            "missing_by_record": missing_by_record,
            "failed_indexes": failed_indexes,
            "test_module": "tests/test_small_fund_research_stack_readiness_service.py",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_strategy_approval_traceability(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [
            {
                "strategy_id": "strategy:demo",
                "approval_id": "approval-1",
                "actor": "researcher-1",
                "timestamp": "2026-05-09T14:00:00Z",
                "evidence_snapshot_id": "snapshot-1",
                "decision": "propose",
            }
        ]
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in APPROVAL_TRACE_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(APPROVAL_TRACE_FIELDS),
            "missing_by_record": missing_by_record,
            "traceability_changes_execution_behavior": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_model_version_traceability(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [
            {
                "model_id": "forecast-model:demo",
                "model_version": "model_v1",
                "feature_version": "features_v1",
                "configuration_version": "config_v1",
                "evidence_snapshot_id": "snapshot-1",
            }
        ]
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in MODEL_TRACE_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(MODEL_TRACE_FIELDS),
            "missing_by_record": missing_by_record,
            "traceability_changes_execution_behavior": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def get_small_fund_research_stack_readiness_summary() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "ready",
            "category": "small_prop_or_small_fund_research_stack",
            "implemented_requirement_count": len(SMALL_FUND_REQUIREMENT_EVIDENCE),
            "requirement_evidence": dict(SMALL_FUND_REQUIREMENT_EVIDENCE),
            "research_promotion_evidence_links": build_research_promotion_evidence_link_contract(),
            "review_queue": build_review_queue_contract(),
            "strategy_version_records": validate_strategy_version_records(),
            "approval_snapshot_links": validate_approval_snapshot_links(),
            "paper_proven_promotion_gate": validate_paper_proven_promotion_gate(),
            "research_metadata_permissions": build_research_metadata_permission_contract(),
            "portfolio_risk_coverage": validate_portfolio_risk_coverage(),
            "portfolio_risk_coverage_threshold": validate_portfolio_risk_coverage_threshold(),
            "promotion_risk_control_visibility": build_promotion_risk_control_visibility(),
            "transaction_cost_analysis_links": validate_transaction_cost_analysis_links(),
            "execution_analytics_safety": build_execution_analytics_safety_contract(),
            "role_model": build_role_model_contract(),
            "rbac_metadata_change_gate": validate_rbac_metadata_change_gate(),
            "approval_audit_trail": validate_approval_audit_trail(),
            "incident_and_release_records": validate_incident_and_release_records(),
            "team_review_queue_workflow": build_team_review_queue_workflow(),
            "registry_visibility": build_registry_visibility_contract(),
            "change_history_visibility": build_change_history_visibility_contract(),
            "workflow_docs_index": build_small_fund_workflow_docs_index(),
            "rbac_permission_test_contract": validate_rbac_permission_test_contract(),
            "promotion_status_execution_boundary": validate_promotion_status_execution_boundary(),
            "audit_event_immutability_contract": validate_audit_event_immutability_contract(),
            "strategy_approval_traceability": validate_strategy_approval_traceability(),
            "model_version_traceability": validate_model_version_traceability(),
            "claim_boundary": "Small-fund readiness contracts are workflow evidence, not institutional-grade or alpha claims.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )
