from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.routers import shadow_mode as shadow_router
from backend.services import human_system_shadow_mode as shadow
from backend.services.human_system_shadow_mode import (
    build_shadow_comparison_row,
    build_shadow_mode_report,
    build_shadow_mode_proof_summary,
    compute_shadow_reward,
    create_human_thesis,
)


def _human(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "human_thesis_id": "human-1",
        "created_at": "2026-05-06T14:00:00Z",
        "symbol": "AAPL",
        "linked_candidate_id": "candidate-1",
        "human_direction": "up",
        "human_confidence": 0.72,
        "human_target_pct": 0.50,
        "human_invalidation_level": 185.0,
        "human_horizon_minutes": 60,
        "human_reason": "VWAP reclaim predicts +0.5 percent within 60 minutes.",
        "setup_type": "vwap_reclaim",
        "engine": "intraday_momentum",
        "regime": "trend_day",
        "system_prediction_id": "system-1",
        "system_direction": "up",
        "system_confidence": 0.68,
        "system_target_pct": 0.40,
        "system_invalidation_level": 185.5,
        "system_horizon_minutes": 60,
        "actual_forward_return": 0.70,
        "baseline_forward_return": 0.10,
        "target_hit": True,
        "invalidation_hit": False,
        "max_adverse_excursion": 0.08,
        "time_to_target": 35,
    }
    payload.update(overrides)
    return payload


