from __future__ import annotations

import inspect
import unittest

from backend.services import small_fund_research_stack_readiness_service as small_fund
from backend.services.category_upgrade_readiness_service import build_category_upgrade_readiness_report
from backend.services.small_fund_research_stack_readiness_service import (
    SMALL_FUND_FIRST_FIVE_REQUIREMENT_EVIDENCE,
    SMALL_FUND_FINAL_REQUIREMENT_EVIDENCE,
    SMALL_FUND_REQUIREMENT_EVIDENCE,
    SMALL_FUND_SECOND_TEN_REQUIREMENT_EVIDENCE,
    SMALL_FUND_THIRD_TEN_REQUIREMENT_EVIDENCE,
    build_change_history_visibility_contract,
    build_execution_analytics_safety_contract,
    build_registry_visibility_contract,
    build_research_promotion_evidence_link_contract,
    build_promotion_risk_control_visibility,
    build_research_metadata_permission_contract,
    build_role_model_contract,
    build_review_queue_contract,
    build_small_fund_workflow_docs_index,
    build_team_review_queue_workflow,
    get_small_fund_research_stack_readiness_summary,
    validate_approval_snapshot_links,
    validate_approval_audit_trail,
    validate_audit_event_immutability_contract,
    validate_incident_and_release_records,
    validate_model_version_traceability,
    validate_paper_proven_promotion_gate,
    validate_portfolio_risk_coverage,
    validate_portfolio_risk_coverage_threshold,
    validate_promotion_status_execution_boundary,
    validate_rbac_permission_test_contract,
    validate_rbac_metadata_change_gate,
    validate_strategy_approval_traceability,
    validate_strategy_version_records,
    validate_transaction_cost_analysis_links,
)


