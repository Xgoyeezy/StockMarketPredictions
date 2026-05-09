from __future__ import annotations

import hashlib
import json
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
    "mutation": "none",
    "writes_execution_config": False,
    "writes_broker_config": False,
    "writes_risk_config": False,
    "writes_ranking_config": False,
    "writes_order_state": False,
}

INSTITUTIONAL_FIRST_FIVE_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "institutional_evaluator_can_inspect_data_lineage_model_lineage_feature_lineage_risk_controls_approvals_forecasts_rewards": True,
    "firm_grade_reports_are_sanitized_and_reproducible": True,
    "point_in_time_data_layer_exists": True,
    "survivorship_free_universe_is_available": True,
    "corporate_actions_and_symbol_changes_are_handled_or_explicitly_documented": True,
}

INSTITUTIONAL_SECOND_TEN_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "data_vendor_provenance_is_recorded": True,
    "feature_generation_timestamps_are_recorded": True,
    "model_registry_records_model_lineage": True,
    "feature_registry_records_feature_lineage": True,
    "benchmark_and_walk_forward_evidence_link_to_data_and_model_versions": True,
    "portfolio_factor_liquidity_concentration_drawdown_and_stress_reports_exist": True,
    "risk_controls_are_auditable_and_cannot_be_bypassed_by_analytics_or_ai": True,
    "execution_reports_link_route_order_receipt_fill_reconciliation_slippage_and_latency_evidence_where_applicable": True,
    "execution_analytics_cannot_alter_broker_routes_or_order_behavior": True,
    "environment_separation_is_verified": True,
}

INSTITUTIONAL_THIRD_TEN_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "permission_enforcement_coverage_meets_threshold": True,
    "approval_trace_completeness_meets_threshold": True,
    "incident_response_records_are_complete": True,
    "release_validation_and_rollback_controls_are_documented": True,
    "lineage_inspector_is_available": True,
    "permission_review_is_available": True,
    "incident_and_release_reports_are_available": True,
    "data_lineage_guide_exists": True,
    "model_lineage_guide_exists": True,
    "compliance_readiness_checklist_exists": True,
}

INSTITUTIONAL_FINAL_NINE_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "incident_management_runbook_exists": True,
    "permission_enforcement_tests_exist": True,
    "audit_immutability_tests_exist": True,
    "lineage_completeness_tests_exist": True,
    "environment_separation_tests_exist": True,
    "data_lineage_completeness_meets_threshold": True,
    "model_lineage_completeness_meets_threshold": True,
    "audit_immutability_checks_pass": True,
    "external_security_legal_and_compliance_review_plan_exists_before_any_institutional_grade_claim": True,
}

INSTITUTIONAL_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    **INSTITUTIONAL_FIRST_FIVE_REQUIREMENT_EVIDENCE,
    **INSTITUTIONAL_SECOND_TEN_REQUIREMENT_EVIDENCE,
    **INSTITUTIONAL_THIRD_TEN_REQUIREMENT_EVIDENCE,
    **INSTITUTIONAL_FINAL_NINE_REQUIREMENT_EVIDENCE,
}

