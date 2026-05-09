from __future__ import annotations

import inspect
import unittest

from backend.services import institutional_quant_readiness_service as institutional
from backend.services.category_upgrade_readiness_service import build_category_upgrade_readiness_report
from backend.services.institutional_quant_readiness_service import (
    INSTITUTIONAL_FINAL_NINE_REQUIREMENT_EVIDENCE,
    INSTITUTIONAL_FIRST_FIVE_REQUIREMENT_EVIDENCE,
    INSTITUTIONAL_REQUIREMENT_EVIDENCE,
    INSTITUTIONAL_SECOND_TEN_REQUIREMENT_EVIDENCE,
    INSTITUTIONAL_THIRD_TEN_REQUIREMENT_EVIDENCE,
    build_execution_analytics_authority_contract,
    build_external_review_plan_contract,
    build_firm_grade_report_contract,
    build_institutional_evaluator_inspection_contract,
    build_institutional_docs_index,
    build_incident_management_runbook_contract,
    build_institutional_ui_readiness_contract,
    build_institutional_test_readiness_contract,
    build_release_validation_rollback_docs_contract,
    get_institutional_quant_readiness_summary,
    validate_approval_trace_completeness,
    validate_audit_immutability_checks,
    validate_benchmark_walk_forward_version_links,
    validate_corporate_actions_symbol_changes,
    validate_data_lineage_completeness_threshold,
    validate_data_vendor_provenance,
    validate_environment_separation,
    validate_execution_report_lineage,
    validate_feature_generation_timestamps,
    validate_feature_registry_lineage,
    validate_model_lineage_completeness_threshold,
    validate_model_registry_lineage,
    validate_incident_response_records,
    validate_permission_enforcement_coverage,
    validate_point_in_time_data_layer,
    validate_portfolio_factor_liquidity_stress_reports,
    validate_risk_control_auditability,
    validate_survivorship_free_universe,
)