class SmallFundResearchStackReadinessServiceTests(unittest.TestCase):
    def test_summary_covers_small_fund_requirements_added_so_far(self) -> None:
        summary = get_small_fund_research_stack_readiness_summary()

        self.assertEqual(summary["implemented_requirement_count"], len(SMALL_FUND_REQUIREMENT_EVIDENCE))
        self.assertEqual(summary["requirement_evidence"], SMALL_FUND_REQUIREMENT_EVIDENCE)
        for key in SMALL_FUND_FIRST_FIVE_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in SMALL_FUND_SECOND_TEN_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in SMALL_FUND_THIRD_TEN_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in SMALL_FUND_FINAL_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        self.assertTrue(all(summary["requirement_evidence"].values()))
        self.assertTrue(summary["read_only"])
        self.assertFalse(summary["can_submit_orders"])
        self.assertFalse(summary["can_submit_live_orders"])
        self.assertFalse(summary["can_grant_ai_order_authority"])
        self.assertFalse(summary["writes_execution_config"])
        self.assertIn("not institutional-grade", summary["claim_boundary"])

    def test_evidence_links_review_queue_versions_and_approval_snapshots(self) -> None:
        links = build_research_promotion_evidence_link_contract({"benchmark": "b1", "walk_forward": "wf1", "data": "d1", "execution": "e1", "risk": "r1"})
        queue = build_review_queue_contract()
        versions = validate_strategy_version_records(
            [{"strategy_version": "s1", "model_version": "m1", "feature_version": "f1", "configuration_version": "c1"}, {"strategy_version": "s2"}]
        )
        approvals = validate_approval_snapshot_links([{"approval_id": "a1", "evidence_snapshot_id": "snap1"}, {"approval_id": "a2"}])

        self.assertEqual(links["status"], "passed")
        self.assertEqual(set(queue["supported_actions"]), {"approve", "reject", "hold", "rollback"})
        self.assertTrue(queue["rollback_metadata_only"])
        self.assertEqual(versions["status"], "needs_evidence")
        self.assertEqual(versions["missing_by_record"][1]["missing_fields"], ["model_version", "feature_version", "configuration_version"])
        self.assertEqual(approvals["status"], "needs_evidence")
        self.assertEqual(approvals["missing_snapshot_link_indexes"], [1])

    def test_paper_proven_promotion_requires_benchmark_and_walk_forward(self) -> None:
        blocked = validate_paper_proven_promotion_gate({"target_status": "paper_proven", "benchmark_passed": True, "walk_forward_passed": False})
        passed = validate_paper_proven_promotion_gate({"target_status": "paper_proven", "benchmark_passed": True, "walk_forward_passed": True})

        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(passed["status"], "passed")
        self.assertFalse(passed["promotion_changes_execution_behavior"])
        self.assertFalse(passed["can_change_broker_routes"])

    def test_second_batch_permissions_risk_tca_and_execution_safety(self) -> None:
        permissions = build_research_metadata_permission_contract()
        risk = validate_portfolio_risk_coverage([{"exposure": 0.4, "concentration": 0.2, "liquidity": "normal", "drawdown": 0.03, "stress": "reviewed"}, {"exposure": 0.2}])
        risk_visibility = build_promotion_risk_control_visibility()
        tca = validate_transaction_cost_analysis_links(
            [
                {
                    "order_id": "o1",
                    "candidate_id": "c1",
                    "quote_id": "q1",
                    "spread": 0.01,
                    "slippage": 0.02,
                    "route": "broker_paper",
                    "receipt_id": "r1",
                    "fill_id": "f1",
                    "reconciliation_id": "rec1",
                },
                {"order_id": "o2"},
            ]
        )
        execution_safety = build_execution_analytics_safety_contract()

        self.assertTrue(permissions["unauthorized_status_changes_blocked"])
        self.assertFalse(permissions["status_changes_write_execution_config"])
        self.assertEqual(risk["status"], "needs_evidence")
        self.assertIn("concentration", risk["missing_by_record"][1]["missing_fields"])
        self.assertTrue(risk_visibility["active_or_blocking_visible"])
        self.assertFalse(risk_visibility["promotion_review_changes_risk_controls"])
        self.assertEqual(tca["status"], "needs_evidence")
        self.assertIn("candidate_id", tca["missing_by_record"][1]["missing_fields"])
        self.assertFalse(execution_safety["execution_analytics_can_submit_orders"])
        self.assertFalse(execution_safety["execution_analytics_can_alter_order_settings"])

    def test_portfolio_risk_coverage_threshold_is_read_only(self) -> None:
        passed = validate_portfolio_risk_coverage_threshold(
            [{"exposure": 0.4, "concentration": 0.2, "liquidity": "normal", "drawdown": 0.03, "stress": "reviewed"}],
            threshold=1.0,
        )
        weak = validate_portfolio_risk_coverage_threshold(
            [{"exposure": 0.4, "concentration": 0.2, "liquidity": "normal", "drawdown": 0.03, "stress": "reviewed"}, {"exposure": 0.2}],
            threshold=0.75,
        )

        self.assertEqual(passed["status"], "passed")
        self.assertEqual(passed["coverage_rate"], 1.0)
        self.assertFalse(passed["threshold_changes_risk_controls"])
        self.assertEqual(weak["status"], "needs_evidence")
        self.assertEqual(weak["coverage_rate"], 0.5)
        self.assertFalse(weak["can_bypass_risk_gates"])

    def test_second_batch_roles_rbac_audit_incidents_and_team_workflow(self) -> None:
        roles = build_role_model_contract()
        allowed = validate_rbac_metadata_change_gate({"role": "researcher", "action": "propose", "metadata_change": True})
        blocked = validate_rbac_metadata_change_gate({"role": "operator", "action": "approve", "metadata_change": True})
        audit = validate_approval_audit_trail(
            [{"actor": "researcher-1", "action": "propose", "timestamp": "2026-05-09T14:00:00Z", "entity_id": "strategy:demo", "previous_status": "research", "new_status": "candidate"}, {"actor": "admin-1"}]
        )
        incidents = validate_incident_and_release_records([{"incident_id": "i1", "release_validation_id": "r1"}, {"incident_id": "i2"}])
        workflow = build_team_review_queue_workflow()

        self.assertEqual(set(roles["role_keys"]), {"operator", "researcher", "risk_manager", "admin"})
        self.assertEqual(allowed["status"], "passed")
        self.assertEqual(blocked["status"], "blocked")
        self.assertFalse(allowed["writes_execution_config"])
        self.assertEqual(audit["status"], "needs_evidence")
        self.assertIn("action", audit["missing_by_record"][1]["missing_fields"])
        self.assertEqual(incidents["status"], "needs_evidence")
        self.assertEqual(incidents["missing_record_indexes"], [1])
        self.assertTrue(workflow["supports_team_workflow"])
        self.assertFalse(workflow["workflow_changes_execution_behavior"])

    def test_third_batch_registry_change_history_and_docs_are_cross_referenced(self) -> None:
        registry = build_registry_visibility_contract()
        change_history = build_change_history_visibility_contract()
        docs = build_small_fund_workflow_docs_index()

        self.assertEqual(registry["status"], "passed")
        self.assertTrue(registry["all_visible"])
        self.assertGreaterEqual(registry["registry_count"], 3)
        self.assertFalse(registry["visibility_changes_execution_behavior"])
        self.assertEqual(change_history["status"], "passed")
        self.assertIn("actor", change_history["change_history_fields"])
        self.assertIn("evidence_snapshot_id", change_history["change_history_fields"])
        self.assertFalse(change_history["change_history_changes_execution_behavior"])
        self.assertIn("role_model", docs["docs"])
        self.assertIn("#strategy-promotion-process", docs["docs"]["strategy_promotion_process"])
        self.assertIn("#incident-and-release-workflow", docs["docs"]["incident_workflow"])
        self.assertFalse(docs["can_submit_orders"])

    def test_third_batch_rbac_execution_boundary_audit_and_traceability(self) -> None:
        rbac_tests = validate_rbac_permission_test_contract()
        execution_boundary = validate_promotion_status_execution_boundary()
        audit = validate_audit_event_immutability_contract(
            [
                {"event_id": "e1", "event_hash": "h1", "previous_event_hash": "genesis", "append_only": True, "tamper_evident": True},
                {"event_id": "e2", "event_hash": "", "previous_event_hash": "h1", "append_only": True, "tamper_evident": False},
            ]
        )
        strategy_trace = validate_strategy_approval_traceability(
            [
                {
                    "strategy_id": "strategy:demo",
                    "approval_id": "a1",
                    "actor": "researcher-1",
                    "timestamp": "2026-05-09T14:00:00Z",
                    "evidence_snapshot_id": "snap1",
                    "decision": "propose",
                },
                {"strategy_id": "strategy:demo"},
            ]
        )
        model_trace = validate_model_version_traceability(
            [
                {
                    "model_id": "model:demo",
                    "model_version": "m1",
                    "feature_version": "f1",
                    "configuration_version": "c1",
                    "evidence_snapshot_id": "snap1",
                },
                {"model_id": "model:demo"},
            ]
        )

        self.assertEqual(rbac_tests["status"], "passed")
        self.assertEqual(rbac_tests["passed_by_check"], [True, True, True, True])
        self.assertEqual(execution_boundary["status"], "passed")
        self.assertFalse(execution_boundary["promotion_status_changes_execution_behavior"])
        self.assertFalse(execution_boundary["can_change_broker_routes"])
        self.assertEqual(audit["status"], "needs_evidence")
        self.assertEqual(audit["failed_indexes"], [1])
        self.assertIn("event_hash", audit["missing_by_record"][1]["missing_fields"])
        self.assertEqual(strategy_trace["status"], "needs_evidence")
        self.assertIn("approval_id", strategy_trace["missing_by_record"][1]["missing_fields"])
        self.assertEqual(model_trace["status"], "needs_evidence")
        self.assertIn("model_version", model_trace["missing_by_record"][1]["missing_fields"])

    def test_category_report_marks_next_ten_requirements_complete(self) -> None:
        report = build_category_upgrade_readiness_report()
        requirements = report["documented_scope_coverage"]["requirements"]
        solo_rows = [row for row in requirements if row["category_key"] == "solo_systematic_trader_platform"]
        small_fund_rows = [row for row in requirements if row["category_key"] == "small_prop_or_small_fund_research_stack"]

        self.assertTrue(all(row["status"] == "complete" for row in solo_rows[:30]))
        self.assertTrue(all(row["status"] == "complete" for row in small_fund_rows[:26]))
        self.assertTrue(report["documented_scope_coverage"]["all_documented_scope_added"])
        self.assertEqual(report["documented_scope_coverage"]["complete_count"], report["documented_scope_coverage"]["requirement_count"])

    def test_service_contains_no_execution_broker_risk_ai_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(small_fund)
        forbidden_calls = (
            "place_order(",
            "submit_order(",
            "submit_live_order(",
            "set_broker_route(",
            "update_ranking_weight(",
            "update_risk_config(",
            "set_risk_limit(",
            "set_risk_kill_switch(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