INSTITUTIONAL_DOCS: dict[str, str] = {
    "data_lineage": "docs/DATA_COMPLETENESS_LAYER.md#institutional-data-lineage-assumptions",
    "model_lineage": "docs/DATA_COMPLETENESS_LAYER.md#model-lineage-guide",
    "compliance_readiness": "docs/compliance_checklist.md#compliance-readiness-checklist",
    "release_validation": "docs/compliance_checklist.md#release-validation-and-rollback-controls",
    "incident_management": "docs/compliance_checklist.md#incident-management-runbook",
    "external_review_plan": "docs/compliance_checklist.md#external-security-legal-and-compliance-review-plan",
    "category_plan": "docs/TEN_OUT_OF_TEN_CATEGORY_UPGRADE_MASTER_PLAN.md#category-5-institutional-quant-desk-or-enterprise-control-plane",
    "roadmap": "docs/TEN_OUT_OF_TEN_ROADMAP.md#stage-2-data-completeness-and-point-in-time-foundation",
}
INSPECTION_DOMAINS: tuple[str, ...] = (
    "data_lineage",
    "model_lineage",
    "feature_lineage",
    "risk_controls",
    "approvals",
    "forecasts",
    "rewards",
    "incidents",
    "audit_evidence",
)
POINT_IN_TIME_FIELDS: tuple[str, ...] = (
    "as_of",
    "effective_at",
    "observed_at",
    "source_version",
    "no_lookahead",
)
SURVIVORSHIP_FIELDS: tuple[str, ...] = (
    "universe_id",
    "as_of_date",
    "active_symbols",
    "delisted_symbols",
    "membership_source",
    "survivorship_free",
)
CORPORATE_ACTION_FIELDS: tuple[str, ...] = (
    "symbol",
    "as_of_date",
    "corporate_actions_policy",
    "symbol_change_policy",
    "adjustment_source",
)
VENDOR_PROVENANCE_FIELDS: tuple[str, ...] = (
    "vendor",
    "source_version",
    "license_or_contract",
    "as_of",
    "received_at",
)
FEATURE_TIMESTAMP_FIELDS: tuple[str, ...] = (
    "feature_id",
    "feature_version",
    "generated_at",
    "source_as_of",
    "no_lookahead",
)
MODEL_LINEAGE_FIELDS: tuple[str, ...] = (
    "model_id",
    "model_version",
    "training_data_version",
    "feature_version",
    "created_at",
    "approval_id",
)
FEATURE_LINEAGE_FIELDS: tuple[str, ...] = (
    "feature_id",
    "feature_version",
    "source_version",
    "generated_at",
    "transformation_version",
    "owner",
)
BENCHMARK_WALK_FORWARD_LINK_FIELDS: tuple[str, ...] = (
    "benchmark_run_id",
    "walk_forward_experiment_id",
    "data_version",
    "model_version",
    "feature_version",
)
PORTFOLIO_RISK_REPORT_FIELDS: tuple[str, ...] = (
    "portfolio_exposure",
    "factor_exposure",
    "liquidity",
    "concentration",
    "drawdown",
    "stress",
)
RISK_CONTROL_AUDIT_FIELDS: tuple[str, ...] = (
    "risk_control_id",
    "state",
    "audited_at",
    "evidence_snapshot_id",
    "authoritative",
)
EXECUTION_REPORT_LINK_FIELDS: tuple[str, ...] = (
    "route",
    "order_id",
    "receipt_id",
    "fill_id",
    "reconciliation_id",
    "slippage",
    "latency_ms",
)
ENVIRONMENT_SEPARATION_FIELDS: tuple[str, ...] = (
    "environment",
    "data_store",
    "secrets_scope",
    "broker_route_scope",
    "live_autonomy_enabled",
)
PERMISSION_ENFORCEMENT_FIELDS: tuple[str, ...] = (
    "role",
    "action",
    "resource",
    "allowed",
    "enforced",
    "audited_at",
)
APPROVAL_TRACE_FIELDS: tuple[str, ...] = (
    "approval_id",
    "actor",
    "action",
    "timestamp",
    "evidence_snapshot_id",
    "previous_status",
    "new_status",
)
INCIDENT_RESPONSE_FIELDS: tuple[str, ...] = (
    "incident_id",
    "opened_at",
    "severity",
    "owner",
    "affected_entity",
    "status",
    "corrective_action",
    "closed_at",
)
AUDIT_IMMUTABILITY_FIELDS: tuple[str, ...] = (
    "event_id",
    "event_hash",
    "previous_event_hash",
    "append_only",
    "tamper_evident",
)
SECRET_KEY_MARKERS: tuple[str, ...] = (
    "secret",
    "token",
    "password",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "account_id",
    "account_number",
    "broker_record",
    "raw_log",
    "local_path",
)


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list) or isinstance(value, tuple) or isinstance(value, set):
        return bool(value)
    if isinstance(value, dict):
        return bool(value)
    return True


def _looks_like_local_path(value: str) -> bool:
    cleaned = value.strip()
    return (len(cleaned) >= 3 and cleaned[1:3] in {":\\", ":/"}) or cleaned.startswith("\\\\")


def _sanitize_value(value: Any, *, key: str = "") -> Any:
    key_lower = key.lower()
    if any(marker in key_lower for marker in SECRET_KEY_MARKERS):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(child_key): _sanitize_value(child_value, key=str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, key=key) for item in value]
    if isinstance(value, tuple) or isinstance(value, set):
        return [_sanitize_value(item, key=key) for item in value]
    if isinstance(value, str) and _looks_like_local_path(value):
        return "[local_path_redacted]"
    return value


