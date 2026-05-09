from __future__ import annotations

import inspect
import unittest

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.services import professional_benchmark_suite as suite
from backend.services.professional_benchmark_suite import (
    build_professional_benchmark_report,
    compute_baseline_comparison,
    compute_score_bucket_separation,
)


def _reward_row(
    *,
    record_id: str,
    score_bucket: str,
    total_reward: float,
    actual_forward_return: float,
    baseline_forward_return: float = 0.05,
    setup_type: str = "vwap_reclaim",
    engine: str = "intraday_momentum",
    regime: str = "trend_day",
    blocker: str | None = None,
    ai_verdict: str = "approve_evidence",
    slippage_bps: float = 4.0,
    spread_bps: float = 8.0,
) -> dict[str, object]:
    blockers = [blocker] if blocker else []
    return {
        "record_id": record_id,
        "symbol": "AAPL",
        "prediction_created_at": "2026-05-05T14:00:00Z",
        "engine": engine,
        "setup_type": setup_type,
        "regime": regime,
        "score_bucket": score_bucket,
        "blockers": blockers,
        "blocked": bool(blocker),
        "allowed": not blocker,
        "ai_verdict": ai_verdict,
        "rewardable": True,
        "total_reward": total_reward,
        "actual_forward_return": actual_forward_return,
        "baseline_forward_return": baseline_forward_return,
        "spy_forward_return": 0.03,
        "qqq_forward_return": 0.04,
        "sector_etf_forward_return": 0.02,
        "random_candidate_forward_return": baseline_forward_return,
        "simple_momentum_forward_return": 0.06,
        "simple_mean_reversion_forward_return": -0.02,
        "simple_vwap_reclaim_forward_return": 0.07,
        "opening_range_breakout_forward_return": 0.01,
        "slippage_bps": slippage_bps,
        "spread_bps": spread_bps,
        "confidence": 0.75 if total_reward > 0 else 0.8,
        "trade_executed": True,
    }


def _edge_rows() -> list[dict[str, object]]:
    return [
        _reward_row(record_id="high-1", score_bucket="90_100", total_reward=0.80, actual_forward_return=0.90),
        _reward_row(record_id="high-2", score_bucket="90_100", total_reward=0.70, actual_forward_return=0.80),
        _reward_row(record_id="mid-1", score_bucket="80_89", total_reward=0.45, actual_forward_return=0.55),
        _reward_row(record_id="mid-2", score_bucket="60_79", total_reward=0.25, actual_forward_return=0.35, setup_type="opening_range_breakout"),
        _reward_row(record_id="low-1", score_bucket="0_39", total_reward=0.05, actual_forward_return=0.15, blocker="wide_spread", ai_verdict="reject_evidence"),
        _reward_row(record_id="low-2", score_bucket="0_39", total_reward=0.00, actual_forward_return=0.05, blocker="cooldown", ai_verdict="wait_for_confirmation"),
    ]


