from __future__ import annotations

import inspect
import unittest

from backend.services import proof_metrics_dashboard as proof_metrics
from backend.services.proof_metrics_dashboard import build_proof_metrics_dashboard_report
from tests.productized_control_plane_test_support import build_test_client, build_test_context, clear_test_overrides


def _ready_reports() -> dict[str, dict[str, object]]:
    return {
        "data_completeness": {
            "status": "ready",
            "summary": {"proof_field_coverage_rate": 1.0, "proof_field_ready": True, "benchmark_ready": True},
            "finish_tracker": {},
        },
        "evidence_outcomes": {
            "status": "ready",
            "summary": {"baseline_coverage_rate": 1.0, "execution_cost_coverage_rate": 1.0},
            "finish_tracker": {},
        },
        "professional_benchmark": {
            "status": "ready_for_human_review",
            "summary": {"benchmark_proof_requirements_passed": 6, "benchmark_proof_requirements_total": 6, "benchmark_proof_ready": True},
            "proof_summary": {"proof_ready": True},
            "finish_tracker": {},
        },
        "walk_forward": {
            "status": "ready",
            "summary": {"walk_forward_requirements_passed": 6, "walk_forward_requirements_total": 6, "walk_forward_proof_ready": True},
            "proof_summary": {"proof_ready": True},
            "finish_tracker": {},
        },
        "score_calibration": {
            "status": "ready",
            "summary": {"calibration_requirements_passed": 6, "calibration_requirements_total": 6, "calibration_proof_ready": True},
            "proof_summary": {"proof_ready": True},
            "finish_tracker": {},
        },
        "evidence_reward": {
            "status": "ready",
            "summary": {"rewardable_prediction_count": 3},
            "finish_tracker": {},
        },
        "execution_quality": {
            "status": "ready",
            "summary": {"execution_quality_requirements_passed": 6, "execution_quality_requirements_total": 6, "execution_quality_proof_ready": True},
            "proof_summary": {"proof_ready": True, "summary": {"passed_requirement_count": 6, "requirement_count": 6}},
            "finish_tracker": {},
        },
        "risk_audit": {
            "status": "ready_for_human_review",
            "risk_audit_hardening_plan": {
                "summary": {
                    "ready_item_count": 7,
                    "item_count": 7,
                    "claim_permissions": {"cautious_internal_risk_audit_review": True},
                }
            },
            "finish_tracker": {},
        },
        "portfolio_risk": {
            "status": "ready",
            "summary": {"portfolio_risk_requirements_passed": 6, "portfolio_risk_requirements_total": 6, "portfolio_risk_proof_ready": True},
            "proof_summary": {"proof_ready": True, "summary": {"passed_requirement_count": 6, "requirement_count": 6}},
            "finish_tracker": {},
        },
        "forecast_validation": {
            "status": "ready",
            "summary": {"validation_requirements_passed": 3, "validation_requirements_total": 3, "forecast_validation_ready": True},
            "finish_tracker": {},
        },
        "shadow_mode": {
            "status": "ready",
            "summary": {"comparison_count": 3, "shadow_proof_ready": True},
            "proof_summary": {"proof_ready": True},
            "finish_tracker": {},
        },
        "research_promotion": {
            "status": "ready",
            "summary": {"promotion_requirements_passed": 6, "promotion_requirements_total": 6, "promotion_proof_ready": True},
            "proof_summary": {"proof_ready": True, "summary": {"passed_requirement_count": 6, "requirement_count": 6}},
            "finish_tracker": {},
        },
        "ai_committee": {
            "status": "ready",
            "summary": {"memo_count": 0},
            "safety_checks_passed": True,
            "finish_tracker": {},
        },
    }


class ProofMetricsDashboardTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_test_overrides()

    def test_dashboard_reports_gate_blockers_and_read_only_boundary(self) -> None:
        reports = _ready_reports()
        reports["data_completeness"]["summary"] = {"proof_field_coverage_rate": 0.25, "proof_field_ready": False, "benchmark_ready": False}
        reports["professional_benchmark"]["summary"] = {
            "benchmark_proof_requirements_passed": 2,
            "benchmark_proof_requirements_total": 6,
            "benchmark_proof_ready": False,
        }
        reports["professional_benchmark"]["proof_summary"] = {"proof_ready": False}

        report = build_proof_metrics_dashboard_report(source_reports=reports, generated_at="2026-05-12T00:00:00Z")

        self.assertEqual(report["status"], "blocked_by_evidence")
        self.assertTrue(report["research_only"])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["can_submit_orders"])
        self.assertFalse(report["can_submit_live_orders"])
        self.assertFalse(report["can_change_broker_routes"])
        self.assertFalse(report["can_bypass_risk_gates"])
        self.assertFalse(report["can_clear_kill_switch"])
        self.assertEqual(report["mutation"], "none")
        self.assertIn("finish_tracker", report)
        self.assertGreaterEqual(report["summary"]["critical_open_metric_count"], 1)
        self.assertIn("Proof-field coverage", report["summary"]["top_blockers"])
        data_gate = next(row for row in report["gate_groups"] if row["gate"] == "Data Gate")
        self.assertEqual(data_gate["status"], "blocked_by_evidence")
        self.assertIn("benchmark_ready", data_gate["blocked_claims"])

    def test_all_ready_inputs_allow_human_review_but_not_trading(self) -> None:
        report = build_proof_metrics_dashboard_report(source_reports=_ready_reports(), generated_at="2026-05-12T00:00:00Z")

        self.assertEqual(report["status"], "ready_for_human_review")
        self.assertTrue(report["summary"]["proof_ready"])
        self.assertEqual(report["summary"]["open_metric_count"], 0)
        self.assertFalse(report["can_submit_orders"])
        self.assertFalse(report["can_submit_live_orders"])
        self.assertIn("do not authorize live trading", report["finish_tracker"]["summary"]["safe_boundary"].lower())

    def test_source_unavailable_degrades_without_crashing(self) -> None:
        report = build_proof_metrics_dashboard_report(source_reports={"data_completeness": _ready_reports()["data_completeness"]})

        self.assertEqual(report["status"], "blocked_by_evidence")
        self.assertGreater(report["summary"]["source_unavailable_count"], 0)
        self.assertIn("unavailable", " ".join(report["warnings"]).lower())
        self.assertTrue(any(row["status"] == "source_unavailable" for row in report["metrics"]))

    def test_attached_proof_plan_blocks_ready_status_until_plan_is_clean(self) -> None:
        reports = _ready_reports()
        reports["forecast_validation"]["forecast_validation_hardening_plan"] = {
            "status": "blocked_by_evidence",
            "summary": {
                "item_count": 7,
                "open_item_count": 2,
                "critical_open_items": 1,
                "top_hardening_item": "Actual path coverage",
            },
        }

        report = build_proof_metrics_dashboard_report(source_reports=reports, generated_at="2026-05-12T00:00:00Z")
        forecast_row = next(row for row in report["metrics"] if row["key"] == "forecast_validation")

        self.assertEqual(report["status"], "blocked_by_evidence")
        self.assertEqual(forecast_row["status"], "blocked_by_evidence")
        self.assertEqual(forecast_row["value"], 3)
        self.assertEqual(forecast_row["target"], 3)
        self.assertEqual(forecast_row["proof_plan_open_items"], 2)
        self.assertEqual(forecast_row["proof_plan_critical_open_items"], 1)
        self.assertEqual(forecast_row["proof_plan_top_item"], "Actual path coverage")
        self.assertFalse(forecast_row["can_submit_orders"])
        self.assertFalse(forecast_row["can_change_ranking_weights"])

    def test_api_route_returns_summary(self) -> None:
        context = build_test_context(slug="proof-metrics-test", plan_key="professional")
        client = build_test_client(context)

        response = client.get("/api/proof-metrics/summary")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertTrue(data["research_only"])
        self.assertTrue(data["proof_visibility_only"])
        self.assertFalse(data["can_submit_orders"])
        self.assertFalse(data["can_submit_live_orders"])
        self.assertIn("metrics", data)
        self.assertIn("gate_groups", data)
        self.assertIn("source_reports", data)
        self.assertIn("finish_tracker", data)
        context.close()

    def test_service_contains_no_execution_broker_risk_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(proof_metrics)
        forbidden_calls = (
            "place_order(",
            "submit_order(",
            "set_broker_route(",
            "update_ranking_weight(",
            "update_risk_config(",
            "enable_live_trading(",
            "route_order(",
            "clear_kill_switch(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
