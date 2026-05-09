from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from backend.services import retail_paper_operator_readiness_service as retail
from backend.services.category_upgrade_readiness_service import build_category_upgrade_readiness_report
from backend.services.retail_paper_operator_readiness_service import (
    FIRST_TEN_RETAIL_REQUIREMENT_EVIDENCE,
    RETAIL_REQUIREMENT_EVIDENCE,
    RETAIL_PROOF_METRIC_REQUIREMENT_EVIDENCE,
    SECOND_TEN_RETAIL_REQUIREMENT_EVIDENCE,
    build_broker_readiness_wizard,
    build_customer_safe_empty_states,
    build_daily_operator_summary,
    build_demo_evidence_policy,
    build_guided_onboarding_checklist,
    build_operator_docs_index,
    build_onboarding_state_transition_contract,
    build_operator_surface_label_contract,
    build_paper_mode_health_checklist,
    build_retail_research_language,
    build_strategy_explainers,
    build_support_export_governance_policy,
    build_user_facing_proof_labels,
    explain_paper_order_event,
    get_retail_paper_operator_readiness_summary,
    measure_retail_operator_readiness_metrics,
    validate_no_trade_record,
)


class RetailPaperOperatorReadinessServiceTests(unittest.TestCase):
    def test_summary_covers_retail_requirements_added_so_far(self) -> None:
        summary = get_retail_paper_operator_readiness_summary()

        self.assertEqual(summary["implemented_requirement_count"], 25)
        self.assertEqual(summary["requirement_evidence"], RETAIL_REQUIREMENT_EVIDENCE)
        for key in FIRST_TEN_RETAIL_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in SECOND_TEN_RETAIL_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        for key in RETAIL_PROOF_METRIC_REQUIREMENT_EVIDENCE:
            self.assertTrue(summary["requirement_evidence"][key])
        self.assertTrue(all(summary["requirement_evidence"].values()))
        self.assertTrue(summary["read_only"])
        self.assertFalse(summary["can_submit_orders"])
        self.assertFalse(summary["can_submit_live_orders"])

    def test_guided_onboarding_and_health_states_are_operator_safe(self) -> None:
        onboarding = build_guided_onboarding_checklist(paper_ready=True)
        health = build_paper_mode_health_checklist()
        states = {row["state"] for row in health["states"]}

        self.assertEqual(onboarding["status"], "paper_ready")
        self.assertTrue(all(step["requires_code_changes"] is False for step in onboarding["steps"]))
        self.assertEqual(states, {"ready", "watching", "blocked", "killed"})
        self.assertFalse(health["can_change_broker_routes"])
        self.assertFalse(health["can_clear_kill_switch"])

    def test_daily_summary_and_no_trade_contract_include_required_fields(self) -> None:
        no_trade = {
            "blocker": "stale_data",
            "desk": "Macro Trend Desk",
            "timestamp": "2026-05-09T14:00:00Z",
            "next_scan": "2026-05-09T14:05:00Z",
            "explanation": "The feed is stale, so the desk is waiting.",
        }
        validation = validate_no_trade_record(no_trade)
        summary = build_daily_operator_summary(no_trades=[no_trade], blockers=["stale_data"], missed_opportunities=[{"symbol": "SPY"}])

        self.assertTrue(validation["valid"])
        self.assertEqual(validation["missing_fields"], [])
        self.assertEqual(summary["no_trade_count"], 1)
        self.assertEqual(summary["blocker_count"], 1)
        self.assertEqual(summary["missed_opportunity_count"], 1)
        self.assertIn("Resolve blockers", summary["next_safe_action"])

    def test_demo_research_missed_opportunity_and_surface_contracts_preserve_claim_boundaries(self) -> None:
        demo = build_demo_evidence_policy()
        research = build_retail_research_language()
        surface = build_operator_surface_label_contract()

        self.assertTrue(demo["is_synthetic_sample"])
        self.assertFalse(demo["count_as_real_time_market_observed_evidence"])
        self.assertFalse(demo["merge_with_market_observed_evidence"])
        self.assertIn("proven alpha", research["claims_to_avoid"])
        self.assertTrue(surface["paper_first_label_visible"])
        self.assertTrue(surface["all_required_blockers_visible"])
        self.assertTrue(all(item["can_auto_clear"] is False for item in surface["visible_blockers"]))

    def test_category_report_marks_first_ten_retail_requirements_complete(self) -> None:
        report = build_category_upgrade_readiness_report()
        requirements = report["documented_scope_coverage"]["requirements"]
        retail_rows = [row for row in requirements if row["category_key"] == "retail_trading_bot"]
        first_twenty = retail_rows[:20]

        self.assertEqual(len(first_twenty), 20)
        self.assertTrue(all(row["status"] == "complete" for row in first_twenty))
        self.assertTrue(report["documented_scope_coverage"]["all_documented_scope_added"])
        self.assertEqual(report["documented_scope_coverage"]["complete_count"], 158)

    def test_broker_wizard_and_paper_order_explanations_are_read_only(self) -> None:
        wizard = build_broker_readiness_wizard(
            {
                "paper_mode_asserted": True,
                "credentials": {"api_key_present": True, "secret_key_present": True},
                "reconciliation": {"needs_review": False},
            }
        )
        fill = explain_paper_order_event({"status": "filled"})
        rejected = explain_paper_order_event({"status": "rejected", "reason": "market closed"})

        self.assertEqual(wizard["status"], "ready")
        self.assertFalse(wizard["can_change_broker_routes"])
        self.assertFalse(wizard["can_submit_orders"])
        self.assertIn("simulated execution evidence", fill["plain_language_explanation"])
        self.assertIn("market closed", rejected["plain_language_explanation"])
        self.assertTrue(rejected["paper_evidence_only"])

    def test_second_batch_ui_docs_and_support_contracts_exist(self) -> None:
        support = build_support_export_governance_policy()
        labels = build_user_facing_proof_labels()
        empty_states = build_customer_safe_empty_states()
        explainers = build_strategy_explainers()
        docs_index = build_operator_docs_index()
        transition_contract = build_onboarding_state_transition_contract()

        self.assertTrue(support["sanitized"])
        self.assertFalse(support["raw_logs_included"])
        self.assertTrue(labels["distinguishes_paper_from_live_money"])
        self.assertEqual(len(empty_states), 3)
        self.assertTrue(all(item["why_empty"] and item["next_safe_action"] for item in empty_states))
        self.assertEqual({item["desk_key"] for item in explainers}, {"macro_trend", "stat_arb", "equities_momentum", "event_driven", "options_volatility"})
        self.assertTrue(Path(docs_index["doc"]).exists())
        self.assertIn("first_session_checklist", docs_index["sections"])
        self.assertIn("no_trade_explanation_guide", docs_index["sections"])
        self.assertIn("broker_readiness_guide", docs_index["sections"])
        self.assertFalse(transition_contract["auto_clears_kill_switch"])
        self.assertFalse(transition_contract["changes_broker_routes"])
        self.assertFalse(transition_contract["submits_orders"])

    def test_retail_proof_metrics_are_measured_without_claims(self) -> None:
        metrics = measure_retail_operator_readiness_metrics(
            onboarding_sessions=[{"started_at": "2026-05-09T13:30:00Z", "paper_ready_at": "2026-05-09T13:35:30Z"}],
            no_trade_records=[
                {
                    "blocker": "setup_not_confirmed",
                    "desk": "Equities Momentum Desk",
                    "timestamp": "2026-05-09T13:30:00Z",
                    "next_scan": "2026-05-09T13:35:00Z",
                    "explanation": "No setup passed.",
                }
            ],
            paper_readiness_checks=[{"status": "ready"}, {"status": "blocked"}],
        )

        self.assertEqual(metrics["time_to_first_paper_ready_state_seconds"], 330.0)
        self.assertEqual(metrics["no_trade_explanation_coverage_rate"], 1.0)
        self.assertEqual(metrics["paper_readiness_pass_rate"], 0.5)
        self.assertFalse(metrics["metrics_are_claims"])

    def test_service_contains_no_execution_broker_risk_ai_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(retail)
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
