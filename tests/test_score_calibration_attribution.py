from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.services import score_calibration_attribution as calibration
from backend.services.score_calibration_attribution import (
    assign_score_bucket,
    build_score_calibration_report,
    compute_feature_attribution,
    compute_score_bucket_analysis,
    normalize_calibration_records,
)


def _row(
    *,
    record_id: str,
    score: float | None,
    reward: float | None,
    actual: float | None,
    baseline: float | None = 0.05,
    setup_type: str = "vwap_reclaim",
    engine: str = "intraday_momentum",
    regime: str = "trend_day",
    blocker: str | None = None,
    ai_verdict: str = "approve_evidence",
    slippage_bps: float = 2.0,
    spread_bps: float = 4.0,
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "symbol": "AAPL",
        "score": score,
        "setup_type": setup_type,
        "engine": engine,
        "regime": regime,
        "blockers": [blocker] if blocker else [],
        "blocked": bool(blocker),
        "ai_verdict": ai_verdict,
        "rewardable": reward is not None,
        "total_reward": reward,
        "actual_forward_return": actual,
        "baseline_forward_return": baseline,
        "direction_correct": actual is not None and actual > 0,
        "slippage_bps": slippage_bps,
        "spread_bps": spread_bps,
        "component_scores": {"vwap_score": 0.8 if setup_type == "vwap_reclaim" else 0.0},
    }


def _calibration_rows() -> list[dict[str, object]]:
    return [
        _row(record_id="b1", score=10, reward=-0.40, actual=-0.30, setup_type="weak_breakout", regime="range_day"),
        _row(record_id="b2", score=30, reward=-0.20, actual=-0.10, setup_type="weak_breakout", regime="range_day"),
        _row(record_id="m1", score=50, reward=0.05, actual=0.10, setup_type="pullback", regime="trend_day"),
        _row(record_id="g1", score=70, reward=0.25, actual=0.35, setup_type="vwap_reclaim", regime="trend_day"),
        _row(record_id="g2", score=90, reward=0.70, actual=0.80, setup_type="vwap_reclaim", regime="trend_day"),
        _row(record_id="fp1", score=85, reward=-0.35, actual=-0.25, setup_type="failed_breakout", regime="range_day"),
        _row(record_id="fn1", score=15, reward=0.45, actual=0.55, setup_type="oversold_bounce", regime="risk_off", blocker="cooldown", ai_verdict="reject_evidence"),
    ]


class ScoreCalibrationAttributionTests(unittest.TestCase):
    def test_bucket_assignment(self) -> None:
        self.assertEqual(assign_score_bucket(0), "0_20")
        self.assertEqual(assign_score_bucket(19.99), "0_20")
        self.assertEqual(assign_score_bucket(20), "20_40")
        self.assertEqual(assign_score_bucket(40), "40_60")
        self.assertEqual(assign_score_bucket(60), "60_80")
        self.assertEqual(assign_score_bucket(80), "80_100")
        self.assertEqual(assign_score_bucket(1.0, multiplier=100), "80_100")
        self.assertEqual(assign_score_bucket(None), "unknown")

    def test_bucket_lift_and_monotonicity_score(self) -> None:
        records = normalize_calibration_records(_calibration_rows()[:5])
        section = compute_score_bucket_analysis(records)

        self.assertTrue(section["available"])
        self.assertAlmostEqual(section["bucket_lift"], 1.0)
        self.assertEqual(section["monotonicity_score"], 1.0)

    def test_feature_lift(self) -> None:
        records = normalize_calibration_records(_calibration_rows())
        section = compute_feature_attribution(records)
        positive = {row["feature"]: row for row in section["top_positive_features"]}
        negative = {row["feature"]: row for row in section["top_negative_features"]}

        self.assertIn("setup_type:vwap_reclaim", positive)
        self.assertGreater(positive["setup_type:vwap_reclaim"]["lift"], 0)
        self.assertIn("setup_type:weak_breakout", negative)
        self.assertLess(negative["setup_type:weak_breakout"]["lift"], 0)

    def test_false_positive_driver(self) -> None:
        records = normalize_calibration_records(_calibration_rows())
        section = compute_feature_attribution(records)
        drivers = {row["feature"]: row for row in section["false_positive_drivers"]}

        self.assertIn("setup_type:failed_breakout", drivers)
        self.assertEqual(drivers["setup_type:failed_breakout"]["false_positive_rate"], 1.0)

    def test_false_negative_driver(self) -> None:
        records = normalize_calibration_records(_calibration_rows())
        section = compute_feature_attribution(records)
        drivers = {row["feature"]: row for row in section["false_negative_drivers"]}

        self.assertIn("blocker:cooldown", drivers)
        self.assertEqual(drivers["blocker:cooldown"]["false_negative_rate"], 1.0)

    def test_missing_score_and_reward_fields(self) -> None:
        report = build_score_calibration_report(
            records=[
                _row(record_id="missing-score", score=None, reward=0.2, actual=0.25),
                _row(record_id="missing-reward", score=80, reward=None, actual=0.20),
            ],
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertIn("score", report["missing_fields"])
        self.assertIn("total_reward", report["missing_fields"])
        self.assertEqual(report["summary"]["rewardable_count"], 1)

    def test_safe_recommendation_generation(self) -> None:
        report = build_score_calibration_report(records=_calibration_rows()[:5], generated_at="2026-05-06T00:00:00Z")
        recommendations = report["aggregations"]["recommendations"]

        self.assertTrue(recommendations)
        self.assertTrue(all(row["manual_review_only"] for row in recommendations))
        self.assertFalse(report["can_submit_orders"])
        self.assertFalse(report["can_submit_live_orders"])
        self.assertEqual(report["mutation"], "none")

    def test_api_response_shape(self) -> None:
        client = TestClient(create_app())
        benchmark = {"records": _calibration_rows(), "warnings": [], "missing_fields": {}}
        with patch.object(calibration, "get_professional_benchmark_summary", lambda db=None, current_user=None: benchmark):
            for path in (
                "/api/score-calibration/summary",
                "/api/score-calibration/buckets",
                "/api/score-calibration/features",
                "/api/score-calibration/regimes",
                "/api/score-calibration/recommendations",
            ):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["ok"])
                data = payload["data"]
                self.assertTrue(data["research_only"])
                self.assertIn("summary", data)
                self.assertIn("records", data)
                self.assertIn("aggregations", data)
                self.assertIn("warnings", data)
                self.assertIn("missing_fields", data)
                self.assertIn("safety_notes", data)
                self.assertIn("Does not change ranking weights automatically.", data["safety_notes"])

    def test_service_contains_no_execution_broker_ranking_or_risk_mutation_calls(self) -> None:
        source = inspect.getsource(calibration)
        forbidden_calls = (
            "place_order(",
            "submit_order(",
            "clear_kill_switch(",
            "set_broker_route(",
            "update_ranking_weight(",
            "update_risk_config(",
            "enable_live_trading(",
            "set_risk_limit(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
