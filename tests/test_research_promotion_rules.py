from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.services import research_promotion_rules as rules
from backend.services.exceptions import ValidationServiceError
from backend.services.research_promotion_rules import (
    build_research_promotion_report,
    build_research_promotion_cleanup_plan,
    get_research_promotion_entity,
    update_research_promotion_status,
)


def _benchmark(
    status: str = "edge_detected",
    *,
    candidates: int = 30,
    rewardable: int = 12,
    edge: float | None = 0.08,
    lift: float | None = 0.10,
    data_quality: float = 82.0,
    max_drawdown: float = 0.20,
    slippage_adjusted_reward: float = 0.08,
    false_positive_rate: float = 0.10,
    false_negative_rate: float = 0.20,
    blocker_value: float = 0.12,
    false_block_rate: float = 0.10,
    forecast_accuracy: float = 0.62,
) -> dict[str, object]:
    return {
        "status": status,
        "summary": {
            "benchmark_verdict": status,
            "candidate_count": candidates,
            "rewardable_count": rewardable,
            "baseline_relative_edge": edge,
            "score_bucket_lift": lift,
            "data_quality_score": data_quality,
            "max_drawdown": max_drawdown,
            "profit_factor": 1.8,
        },
        "sections": {
            "reward_by_setup": {
                "items": [
                    {
                        "setup_type": "vwap_reclaim",
                        "candidate_count": candidates,
                        "rewardable_count": rewardable,
                        "avg_reward": 0.22,
                        "win_rate": 0.68,
                    }
                ]
            },
            "reward_by_engine": {
                "items": [
                    {
                        "engine": "intraday_momentum",
                        "candidate_count": candidates,
                        "rewardable_count": rewardable,
                        "avg_reward": 0.20,
                        "win_rate": 0.65,
                    }
                ]
            },
            "blocker_value": {
                "items": [
                    {
                        "blocker": "wide_spread",
                        "times_seen": 9,
                        "estimated_blocker_value": blocker_value,
                        "false_block_rate": false_block_rate,
                    }
                ]
            },
            "ai_verdict_accuracy": {
                "verdict_count": rewardable,
                "false_positive_rate": false_positive_rate,
                "false_negative_rate": false_negative_rate,
                "items": [],
            },
            "forecast_accuracy": {
                "direction_accuracy": forecast_accuracy,
                "items": [{"model_name": "forecast_validation_v1", "direction_accuracy": forecast_accuracy}],
            },
            "execution_quality": {"available": True, "slippage_adjusted_reward": slippage_adjusted_reward},
            "score_bucket_separation": {"available": True, "score_bucket_lift": lift, "items": []},
        },
        "warnings": [],
        "missing_fields": {},
    }


def _completeness(
    *,
    completion_rate: float = 0.82,
    rewardability_rate: float = 0.70,
    benchmark_ready: bool = True,
    missing_fields: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "summary": {
            "completion_rate": completion_rate,
            "rewardability_rate": rewardability_rate,
            "benchmark_ready": benchmark_ready,
            "highest_priority_missing_fields": missing_fields or [],
        },
        "warnings": [],
    }


def _walk_forward(*, frozen: bool = False, passed: bool = False) -> dict[str, object]:
    records: list[dict[str, object]] = []
    if frozen:
        records.append({"experiment_id": "wf-frozen", "status": "frozen", "metrics": {"verdict": "insufficient_evidence"}})
    if passed:
        records.append({"experiment_id": "wf-complete", "status": "completed", "metrics": {"verdict": "weak_pass"}})
    return {"records": records, "warnings": []}


def _first_strategy(report: dict[str, object]) -> dict[str, object]:
    return next(row for row in report["records"] if row["entity_id"] == "strategy:quant_evidence_os")


