from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.services.evidence_reward_engine import (
    build_evidence_reward_report,
    compute_baseline_relative_score,
    compute_blocker_correctness_bonus,
    compute_drawdown_penalty,
    compute_forward_return_score,
    compute_missed_move_penalty,
    compute_risk_violation_penalty,
    compute_slippage_penalty,
    compute_spread_penalty,
    compute_total_reward,
)


class EvidenceRewardEngineServiceTests(unittest.TestCase):
    def _empty_frame(self) -> pd.DataFrame:
        return pd.DataFrame()

    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")

    def _build_report(self, root: Path, closed_trades: pd.DataFrame | None = None) -> dict:
        return build_evidence_reward_report(
            tenant_slug="test-tenant",
            root=root,
            open_trades=self._empty_frame(),
            closed_trades=closed_trades if closed_trades is not None else self._empty_frame(),
            pending_orders=self._empty_frame(),
        )

    def _valid_prediction(self, **overrides) -> dict:
        row = {
            "candidate_lifecycle_id": "cand-valid",
            "ticker": "AAPL",
            "desk_key": "intraday_momentum",
            "scan_time": "2026-05-05T14:00:00+00:00",
            "prediction_created_at": "2026-05-05T14:00:00+00:00",
            "actual_forward_return_observed_at": "2026-05-05T15:00:00+00:00",
            "predicted_direction": "bullish",
            "prediction_horizon_minutes": 60,
            "predicted_target_pct": 0.6,
            "invalidation_level": 198.5,
            "confidence": 0.72,
            "actual_forward_return": 0.8,
            "baseline_forward_return": 0.1,
            "max_adverse_excursion": -0.1,
            "hit_target": True,
            "hit_invalidation": False,
            "time_to_target_minutes": 30,
            "opportunity_type": "vwap_reclaim",
            "opportunity_score": 91,
            "regime": "trend_day",
            "final_state": "eligible",
            "ai_verdict": "approve_evidence",
        }
        row.update(overrides)
        return row

    def test_valid_pre_move_prediction_earns_reward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl", [self._valid_prediction()])
            report = self._build_report(root)

        row = report["candidate_rows"][0]
        self.assertEqual(report["summary"]["rewardable_candidate_count"], 1)
        self.assertEqual(report["summary"]["rewardable_count"], 1)
        self.assertEqual(row["prediction_contract_status"], "rewardable")
        self.assertEqual(row["reward_components"]["forward_return_score"], 0.8)
        self.assertEqual(row["reward_components"]["baseline_relative_score"], 0.7)
        self.assertEqual(row["reward_components"]["drawdown_penalty"], 0.05)
        self.assertEqual(row["component_scores"], row["reward_components"])
        self.assertEqual(row["total_reward"], 1.45)
        self.assertEqual(row["reason"], "rewardable_prediction_contract")
        self.assertIn("safety_notes", report)
        self.assertIn("records", report)
        self.assertIn("aggregations", report)
        self.assertTrue(report["research_only"])
        self.assertFalse(report["can_submit_orders"])
        self.assertFalse(report["can_submit_live_orders"])
        self.assertEqual(report["mutation"], "none")

    def test_reward_component_functions_are_transparent(self) -> None:
        components = {
            "forward_return_score": compute_forward_return_score(0.8, "bullish"),
            "baseline_relative_score": compute_baseline_relative_score(0.8, 0.1, "bullish"),
            "drawdown_penalty": compute_drawdown_penalty(-0.2),
            "slippage_penalty": compute_slippage_penalty(5),
            "spread_penalty": compute_spread_penalty(25),
            "risk_violation_penalty": compute_risk_violation_penalty(True),
            "blocker_correctness_bonus": compute_blocker_correctness_bonus(blocked=True, actual_forward_return=-0.3, predicted_direction="bullish"),
            "missed_move_penalty": compute_missed_move_penalty(blocked=True, actual_forward_return=0.4, predicted_direction="bullish"),
        }

        self.assertEqual(components["forward_return_score"], 0.8)
        self.assertEqual(components["baseline_relative_score"], 0.7)
        self.assertEqual(components["drawdown_penalty"], 0.1)
        self.assertEqual(components["slippage_penalty"], 0.05)
        self.assertEqual(components["spread_penalty"], 0.05)
        self.assertEqual(components["risk_violation_penalty"], 1.0)
        self.assertEqual(components["blocker_correctness_bonus"], 0.25)
        self.assertEqual(components["missed_move_penalty"], 0.5)
        self.assertEqual(compute_total_reward(components), 0.05)

    def test_visual_setup_only_row_is_visible_but_not_rewardable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [
                    {
                        "candidate_lifecycle_id": "cand-label",
                        "ticker": "MSFT",
                        "opportunity_type": "bullish_chart",
                        "actual_forward_return": 1.2,
                        "final_state": "eligible",
                    }
                ],
            )
            report = self._build_report(root)

        self.assertEqual(report["summary"]["candidate_count"], 1)
        self.assertEqual(report["summary"]["rewardable_candidate_count"], 0)
        self.assertEqual(report["candidate_rows"][0]["prediction_contract_status"], "incomplete")
        self.assertIn("predicted_direction", report["candidate_rows"][0]["missing_prediction_fields"])

    def test_missing_required_contract_fields_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = self._valid_prediction(predicted_direction=None, prediction_horizon_minutes=None, predicted_target_pct=None, invalidation_level=None, confidence=None)
            self._write_jsonl(root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl", [row])
            report = self._build_report(root)

        missing = set(report["candidate_rows"][0]["missing_prediction_fields"])
        self.assertTrue({"predicted_direction", "prediction_horizon_minutes", "predicted_target_pct", "invalidation_level", "confidence"}.issubset(missing))
        self.assertEqual(report["summary"]["incomplete_prediction_count"], 1)

    def test_baseline_missing_excludes_reward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [self._valid_prediction(baseline_forward_return=None)],
            )
            report = self._build_report(root)

        self.assertEqual(report["candidate_rows"][0]["prediction_contract_status"], "baseline_missing")
        self.assertEqual(report["summary"]["baseline_missing_count"], 1)
        self.assertIsNone(report["summary"]["avg_reward"])

    def test_post_move_prediction_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [self._valid_prediction(prediction_created_at="2026-05-05T15:30:00+00:00")],
            )
            report = self._build_report(root)

        self.assertEqual(report["candidate_rows"][0]["prediction_contract_status"], "post_move")
        self.assertEqual(report["summary"]["post_move_excluded_count"], 1)

    def test_late_correct_prediction_is_penalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [self._valid_prediction(hit_target=True, time_to_target_minutes=90)],
            )
            report = self._build_report(root)

        components = report["candidate_rows"][0]["reward_components"]
        self.assertEqual(components["late_timing_penalty"], 0.35)
        self.assertEqual(components["timing_bonus"], 0.0)

    def test_target_hit_low_adverse_is_rewarded_and_invalidation_hit_penalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [
                    self._valid_prediction(candidate_lifecycle_id="target-hit"),
                    self._valid_prediction(candidate_lifecycle_id="invalidated", hit_invalidation=True, max_adverse_excursion=-1.0),
                ],
            )
            report = self._build_report(root)

        rows = {row["candidate_lifecycle_id"]: row for row in report["candidate_rows"]}
        self.assertGreater(rows["target-hit"]["reward_components"]["low_adverse_excursion_bonus"], 0)
        self.assertEqual(rows["invalidated"]["reward_components"]["invalidation_hit_penalty"], 0.75)
        self.assertLess(rows["invalidated"]["total_reward"], rows["target-hit"]["total_reward"])

    def test_high_confidence_wrong_and_baseline_underperformance_are_penalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [self._valid_prediction(confidence=0.95, actual_forward_return=-0.8, baseline_forward_return=0.0, hit_target=False)],
            )
            report = self._build_report(root)

        components = report["candidate_rows"][0]["reward_components"]
        self.assertGreater(components["high_confidence_wrong_penalty"], 0)
        self.assertGreater(components["baseline_underperformance_penalty"], 0)
        self.assertLess(report["candidate_rows"][0]["total_reward"], -1.0)

    def test_aggregations_use_rewardable_predictions_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [
                    self._valid_prediction(candidate_lifecycle_id="rewardable", opportunity_type="vwap_reclaim"),
                    {
                        "candidate_lifecycle_id": "incomplete",
                        "ticker": "NVDA",
                        "opportunity_type": "vwap_reclaim",
                        "actual_forward_return": 4.0,
                    },
                ],
            )
            report = self._build_report(root)

        setup = {item["setup_type"]: item for item in report["setup_rewards"]}["vwap_reclaim"]
        self.assertEqual(setup["candidate_count"], 2)
        self.assertEqual(setup["rewardable_candidate_count"], 1)
        self.assertEqual(report["summary"]["reward_distribution"]["positive"], 0)
        self.assertEqual(report["summary"]["reward_distribution"]["strong_positive"], 1)

    def test_simulation_evidence_is_excluded_from_live_reward_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [{**self._valid_prediction(candidate_lifecycle_id="sim-only"), "simulation_evidence": True}],
            )
            self._write_jsonl(
                root / "runtime-exports" / "simulation-evidence" / "2026-05-05" / "test-tenant.jsonl",
                [{"ticker": "SPY", "scenario_probability": 0.9, "counts_toward_live_million": False}],
            )
            report = self._build_report(root)

        self.assertEqual(report["summary"]["candidate_count"], 0)
        self.assertEqual(report["summary"]["source_counts"]["simulation_evidence_rows_excluded"], 1)

    def test_api_response_shape_has_safety_flags(self) -> None:
        client = TestClient(create_app())
        for path in (
            "/api/evidence-reward/summary",
            "/api/evidence-reward/candidates",
            "/api/evidence-reward/blockers",
            "/api/evidence-reward/engines",
            "/api/evidence-reward/setups",
            "/api/evidence-reward/ai",
            "/api/evidence-reward/regimes",
            "/api/orgs/trade-automation/evidence-reward/summary",
            "/api/orgs/trade-automation/evidence-reward/candidates",
            "/api/orgs/trade-automation/evidence-reward/blockers",
            "/api/orgs/trade-automation/evidence-reward/engines",
            "/api/orgs/trade-automation/evidence-reward/setups",
            "/api/orgs/trade-automation/evidence-reward/ai",
            "/api/orgs/trade-automation/evidence-reward/regimes",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertIn("data", payload)
            self.assertTrue(payload["data"]["research_only"])
            self.assertIn("safety_notes", payload["data"])
            self.assertIn("Research only. Does not affect trading.", payload["data"]["safety_notes"])
            self.assertIn("summary", payload["data"])
            self.assertIn("missing_fields", payload["data"])
            self.assertFalse(payload["data"]["can_submit_orders"])
            self.assertFalse(payload["data"]["can_submit_live_orders"])
            self.assertEqual(payload["data"]["mutation"], "none")

    def test_evidence_reward_service_has_no_execution_mutation_calls(self) -> None:
        source = Path("backend/services/evidence_reward_engine.py").read_text(encoding="utf-8")

        forbidden_calls = (
            "submit_order(",
            "place_order(",
            "clear_kill_switch(",
            "set_broker_route(",
            "update_ranking_weight(",
            "enable_live_trading(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