class HumanSystemShadowModeTests(unittest.TestCase):
    def test_create_human_thesis_writes_research_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "human_theses.json"
            result = create_human_thesis(_human(), store_path=store)

            self.assertEqual(result["status"], "created")
            self.assertTrue(result["research_only"])
            self.assertFalse(result["can_submit_orders"])
            self.assertTrue(store.exists())
            report = build_shadow_mode_report(store_path=store)
            self.assertEqual(report["summary"]["record_count"], 1)

    def test_missing_required_human_fields(self) -> None:
        row = build_shadow_comparison_row(_human(symbol="", human_direction="bullish chart", human_target_pct=None), system_records=[])

        self.assertFalse(row["human_rewardable"])
        self.assertIn("symbol", row["missing_fields"])
        self.assertIn("human_direction", row["missing_fields"])
        self.assertIn("human_target_pct", row["missing_fields"])

    def test_rewardable_human_thesis(self) -> None:
        row = build_shadow_comparison_row(_human(), system_records=[])

        self.assertTrue(row["human_rewardable"])
        self.assertGreater(row["human_reward"], 1.0)
        self.assertTrue(row["human_direction_correct"])
        self.assertTrue(row["target_hit"])

    def test_non_rewardable_human_thesis_without_outcomes(self) -> None:
        row = build_shadow_comparison_row(_human(actual_forward_return=None, baseline_forward_return=None), system_records=[])

        self.assertFalse(row["human_rewardable"])
        self.assertIn("actual_forward_return", row["missing_fields"])
        self.assertIn("baseline_forward_return", row["missing_fields"])

    def test_human_wins_fixture(self) -> None:
        row = build_shadow_comparison_row(_human(system_direction="down", system_confidence=0.80), system_records=[])

        self.assertEqual(row["winner"], "human")
        self.assertGreater(row["human_reward"], row["system_reward"])

    def test_system_wins_fixture(self) -> None:
        row = build_shadow_comparison_row(_human(human_direction="down", human_confidence=0.82, system_direction="up", system_confidence=0.70), system_records=[])

        self.assertEqual(row["winner"], "system")
        self.assertGreater(row["system_reward"], row["human_reward"])

    def test_both_wrong_fixture(self) -> None:
        report = build_shadow_mode_report(
            records=[_human(human_direction="up", system_direction="up", actual_forward_return=-0.60, target_hit=False, invalidation_hit=True)],
            generated_at="2026-05-06T00:00:00Z",
        )

        row = report["records"][0]
        self.assertFalse(row["human_direction_correct"])
        self.assertFalse(row["system_direction_correct"])
        self.assertLess(row["human_reward"], 0)
        self.assertLess(row["system_reward"], 0)

    def test_target_and_invalidation_components(self) -> None:
        target_reward = compute_shadow_reward(
            direction="up",
            confidence=0.7,
            target_pct=0.5,
            actual_forward_return=0.7,
            baseline_forward_return=0.1,
            hit_target=True,
            hit_invalidation=False,
            horizon_minutes=60,
            time_to_target=30,
        )
        invalidated_reward = compute_shadow_reward(
            direction="up",
            confidence=0.7,
            target_pct=0.5,
            actual_forward_return=-0.4,
            baseline_forward_return=0.1,
            hit_target=False,
            hit_invalidation=True,
            horizon_minutes=60,
            time_to_target=None,
        )

        self.assertTrue(target_reward["target_hit"])
        self.assertFalse(target_reward["invalidation_hit"])
        self.assertTrue(invalidated_reward["invalidation_hit"])
        self.assertGreater(target_reward["total_reward"], invalidated_reward["total_reward"])

    def test_bias_diagnostic_detection(self) -> None:
        report = build_shadow_mode_report(
            records=[
                _human(
                    human_direction="down",
                    human_confidence=0.90,
                    system_direction="up",
                    system_confidence=0.90,
                    actual_forward_return=0.80,
                    max_adverse_excursion=0.75,
                    blockers=["wide_spread"],
                    system_candidate_reward=2.0,
                )
            ],
            generated_at="2026-05-06T00:00:00Z",
        )

        biases = report["aggregations"]["bias_diagnostics"]["counts"]
        self.assertGreaterEqual(biases.get("high_confidence_wrong_calls", 0), 1)
        self.assertGreaterEqual(biases.get("chasing_extended_moves", 0), 1)
        self.assertGreaterEqual(biases.get("overriding_strong_system_evidence", 0), 1)

    def test_shadow_proof_ready_with_same_opportunity_after_cost_system_improvement(self) -> None:
        rows = [
            _human(
                human_thesis_id=f"human-{index}",
                system_prediction_id=f"system-{index}",
                linked_candidate_id=f"candidate-{index}",
                human_direction="down",
                system_direction="up",
                system_confidence=0.82,
                outcome_window_closed_at="2026-05-06T15:00:00Z",
                cost_model="spread_slippage_v1",
                human_reward_after_costs=-0.35,
                system_reward_after_costs=1.10,
                spread=0.01,
                slippage=0.02,
                fill_assumption="paper_fill_mid_after_spread_slippage",
                risk_adjustment=0.05,
                risk_gate_state="active",
                kill_switch_state="clear",
                portfolio_exposure=0.12,
                record_digest=f"digest-{index}",
                immutable_after_outcome_close=True,
            )
            for index in range(3)
        ]
        report = build_shadow_mode_report(records=rows, generated_at="2026-05-06T00:00:00Z")
        proof = build_shadow_mode_proof_summary(report["records"], report["aggregations"])

        self.assertTrue(proof["proof_ready"])
        self.assertEqual(proof["status"], "ready_for_human_review")
        self.assertEqual(report["summary"]["shadow_requirements_passed"], 10)
        self.assertGreaterEqual(report["summary"]["system_decision_quality_delta"], 0.0)
        self.assertTrue(all(row["research_only"] for row in proof["requirements"]))
        self.assertFalse(any(row["changes_execution"] for row in proof["requirements"]))
        self.assertFalse(any(row["changes_risk_gates"] for row in proof["requirements"]))

    def test_shadow_proof_blocks_missing_context_and_human_outperformance_claims(self) -> None:
        report = build_shadow_mode_report(
            records=[
                _human(
                    system_direction="down",
                    system_prediction_id="",
                    linked_candidate_id="",
                    outcome_window_closed_at=None,
                    human_reward_after_costs=1.4,
                    system_reward_after_costs=0.3,
                )
            ],
            generated_at="2026-05-06T00:00:00Z",
        )

        failed_keys = {row["key"] for row in report["proof_summary"]["requirements"] if not row["passed"]}
        self.assertIn("same_opportunity_sample", failed_keys)
        self.assertIn("same_opportunity_linkage", failed_keys)
        self.assertIn("cost_risk_context", failed_keys)
        self.assertIn("system_after_cost_improvement", failed_keys)
        self.assertFalse(report["proof_summary"]["proof_ready"])
        self.assertFalse(report["can_submit_orders"])
        self.assertFalse(report["writes_ranking_config"])

    def test_api_response_shape(self) -> None:
        client = TestClient(create_app())
        original_summary = shadow_router.get_shadow_mode_summary
        original_records = shadow_router.get_shadow_mode_records
        original_comparisons = shadow_router.get_shadow_mode_comparisons
        original_bias = shadow_router.get_shadow_mode_bias
        original_create = shadow_router.create_human_thesis
        report = build_shadow_mode_report(records=[_human()], generated_at="2026-05-06T00:00:00Z")
        shadow_router.get_shadow_mode_summary = lambda db=None, current_user=None: report
        shadow_router.get_shadow_mode_records = lambda db=None, current_user=None: report
        shadow_router.get_shadow_mode_comparisons = lambda db=None, current_user=None: report
        shadow_router.get_shadow_mode_bias = lambda db=None, current_user=None: {**report, "records": report["aggregations"]["bias_diagnostics"]["items"]}
        shadow_router.create_human_thesis = lambda payload, current_user=None: {"status": "created", "generated_at": "2026-05-06T00:00:00Z", "research_only": True, "record": report["records"][0], "summary": {}, "warnings": [], "missing_fields": {}, "safety_notes": list(shadow.SAFETY_NOTES), **shadow.SAFETY_FLAGS}
        try:
            for path in (
                "/api/shadow-mode/summary",
                "/api/shadow-mode/records",
                "/api/shadow-mode/comparisons",
                "/api/shadow-mode/bias",
            ):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["ok"])
                data = payload["data"]
                self.assertTrue(data["research_only"])
                self.assertFalse(data["can_submit_orders"])
                self.assertFalse(data["can_submit_live_orders"])
                self.assertIn("proof_summary", data)
                self.assertIn("shadow_proof_ready", data["summary"])
                self.assertIn("safety_notes", data)
                self.assertIn("Does not place orders.", data["safety_notes"])
            post = client.post("/api/shadow-mode/human-thesis", json=_human())
            self.assertEqual(post.status_code, 200)
            self.assertTrue(post.json()["data"]["research_only"])
        finally:
            shadow_router.get_shadow_mode_summary = original_summary
            shadow_router.get_shadow_mode_records = original_records
            shadow_router.get_shadow_mode_comparisons = original_comparisons
            shadow_router.get_shadow_mode_bias = original_bias
            shadow_router.create_human_thesis = original_create

    def test_service_contains_no_execution_broker_risk_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(shadow)
        forbidden_calls = (
            "place_order(",
            "submit_order(",
            "clear_kill_switch(",
            "set_broker_route(",
            "set_risk_kill_switch(",
            "update_ranking_weight(",
            "update_risk_config(",
            "enable_live_trading(",
            "route_order(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
