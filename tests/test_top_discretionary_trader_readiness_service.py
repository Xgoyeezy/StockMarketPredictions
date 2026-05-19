from __future__ import annotations

import inspect
import unittest

from backend.services import top_discretionary_trader_readiness_service as discretionary
from backend.services.category_upgrade_readiness_service import build_category_upgrade_readiness_report
from backend.services.top_discretionary_trader_readiness_service import (
    TOP_DISCRETIONARY_FIRST_TEN_REQUIREMENT_EVIDENCE,
    TOP_DISCRETIONARY_FINAL_FIVE_REQUIREMENT_EVIDENCE,
    TOP_DISCRETIONARY_REQUIREMENT_EVIDENCE,
    TOP_DISCRETIONARY_SECOND_TEN_REQUIREMENT_EVIDENCE,
    build_review_note_execution_separation_contract,
    build_shadow_mode_bypass_safety_contract,
    build_shadow_mode_docs_index,
    build_shadow_mode_ui_readiness_contract,
    build_shadow_no_order_authority_contract,
    build_top_discretionary_test_contracts,
    build_post_session_trader_review,
    get_top_discretionary_trader_readiness_summary,
    validate_closed_outcome_immutability,
    validate_direction_accuracy_comparison,
    validate_human_override_risk_context,
    validate_human_thesis_capture_contract,
    validate_override_quality_after_costs_and_risk,
    validate_pre_outcome_human_timestamp,
    validate_reward_comparison_execution_cost_fields,
    validate_same_opportunity_set,
    validate_system_net_decision_quality_after_costs_and_risk,
    validate_system_decision_match_contract,
    validate_false_positive_false_negative_reporting,
    validate_target_hit_rate_comparison,
)


def _record(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "human_thesis_id": "human-1",
        "created_at": "2026-05-06T14:00:00+00:00",
        "outcome_window_closed_at": "2026-05-06T15:00:00+00:00",
        "symbol": "AAPL",
        "linked_candidate_id": "candidate-1",
        "human_direction": "up",
        "human_confidence": 0.72,
        "human_target_pct": 0.50,
        "human_invalidation_level": 185.0,
        "human_horizon_minutes": 60,
        "human_reason": "VWAP reclaim predicts +0.5 percent within 60 minutes.",
        "system_prediction_id": "system-1",
        "system_direction": "down",
        "system_confidence": 0.68,
        "system_target_pct": 0.40,
        "system_invalidation_level": 185.5,
        "system_horizon_minutes": 60,
        "cost_model": "spread_slippage_v1",
        "actual_forward_return": 0.70,
        "baseline_forward_return": 0.10,
        "target_hit": True,
        "invalidation_hit": False,
        "max_adverse_excursion": 0.08,
        "time_to_target": 35,
        "human_reward_after_costs": 1.36,
        "system_reward_after_costs": -0.80,
        "spread": 0.01,
        "slippage": 0.02,
        "fill_assumption": "paper_fill_mid_after_spread_slippage",
        "risk_adjustment": 0.05,
        "blockers": ["none"],
        "risk_gate_state": "active",
        "kill_switch_state": "clear",
        "portfolio_exposure": 0.12,
        "record_digest": "digest-1",
        "immutable_after_outcome_close": True,
        "review_note": "Human thesis review note only.",
    }
    row.update(overrides)
    return row


