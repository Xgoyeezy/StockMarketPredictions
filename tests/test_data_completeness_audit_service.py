from __future__ import annotations

import inspect
import unittest

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.services import data_completeness_audit as audit
from backend.services.data_completeness_audit import (
    audit_record,
    build_data_completeness_report,
)


def _complete_candidate() -> dict[str, object]:
    return {
        "record_id": "candidate-1",
        "symbol": "AAPL",
        "prediction_created_at": "2026-05-06T14:00:00Z",
        "engine": "intraday_momentum",
        "setup_type": "vwap_reclaim",
        "score": 88,
        "allowed": True,
        "blocked": False,
        "blockers": [],
        "actual_forward_return": 0.42,
        "baseline_forward_return": 0.10,
        "regime": "trend_day",
    }


def _complete_forecast() -> dict[str, object]:
    return {
        "prediction_id": "forecast-1",
        "symbol": "SPY",
        "prediction_created_at": "2026-05-06T14:00:00Z",
        "horizon_minutes": 60,
        "forecast_series": [100.0, 100.2, 100.6],
        "predicted_direction": "up",
        "predicted_target_pct": 0.60,
        "invalidation_level": 99.4,
        "confidence": 0.72,
        "actual_series": [100.0, 100.3, 100.7],
        "actual_forward_return": 0.70,
        "baseline_forward_return": 0.15,
        "regime": "trend_day",
    }


def _complete_execution() -> dict[str, object]:
    return {
        "symbol": "AAPL",
        "timestamp": "2026-05-06T14:01:00Z",
        "order_id": "paper-order-1",
        "intended_price": 100.0,
        "fill_price": 100.03,
        "spread_at_signal": 7.0,
        "slippage": 3.0,
        "fill_delay": 410,
        "route": "broker_paper",
        "paper_fill_status": "filled",
        "regime": "trend_day",
    }