class ProfessionalBenchmarkSuiteServiceTests(unittest.TestCase):
    def test_empty_evidence_state_returns_insufficient_evidence(self) -> None:
        report = build_professional_benchmark_report(records=[], forecast_records=[], generated_at="2026-05-06T00:00:00Z")

        self.assertEqual(report["status"], "insufficient_evidence")
        self.assertTrue(report["research_only"])
        self.assertFalse(report["can_submit_orders"])
        self.assertFalse(report["can_submit_live_orders"])
        self.assertEqual(report["mutation"], "none")
        self.assertEqual(report["summary"]["candidate_count"], 0)

    def test_insufficient_evidence_verdict(self) -> None:
        report = build_professional_benchmark_report(records=_edge_rows()[:2], forecast_records=[], generated_at="2026-05-06T00:00:00Z")

        self.assertEqual(report["status"], "insufficient_evidence")
        self.assertTrue(report["summary"]["sample_size_warning"])

    def test_data_quality_too_weak_verdict(self) -> None:
        rows = [{"symbol": "AAPL", "setup_type": "visual_label_only"} for _ in range(6)]
        report = build_professional_benchmark_report(records=rows, forecast_records=[], generated_at="2026-05-06T00:00:00Z")

        self.assertEqual(report["status"], "data_quality_too_weak")
        self.assertGreater(report["summary"]["candidate_count"], 0)
        self.assertIn("actual_forward_return", report["missing_fields"])

    def test_edge_detected_with_fixture_data(self) -> None:
        report = build_professional_benchmark_report(records=_edge_rows(), forecast_records=[], generated_at="2026-05-06T00:00:00Z")

        self.assertEqual(report["status"], "edge_detected")
        self.assertGreater(report["summary"]["average_reward"], 0)
        self.assertGreater(report["summary"]["baseline_relative_edge"], 0.1)
        self.assertGreater(report["summary"]["score_bucket_lift"], 0)

    def test_no_edge_detected_with_fixture_data(self) -> None:
        rows = [
            _reward_row(record_id=f"loss-{index}", score_bucket="90_100" if index < 3 else "0_39", total_reward=-0.30, actual_forward_return=-0.20, baseline_forward_return=0.10)
            for index in range(6)
        ]
        report = build_professional_benchmark_report(records=rows, forecast_records=[], generated_at="2026-05-06T00:00:00Z")

        self.assertEqual(report["status"], "no_edge_detected")
        self.assertLess(report["summary"]["average_reward"], 0)

    def test_baseline_comparison_math(self) -> None:
        comparison = compute_baseline_comparison(suite._normalize_records(_edge_rows()))
        spy = next(row for row in comparison["items"] if row["key"] == "spy_drift")

        self.assertTrue(spy["available"])
        self.assertAlmostEqual(spy["baseline_expected_value"], 0.03)
        self.assertAlmostEqual(spy["baseline_relative_edge"], 0.436667)

    def test_score_bucket_separation(self) -> None:
        section = compute_score_bucket_separation(suite._normalize_records(_edge_rows()))

        self.assertTrue(section["available"])
        self.assertGreater(section["score_bucket_lift"], 0)
        self.assertTrue(section["items"])

    def test_blocker_value_aggregation(self) -> None:
        report = build_professional_benchmark_report(records=_edge_rows(), forecast_records=[], generated_at="2026-05-06T00:00:00Z")
        blocker_items = report["sections"]["blocker_value"]["items"]

        self.assertTrue(blocker_items)
        self.assertEqual({row["blocker"] for row in blocker_items}, {"wide_spread", "cooldown"})
        self.assertIn("false_block_rate", blocker_items[0])

    def test_ai_verdict_aggregation(self) -> None:
        report = build_professional_benchmark_report(records=_edge_rows(), forecast_records=[], generated_at="2026-05-06T00:00:00Z")
        ai = report["sections"]["ai_verdict_accuracy"]

        self.assertTrue(ai["available"])
        self.assertGreaterEqual(ai["verdict_count"], 6)
        self.assertIn("false_negative_rate", ai)

    def test_forecast_accuracy_aggregation(self) -> None:
        forecast_records = [
            {
                "evaluation": {
                    "prediction_id": "forecast-1",
                    "symbol": "SPY",
                    "rewardable": True,
                    "direction_accuracy": 1.0,
                    "path_mae": 0.1,
                    "path_rmse": 0.15,
                    "timing_error": 2.0,
                    "confidence_calibration": 0.08,
                    "forecast_total_reward": 1.2,
                }
            }
        ]
        report = build_professional_benchmark_report(records=_edge_rows(), forecast_records=forecast_records, generated_at="2026-05-06T00:00:00Z")
        forecast = report["sections"]["forecast_accuracy"]

        self.assertTrue(forecast["available"])
        self.assertEqual(forecast["validated_forecasts"], 1)
        self.assertEqual(forecast["direction_accuracy"], 1.0)

    def test_execution_adjusted_reward(self) -> None:
        report = build_professional_benchmark_report(records=_edge_rows(), forecast_records=[], generated_at="2026-05-06T00:00:00Z")
        execution = report["sections"]["execution_quality"]

        self.assertTrue(execution["available"])
        self.assertLess(execution["slippage_adjusted_reward"], report["summary"]["average_reward"])

    def test_api_response_shape_and_safety_notes(self) -> None:
        client = TestClient(create_app())
        for path in (
            "/api/professional-benchmark/summary",
            "/api/professional-benchmark/baselines",
            "/api/professional-benchmark/score-buckets",
            "/api/professional-benchmark/blockers",
            "/api/professional-benchmark/ai",
            "/api/professional-benchmark/forecast",
            "/api/professional-benchmark/execution",
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
            self.assertIn("baselines", data)
            self.assertIn("warnings", data)
            self.assertIn("missing_fields", data)
            self.assertIn("safety_notes", data)
            self.assertIn("Does not place orders.", data["safety_notes"])

    def test_service_contains_no_execution_mutation_calls(self) -> None:
        source = inspect.getsource(suite)
        forbidden_calls = (
            "place_order(",
            "submit_order(",
            "clear_kill_switch(",
            "set_broker_route(",
            "update_ranking_weight(",
            "enable_live_trading(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