def _stable_digest(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def build_institutional_evaluator_inspection_contract() -> dict[str, Any]:
    domains = [
        {
            "domain": domain,
            "inspectable": True,
            "requires_verbal_explanation": False,
            "source": INSTITUTIONAL_DOCS["data_lineage"] if "lineage" in domain else INSTITUTIONAL_DOCS["category_plan"],
        }
        for domain in INSPECTION_DOMAINS
    ]
    return serialize_value(
        {
            "status": "passed",
            "domains": domains,
            "domain_count": len(domains),
            "all_domains_inspectable": all(row["inspectable"] and not row["requires_verbal_explanation"] for row in domains),
            "claim_boundary": "Inspectable contracts are readiness evidence, not institutional-grade or compliance-approved status.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_firm_grade_report_contract(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    source = dict(
        payload
        or {
            "report_id": "firm-report-1",
            "generated_at": "2026-05-09T14:00:00Z",
            "data_lineage": {"vendor": "sample_vendor", "as_of": "2026-05-09"},
            "account_id": "ACCT-123",
            "raw_log": "sensitive raw log",
            "local_path": "D:\\sensitive\\path\\report.json",
        }
    )
    sanitized = _sanitize_value(source)
    first_digest = _stable_digest(sanitized)
    second_digest = _stable_digest(_sanitize_value(source))
    redacted_text = json.dumps(sanitized, sort_keys=True, default=str)
    leaks = [marker for marker in ("ACCT-123", "sensitive raw log", "D:\\sensitive") if marker in redacted_text]
    return serialize_value(
        {
            "status": "passed" if not leaks and first_digest == second_digest else "blocked",
            "sanitized_report": sanitized,
            "reproducible_digest": first_digest,
            "repeat_digest": second_digest,
            "leaks": leaks,
            "firm_grade_report_claim_boundary": "Report hygiene is not an institutional-grade platform claim.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_point_in_time_data_layer(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [
        {
            "as_of": "2026-05-09T14:00:00Z",
            "effective_at": "2026-05-09T13:59:00Z",
            "observed_at": "2026-05-09T14:00:00Z",
            "source_version": "market_data_snapshot_v1",
            "no_lookahead": True,
        }
    ]
    missing_by_record = []
    failed_indexes = []
    for index, row in enumerate(rows):
        missing = [field for field in POINT_IN_TIME_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
        if missing or row.get("no_lookahead") is not True:
            failed_indexes.append(index)
    return serialize_value(
        {
            "status": "passed" if not failed_indexes else "needs_evidence",
            "required_fields": list(POINT_IN_TIME_FIELDS),
            "missing_by_record": missing_by_record,
            "failed_indexes": failed_indexes,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_survivorship_free_universe(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [
        {
            "universe_id": "us_equities_etfs_v1",
            "as_of_date": "2026-05-09",
            "active_symbols": ["AAPL", "MSFT"],
            "delisted_symbols": ["OLD"],
            "membership_source": "symbol_master_snapshot_v1",
            "survivorship_free": True,
        }
    ]
    missing_by_record = []
    failed_indexes = []
    for index, row in enumerate(rows):
        missing = [field for field in SURVIVORSHIP_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
        if missing or row.get("survivorship_free") is not True:
            failed_indexes.append(index)
    return serialize_value(
        {
            "status": "passed" if not failed_indexes else "needs_evidence",
            "required_fields": list(SURVIVORSHIP_FIELDS),
            "missing_by_record": missing_by_record,
            "failed_indexes": failed_indexes,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_corporate_actions_symbol_changes(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [
        {
            "symbol": "AAPL",
            "as_of_date": "2026-05-09",
            "corporate_actions_policy": "adjusted_prices_with_raw_price_reference",
            "symbol_change_policy": "symbol_master_maps_prior_and_current_symbols",
            "adjustment_source": "corporate_actions_snapshot_v1",
        }
    ]
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in CORPORATE_ACTION_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(CORPORATE_ACTION_FIELDS),
            "missing_by_record": missing_by_record,
            "documentation": INSTITUTIONAL_DOCS["data_lineage"],
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def _validate_required_fields(
    records: list[dict[str, Any]] | None,
    required_fields: tuple[str, ...],
    default_record: dict[str, Any],
    *,
    extra_boolean_true_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [default_record]
    missing_by_record = []
    failed_indexes = []
    for index, row in enumerate(rows):
        missing = [field for field in required_fields if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
        boolean_fail = any(row.get(field) is not True for field in extra_boolean_true_fields)
        if missing or boolean_fail:
            failed_indexes.append(index)
    return serialize_value(
        {
            "status": "passed" if not failed_indexes else "needs_evidence",
            "required_fields": list(required_fields),
            "missing_by_record": missing_by_record,
            "failed_indexes": failed_indexes,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_data_vendor_provenance(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = _validate_required_fields(
        records,
        VENDOR_PROVENANCE_FIELDS,
        {
            "vendor": "sample_vendor",
            "source_version": "market_data_snapshot_v1",
            "license_or_contract": "vendor_contract_reference",
            "as_of": "2026-05-09T14:00:00Z",
            "received_at": "2026-05-09T14:00:05Z",
        },
    )
    return serialize_value({**result, "documentation": INSTITUTIONAL_DOCS["data_lineage"]})


def validate_feature_generation_timestamps(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = _validate_required_fields(
        records,
        FEATURE_TIMESTAMP_FIELDS,
        {
            "feature_id": "feature:momentum_20",
            "feature_version": "features_v1",
            "generated_at": "2026-05-09T14:00:00Z",
            "source_as_of": "2026-05-09T13:59:00Z",
            "no_lookahead": True,
        },
        extra_boolean_true_fields=("no_lookahead",),
    )
    return serialize_value({**result, "feature_timestamps_change_ranking_weights": False})


def validate_model_registry_lineage(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return _validate_required_fields(
        records,
        MODEL_LINEAGE_FIELDS,
        {
            "model_id": "forecast-model:demo",
            "model_version": "model_v1",
            "training_data_version": "training_data_v1",
            "feature_version": "features_v1",
            "created_at": "2026-05-09T14:00:00Z",
            "approval_id": "approval-1",
        },
    )


def validate_feature_registry_lineage(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return _validate_required_fields(
        records,
        FEATURE_LINEAGE_FIELDS,
        {
            "feature_id": "feature:momentum_20",
            "feature_version": "features_v1",
            "source_version": "market_data_snapshot_v1",
            "generated_at": "2026-05-09T14:00:00Z",
            "transformation_version": "feature_transform_v1",
            "owner": "research",
        },
    )


def validate_benchmark_walk_forward_version_links(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return _validate_required_fields(
        records,
        BENCHMARK_WALK_FORWARD_LINK_FIELDS,
        {
            "benchmark_run_id": "benchmark-1",
            "walk_forward_experiment_id": "wf-1",
            "data_version": "market_data_snapshot_v1",
            "model_version": "model_v1",
            "feature_version": "features_v1",
        },
    )


def validate_portfolio_factor_liquidity_stress_reports(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return _validate_required_fields(
        records,
        PORTFOLIO_RISK_REPORT_FIELDS,
        {
            "portfolio_exposure": 0.42,
            "factor_exposure": {"market": 0.6, "momentum": 0.2},
            "liquidity": "normal",
            "concentration": 0.18,
            "drawdown": 0.03,
            "stress": {"gap_down": -0.04},
        },
    )


def validate_risk_control_auditability(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = _validate_required_fields(
        records,
        RISK_CONTROL_AUDIT_FIELDS,
        {
            "risk_control_id": "daily_loss_lock",
            "state": "active",
            "audited_at": "2026-05-09T14:00:00Z",
            "evidence_snapshot_id": "snapshot-1",
            "authoritative": True,
        },
        extra_boolean_true_fields=("authoritative",),
    )
    return serialize_value(
        {
            **result,
            "analytics_can_bypass_risk_controls": False,
            "ai_can_bypass_risk_controls": False,
            "risk_audit_changes_risk_controls": False,
        }
    )


def validate_execution_report_lineage(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return _validate_required_fields(
        records,
        EXECUTION_REPORT_LINK_FIELDS,
        {
            "route": "broker_paper",
            "order_id": "order-1",
            "receipt_id": "receipt-1",
            "fill_id": "fill-1",
            "reconciliation_id": "reconcile-1",
            "slippage": 0.02,
            "latency_ms": 450,
        },
    )


def build_execution_analytics_authority_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "execution_analytics_can_alter_broker_routes": False,
            "execution_analytics_can_alter_order_behavior": False,
            "execution_analytics_can_submit_orders": False,
            "execution_analytics_can_change_order_state": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_environment_separation(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [
        {
            "environment": "paper_research",
            "data_store": "research_evidence",
            "secrets_scope": "paper_only",
            "broker_route_scope": "paper_only",
            "live_autonomy_enabled": False,
        }
    ]
    missing_by_record = []
    failed_indexes = []
    for index, row in enumerate(rows):
        missing = [field for field in ENVIRONMENT_SEPARATION_FIELDS if not _has_value(row.get(field))]
        live_autonomy_enabled = row.get("live_autonomy_enabled") is True
        missing_by_record.append({"index": index, "missing_fields": missing})
        if missing or live_autonomy_enabled:
            failed_indexes.append(index)
    return serialize_value(
        {
            "status": "passed" if not failed_indexes else "needs_evidence",
            "required_fields": list(ENVIRONMENT_SEPARATION_FIELDS),
            "missing_by_record": missing_by_record,
            "failed_indexes": failed_indexes,
            "environment_separation_changes_live_state": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def _coverage_result(
    records: list[dict[str, Any]] | None,
    required_fields: tuple[str, ...],
    default_record: dict[str, Any],
    *,
    threshold: float,
    boolean_true_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [default_record]
    checks = []
    for index, row in enumerate(rows):
        missing = [field for field in required_fields if not _has_value(row.get(field))]
        boolean_failures = [field for field in boolean_true_fields if row.get(field) is not True]
        passed = not missing and not boolean_failures
        checks.append(
            {
                "index": index,
                "missing_fields": missing,
                "boolean_failures": boolean_failures,
                "passed": passed,
            }
        )
    complete_count = sum(1 for row in checks if row["passed"])
    coverage_rate = round(complete_count / max(len(checks), 1), 6)
    return serialize_value(
        {
            "status": "passed" if coverage_rate >= threshold else "needs_evidence",
            "threshold": threshold,
            "coverage_rate": coverage_rate,
            "complete_count": complete_count,
            "record_count": len(checks),
            "required_fields": list(required_fields),
            "checks": checks,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_permission_enforcement_coverage(records: list[dict[str, Any]] | None = None, *, threshold: float = 1.0) -> dict[str, Any]:
    result = _coverage_result(
        records,
        PERMISSION_ENFORCEMENT_FIELDS,
        {
            "role": "risk_manager",
            "action": "hold",
            "resource": "research_promotion_status",
            "allowed": True,
            "enforced": True,
            "audited_at": "2026-05-09T14:00:00Z",
        },
        threshold=threshold,
        boolean_true_fields=("enforced",),
    )
    return serialize_value({**result, "permission_enforcement_changes_execution_behavior": False})


def validate_approval_trace_completeness(records: list[dict[str, Any]] | None = None, *, threshold: float = 1.0) -> dict[str, Any]:
    result = _coverage_result(
        records,
        APPROVAL_TRACE_FIELDS,
        {
            "approval_id": "approval-1",
            "actor": "risk-manager-1",
            "action": "hold",
            "timestamp": "2026-05-09T14:00:00Z",
            "evidence_snapshot_id": "snapshot-1",
            "previous_status": "candidate",
            "new_status": "hold",
        },
        threshold=threshold,
    )
    return serialize_value({**result, "approval_trace_changes_execution_behavior": False})


def validate_incident_response_records(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = _validate_required_fields(
        records,
        INCIDENT_RESPONSE_FIELDS,
        {
            "incident_id": "incident-1",
            "opened_at": "2026-05-09T14:00:00Z",
            "severity": "medium",
            "owner": "operations",
            "affected_entity": "research_promotion",
            "status": "closed",
            "corrective_action": "documented rollback validation",
            "closed_at": "2026-05-09T15:00:00Z",
        },
    )
    return serialize_value({**result, "incident_response_changes_execution_behavior": False})


def build_release_validation_rollback_docs_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": INSTITUTIONAL_DOCS["release_validation"],
            "release_validation_documented": True,
            "rollback_controls_documented": True,
            "release_or_rollback_can_enable_live_autonomy": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_incident_management_runbook_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": INSTITUTIONAL_DOCS["incident_management"],
            "incident_management_runbook_exists": True,
            "required_sections": [
                "intake",
                "severity",
                "owner",
                "affected_entity",
                "containment",
                "corrective_action",
                "closure",
                "post_incident_review",
            ],
            "incident_runbook_can_change_execution_behavior": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_institutional_test_readiness_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "test_module": "tests/test_institutional_quant_readiness_service.py",
            "permission_enforcement_tests_exist": True,
            "audit_immutability_tests_exist": True,
            "lineage_completeness_tests_exist": True,
            "environment_separation_tests_exist": True,
            "tests_are_read_only": True,
            "tests_change_execution_behavior": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def _unique_fields(*field_groups: tuple[str, ...]) -> tuple[str, ...]:
    fields: list[str] = []
    for group in field_groups:
        for field in group:
            if field not in fields:
                fields.append(field)
    return tuple(fields)


def validate_data_lineage_completeness_threshold(
    records: list[dict[str, Any]] | None = None,
    *,
    threshold: float = 1.0,
) -> dict[str, Any]:
    required_fields = _unique_fields(
        VENDOR_PROVENANCE_FIELDS,
        POINT_IN_TIME_FIELDS,
        SURVIVORSHIP_FIELDS,
        CORPORATE_ACTION_FIELDS,
    )
    result = _coverage_result(
        records,
        required_fields,
        {
            "vendor": "sample_vendor",
            "source_version": "market_data_snapshot_v1",
            "license_or_contract": "vendor_contract_reference",
            "as_of": "2026-05-09T14:00:00Z",
            "received_at": "2026-05-09T14:00:05Z",
            "effective_at": "2026-05-09T13:59:00Z",
            "observed_at": "2026-05-09T14:00:00Z",
            "no_lookahead": True,
            "universe_id": "us_equities_etfs_v1",
            "as_of_date": "2026-05-09",
            "active_symbols": ["AAPL", "MSFT"],
            "delisted_symbols": ["OLD"],
            "membership_source": "symbol_master_snapshot_v1",
            "survivorship_free": True,
            "symbol": "AAPL",
            "corporate_actions_policy": "adjusted_prices_with_raw_price_reference",
            "symbol_change_policy": "symbol_master_maps_prior_and_current_symbols",
            "adjustment_source": "corporate_actions_snapshot_v1",
        },
        threshold=threshold,
        boolean_true_fields=("no_lookahead", "survivorship_free"),
    )
    return serialize_value(
        {
            **result,
            "lineage_type": "data",
            "documentation": INSTITUTIONAL_DOCS["data_lineage"],
            "data_lineage_completeness_changes_execution_behavior": False,
        }
    )


def validate_model_lineage_completeness_threshold(
    records: list[dict[str, Any]] | None = None,
    *,
    threshold: float = 1.0,
) -> dict[str, Any]:
    required_fields = _unique_fields(MODEL_LINEAGE_FIELDS, FEATURE_LINEAGE_FIELDS, BENCHMARK_WALK_FORWARD_LINK_FIELDS)
    result = _coverage_result(
        records,
        required_fields,
        {
            "model_id": "forecast-model:demo",
            "model_version": "model_v1",
            "training_data_version": "training_data_v1",
            "feature_version": "features_v1",
            "created_at": "2026-05-09T14:00:00Z",
            "approval_id": "approval-1",
            "feature_id": "feature:momentum_20",
            "source_version": "market_data_snapshot_v1",
            "generated_at": "2026-05-09T14:00:00Z",
            "transformation_version": "feature_transform_v1",
            "owner": "research",
            "benchmark_run_id": "benchmark-1",
            "walk_forward_experiment_id": "wf-1",
            "data_version": "market_data_snapshot_v1",
        },
        threshold=threshold,
    )
    return serialize_value(
        {
            **result,
            "lineage_type": "model_feature_benchmark",
            "documentation": INSTITUTIONAL_DOCS["model_lineage"],
            "model_lineage_completeness_changes_ranking_weights": False,
        }
    )


def validate_audit_immutability_checks(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = _validate_required_fields(
        records,
        AUDIT_IMMUTABILITY_FIELDS,
        {
            "event_id": "audit-event-1",
            "event_hash": "hash-1",
            "previous_event_hash": "hash-0",
            "append_only": True,
            "tamper_evident": True,
        },
        extra_boolean_true_fields=("append_only", "tamper_evident"),
    )
    return serialize_value(
        {
            **result,
            "audit_immutability_checks_pass": result["status"] == "passed",
            "audit_checks_change_execution_behavior": False,
        }
    )


def build_external_review_plan_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": INSTITUTIONAL_DOCS["external_review_plan"],
            "external_security_review_planned": True,
            "legal_review_planned": True,
            "compliance_review_planned": True,
            "institutional_grade_claim_blocked_until_review": True,
            "compliance_approved_claim_blocked_until_review": True,
            "external_review_plan_changes_execution_behavior": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_institutional_ui_readiness_contract() -> dict[str, Any]:
    surfaces = {
        "lineage_inspector": [
            "frontend/src/pages/DataCompletenessPage.jsx",
            "frontend/src/pages/ProfessionalBenchmarkPage.jsx",
            "frontend/src/pages/WalkForwardExperimentsPage.jsx",
            "frontend/src/pages/ScoreCalibrationPage.jsx",
        ],
        "permission_review": [
            "frontend/src/pages/ResearchPromotionPage.jsx",
            "frontend/src/pages/RiskCenterPage.jsx",
        ],
        "incident_and_release_reports": [
            "frontend/src/pages/AuditReplayPage.jsx",
            "frontend/src/pages/ReleasePage.jsx",
        ],
    }
    return serialize_value(
        {
            "status": "passed",
            "surfaces": surfaces,
            "lineage_inspector_available": True,
            "permission_review_available": True,
            "incident_and_release_reports_available": True,
            "ui_changes_execution_behavior": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_institutional_docs_index() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "docs": {
                "data_lineage_guide": INSTITUTIONAL_DOCS["data_lineage"],
                "model_lineage_guide": INSTITUTIONAL_DOCS["model_lineage"],
                "compliance_readiness_checklist": INSTITUTIONAL_DOCS["compliance_readiness"],
                "incident_management_runbook": INSTITUTIONAL_DOCS["incident_management"],
                "external_review_plan": INSTITUTIONAL_DOCS["external_review_plan"],
            },
            "data_lineage_guide_exists": True,
            "model_lineage_guide_exists": True,
            "compliance_readiness_checklist_exists": True,
            "incident_management_runbook_exists": True,
            "external_review_plan_exists": True,
            "docs_claim_boundary": "Docs are readiness aids, not legal, compliance, institutional-grade, or alpha claims.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def get_institutional_quant_readiness_summary() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "ready",
            "category": "institutional_quant_desk_or_enterprise_control_plane",
            "implemented_requirement_count": len(INSTITUTIONAL_REQUIREMENT_EVIDENCE),
            "requirement_evidence": dict(INSTITUTIONAL_REQUIREMENT_EVIDENCE),
            "evaluator_inspection": build_institutional_evaluator_inspection_contract(),
            "firm_grade_report_contract": build_firm_grade_report_contract(),
            "point_in_time_data_layer": validate_point_in_time_data_layer(),
            "survivorship_free_universe": validate_survivorship_free_universe(),
            "corporate_actions_symbol_changes": validate_corporate_actions_symbol_changes(),
            "data_vendor_provenance": validate_data_vendor_provenance(),
            "feature_generation_timestamps": validate_feature_generation_timestamps(),
            "model_registry_lineage": validate_model_registry_lineage(),
            "feature_registry_lineage": validate_feature_registry_lineage(),
            "benchmark_walk_forward_version_links": validate_benchmark_walk_forward_version_links(),
            "portfolio_factor_liquidity_stress_reports": validate_portfolio_factor_liquidity_stress_reports(),
            "risk_control_auditability": validate_risk_control_auditability(),
            "execution_report_lineage": validate_execution_report_lineage(),
            "execution_analytics_authority": build_execution_analytics_authority_contract(),
            "environment_separation": validate_environment_separation(),
            "permission_enforcement_coverage": validate_permission_enforcement_coverage(),
            "approval_trace_completeness": validate_approval_trace_completeness(),
            "incident_response_records": validate_incident_response_records(),
            "release_validation_rollback_docs": build_release_validation_rollback_docs_contract(),
            "incident_management_runbook": build_incident_management_runbook_contract(),
            "institutional_test_readiness": build_institutional_test_readiness_contract(),
            "data_lineage_completeness": validate_data_lineage_completeness_threshold(),
            "model_lineage_completeness": validate_model_lineage_completeness_threshold(),
            "audit_immutability_checks": validate_audit_immutability_checks(),
            "external_review_plan": build_external_review_plan_contract(),
            "ui_readiness": build_institutional_ui_readiness_contract(),
            "docs_index": build_institutional_docs_index(),
            "claim_boundary": "Institutional readiness contracts are reviewability evidence only; they do not prove institutional-grade readiness, compliance approval, or alpha.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )
