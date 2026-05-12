from __future__ import annotations

import inspect
import unittest

from backend.services import hft_future_readiness_service as hft_future
from backend.services.category_upgrade_readiness_service import build_category_upgrade_readiness_report
from backend.services.hft_future_readiness_service import (
    HFT_FUTURE_REQUIREMENT_EVIDENCE,
    build_exchange_grade_kill_switch_future_requirements,
    build_hft_docs_index,
    build_hft_feasibility_data_requirements,
    build_hft_future_only_test_plan,
    build_hft_future_proof_metrics,
    build_hft_governance_prerequisites,
    build_hft_no_paper_evidence_proof_test_contract,
    build_hft_ui_claim_boundary,
    build_market_microstructure_research_plan,
    build_no_current_hft_metric_claim_contract,
    build_separate_hft_approval_contract,
    build_venue_analysis_plan,
    get_hft_future_readiness_summary,
)


class HftFutureReadinessServiceTests(unittest.TestCase):
    def test_summary_covers_all_future_only_hft_requirements(self) -> None:
        summary = get_hft_future_readiness_summary()

        self.assertEqual(summary["implemented_requirement_count"], len(HFT_FUTURE_REQUIREMENT_EVIDENCE))
        self.assertEqual(summary["requirement_evidence"], HFT_FUTURE_REQUIREMENT_EVIDENCE)
        for key in HFT_FUTURE_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        self.assertTrue(summary["future_only"])
        self.assertFalse(summary["current_hft_capability"])
        self.assertFalse(summary["current_direct_market_access"])
        self.assertFalse(summary["current_colocation"])
        self.assertFalse(summary["can_submit_orders"])
        self.assertFalse(summary["can_submit_live_orders"])
        self.assertIn("does not make the current platform HFT-capable", summary["claim_boundary"])

    def test_hft_data_microstructure_venue_and_kill_switch_contracts_are_future_only(self) -> None:
        data_requirements = build_hft_feasibility_data_requirements()
        microstructure = build_market_microstructure_research_plan()
        venue = build_venue_analysis_plan()
        kill_switch = build_exchange_grade_kill_switch_future_requirements()

        self.assertEqual(data_requirements["status"], "passed")
        self.assertIn("tick_data", data_requirements["requirements"])
        self.assertIn("order_book_data", data_requirements["requirements"])
        self.assertTrue(data_requirements["data_requirements_are_future_only"])
        self.assertEqual(microstructure["status"], "passed")
        self.assertIn("queue_dynamics", microstructure["topics"])
        self.assertFalse(microstructure["plan_changes_execution_behavior"])
        self.assertEqual(venue["status"], "passed")
        self.assertIn("fee_rebate_model", venue["topics"])
        self.assertFalse(venue["plan_changes_broker_routes"])
        self.assertEqual(kill_switch["status"], "passed")
        self.assertIn("latency_spike", kill_switch["requirements"])
        self.assertTrue(kill_switch["documented_for_future_study_only"])
        self.assertFalse(kill_switch["current_kill_switch_logic_changed"])

    def test_hft_governance_ui_docs_and_paper_proof_boundaries(self) -> None:
        metrics_claim = build_no_current_hft_metric_claim_contract()
        governance = build_hft_governance_prerequisites()
        approval = build_separate_hft_approval_contract()
        ui = build_hft_ui_claim_boundary()
        docs = build_hft_docs_index()
        no_paper_test = build_hft_no_paper_evidence_proof_test_contract()

        self.assertEqual(metrics_claim["status"], "passed")
        self.assertFalse(metrics_claim["current_evidence_control_plane_metrics_presented_as_hft_proof"])
        self.assertFalse(metrics_claim["paper_evidence_presented_as_hft_proof"])
        self.assertEqual(governance["status"], "passed")
        self.assertTrue(governance["legal_requirements_listed"])
        self.assertTrue(governance["capital_requirements_listed"])
        self.assertFalse(governance["approval_contract_can_enable_hft_build"])
        self.assertEqual(approval["status"], "passed")
        self.assertTrue(approval["separate_approval_is_required"])
        self.assertFalse(approval["approval_exists_now"])
        self.assertFalse(approval["current_product_can_start_hft_infrastructure_work"])
        self.assertEqual(ui["status"], "passed")
        self.assertFalse(ui["current_ui_implies_hft_capability"])
        self.assertTrue(ui["hft_mentions_are_feasibility_only"])
        self.assertEqual(docs["status"], "passed")
        self.assertTrue(docs["hft_feasibility_study_exists_before_implementation"])
        self.assertTrue(docs["hft_claims_to_avoid_language_exists"])
        self.assertEqual(no_paper_test["status"], "passed")
        self.assertTrue(no_paper_test["no_current_test_treats_paper_evidence_as_hft_proof"])
        self.assertFalse(no_paper_test["paper_evidence_can_support_hft_claims"])

    def test_hft_future_only_test_plan_and_proof_metrics_are_not_current_capability_claims(self) -> None:
        test_plan = build_hft_future_only_test_plan()
        metrics = build_hft_future_proof_metrics()

        self.assertEqual(test_plan["status"], "passed")
        self.assertTrue(test_plan["covers_latency_distribution"])
        self.assertTrue(test_plan["covers_order_book_reconstruction"])
        self.assertTrue(test_plan["covers_queue_model"])
        self.assertTrue(test_plan["covers_kill_switch_response"])
        self.assertTrue(test_plan["test_plan_is_future_only"])
        self.assertEqual(metrics["status"], "passed")
        self.assertIn("latency_distribution", metrics["metrics"])
        self.assertIn("market_data_latency", metrics["metrics"])
        self.assertIn("order_acknowledgement_latency", metrics["metrics"])
        self.assertIn("queue_position_accuracy", metrics["metrics"])
        self.assertIn("venue_routing_performance", metrics["metrics"])
        self.assertTrue(metrics["metrics_are_future_only"])
        self.assertFalse(metrics["current_product_meets_hft_metrics"])

    def test_default_category_report_marks_all_documented_scope_added_but_keeps_hft_future_only(self) -> None:
        report = build_category_upgrade_readiness_report()
        coverage = report["documented_scope_coverage"]
        hft_rows = [row for row in coverage["requirements"] if row["category_key"] == "hft_or_elite_execution_platform"]
        hft_category = next(row for row in report["categories"] if row["key"] == "hft_or_elite_execution_platform")

        self.assertGreaterEqual(coverage["requirement_count"], 158)
        self.assertEqual(coverage["complete_count"], coverage["requirement_count"])
        self.assertEqual(coverage["missing_count"], 0)
        self.assertTrue(coverage["all_documented_scope_added"])
        self.assertTrue(all(row["status"] == "complete" for row in hft_rows))
        self.assertEqual(hft_category["status"], "future_only")

    def test_service_contains_no_execution_broker_risk_ai_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(hft_future)
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
