from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from backend.services import solo_systematic_readiness_service as solo
from backend.services.category_upgrade_readiness_service import build_category_upgrade_readiness_report
from backend.services.solo_systematic_readiness_service import (
    SOLO_FIRST_FIVE_REQUIREMENT_EVIDENCE,
    SOLO_FOURTH_FIVE_REQUIREMENT_EVIDENCE,
    SOLO_REQUIREMENT_EVIDENCE,
    SOLO_SECOND_TEN_REQUIREMENT_EVIDENCE,
    SOLO_THIRD_TEN_REQUIREMENT_EVIDENCE,
    build_analytics_ranking_weight_safety_contract,
    build_manual_recommendation_separation,
    build_research_risk_gate_contract,
    build_research_visibility_contract,
    build_research_view_separation,
    build_solo_methodology_docs_index,
    build_solo_research_review_bundle,
    get_solo_systematic_readiness_summary,
    report_edge_before_after_costs,
    validate_baseline_forward_returns,
    validate_feature_lift_stability,
    validate_execution_quality_report_fields,
    validate_feature_snapshot_timestamps,
    validate_forecast_validation_report_fields,
    validate_forward_return_horizon_closure,
    validate_baseline_comparison_design,
    validate_experiment_versioning,
    validate_no_lookahead_records,
    validate_positive_after_cost_edge,
    validate_professional_benchmark_report_fields,
    validate_required_reward_fields,
    validate_risk_gate_context,
    validate_score_bucket_walk_forward_lift,
    validate_simulation_evidence_separation,
    validate_walk_forward_frozen_versions,
    validate_walk_forward_frozen_snapshot_test_contract,
    validate_walk_forward_pass_rate,
)