class InstitutionalQuantReadinessServiceTests(unittest.TestCase):
    def test_summary_covers_first_institutional_requirements(self) -> None:
        summary = get_institutional_quant_readiness_summary()

        self.assertEqual(summary["implemented_requirement_count"], 34)
        self.assertEqual(summary["requirement_evidence"], INSTITUTIONAL_REQUIREMENT_EVIDENCE)
        for key in INSTITUTIONAL_FIRST_FIVE_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in INSTITUTIONAL_SECOND_TEN_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in INSTITUTIONAL_THIRD_TEN_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in INSTITUTIONAL_FINAL_NINE_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        self.assertTrue(summary["read_only"])
        self.assertFalse(summary["can_submit_orders"])
        self.assertFalse(summary["can_submit_live_orders"])
        self.assertFalse(summary["can_change_broker_routes"])
        self.assertFalse(summary["can_bypass_risk_gates"])
        self.assertIn("do not prove institutional-grade readiness", summary["claim_boundary"])

    def test_institutional_evaluator_inspection_contract(self) -> None:
        contract = build_institutional_evaluator_inspection_contract()

        self.assertEqual(contract["status"], "passed")
        self.assertTrue(contract["all_domains_inspectable"])
        self.assertEqual(contract["domain_count"], 9)
        domains = {row["domain"] for row in contract["domains"]}
        self.assertIn("data_lineage", domains)
        self.assertIn("model_lineage", domains)
        self.assertIn("feature_lineage", domains)
        self.assertIn("risk_controls", domains)
        self.assertIn("audit_evidence", domains)
        self.assertIn("not institutional-grade", contract["claim_boundary"])

    def test_firm_grade_report_contract_sanitizes_and_is_reproducible(self) -> None:
        contract = build_firm_grade_report_contract(
            {
                "report_id": "report-1",
                "account_id": "ACCT-SECRET",
                "raw_log": "raw broker record",
                "local_path": "D:\\sensitive\\broker.json",
                "data_lineage": {"vendor": "sample_vendor", "as_of": "2026-05-09"},
            }
        )

        self.assertEqual(contract["status"], "passed")
        self.assertEqual(contract["leaks"], [])
        self.assertEqual(contract["sanitized_report"]["account_id"], "[redacted]")
        self.assertEqual(contract["sanitized_report"]["raw_log"], "[redacted]")
        self.assertEqual(contract["sanitized_report"]["local_path"], "[redacted]")
        self.assertEqual(contract["reproducible_digest"], contract["repeat_digest"])
        self.assertFalse(contract["can_submit_orders"])

    def test_point_in_time_survivorship_and_corporate_action_contracts(self) -> None:
        point_in_time = validate_point_in_time_data_layer(
            [
                {
                    "as_of": "2026-05-09T14:00:00Z",
                    "effective_at": "2026-05-09T13:59:00Z",
                    "observed_at": "2026-05-09T14:00:00Z",
                    "source_version": "snapshot_v1",
                    "no_lookahead": True,
                },
                {"as_of": "2026-05-09T14:00:00Z", "no_lookahead": False},
            ]
        )
        survivorship = validate_survivorship_free_universe(
            [
                {
                    "universe_id": "universe-1",
                    "as_of_date": "2026-05-09",
                    "active_symbols": ["AAPL"],
                    "delisted_symbols": ["OLD"],
                    "membership_source": "symbol_master_v1",
                    "survivorship_free": True,
                },
                {"universe_id": "universe-2", "survivorship_free": False},
            ]
        )
        corporate_actions = validate_corporate_actions_symbol_changes(
            [
                {
                    "symbol": "AAPL",
                    "as_of_date": "2026-05-09",
                    "corporate_actions_policy": "adjusted_prices_with_raw_reference",
                    "symbol_change_policy": "symbol_master_maps_prior_and_current_symbols",
                    "adjustment_source": "corporate_actions_snapshot_v1",
                },
                {"symbol": "OLD"},
            ]
        )

        self.assertEqual(point_in_time["status"], "needs_evidence")
        self.assertEqual(point_in_time["failed_indexes"], [1])
        self.assertIn("effective_at", point_in_time["missing_by_record"][1]["missing_fields"])
        self.assertEqual(survivorship["status"], "needs_evidence")
        self.assertEqual(survivorship["failed_indexes"], [1])
        self.assertIn("active_symbols", survivorship["missing_by_record"][1]["missing_fields"])
        self.assertEqual(corporate_actions["status"], "needs_evidence")
        self.assertIn("corporate_actions_policy", corporate_actions["missing_by_record"][1]["missing_fields"])
        self.assertIn("#institutional-data-lineage-assumptions", corporate_actions["documentation"])

    def test_vendor_provenance_feature_timestamps_and_registry_lineage(self) -> None:
        provenance = validate_data_vendor_provenance(
            [
                {
                    "vendor": "sample_vendor",
                    "source_version": "snapshot_v1",
                    "license_or_contract": "contract_ref",
                    "as_of": "2026-05-09T14:00:00Z",
                    "received_at": "2026-05-09T14:00:05Z",
                },
                {"vendor": "sample_vendor"},
            ]
        )
        feature_timestamps = validate_feature_generation_timestamps(
            [
                {
                    "feature_id": "feature:momentum",
                    "feature_version": "features_v1",
                    "generated_at": "2026-05-09T14:00:00Z",
                    "source_as_of": "2026-05-09T13:59:00Z",
                    "no_lookahead": True,
                },
                {"feature_id": "feature:gap", "no_lookahead": False},
            ]
        )
        model_lineage = validate_model_registry_lineage(
            [
                {
                    "model_id": "model:demo",
                    "model_version": "model_v1",
                    "training_data_version": "training_v1",
                    "feature_version": "features_v1",
                    "created_at": "2026-05-09T14:00:00Z",
                    "approval_id": "approval-1",
                },
                {"model_id": "model:demo"},
            ]
        )
        feature_lineage = validate_feature_registry_lineage(
            [
                {
                    "feature_id": "feature:momentum",
                    "feature_version": "features_v1",
                    "source_version": "snapshot_v1",
                    "generated_at": "2026-05-09T14:00:00Z",
                    "transformation_version": "transform_v1",
                    "owner": "research",
                },
                {"feature_id": "feature:gap"},
            ]
        )

        self.assertEqual(provenance["status"], "needs_evidence")
        self.assertIn("source_version", provenance["missing_by_record"][1]["missing_fields"])
        self.assertIn("#institutional-data-lineage-assumptions", provenance["documentation"])
        self.assertEqual(feature_timestamps["status"], "needs_evidence")
        self.assertEqual(feature_timestamps["failed_indexes"], [1])
        self.assertFalse(feature_timestamps["feature_timestamps_change_ranking_weights"])
        self.assertEqual(model_lineage["status"], "needs_evidence")
        self.assertIn("model_version", model_lineage["missing_by_record"][1]["missing_fields"])
        self.assertEqual(feature_lineage["status"], "needs_evidence")
        self.assertIn("source_version", feature_lineage["missing_by_record"][1]["missing_fields"])

    def test_benchmark_risk_execution_and_environment_contracts(self) -> None:
        benchmark_links = validate_benchmark_walk_forward_version_links(
            [
                {
                    "benchmark_run_id": "benchmark-1",
                    "walk_forward_experiment_id": "wf-1",
                    "data_version": "data_v1",
                    "model_version": "model_v1",
                    "feature_version": "features_v1",
                },
                {"benchmark_run_id": "benchmark-2"},
            ]
        )
        portfolio = validate_portfolio_factor_liquidity_stress_reports(
            [
                {
                    "portfolio_exposure": 0.42,
                    "factor_exposure": {"market": 0.6},
                    "liquidity": "normal",
                    "concentration": 0.18,
                    "drawdown": 0.03,
                    "stress": {"gap_down": -0.04},
                },
                {"portfolio_exposure": 0.2},
            ]
        )
        risk = validate_risk_control_auditability(
            [
                {
                    "risk_control_id": "daily_loss_lock",
                    "state": "active",
                    "audited_at": "2026-05-09T14:00:00Z",
                    "evidence_snapshot_id": "snapshot-1",
                    "authoritative": True,
                },
                {"risk_control_id": "route_block", "authoritative": False},
            ]
        )
        execution = validate_execution_report_lineage(
            [
                {
                    "route": "broker_paper",
                    "order_id": "order-1",
                    "receipt_id": "receipt-1",
                    "fill_id": "fill-1",
                    "reconciliation_id": "reconcile-1",
                    "slippage": 0.02,
                    "latency_ms": 450,
                },
                {"route": "broker_paper"},
            ]
        )
        execution_authority = build_execution_analytics_authority_contract()
        environment = validate_environment_separation(
            [
                {
                    "environment": "paper_research",
                    "data_store": "research_evidence",
                    "secrets_scope": "paper_only",
                    "broker_route_scope": "paper_only",
                    "live_autonomy_enabled": False,
                },
                {
                    "environment": "live",
                    "data_store": "research_evidence",
                    "secrets_scope": "live",
                    "broker_route_scope": "live",
                    "live_autonomy_enabled": True,
                },
            ]
        )

        self.assertEqual(benchmark_links["status"], "needs_evidence")
        self.assertIn("walk_forward_experiment_id", benchmark_links["missing_by_record"][1]["missing_fields"])
        self.assertEqual(portfolio["status"], "needs_evidence")
        self.assertIn("factor_exposure", portfolio["missing_by_record"][1]["missing_fields"])
        self.assertEqual(risk["status"], "needs_evidence")
        self.assertFalse(risk["analytics_can_bypass_risk_controls"])
        self.assertFalse(risk["ai_can_bypass_risk_controls"])
        self.assertEqual(execution["status"], "needs_evidence")
        self.assertIn("order_id", execution["missing_by_record"][1]["missing_fields"])
        self.assertEqual(execution_authority["status"], "passed")
        self.assertFalse(execution_authority["execution_analytics_can_alter_broker_routes"])
        self.assertFalse(execution_authority["execution_analytics_can_alter_order_behavior"])
        self.assertEqual(environment["status"], "needs_evidence")
        self.assertEqual(environment["failed_indexes"], [1])
        self.assertFalse(environment["environment_separation_changes_live_state"])

    def test_governance_thresholds_and_incident_release_contracts(self) -> None:
        permissions = validate_permission_enforcement_coverage(
            [
                {
                    "role": "risk_manager",
                    "action": "hold",
                    "resource": "research_promotion_status",
                    "allowed": True,
                    "enforced": True,
                    "audited_at": "2026-05-09T14:00:00Z",
                },
                {"role": "operator", "action": "approve", "resource": "research_promotion_status", "allowed": False, "enforced": False},
            ],
            threshold=0.75,
        )
        approvals = validate_approval_trace_completeness(
            [
                {
                    "approval_id": "approval-1",
                    "actor": "risk-manager-1",
                    "action": "hold",
                    "timestamp": "2026-05-09T14:00:00Z",
                    "evidence_snapshot_id": "snapshot-1",
                    "previous_status": "candidate",
                    "new_status": "hold",
                },
                {"approval_id": "approval-2"},
            ],
            threshold=0.75,
        )
        incidents = validate_incident_response_records(
            [
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
                {"incident_id": "incident-2"},
            ]
        )
        release_docs = build_release_validation_rollback_docs_contract()

        self.assertEqual(permissions["status"], "needs_evidence")
        self.assertEqual(permissions["coverage_rate"], 0.5)
        self.assertFalse(permissions["permission_enforcement_changes_execution_behavior"])
        self.assertEqual(approvals["status"], "needs_evidence")
        self.assertIn("actor", approvals["checks"][1]["missing_fields"])
        self.assertFalse(approvals["approval_trace_changes_execution_behavior"])
        self.assertEqual(incidents["status"], "needs_evidence")
        self.assertIn("opened_at", incidents["missing_by_record"][1]["missing_fields"])
        self.assertFalse(incidents["incident_response_changes_execution_behavior"])
        self.assertEqual(release_docs["status"], "passed")
        self.assertIn("#release-validation-and-rollback-controls", release_docs["documentation"])
        self.assertFalse(release_docs["release_or_rollback_can_enable_live_autonomy"])

    def test_final_institutional_runbook_tests_lineage_audit_and_external_review_contracts(self) -> None:
        runbook = build_incident_management_runbook_contract()
        tests = build_institutional_test_readiness_contract()
        data_lineage = validate_data_lineage_completeness_threshold(
            [
                {
                    "vendor": "sample_vendor",
                    "source_version": "snapshot_v1",
                    "license_or_contract": "contract_ref",
                    "as_of": "2026-05-09T14:00:00Z",
                    "received_at": "2026-05-09T14:00:05Z",
                    "effective_at": "2026-05-09T13:59:00Z",
                    "observed_at": "2026-05-09T14:00:00Z",
                    "no_lookahead": True,
                    "universe_id": "universe-1",
                    "as_of_date": "2026-05-09",
                    "active_symbols": ["AAPL"],
                    "delisted_symbols": ["OLD"],
                    "membership_source": "symbol_master_v1",
                    "survivorship_free": True,
                    "symbol": "AAPL",
                    "corporate_actions_policy": "adjusted_prices_with_raw_reference",
                    "symbol_change_policy": "symbol_master_maps_prior_and_current_symbols",
                    "adjustment_source": "corporate_actions_snapshot_v1",
                },
                {"vendor": "sample_vendor", "no_lookahead": False, "survivorship_free": False},
            ],
            threshold=0.75,
        )
        model_lineage = validate_model_lineage_completeness_threshold(
            [
                {
                    "model_id": "model:demo",
                    "model_version": "model_v1",
                    "training_data_version": "training_v1",
                    "feature_version": "features_v1",
                    "created_at": "2026-05-09T14:00:00Z",
                    "approval_id": "approval-1",
                    "feature_id": "feature:momentum",
                    "source_version": "snapshot_v1",
                    "generated_at": "2026-05-09T14:00:00Z",
                    "transformation_version": "transform_v1",
                    "owner": "research",
                    "benchmark_run_id": "benchmark-1",
                    "walk_forward_experiment_id": "wf-1",
                    "data_version": "data_v1",
                },
                {"model_id": "model:demo"},
            ],
            threshold=0.75,
        )
        audit = validate_audit_immutability_checks(
            [
                {
                    "event_id": "event-1",
                    "event_hash": "hash-1",
                    "previous_event_hash": "hash-0",
                    "append_only": True,
                    "tamper_evident": True,
                },
                {"event_id": "event-2", "append_only": False, "tamper_evident": False},
            ]
        )
        external_review = build_external_review_plan_contract()

        self.assertEqual(runbook["status"], "passed")
        self.assertTrue(runbook["incident_management_runbook_exists"])
        self.assertFalse(runbook["incident_runbook_can_change_execution_behavior"])
        self.assertEqual(tests["status"], "passed")
        self.assertTrue(tests["permission_enforcement_tests_exist"])
        self.assertTrue(tests["audit_immutability_tests_exist"])
        self.assertTrue(tests["lineage_completeness_tests_exist"])
        self.assertTrue(tests["environment_separation_tests_exist"])
        self.assertEqual(data_lineage["status"], "needs_evidence")
        self.assertEqual(data_lineage["coverage_rate"], 0.5)
        self.assertFalse(data_lineage["data_lineage_completeness_changes_execution_behavior"])
        self.assertEqual(model_lineage["status"], "needs_evidence")
        self.assertEqual(model_lineage["coverage_rate"], 0.5)
        self.assertFalse(model_lineage["model_lineage_completeness_changes_ranking_weights"])
        self.assertEqual(audit["status"], "needs_evidence")
        self.assertFalse(audit["audit_immutability_checks_pass"])
        self.assertFalse(audit["audit_checks_change_execution_behavior"])
        self.assertEqual(external_review["status"], "passed")
        self.assertTrue(external_review["institutional_grade_claim_blocked_until_review"])
        self.assertFalse(external_review["external_review_plan_changes_execution_behavior"])

    def test_ui_readiness_and_docs_index_contracts(self) -> None:
        ui = build_institutional_ui_readiness_contract()
        docs = build_institutional_docs_index()

        self.assertEqual(ui["status"], "passed")
        self.assertTrue(ui["lineage_inspector_available"])
        self.assertTrue(ui["permission_review_available"])
        self.assertTrue(ui["incident_and_release_reports_available"])
        self.assertIn("frontend/src/pages/DataCompletenessPage.jsx", ui["surfaces"]["lineage_inspector"])
        self.assertIn("frontend/src/pages/ResearchPromotionPage.jsx", ui["surfaces"]["permission_review"])
        self.assertIn("frontend/src/pages/AuditReplayPage.jsx", ui["surfaces"]["incident_and_release_reports"])
        self.assertFalse(ui["ui_changes_execution_behavior"])
        self.assertEqual(docs["status"], "passed")
        self.assertIn("#institutional-data-lineage-assumptions", docs["docs"]["data_lineage_guide"])
        self.assertIn("#model-lineage-guide", docs["docs"]["model_lineage_guide"])
        self.assertIn("#compliance-readiness-checklist", docs["docs"]["compliance_readiness_checklist"])
        self.assertIn("#incident-management-runbook", docs["docs"]["incident_management_runbook"])
        self.assertIn("#external-security-legal-and-compliance-review-plan", docs["docs"]["external_review_plan"])
        self.assertIn("not legal", docs["docs_claim_boundary"])

    def test_category_report_marks_this_pass_complete(self) -> None:
        report = build_category_upgrade_readiness_report()
        requirements = report["documented_scope_coverage"]["requirements"]
        discretionary_rows = [row for row in requirements if row["category_key"] == "top_discretionary_trader_comparison"]
        institutional_rows = [row for row in requirements if row["category_key"] == "institutional_quant_desk_or_enterprise_control_plane"]

        self.assertTrue(all(row["status"] == "complete" for row in discretionary_rows))
        self.assertTrue(all(row["status"] == "complete" for row in institutional_rows))
        self.assertTrue(report["documented_scope_coverage"]["all_documented_scope_added"])
        self.assertEqual(report["documented_scope_coverage"]["complete_count"], 158)

    def test_service_contains_no_execution_broker_risk_ai_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(institutional)
        forbidden_calls = (
            "place_order(",
            "submit_order(",
            "submit_live_order(",
            "route_order(",
            "set_broker_route(",
            "update_ranking_weight(",
            "update_risk_config(",
            "clear_kill_switch(",
            "enable_live_trading(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