class DataCompletenessAuditServiceTests(unittest.TestCase):
    def test_empty_evidence_state(self) -> None:
        report = build_data_completeness_report(
            candidate_records=[],
            forecast_records=[],
            ai_records=[],
            blocker_records=[],
            missed_move_records=[],
            paper_trade_records=[],
            execution_records=[],
            benchmark_records=[],
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertEqual(report["status"], "empty")
        self.assertTrue(report["research_only"])
        self.assertEqual(report["summary"]["total_records"], 0)
        self.assertIn("No evidence records", report["warnings"][0])

    def test_complete_candidate_record(self) -> None:
        row = audit_record(_complete_candidate(), source_type="candidate")

        self.assertTrue(row["complete"])
        self.assertTrue(row["rewardable"])
        self.assertEqual(row["missing_fields"], [])
        self.assertEqual(row["fields"]["actual_forward_return"], 0.42)

    def test_incomplete_candidate_record_explains_missing_fields(self) -> None:
        candidate = _complete_candidate()
        candidate.pop("actual_forward_return")
        candidate.pop("baseline_forward_return")
        row = audit_record(candidate, source_type="candidate")

        self.assertFalse(row["complete"])
        self.assertFalse(row["rewardable"])
        self.assertIn("actual_forward_return", row["missing_fields"])
        self.assertIn("baseline_forward_return", row["missing_fields"])
        self.assertIn("Missing required candidate fields", row["reason"])

    def test_complete_forecast_record(self) -> None:
        row = audit_record(_complete_forecast(), source_type="forecast")

        self.assertTrue(row["complete"])
        self.assertTrue(row["rewardable"])
        self.assertEqual(row["missing_fields"], [])

    def test_incomplete_forecast_record_reports_series_gaps(self) -> None:
        forecast = _complete_forecast()
        forecast.pop("forecast_series")
        forecast.pop("actual_series")
        row = audit_record(forecast, source_type="forecast")

        self.assertFalse(row["complete"])
        self.assertIn("forecast_series", row["missing_fields"])
        self.assertIn("actual_series", row["missing_fields"])

    def test_missing_actual_and_baseline_forward_return_are_benchmark_blockers(self) -> None:
        incomplete = _complete_candidate()
        incomplete.pop("actual_forward_return")
        incomplete.pop("baseline_forward_return")
        report = build_data_completeness_report(
            candidate_records=[_complete_candidate(), incomplete],
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertEqual(report["summary"]["total_records"], 2)
        self.assertEqual(report["summary"]["complete_records"], 1)
        self.assertEqual(report["missing_fields"]["actual_forward_return"], 1)
        self.assertEqual(report["missing_fields"]["baseline_forward_return"], 1)
        blocker_fields = {row["field"] for row in report["summary"]["benchmark_blockers"]}
        self.assertIn("actual_forward_return", blocker_fields)
        self.assertIn("baseline_forward_return", blocker_fields)

    def test_cleanup_plan_prioritizes_proof_gaps_without_fabricating_data(self) -> None:
        incomplete_candidate = _complete_candidate()
        incomplete_candidate.pop("actual_forward_return")
        incomplete_candidate.pop("baseline_forward_return")
        incomplete_candidate.pop("regime")
        incomplete_forecast = _complete_forecast()
        incomplete_forecast.pop("actual_series")
        incomplete_execution = _complete_execution()
        incomplete_execution.pop("slippage")
        report = build_data_completeness_report(
            candidate_records=[incomplete_candidate],
            forecast_records=[incomplete_forecast],
            execution_records=[incomplete_execution],
            generated_at="2026-05-06T00:00:00Z",
        )

        cleanup_plan = report["data_cleanup_plan"]
        by_key = {row["key"]: row for row in cleanup_plan["items"]}
        self.assertEqual(cleanup_plan["status"], "needs_attention")
        self.assertEqual(report["summary"]["cleanup_plan_status"], "needs_attention")
        self.assertGreaterEqual(report["summary"]["cleanup_plan_critical_open_items"], 2)
        self.assertEqual(by_key["missing_forward_returns"]["status"], "needs_cleanup")
        self.assertEqual(by_key["missing_baselines"]["status"], "needs_cleanup")
        self.assertEqual(by_key["missing_forecast_actuals"]["status"], "needs_cleanup")
        self.assertEqual(by_key["missing_execution_evidence"]["status"], "needs_cleanup")
        self.assertIn("Professional Benchmark", by_key["missing_forward_returns"]["blocked_reports"])
        self.assertIn("actual_forward_return", by_key["missing_forward_returns"]["missing_field_counts"])
        self.assertIn("baseline_forward_return", by_key["missing_baselines"]["missing_field_counts"])
        self.assertTrue(all(item["manual_review_only"] for item in cleanup_plan["items"]))
        self.assertFalse(any(item["changes_execution"] for item in cleanup_plan["items"]))
        self.assertIn("does not fabricate", cleanup_plan["summary"]["safe_boundary"])

    def test_proof_field_coverage_blocks_benchmark_readiness_when_roadmap_fields_are_missing(self) -> None:
        candidate = _complete_candidate()
        candidate.pop("baseline_forward_return")
        candidate.pop("regime")
        forecast = _complete_forecast()
        forecast.pop("actual_series")
        execution = _complete_execution()
        execution.pop("slippage")
        report = build_data_completeness_report(
            candidate_records=[candidate],
            forecast_records=[forecast],
            execution_records=[execution],
            benchmark_records=[
                {
                    "status": "ready",
                    "generated_at": "2026-05-06T00:00:00Z",
                    "benchmark_verdict": "ready",
                    "candidate_count": 1,
                    "rewardable_count": 1,
                    "missing_fields": {},
                }
            ],
            generated_at="2026-05-06T00:00:00Z",
        )

        coverage = report["proof_field_coverage"]
        by_key = {row["key"]: row for row in coverage["records"]}
        self.assertFalse(report["summary"]["benchmark_ready"])
        self.assertFalse(report["summary"]["proof_field_ready"])
        self.assertEqual(by_key["baseline_returns"]["status"], "needs_attention")
        self.assertEqual(by_key["forecast_actuals"]["status"], "needs_attention")
        self.assertEqual(by_key["execution_costs"]["status"], "needs_attention")
        self.assertEqual(by_key["regime_labels"]["status"], "needs_attention")
        self.assertIn("baseline_return", by_key["baseline_returns"]["missing_group_counts"])
        self.assertIn("actual_series", by_key["forecast_actuals"]["missing_group_counts"])
        self.assertIn("slippage", by_key["execution_costs"]["missing_group_counts"])
        self.assertTrue(all(action["manual_review_only"] for action in coverage["safe_next_actions"]))
        self.assertFalse(any(action["changes_execution"] for action in coverage["safe_next_actions"]))

    def test_cleanup_plan_counts_missing_forecast_horizon_context(self) -> None:
        forecast = _complete_forecast()
        forecast.pop("horizon_minutes")
        report = build_data_completeness_report(
            forecast_records=[forecast],
            generated_at="2026-05-06T00:00:00Z",
        )

        cleanup_by_key = {row["key"]: row for row in report["data_cleanup_plan"]["items"]}
        lineage_context = cleanup_by_key["missing_lineage_and_context"]

        self.assertEqual(lineage_context["status"], "needs_cleanup")
        self.assertIn("horizon_minutes", lineage_context["missing_field_counts"])
        self.assertIn("horizon_minutes", {row["field"] for row in report["summary"]["benchmark_blockers"]})
        self.assertTrue(all(action["manual_review_only"] for action in report["safe_next_actions"]))
        self.assertFalse(any(action["changes_execution"] for action in report["safe_next_actions"]))

    def test_proof_field_coverage_ready_when_required_roadmap_fields_are_present(self) -> None:
        report = build_data_completeness_report(
            candidate_records=[_complete_candidate()],
            forecast_records=[_complete_forecast()],
            execution_records=[_complete_execution()],
            benchmark_records=[
                {
                    "status": "ready",
                    "generated_at": "2026-05-06T00:00:00Z",
                    "benchmark_verdict": "ready",
                    "candidate_count": 1,
                    "rewardable_count": 1,
                    "missing_fields": {},
                }
            ],
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertTrue(report["summary"]["proof_field_ready"])
        self.assertTrue(report["summary"]["benchmark_ready"])
        self.assertEqual(report["proof_field_coverage"]["status"], "ready")
        self.assertEqual(report["proof_field_coverage"]["summary"]["ready_requirement_count"], 6)
        self.assertEqual(report["proof_field_coverage"]["summary"]["average_coverage_rate"], 1.0)

    def test_missing_execution_fields(self) -> None:
        execution = _complete_execution()
        execution.pop("fill_price")
        execution.pop("slippage")
        row = audit_record(execution, source_type="execution")

        self.assertFalse(row["complete"])
        self.assertIn("fill_price", row["missing_fields"])
        self.assertIn("slippage", row["missing_fields"])

    def test_aggregation_accuracy(self) -> None:
        incomplete_forecast = _complete_forecast()
        incomplete_forecast.pop("baseline_forward_return")
        report = build_data_completeness_report(
            candidate_records=[_complete_candidate()],
            forecast_records=[_complete_forecast(), incomplete_forecast],
            execution_records=[_complete_execution()],
            benchmark_records=[
                {
                    "status": "insufficient_evidence",
                    "generated_at": "2026-05-06T00:00:00Z",
                    "benchmark_verdict": "insufficient_evidence",
                    "candidate_count": 1,
                    "rewardable_count": 1,
                    "missing_fields": {},
                }
            ],
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertEqual(report["summary"]["total_records"], 5)
        self.assertEqual(report["summary"]["complete_records"], 4)
        self.assertEqual(report["summary"]["incomplete_records"], 1)
        self.assertEqual(report["summary"]["source_summaries"]["forecast"]["total_records"], 2)
        self.assertEqual(report["aggregations"]["missing_by_source"]["forecast"]["baseline_forward_return"], 1)

    def test_ai_blocker_missed_move_and_paper_trade_contracts(self) -> None:
        report = build_data_completeness_report(
            ai_records=[
                {
                    "symbol": "AAPL",
                    "timestamp": "2026-05-06T14:00:00Z",
                    "ai_verdict": "reject",
                    "confidence": 0.81,
                    "reason": "wide spread",
                    "linked_candidate_id": "candidate-1",
                    "actual_outcome": -0.20,
                }
            ],
            blocker_records=[
                {
                    "symbol": "AAPL",
                    "timestamp": "2026-05-06T14:00:00Z",
                    "blocked_reason": "wide_spread",
                    "actual_forward_return": -0.20,
                    "baseline_forward_return": 0.05,
                }
            ],
            missed_move_records=[
                {
                    "symbol": "MSFT",
                    "timestamp": "2026-05-06T15:00:00Z",
                    "blocked_reason": "cooldown",
                    "forward_return": 0.55,
                    "baseline_forward_return": 0.08,
                    "move_magnitude": 0.55,
                    "recoverable_flag": True,
                }
            ],
            paper_trade_records=[
                {
                    "symbol": "AAPL",
                    "timestamp": "2026-05-06T14:03:00Z",
                    "order_id": "paper-order-1",
                    "route": "broker_paper",
                    "paper_fill_status": "filled",
                    "fill_price": 100.03,
                    "paper_trade_outcome": 0.22,
                }
            ],
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertEqual(report["summary"]["complete_records"], 4)
        self.assertEqual(report["summary"]["rewardable_records"], 4)

    def test_simulation_evidence_is_visible_but_not_rewardable(self) -> None:
        candidate = _complete_candidate()
        candidate["evidence_pool"] = "simulation_evidence"
        row = audit_record(candidate, source_type="candidate")

        self.assertTrue(row["complete"])
        self.assertFalse(row["rewardable"])
        self.assertIn("Simulation evidence remains separate", row["warnings"][0])

    def test_api_response_shape_and_safety_flags(self) -> None:
        client = TestClient(create_app())
        for path in (
            "/api/data-completeness/summary",
            "/api/data-completeness/candidates",
            "/api/data-completeness/forecasts",
            "/api/data-completeness/ai",
            "/api/data-completeness/blockers",
            "/api/data-completeness/execution",
            "/api/data-completeness/benchmark-readiness",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            data = payload["data"]
            self.assertTrue(data["research_only"])
            self.assertFalse(data["can_submit_orders"])
            self.assertFalse(data["can_submit_live_orders"])
            self.assertEqual(data["mutation"], "none")
            self.assertIn("summary", data)
            self.assertIn("records", data)
            self.assertIn("aggregations", data)
            self.assertIn("proof_field_coverage", data)
            self.assertIn("data_cleanup_plan", data)
            self.assertIn("proof_field_ready", data["summary"])
            self.assertIn("proof_field_coverage_rate", data["summary"])
            self.assertIn("cleanup_plan_status", data["summary"])
            self.assertIn("missing_fields", data)
            self.assertIn("safety_notes", data)
            self.assertIn("Does not place orders.", data["safety_notes"])

    def test_service_contains_no_execution_broker_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(audit)
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