class SoloSystematicReadinessServiceTests(unittest.TestCase):
    def test_summary_covers_solo_requirements_added_so_far(self) -> None:
        summary = get_solo_systematic_readiness_summary()

        self.assertEqual(summary["implemented_requirement_count"], len(SOLO_REQUIREMENT_EVIDENCE))
        self.assertEqual(summary["requirement_evidence"], SOLO_REQUIREMENT_EVIDENCE)
        for key in SOLO_FIRST_FIVE_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in SOLO_SECOND_TEN_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in SOLO_THIRD_TEN_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in SOLO_FOURTH_FIVE_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        self.assertTrue(all(summary["requirement_evidence"].values()))
        self.assertTrue(summary["read_only"])
        self.assertFalse(summary["can_submit_orders"])
        self.assertFalse(summary["can_submit_live_orders"])
        self.assertIn("not proof of edge", summary["claim_boundary"])

    def test_combined_review_bundle_and_view_separation_are_read_only(self) -> None:
        bundle = build_solo_research_review_bundle()
        separation = build_research_view_separation()

        self.assertTrue(bundle["can_review_together"])
        self.assertEqual(set(bundle["sections"]), {"benchmark", "walk_forward", "data_completeness", "score_calibration", "execution_quality", "forecast_validation"})
        self.assertFalse(separation["manual_recommendations_change_config"])
        self.assertFalse(separation["analytics_change_ranking_weights"])

    def test_data_validators_report_reward_forward_and_baseline_requirements(self) -> None:
        reward = validate_required_reward_fields(
            [
                {
                    "symbol": "SPY",
                    "prediction_created_at": "2026-05-09T13:30:00Z",
                    "engine": "sample",
                    "setup_type": "sample",
                    "score": 81,
                    "actual_forward_return": 0.1,
                    "baseline_forward_return": 0.03,
                }
            ]
        )
        horizon = validate_forward_return_horizon_closure(
            [{"actual_forward_return": 0.1, "horizon_closed": True}, {"actual_forward_return": 0.2, "horizon_closed": False}]
        )
        baseline = validate_baseline_forward_returns(
            [{"benchmarkable": True, "baseline_forward_return": 0.03}, {"benchmarkable": True, "baseline_forward_return": None}]
        )

        self.assertEqual(reward["status"], "passed")
        self.assertEqual(reward["completion_rate"], 1.0)
        self.assertEqual(horizon["status"], "blocked")
        self.assertEqual(horizon["violation_count"], 1)
        self.assertEqual(baseline["coverage"], 0.5)
        self.assertEqual(baseline["status"], "needs_evidence")

    def test_second_batch_data_and_research_validators(self) -> None:
        feature = validate_feature_snapshot_timestamps([{"feature_snapshot": {"generated_at": "2026-05-09T13:30:00Z"}}, {}])
        simulation = validate_simulation_evidence_separation(
            [{"evidence_pool": "simulation_evidence", "counts_as_market_observed": False}, {"simulation_evidence": True, "counts_as_market_observed": True}]
        )
        bucket = validate_score_bucket_walk_forward_lift(
            [
                {"score_bucket": "80_to_100", "cost_adjusted_reward": 0.2, "walk_forward_status": "frozen"},
                {"score_bucket": "60_to_80", "cost_adjusted_reward": 0.05, "walk_forward_status": "frozen"},
            ]
        )
        benchmark = validate_professional_benchmark_report_fields({"baseline_relative_edge": 0.11, "score_bucket_lift": 0.07, "slippage_adjusted_reward": 0.03})
        walk_forward = validate_walk_forward_frozen_versions(
            [
                {
                    "ranking_formula_version": "rank_v1",
                    "reward_formula_version": "reward_v1",
                    "forecast_model_version": "forecast_v1",
                    "baseline_definition_version": "baseline_v1",
                    "feature_version": "features_v1",
                }
            ]
        )
        forecast = validate_forecast_validation_report_fields(
            {"direction_accuracy": 0.56, "path_error": 0.2, "timing_error": 2.0, "confidence_calibration": 0.08}
        )

        self.assertEqual(feature["status"], "needs_evidence")
        self.assertEqual(feature["missing_timestamp_indexes"], [1])
        self.assertEqual(simulation["status"], "blocked")
        self.assertEqual(simulation["violation_count"], 1)
        self.assertEqual(bucket["status"], "passed")
        self.assertGreater(bucket["lift"], 0)
        self.assertEqual(benchmark["status"], "passed")
        self.assertEqual(walk_forward["status"], "passed")
        self.assertEqual(forecast["status"], "passed")

    def test_second_batch_risk_execution_and_cost_contracts_are_read_only(self) -> None:
        risk_contract = build_research_risk_gate_contract()
        risk_context = validate_risk_gate_context([{"symbol": "SPY", "risk_gate_state": "clear"}, {"symbol": "QQQ"}])
        execution = validate_execution_quality_report_fields(
            [{"slippage": 1.0, "spread": 2.0, "fill_delay": 300, "route": "broker_paper"}]
        )
        edge = report_edge_before_after_costs([{"edge_before_costs": 0.12, "edge_after_costs": 0.04}])

        self.assertFalse(risk_contract["research_recommendations_can_bypass_risk_gates"])
        self.assertTrue(risk_contract["risk_gates_authoritative"])
        self.assertEqual(risk_context["status"], "needs_evidence")
        self.assertEqual(risk_context["missing_risk_gate_context_indexes"], [1])
        self.assertEqual(execution["status"], "passed")
        self.assertEqual(edge["status"], "reported")
        self.assertEqual(edge["edge_before_costs"], 0.12)
        self.assertEqual(edge["edge_after_costs"], 0.04)
        self.assertFalse(edge["can_submit_orders"])

    def test_third_batch_version_visibility_docs_and_test_contracts(self) -> None:
        versioning = validate_experiment_versioning(
            [
                {
                    "formula_version": "formula_v1",
                    "model_version": "model_v1",
                    "feature_version": "features_v1",
                    "baseline_version": "baseline_v1",
                    "universe_version": "universe_v1",
                },
                {"formula_version": "formula_v1"},
            ]
        )
        recommendations = build_manual_recommendation_separation()
        visibility = build_research_visibility_contract()
        docs = build_solo_methodology_docs_index()
        no_lookahead = validate_no_lookahead_records(
            [
                {"prediction_timestamp": 100, "feature_generated_timestamp": 90, "outcome_timestamp": 130},
                {"prediction_timestamp": 100, "feature_generated_timestamp": 110, "outcome_timestamp": 130},
            ]
        )
        baseline = validate_baseline_comparison_design({"same_universe": True, "same_session": True, "same_tradability_filter": True, "same_cost_assumptions": False, "baseline_forward_returns_available": True})

        self.assertEqual(versioning["status"], "needs_evidence")
        self.assertEqual(versioning["missing_by_record"][1]["missing_fields"], ["model_version", "feature_version", "baseline_version", "universe_version"])
        self.assertFalse(recommendations["manual_recommendations_change_config"])
        self.assertFalse(recommendations["manual_recommendations_change_execution"])
        self.assertTrue(visibility["score_bucket_separation_visible"])
        self.assertTrue(visibility["feature_attribution_visible"])
        self.assertTrue(visibility["out_of_sample_stability_visible"])
        self.assertTrue(Path(docs["docs"]["benchmark_methodology"].split("#", 1)[0]).exists())
        self.assertTrue(Path(docs["docs"]["walk_forward_methodology"].split("#", 1)[0]).exists())
        self.assertTrue(Path(docs["docs"]["cost_model_assumptions"].split("#", 1)[0]).exists())
        self.assertEqual(no_lookahead["status"], "blocked")
        self.assertEqual(no_lookahead["violating_record_indexes"], [1])
        self.assertEqual(baseline["status"], "needs_evidence")
        self.assertEqual(baseline["missing_checks"], ["same_cost_assumptions"])

    def test_fourth_batch_frozen_snapshot_ranking_edge_walk_forward_and_feature_stability(self) -> None:
        snapshot = validate_walk_forward_frozen_snapshot_test_contract(
            [{"status": "frozen", "snapshot_immutable": True, "versions_present": True, "evaluation_started_after_freeze": True}]
        )
        ranking = build_analytics_ranking_weight_safety_contract()
        edge = validate_positive_after_cost_edge({"baseline_relative_edge": 0.07, "edge_after_costs": 0.02})
        pass_rate = validate_walk_forward_pass_rate([{"verdict": "passed"}, {"verdict": "failed"}, {"verdict": "passed"}], threshold=0.6)
        feature_lift = validate_feature_lift_stability(
            [{"regime": "trend", "feature_lift": 0.04}, {"regime": "range", "feature_lift": 0.02}, {"regime": "volatile", "feature_lift": -0.01}],
            min_positive_regime_rate=0.6,
        )

        self.assertEqual(snapshot["status"], "passed")
        self.assertFalse(ranking["analytics_can_change_ranking_weights"])
        self.assertTrue(ranking["ranking_weight_changes_require_manual_config_workflow"])
        self.assertEqual(edge["status"], "passed")
        self.assertEqual(pass_rate["status"], "passed")
        self.assertEqual(pass_rate["pass_rate"], 0.666667)
        self.assertEqual(feature_lift["status"], "passed")
        self.assertEqual(feature_lift["positive_regime_rate"], 0.666667)

    def test_category_report_marks_next_ten_requirements_complete(self) -> None:
        report = build_category_upgrade_readiness_report()
        requirements = report["documented_scope_coverage"]["requirements"]
        retail_rows = [row for row in requirements if row["category_key"] == "retail_trading_bot"]
        solo_rows = [row for row in requirements if row["category_key"] == "solo_systematic_trader_platform"]

        self.assertTrue(all(row["status"] == "complete" for row in retail_rows[:25]))
        self.assertTrue(all(row["status"] == "complete" for row in solo_rows[:30]))
        self.assertTrue(report["documented_scope_coverage"]["all_documented_scope_added"])
        self.assertEqual(report["documented_scope_coverage"]["complete_count"], report["documented_scope_coverage"]["requirement_count"])

    def test_service_contains_no_execution_broker_risk_ai_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(solo)
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
    build_analytics_ranking_weight_safety_contract,
    validate_positive_after_cost_edge,
