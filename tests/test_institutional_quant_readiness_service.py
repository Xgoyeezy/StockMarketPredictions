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

        self.assertEqual(summary["implemented_requirement_count"], len(INSTITUTIONAL_REQUIREMENT_EVIDENCE))
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
                "schema_version": "firm_grade_report_v1",
                "generated_at": "2026-05-09T14:00:00Z",
                "source_evidence_snapshot_ids": ["snapshot-1"],
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
        self.assertIn("source_evidence_snapshots", contract["required_sections"])
        self.assertIn("claim_boundaries", contract["required_sections"])
        self.assertIn("raw_broker_payloads", contract["excluded_fields"])
        self.assertTrue(contract["schema_version_required"])
        self.assertTrue(contract["source_evidence_snapshots_required"])
        self.assertFalse(contract["can_support_institutional_grade_claim_without_external_review"])
        self.assertFalse(contract["can_certify_compliance"])
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
                    "model_artifact_digest": "sha256:model-demo",
                    "training_window_start": "2025-01-01",
                    "training_window_end": "2026-01-01",
                    "validation_report_id": "validation-report-1",
                    "approval_scope": "research_only",
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
                    "input_snapshot_id": "snapshot-1",
                    "output_schema_version": "feature_schema_v1",
                    "no_lookahead": True,
                },
                {"feature_id": "feature:gap", "no_lookahead": False},
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
        self.assertIn("model_artifact_digest", model_lineage["missing_by_record"][1]["missing_fields"])
        self.assertIn("#model-version-traceability", model_lineage["documentation"])
        self.assertFalse(model_lineage["model_registry_changes_ranking_weights"])
        self.assertFalse(model_lineage["model_registry_changes_execution_behavior"])
        self.assertEqual(feature_lineage["status"], "needs_evidence")
        self.assertIn("source_version", feature_lineage["missing_by_record"][1]["missing_fields"])
        self.assertIn("input_snapshot_id", feature_lineage["missing_by_record"][1]["missing_fields"])
        self.assertIn("#feature-lineage-completeness", feature_lineage["documentation"])
        self.assertFalse(feature_lineage["feature_lineage_changes_ranking_weights"])
        self.assertFalse(feature_lineage["feature_lineage_changes_execution_behavior"])

    def test_benchmark_risk_execution_and_environment_contracts(self) -> None:
        benchmark_links = validate_benchmark_walk_forward_version_links(
            [
                {
                    "benchmark_run_id": "benchmark-1",
                    "walk_forward_experiment_id": "wf-1",
                    "data_version": "data_v1",
                    "model_version": "model_v1",
                    "feature_version": "features_v1",
                    "ranking_formula_version": "ranking_formula_v1",
                    "reward_formula_version": "reward_formula_v1",
                    "baseline_definition_version": "baseline_v1",
                    "frozen_snapshot_id": "wf-snapshot-1",
                    "frozen_before_outcome": True,
                },
                {"benchmark_run_id": "benchmark-2", "frozen_before_outcome": False},
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
                    "policy_version": "risk_policy_v1",
                    "last_tested_at": "2026-05-09T13:00:00Z",
                    "bypass_allowed": False,
                    "analytics_override_allowed": False,
                    "ai_override_allowed": False,
                },
                {"risk_control_id": "route_block", "authoritative": False, "analytics_override_allowed": True},
            ]
        )
        execution = validate_execution_report_lineage(
            [
                {
                    "candidate_id": "candidate-1",
                    "quote_id": "quote-1",
                    "route": "broker_paper",
                    "execution_lane": "paper",
                    "order_id": "order-1",
                    "receipt_id": "receipt-1",
                    "fill_id": "fill-1",
                    "reconciliation_id": "reconcile-1",
                    "reconciliation_status": "matched",
                    "spread_bps": 4.2,
                    "slippage": 0.02,
                    "fill_delay_ms": 450,
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
                    "execution_lane": "alpaca_paper",
                    "data_store": "research_evidence",
                    "runtime_storage_scope": "paper_research_runtime",
                    "config_namespace": "paper_research",
                    "secrets_scope": "paper_only",
                    "broker_route_scope": "paper_only",
                    "audit_scope": "research_audit",
                    "live_autonomy_enabled": False,
                    "broker_route_mutation_allowed": False,
                    "risk_gate_bypass_allowed": False,
                    "ranking_mutation_allowed": False,
                    "simulation_observed_mixing_allowed": False,
                },
                {
                    "environment": "live",
                    "execution_lane": "live",
                    "data_store": "research_evidence",
                    "runtime_storage_scope": "live_runtime",
                    "config_namespace": "live",
                    "secrets_scope": "live",
                    "broker_route_scope": "live",
                    "audit_scope": "live_audit",
                    "live_autonomy_enabled": True,
                    "broker_route_mutation_allowed": True,
                    "risk_gate_bypass_allowed": True,
                    "ranking_mutation_allowed": True,
                    "simulation_observed_mixing_allowed": True,
                },
            ]
        )

        self.assertEqual(benchmark_links["status"], "needs_evidence")
        self.assertIn("walk_forward_experiment_id", benchmark_links["missing_by_record"][1]["missing_fields"])
        self.assertIn("ranking_formula_version", benchmark_links["missing_by_record"][1]["missing_fields"])
        self.assertIn("#benchmark-and-walk-forward-traceability", benchmark_links["documentation"])
        self.assertFalse(benchmark_links["benchmark_walk_forward_links_change_ranking_weights"])
        self.assertFalse(benchmark_links["benchmark_walk_forward_links_change_execution_behavior"])
        self.assertEqual(portfolio["status"], "needs_evidence")
        self.assertIn("factor_exposure", portfolio["missing_by_record"][1]["missing_fields"])
        self.assertEqual(risk["status"], "needs_evidence")
        self.assertIn("policy_version", risk["missing_by_record"][1]["missing_fields"])
        self.assertEqual(risk["violations_by_record"][1]["violation_fields"], ["analytics_override_allowed"])
        self.assertTrue(risk["blocks_small_fund_claims_when_failed"])
        self.assertIn("#risk-control-auditability", risk["documentation"])
        self.assertFalse(risk["analytics_can_bypass_risk_controls"])
        self.assertFalse(risk["ai_can_bypass_risk_controls"])
        self.assertEqual(execution["status"], "needs_evidence")
        self.assertIn("order_id", execution["missing_by_record"][1]["missing_fields"])
        self.assertIn("candidate_id", execution["missing_by_record"][1]["missing_fields"])
        self.assertIn("#execution-report-lineage", execution["documentation"])
        self.assertFalse(execution["execution_lineage_changes_broker_routes"])
        self.assertFalse(execution["execution_lineage_changes_order_behavior"])
        self.assertFalse(execution["execution_lineage_submits_orders"])
        self.assertEqual(execution_authority["status"], "passed")
        self.assertFalse(execution_authority["execution_analytics_can_alter_broker_routes"])
        self.assertFalse(execution_authority["execution_analytics_can_alter_order_behavior"])
        self.assertEqual(environment["status"], "needs_evidence")
        self.assertEqual(environment["failed_indexes"], [1])
        self.assertEqual(
            set(environment["violations_by_record"][1]["violation_fields"]),
            {
                "live_autonomy_enabled",
                "broker_route_mutation_allowed",
                "risk_gate_bypass_allowed",
                "ranking_mutation_allowed",
                "simulation_observed_mixing_allowed",
            },
        )
        self.assertTrue(environment["blocks_institutional_claims_when_failed"])
        self.assertFalse(environment["environment_separation_can_change_broker_routes"])
        self.assertFalse(environment["environment_separation_can_bypass_risk_gates"])
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
                    "evidence_snapshot_id": "snapshot-1",
                    "audit_event_id": "audit-event-1",
                    "permission_source": "research_permission_policy_v1",
                    "decision_boundary": "research_metadata_only",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "can_change_broker_routes": False,
                    "can_bypass_risk_gates": False,
                    "can_clear_kill_switch": False,
                    "can_change_ranking_weights": False,
                    "can_grant_ai_order_authority": False,
                    "can_change_risk_limits": False,
                },
                {
                    "role": "operator",
                    "action": "approve",
                    "resource": "research_promotion_status",
                    "allowed": False,
                    "enforced": False,
                    "can_submit_orders": True,
                },
            ],
            threshold=0.75,
        )
        approvals = validate_approval_trace_completeness(
            [
                {
                    "approval_id": "approval-1",
                    "actor": "risk-manager-1",
                    "reviewer_role": "risk_manager",
                    "action": "hold",
                    "affected_entity": "research_promotion_status",
                    "strategy_id": "strategy:macro_trend",
                    "strategy_version": "strategy_v1",
                    "promotion_rule_version": "research_promotion_v1",
                    "timestamp": "2026-05-09T14:00:00Z",
                    "evidence_snapshot_id": "snapshot-1",
                    "audit_event_id": "audit-event-1",
                    "previous_status": "candidate",
                    "new_status": "hold",
                    "approval_scope": "research_metadata_only",
                    "decision_reason": "insufficient benchmark evidence",
                    "claim_boundary": "not live-trading approval",
                    "approves_live_trading": False,
                    "approves_order_submission": False,
                    "approves_broker_route_change": False,
                    "approves_risk_gate_bypass": False,
                    "approves_kill_switch_clear": False,
                    "approves_ai_order_authority": False,
                    "approves_ranking_weight_change": False,
                    "approves_risk_limit_change": False,
                    "mutates_immutable_forecast_records": False,
                    "edits_reward_inputs_after_outcome": False,
                },
                {"approval_id": "approval-2", "approves_live_trading": True},
            ],
            threshold=0.75,
        )
        incidents = validate_incident_response_records(
            [
                {
                    "incident_id": "incident-1",
                    "opened_at": "2026-05-09T14:00:00Z",
                    "severity": "medium",
                    "detection_source": "release_validation",
                    "first_visible_symptom": "missing audit event",
                    "owner": "operations",
                    "affected_entity": "research_promotion",
                    "affected_proof_surfaces": ["audit_trail", "research_promotion"],
                    "safety_state_impact": "none",
                    "status": "closed",
                    "containment_note": "held readiness claim",
                    "corrective_action": "documented rollback validation",
                    "verification_performed": "focused audit review",
                    "sanitization_status": "sanitized",
                    "closed_at": "2026-05-09T15:00:00Z",
                    "post_incident_review_note": "added required audit evidence",
                },
                {"incident_id": "incident-2"},
            ]
        )
        release_docs = build_release_validation_rollback_docs_contract()

        self.assertEqual(permissions["status"], "needs_evidence")
        self.assertEqual(permissions["coverage_rate"], 0.5)
        self.assertEqual(permissions["failed_indexes"], [1])
        self.assertIn("evidence_snapshot_id", permissions["checks"][1]["missing_fields"])
        self.assertEqual(permissions["violations_by_record"][1]["violation_fields"], ["can_submit_orders"])
        self.assertTrue(permissions["blocks_institutional_claims_when_failed"])
        self.assertIn("#permission-enforcement-coverage", permissions["documentation"])
        self.assertFalse(permissions["permission_enforcement_can_submit_orders"])
        self.assertFalse(permissions["permission_enforcement_can_change_broker_routes"])
        self.assertFalse(permissions["permission_enforcement_can_bypass_risk_gates"])
        self.assertFalse(permissions["permission_enforcement_can_change_ranking_weights"])
        self.assertFalse(permissions["permission_enforcement_changes_execution_behavior"])
        self.assertEqual(approvals["status"], "needs_evidence")
        self.assertIn("actor", approvals["checks"][1]["missing_fields"])
        self.assertIn("strategy_id", approvals["checks"][1]["missing_fields"])
        self.assertIn("strategy_version", approvals["checks"][1]["missing_fields"])
        self.assertIn("promotion_rule_version", approvals["checks"][1]["missing_fields"])
        self.assertEqual(approvals["failed_indexes"], [1])
        self.assertEqual(approvals["violations_by_record"][1]["violation_fields"], ["approves_live_trading"])
        self.assertTrue(approvals["blocks_small_fund_claims_when_failed"])
        self.assertTrue(approvals["blocks_institutional_claims_when_failed"])
        self.assertIn("#approval-trace-completeness", approvals["documentation"])
        self.assertFalse(approvals["approval_trace_can_approve_live_trading"])
        self.assertFalse(approvals["approval_trace_can_submit_orders"])
        self.assertFalse(approvals["approval_trace_can_change_broker_routes"])
        self.assertFalse(approvals["approval_trace_can_bypass_risk_gates"])
        self.assertFalse(approvals["approval_trace_can_change_ranking_weights"])
        self.assertFalse(approvals["approval_trace_changes_execution_behavior"])
        self.assertEqual(incidents["status"], "needs_evidence")
        self.assertIn("opened_at", incidents["missing_by_record"][1]["missing_fields"])
        self.assertIn("detection_source", incidents["missing_by_record"][1]["missing_fields"])
        self.assertIn("affected_proof_surfaces", incidents["missing_by_record"][1]["missing_fields"])
        self.assertIn("#incident-report-completeness", incidents["documentation"])
        self.assertTrue(incidents["blocks_small_fund_claims_when_failed"])
        self.assertFalse(incidents["incident_response_changes_execution_behavior"])
        self.assertFalse(incidents["incident_response_can_clear_kill_switch"])
        self.assertFalse(incidents["incident_response_can_bypass_risk_gates"])
        self.assertEqual(release_docs["status"], "passed")
        self.assertIn("#release-validation-and-rollback-controls", release_docs["documentation"])
        self.assertIn("affected_proof_surfaces", release_docs["required_release_validation_fields"])
        self.assertIn("sanitization_check", release_docs["required_release_validation_fields"])
        self.assertIn("failed_local_verification", release_docs["blocked_conditions"])
        self.assertFalse(release_docs["release_or_rollback_can_enable_live_autonomy"])
        self.assertFalse(release_docs["release_or_rollback_can_change_broker_routes"])
        self.assertFalse(release_docs["release_or_rollback_can_bypass_risk_gates"])
        self.assertFalse(release_docs["release_or_rollback_can_change_ranking_weights"])

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
                    "model_artifact_digest": "sha256:model-demo",
                    "training_window_start": "2025-01-01",
                    "training_window_end": "2026-01-01",
                    "validation_report_id": "validation-report-1",
                    "approval_scope": "research_only",
                    "feature_id": "feature:momentum",
                    "source_version": "snapshot_v1",
                    "generated_at": "2026-05-09T14:00:00Z",
                    "transformation_version": "transform_v1",
                    "owner": "research",
                    "input_snapshot_id": "snapshot-1",
                    "output_schema_version": "feature_schema_v1",
                    "no_lookahead": True,
                    "benchmark_run_id": "benchmark-1",
                    "walk_forward_experiment_id": "wf-1",
                    "data_version": "data_v1",
                    "ranking_formula_version": "ranking_formula_v1",
                    "reward_formula_version": "reward_formula_v1",
                    "baseline_definition_version": "baseline_v1",
                    "frozen_snapshot_id": "wf-snapshot-1",
                    "frozen_before_outcome": True,
                },
                {"model_id": "model:demo"},
            ],
            threshold=0.75,
        )
        audit = validate_audit_immutability_checks(
            [
                {
                    "event_id": "event-1",
                    "event_type": "research_status_review",
                    "actor": "risk-manager-1",
                    "affected_entity": "research_promotion_status",
                    "timestamp": "2026-05-09T14:00:00Z",
                    "evidence_snapshot_id": "snapshot-1",
                    "source_report": "institutional_readiness",
                    "event_hash": "hash-1",
                    "previous_event_hash": "hash-0",
                    "append_only": True,
                    "tamper_evident": True,
                    "sanitization_status": "sanitized",
                    "safety_boundary": "research_metadata_only",
                    "submits_orders": False,
                    "changes_execution_behavior": False,
                    "changes_broker_routes": False,
                    "bypasses_risk_gates": False,
                    "clears_kill_switches": False,
                    "grants_ai_order_authority": False,
                    "changes_ranking_weights": False,
                    "changes_risk_limits": False,
                    "contains_secrets": False,
                    "contains_account_identifiers": False,
                    "contains_raw_logs": False,
                    "contains_raw_local_paths": False,
                },
                {"event_id": "event-2", "append_only": False, "tamper_evident": False, "contains_raw_logs": True},
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
        self.assertEqual(audit["failed_indexes"], [1])
        self.assertIn("event_type", audit["missing_by_record"][1]["missing_fields"])
        self.assertEqual(audit["violations_by_record"][1]["violation_fields"], ["contains_raw_logs"])
        self.assertTrue(audit["blocks_small_fund_claims_when_failed"])
        self.assertTrue(audit["blocks_institutional_claims_when_failed"])
        self.assertIn("#audit-event-completeness", audit["documentation"])
        self.assertFalse(audit["audit_event_completeness_changes_execution_behavior"])
        self.assertFalse(audit["audit_checks_can_submit_orders"])
        self.assertFalse(audit["audit_checks_can_change_broker_routes"])
        self.assertFalse(audit["audit_checks_can_bypass_risk_gates"])
        self.assertFalse(audit["audit_checks_can_change_ranking_weights"])
        self.assertFalse(audit["audit_checks_change_execution_behavior"])
        self.assertEqual(external_review["status"], "passed")
        self.assertTrue(external_review["institutional_grade_claim_blocked_until_review"])
        self.assertFalse(external_review["external_review_plan_changes_execution_behavior"])
        self.assertTrue(external_review["qualified_reviewer_required"])
        self.assertTrue(external_review["sanitized_firm_grade_report_required"])
        self.assertIn("security_review_scope", external_review["evidence_packet_fields"])
        self.assertIn("sanitization_check", external_review["evidence_packet_fields"])
        self.assertIn("raw_logs", external_review["excluded_from_packet"])
        self.assertFalse(external_review["can_certify_compliance"])
        self.assertFalse(external_review["can_approve_live_trading"])
        self.assertFalse(external_review["can_change_broker_routes"])

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
        self.assertEqual(report["documented_scope_coverage"]["complete_count"], report["documented_scope_coverage"]["requirement_count"])

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