class ResearchPromotionRulesTests(unittest.TestCase):
    def test_needs_more_evidence_due_to_small_sample(self) -> None:
        report = build_research_promotion_report(
            benchmark_report=_benchmark(candidates=4, rewardable=2),
            completeness_report=_completeness(),
            walk_forward_report=_walk_forward(),
            manual_statuses={},
            generated_at="2026-05-06T00:00:00Z",
        )

        strategy = _first_strategy(report)
        self.assertEqual(strategy["promotion_status"], "needs_more_evidence")
        self.assertTrue(report["research_only"])
        self.assertFalse(report["can_submit_orders"])
        self.assertFalse(report["can_submit_live_orders"])

    def test_candidate_status(self) -> None:
        report = build_research_promotion_report(
            benchmark_report=_benchmark(edge=0.04, lift=0.07),
            completeness_report=_completeness(),
            walk_forward_report=_walk_forward(),
            manual_statuses={},
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertEqual(_first_strategy(report)["promotion_status"], "candidate")

    def test_walk_forward_testing_status(self) -> None:
        report = build_research_promotion_report(
            benchmark_report=_benchmark(edge=0.04, lift=0.07),
            completeness_report=_completeness(),
            walk_forward_report=_walk_forward(frozen=True),
            manual_statuses={},
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertEqual(_first_strategy(report)["promotion_status"], "walk_forward_testing")

    def test_paper_proven_status(self) -> None:
        report = build_research_promotion_report(
            benchmark_report=_benchmark(edge=0.24, lift=0.11, slippage_adjusted_reward=0.14),
            completeness_report=_completeness(),
            walk_forward_report=_walk_forward(frozen=True, passed=True),
            manual_statuses={},
            generated_at="2026-05-06T00:00:00Z",
        )

        strategy = _first_strategy(report)
        self.assertEqual(strategy["promotion_status"], "paper_proven")
        self.assertIn("not live approval", strategy["safe_explanation"].lower())

    def test_proof_summary_blocks_when_manual_review_trace_is_missing(self) -> None:
        report = build_research_promotion_report(
            benchmark_report=_benchmark(edge=0.24, lift=0.11, slippage_adjusted_reward=0.14),
            completeness_report=_completeness(),
            walk_forward_report=_walk_forward(frozen=True, passed=True),
            manual_statuses={},
            generated_at="2026-05-06T00:00:00Z",
        )

        proof = report["proof_summary"]
        summary = proof["summary"]

        self.assertFalse(proof["proof_ready"])
        self.assertEqual(proof["status"], "needs_evidence")
        self.assertEqual(summary["manual_review_record_count"], 0)
        self.assertIn("Research promotion proof requirements are incomplete.", report["warnings"])
        self.assertFalse(report["can_submit_orders"])
        self.assertFalse(report["writes_execution_config"])
        failed = {row["key"] for row in proof["requirements"] if not row["passed"]}
        self.assertIn("manual_review_traceability", failed)

    def test_proof_summary_ready_with_traceable_evidence_and_manual_review(self) -> None:
        manual_statuses = {
            "strategy:quant_evidence_os": {
                "entity_id": "strategy:quant_evidence_os",
                "promotion_status": "paper_proven",
                "reason": "Fixture human review.",
                "updated_at": "2026-05-06T00:00:00Z",
                "updated_by": "tester",
                "previous_promotion_status": "walk_forward_testing",
                "computed_promotion_status": "paper_proven",
                "evidence_snapshot": {"benchmark_verdict": "edge_detected", "walk_forward_verdict": "weak_pass"},
                "research_only": True,
            }
        }
        report = build_research_promotion_report(
            benchmark_report=_benchmark(edge=0.24, lift=0.11, slippage_adjusted_reward=0.14),
            completeness_report=_completeness(),
            walk_forward_report=_walk_forward(frozen=True, passed=True),
            manual_statuses=manual_statuses,
            generated_at="2026-05-06T00:00:00Z",
        )

        proof = report["proof_summary"]
        self.assertTrue(proof["proof_ready"])
        self.assertEqual(report["summary"]["promotion_proof_status"], "ready_for_human_review")
        self.assertEqual(report["summary"]["promotion_requirements_passed"], report["summary"]["promotion_requirements_total"])
        self.assertGreaterEqual(report["summary"]["promotion_traceability_coverage"], 0.8)
        self.assertEqual(report["aggregations"]["research_promotion_proof"]["proof_ready"], True)
        self.assertEqual(report["research_promotion_cleanup_plan"]["status"], "ready_for_human_review")
        self.assertTrue(report["summary"]["claim_permissions"]["cautious_internal_promotion_review"])
        self.assertFalse(report["summary"]["claim_permissions"]["automatic_strategy_promotion"])
        self.assertFalse(report["summary"]["claim_permissions"]["live_trading_readiness"])
        self.assertTrue(any(row["manual_review_traceable"] for row in proof["record_readiness"]))

    def test_cleanup_plan_blocks_governance_and_mutation_claims_when_proof_is_missing(self) -> None:
        report = build_research_promotion_report(
            benchmark_report=_benchmark(edge=0.24, lift=0.11, slippage_adjusted_reward=0.14),
            completeness_report=_completeness(),
            walk_forward_report=_walk_forward(frozen=True, passed=True),
            manual_statuses={},
            generated_at="2026-05-06T00:00:00Z",
        )
        plan = build_research_promotion_cleanup_plan(entities=report["records"], proof_summary=report["proof_summary"])
        by_key = {row["key"]: row for row in plan["items"]}

        self.assertEqual(plan["status"], "blocked_by_evidence")
        self.assertEqual(report["summary"]["research_promotion_cleanup_status"], "blocked_by_evidence")
        self.assertIn("manual_review_metadata", by_key)
        self.assertIn("small_fund_governance_claim", plan["summary"]["blocked_claims"])
        self.assertIn("live_trading_readiness", plan["summary"]["blocked_claims"])
        self.assertFalse(plan["summary"]["claim_permissions"]["automatic_strategy_promotion"])
        self.assertFalse(plan["summary"]["claim_permissions"]["ranking_weight_change"])
        self.assertFalse(plan["summary"]["claim_permissions"]["risk_limit_change"])
        self.assertFalse(plan["summary"]["claim_permissions"]["broker_route_change"])
        self.assertFalse(plan["summary"]["claim_permissions"]["live_trading_readiness"])
        self.assertTrue(all(item["manual_review_only"] for item in plan["items"]))
        self.assertFalse(any(item["changes_execution"] for item in plan["items"]))
        self.assertFalse(any(item["changes_order_submission"] for item in plan["items"]))
        self.assertFalse(any(item["changes_broker_routes"] for item in plan["items"]))
        self.assertFalse(any(item["changes_risk_gates"] for item in plan["items"]))
        self.assertFalse(any(item["changes_risk_limits"] for item in plan["items"]))
        self.assertFalse(any(item["changes_ranking_weights"] for item in plan["items"]))
        self.assertFalse(any(item["clears_kill_switch"] for item in plan["items"]))

    def test_rejected_status(self) -> None:
        report = build_research_promotion_report(
            benchmark_report=_benchmark(status="no_edge_detected", edge=-0.24, lift=-0.08),
            completeness_report=_completeness(),
            walk_forward_report=_walk_forward(),
            manual_statuses={},
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertEqual(_first_strategy(report)["promotion_status"], "rejected")

    def test_missing_fields_force_more_evidence(self) -> None:
        report = build_research_promotion_report(
            benchmark_report=_benchmark(edge=0.20),
            completeness_report=_completeness(missing_fields=[{"field": "actual_forward_return", "count": 14}]),
            walk_forward_report=_walk_forward(frozen=True, passed=True),
            manual_statuses={},
            generated_at="2026-05-06T00:00:00Z",
        )

        self.assertEqual(_first_strategy(report)["promotion_status"], "needs_more_evidence")

    def test_manual_status_metadata_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "promotion_statuses.json"
            with patch.object(rules, "get_professional_benchmark_summary", lambda db=None, current_user=None: _benchmark()), patch.object(
                rules,
                "get_data_completeness_summary",
                lambda db=None, current_user=None: _completeness(),
            ), patch.object(rules, "get_walk_forward_experiments", lambda: _walk_forward()):
                response = update_research_promotion_status(
                    "strategy:quant_evidence_os",
                    {"promotion_status": "needs_more_evidence", "reason": "Manual research hold."},
                    store_path=store,
                )
                entity = response["record"]

            self.assertEqual(response["status"], "updated")
            self.assertEqual(entity["promotion_status"], "needs_more_evidence")
            self.assertEqual(entity["manual_status"]["reason"], "Manual research hold.")
            self.assertTrue(entity["manual_status"]["research_only"])
            self.assertFalse(entity["manual_status"]["writes_execution_config"])
            self.assertEqual(entity["manual_status"]["previous_promotion_status"], "candidate")
            self.assertIn("approval_trace_id", entity["manual_status"])
            self.assertIn("evidence_snapshot", entity["manual_status"])
            self.assertEqual(entity["manual_status"]["evidence_snapshot"]["benchmark_verdict"], "edge_detected")

    def test_invalid_manual_status_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "promotion_statuses.json"
            with self.assertRaises(ValidationServiceError):
                update_research_promotion_status(
                    "strategy:quant_evidence_os",
                    {"promotion_status": "enable_live_trading"},
                    store_path=store,
                )

    def test_get_entity_shape(self) -> None:
        report = build_research_promotion_report(
            benchmark_report=_benchmark(),
            completeness_report=_completeness(),
            walk_forward_report=_walk_forward(),
            manual_statuses={},
            generated_at="2026-05-06T00:00:00Z",
        )
        self.assertIn("summary", report)
        self.assertIn("promotion_status", _first_strategy(report))
        self.assertIn("evidence_used", _first_strategy(report))
        self.assertIn("safety_notes", _first_strategy(report))

    def test_api_response_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "promotion_statuses.json"
            client = TestClient(create_app())
            with patch.object(rules, "DEFAULT_STORE_PATH", store), patch.object(
                rules,
                "get_professional_benchmark_summary",
                lambda db=None, current_user=None: _benchmark(),
            ), patch.object(
                rules,
                "get_data_completeness_summary",
                lambda db=None, current_user=None: _completeness(),
            ), patch.object(rules, "get_walk_forward_experiments", lambda: _walk_forward()):
                for path in (
                    "/api/research-promotion/summary",
                    "/api/research-promotion/entities",
                    "/api/research-promotion/entities/strategy:quant_evidence_os",
                ):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    self.assertTrue(payload["ok"])
                    data = payload["data"]
                    self.assertTrue(data["research_only"])
                    self.assertIn("summary", data)
                    self.assertIn("research_promotion_cleanup_plan", data)
                    self.assertIn("claim_permissions", data["summary"])
                    self.assertIn("warnings", data)
                    self.assertIn("safety_notes", data)
                    self.assertIn("Does not place orders.", data["safety_notes"])

                status_response = client.post(
                    "/api/research-promotion/entities/strategy:quant_evidence_os/status",
                    json={"promotion_status": "candidate", "reason": "Fixture manual research status."},
                )
                self.assertEqual(status_response.status_code, 200)
                status_data = status_response.json()["data"]
                self.assertEqual(status_data["record"]["promotion_status"], "candidate")
                self.assertTrue(status_data["record"]["research_only"])

    def test_service_contains_no_execution_broker_ranking_risk_or_kill_switch_mutation_calls(self) -> None:
        source = inspect.getsource(rules)
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