class TopDiscretionaryTraderReadinessServiceTests(unittest.TestCase):
    def test_summary_covers_first_top_discretionary_requirements(self) -> None:
        summary = get_top_discretionary_trader_readiness_summary()

        self.assertEqual(summary["implemented_requirement_count"], len(TOP_DISCRETIONARY_REQUIREMENT_EVIDENCE))
        self.assertEqual(summary["requirement_evidence"], TOP_DISCRETIONARY_REQUIREMENT_EVIDENCE)
        for key in TOP_DISCRETIONARY_FIRST_TEN_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in TOP_DISCRETIONARY_SECOND_TEN_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in TOP_DISCRETIONARY_FINAL_FIVE_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        self.assertTrue(summary["read_only"])
        self.assertFalse(summary["changes_execution"])
        self.assertFalse(summary["changes_order_submission"])
        self.assertFalse(summary["changes_broker_routes"])
        self.assertFalse(summary["changes_risk_gates"])
        self.assertFalse(summary["clears_kill_switch"])
        self.assertFalse(summary["changes_ranking_weights"])
        self.assertFalse(summary["can_submit_orders"])
        self.assertFalse(summary["can_submit_live_orders"])
        self.assertFalse(summary["can_change_broker_routes"])
        self.assertFalse(summary["can_bypass_risk_gates"])
        self.assertFalse(summary["can_grant_ai_order_authority"])
        self.assertIn("not proof", summary["claim_boundary"])

    def test_same_opportunity_and_post_session_review_contracts(self) -> None:
        same_opportunity = validate_same_opportunity_set([_record(), _record(human_horizon_minutes=30)])
        review = build_post_session_trader_review([_record(), _record(human_direction="down", system_direction="up", actual_forward_return=-0.4)])

        self.assertEqual(same_opportunity["status"], "needs_evidence")
        self.assertFalse(same_opportunity["checks"][1]["same_horizon"])
        self.assertEqual(review["status"], "passed")
        self.assertIn("human", review["wins"])
        self.assertIn("missed_winner_count", review["misses"])
        self.assertIn("override_count", review["overrides"])
        self.assertIn("human_direction_accuracy", review["calibration"])
        self.assertFalse(review["can_submit_orders"])

    def test_human_capture_timestamp_and_system_match_contracts(self) -> None:
        capture = validate_human_thesis_capture_contract(_record(human_direction="bullish chart", human_target_pct=None))
        timestamp = validate_pre_outcome_human_timestamp([_record(), _record(created_at="2026-05-06T16:00:00+00:00")])
        system_match = validate_system_decision_match_contract([_record(), _record(cost_model="")])

        self.assertEqual(capture["status"], "needs_evidence")
        self.assertIn("human_direction", capture["missing_fields"])
        self.assertIn("human_target_pct", capture["missing_fields"])
        self.assertEqual(timestamp["status"], "needs_evidence")
        self.assertFalse(timestamp["checks"][1]["passed"])
        self.assertEqual(system_match["status"], "needs_evidence")
        self.assertIn("cost_model", system_match["missing_by_record"][1]["missing_fields"])

    def test_accuracy_target_override_and_risk_context_contracts(self) -> None:
        direction = validate_direction_accuracy_comparison([_record()])
        targets = validate_target_hit_rate_comparison([_record()])
        override_quality = validate_override_quality_after_costs_and_risk([_record(), _record(spread=None)])
        risk_context = validate_human_override_risk_context([_record(), _record(risk_gate_state="")])

        self.assertEqual(direction["status"], "passed")
        self.assertIsNotNone(direction["human_direction_accuracy"])
        self.assertIsNotNone(direction["system_direction_accuracy"])
        self.assertEqual(targets["status"], "passed")
        self.assertIsNotNone(targets["human_target_hit_rate"])
        self.assertIsNotNone(targets["system_target_hit_rate"])
        self.assertEqual(override_quality["status"], "needs_evidence")
        self.assertIn("spread", override_quality["checks"][1]["missing_fields"])
        self.assertFalse(override_quality["can_change_ranking_weights"])
        self.assertEqual(risk_context["status"], "needs_evidence")
        self.assertIn("risk_gate_state", risk_context["missing_by_record"][1]["missing_fields"])
        self.assertFalse(risk_context["risk_context_changes_risk_controls"])

    def test_shadow_safety_execution_cost_and_no_order_authority_contracts(self) -> None:
        bypass = build_shadow_mode_bypass_safety_contract()
        costs = validate_reward_comparison_execution_cost_fields([_record(), _record(fill_assumption=None)])
        no_orders = build_shadow_no_order_authority_contract()

        self.assertEqual(bypass["status"], "passed")
        self.assertTrue(bypass["blockers_visible"])
        self.assertTrue(bypass["risk_gates_authoritative"])
        self.assertTrue(bypass["kill_switches_authoritative"])
        self.assertFalse(bypass["shadow_mode_can_bypass_blockers"])
        self.assertFalse(bypass["shadow_mode_can_bypass_risk_gates"])
        self.assertFalse(bypass["shadow_mode_can_clear_kill_switches"])
        self.assertFalse(bypass["changes_risk_gates"])
        self.assertFalse(bypass["clears_kill_switch"])
        self.assertEqual(costs["status"], "needs_evidence")
        self.assertIn("fill_assumption", costs["missing_by_record"][1]["missing_fields"])
        self.assertFalse(costs["reward_comparison_changes_execution"])
        self.assertFalse(costs["changes_execution"])
        self.assertFalse(costs["changes_order_submission"])
        self.assertEqual(no_orders["status"], "passed")
        self.assertFalse(no_orders["shadow_mode_can_submit_orders"])
        self.assertFalse(no_orders["shadow_mode_can_route_orders"])
        self.assertFalse(no_orders["shadow_mode_can_change_broker_routes"])
        self.assertFalse(no_orders["changes_broker_routes"])

    def test_shadow_governance_ui_and_docs_contracts(self) -> None:
        immutability = validate_closed_outcome_immutability([_record(), _record(record_digest="", immutable_after_outcome_close=False)])
        review_notes = build_review_note_execution_separation_contract()
        ui = build_shadow_mode_ui_readiness_contract()
        docs = build_shadow_mode_docs_index()

        self.assertEqual(immutability["status"], "needs_evidence")
        self.assertTrue(immutability["checks"][0]["passed"])
        self.assertFalse(immutability["checks"][1]["passed"])
        self.assertFalse(immutability["immutability_changes_execution_behavior"])
        self.assertEqual(review_notes["status"], "passed")
        self.assertFalse(review_notes["review_notes_can_authorize_orders"])
        self.assertFalse(review_notes["review_notes_can_change_risk_gates"])
        self.assertEqual(ui["status"], "passed")
        self.assertTrue(ui["thesis_capture_ui_requires_required_fields"])
        self.assertTrue(ui["human_missed_winner_report_visible"])
        self.assertTrue(ui["system_missed_winner_report_visible"])
        self.assertTrue(ui["bias_diagnostics_visible"])
        self.assertFalse(ui["ui_changes_execution_behavior"])
        self.assertEqual(docs["status"], "passed")
        self.assertIn("#methodology", docs["docs"]["methodology"])
        self.assertIn("#override-quality-definitions", docs["docs"]["override_quality_definitions"])
        self.assertIn("not proven", docs["docs_claim_boundary"])

    def test_final_batch_test_contracts_and_proof_metrics(self) -> None:
        test_contracts = build_top_discretionary_test_contracts()
        decision_quality = validate_system_net_decision_quality_after_costs_and_risk(
            [_record(human_reward_after_costs=-0.4, system_reward_after_costs=1.2, system_direction="up", human_direction="down")]
        )
        weak_decision_quality = validate_system_net_decision_quality_after_costs_and_risk(
            [_record(human_reward_after_costs=1.5, system_reward_after_costs=0.7, system_direction="down", human_direction="up")]
        )
        false_rates = validate_false_positive_false_negative_reporting([_record(), _record(human_direction="down", system_direction="up", actual_forward_return=0.7)])

        self.assertEqual(test_contracts["status"], "passed")
        self.assertTrue(test_contracts["same_opportunity_matching_tests_exist"])
        self.assertTrue(test_contracts["pre_outcome_capture_tests_exist"])
        self.assertTrue(test_contracts["shadow_mode_no_order_authority_tests_exist"])
        self.assertFalse(test_contracts["can_submit_orders"])
        self.assertEqual(decision_quality["status"], "passed")
        self.assertTrue(decision_quality["comparisons"][0]["system_improves_or_beats_human"])
        self.assertIn("production proof still requires", decision_quality["claim_boundary"])
        self.assertEqual(weak_decision_quality["status"], "needs_evidence")
        self.assertEqual(false_rates["status"], "passed")
        self.assertIn("human_false_positive_rate", false_rates["metrics"])
        self.assertIn("system_false_negative_rate", false_rates["metrics"])

    def test_category_report_marks_this_pass_complete(self) -> None:
        report = build_category_upgrade_readiness_report()
        requirements = report["documented_scope_coverage"]["requirements"]
        small_fund_rows = [row for row in requirements if row["category_key"] == "small_prop_or_small_fund_research_stack"]
        discretionary_rows = [row for row in requirements if row["category_key"] == "top_discretionary_trader_comparison"]

        self.assertTrue(all(row["status"] == "complete" for row in small_fund_rows))
        self.assertTrue(all(row["status"] == "complete" for row in discretionary_rows))
        self.assertTrue(report["documented_scope_coverage"]["all_documented_scope_added"])
        self.assertEqual(report["documented_scope_coverage"]["complete_count"], report["documented_scope_coverage"]["requirement_count"])

    def test_service_contains_no_execution_broker_risk_ai_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(discretionary)
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
