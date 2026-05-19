from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from backend.services.category_upgrade_readiness_service import build_category_upgrade_readiness_report
from backend.services.candidate_outcome_stamping_service import build_evidence_outcomes_report
from backend.services.data_completeness_audit import build_data_completeness_report
from backend.services.evidence_edge_analytics import build_evidence_edge_report
from backend.services.evidence_reward_engine import build_evidence_reward_report
from backend.services.execution_quality_tca import build_execution_quality_tca_report
from backend.services.forecast_validation_engine import get_forecast_validation_summary
from backend.services.hedge_fund_ai_agents import get_ai_agents_summary
from backend.services.human_system_shadow_mode import build_shadow_mode_report
from backend.services.portfolio_risk_intelligence import build_portfolio_risk_report
from backend.services.professional_benchmark_suite import build_professional_benchmark_report
from backend.services.project_finish_tracker import build_project_finish_tracker
from backend.services.proof_metrics_dashboard import build_proof_metrics_dashboard_report
from backend.services.research_promotion_rules import build_research_promotion_report
from backend.services.score_calibration_attribution import build_score_calibration_report
from backend.services.walk_forward_experiment_registry import get_walk_forward_summary


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TRACKER_IDS = {
    "post_implementation_verification",
    "data_completeness_hardening",
    "candidate_outcome_baseline_stamping",
    "professional_benchmark_proof",
    "walk_forward_validation",
    "score_calibration_feature_attribution",
    "execution_quality_tca",
    "risk_gate_audit_trail_hardening",
    "portfolio_risk_intelligence",
    "human_system_shadow_mode",
    "research_promotion_rules",
    "evidence_reward_and_blocker_value",
    "forecast_validation",
    "proof_metrics_dashboard",
    "proof_first_backlog_scoring",
    "technical_analysis_evidence_setup_admission",
    "ai_committee_research_layer",
    "operator_experience_docs",
    "paper_to_live_gate",
    "future_market_specialist_desks",
    "future_candidate_fusion_market_strategy_benchmark",
    "future_off_exchange_liquidity_research",
    "future_broker_neutral_provider_strategy",
    "future_visual_strategy_evidence_builder",
    "future_governance_institutional_controls",
    "future_cpp_hft_feasibility",
}


def _assert_tracker(testcase: unittest.TestCase, report: dict) -> None:
    tracker = report.get("finish_tracker")
    testcase.assertIsInstance(tracker, dict)
    testcase.assertEqual(tracker["scope"], "project_wide")
    testcase.assertEqual(tracker["summary"]["total_items"], len(EXPECTED_TRACKER_IDS))
    testcase.assertGreaterEqual(tracker["summary"]["critical_open_items"], 1)
    testcase.assertIn("items", tracker)


class ProjectFinishTrackerTests(unittest.TestCase):
    def test_tracker_is_project_wide_and_complete(self) -> None:
        tracker = build_project_finish_tracker(report_name="unit_test")
        ids = {item["id"] for item in tracker["items"]}

        self.assertEqual(tracker["version"], "project_finish_tracker_v2")
        self.assertEqual(tracker["report_name"], "unit_test")
        self.assertEqual(ids, EXPECTED_TRACKER_IDS)
        self.assertIn("docs/TECHNICAL_ANALYSIS_EVIDENCE_SETUP_RESEARCH.md", tracker["source_docs"])
        self.assertEqual(tracker["summary"]["critical_open_items"], 5)
        self.assertIn("do not authorize live trading", tracker["summary"]["safe_boundary"].lower())
        self.assertIn("ai order authority", tracker["summary"]["safe_boundary"].lower())
        self.assertIn("broker-route changes", tracker["summary"]["safe_boundary"].lower())
        self.assertIn("risk-gate bypass", tracker["summary"]["safe_boundary"].lower())
        self.assertIn("ranking-weight mutation", tracker["summary"]["safe_boundary"].lower())
        self.assertIn("technical_analysis_evidence_setup_admission", ids)
        self.assertIn("docs/PROOF_METRICS_DASHBOARD.md", tracker["source_docs"])
        self.assertEqual(tracker["summary"]["status_counts"]["deferred"], 7)
        self.assertEqual(tracker["summary"]["status_counts"]["done"], 1)
        self.assertEqual(tracker["summary"]["status_counts"]["in_progress"], 11)
        self.assertIn("proof decides priority", tracker["summary"]["proof_first_rule"].lower())
        for item in tracker["items"]:
            with self.subTest(item=item["id"]):
                self.assertIsInstance(item.get("next_safe_action"), str)
                self.assertTrue(item["next_safe_action"].strip())
                self.assertEqual(item["next_safe_action"], item["remaining_work"][0])

    def test_major_report_builders_include_finish_tracker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty_frame = pd.DataFrame()
            reports = [
                build_professional_benchmark_report(records=[], forecast_records=[], generated_at="2026-05-09T00:00:00Z"),
                build_data_completeness_report(
                    candidate_records=[],
                    forecast_records=[],
                    ai_records=[],
                    blocker_records=[],
                    missed_move_records=[],
                    paper_trade_records=[],
                    execution_records=[],
                    benchmark_records=[],
                    generated_at="2026-05-09T00:00:00Z",
                ),
                build_evidence_outcomes_report(tenant_slug="test-tenant", root=root),
                build_evidence_edge_report(tenant_slug="test-tenant", root=root, open_trades=empty_frame, closed_trades=empty_frame, pending_orders=empty_frame),
                build_evidence_reward_report(tenant_slug="test-tenant", root=root, open_trades=empty_frame, closed_trades=empty_frame, pending_orders=empty_frame),
                get_forecast_validation_summary(),
                get_walk_forward_summary(store_path=root / "walk_forward.json"),
                build_score_calibration_report(records=[], benchmark_report={"records": []}, generated_at="2026-05-09T00:00:00Z"),
                build_execution_quality_tca_report(records=[], generated_at="2026-05-09T00:00:00Z"),
                build_portfolio_risk_report(records=[], generated_at="2026-05-09T00:00:00Z"),
                build_shadow_mode_report(records=[], store_path=root / "shadow.json", generated_at="2026-05-09T00:00:00Z"),
                build_category_upgrade_readiness_report(generated_at="2026-05-09T00:00:00Z"),
                build_proof_metrics_dashboard_report(source_reports={}, generated_at="2026-05-09T00:00:00Z"),
                get_ai_agents_summary(storage_path=root / "ai_agents.json"),
            ]
            promotion = build_research_promotion_report(
                benchmark_report=reports[0],
                completeness_report=reports[1],
                walk_forward_report=reports[6],
                manual_statuses={},
                generated_at="2026-05-09T00:00:00Z",
            )
            reports.append(promotion)

        for report in reports:
            with self.subTest(status=report.get("status")):
                _assert_tracker(self, report)

    def test_report_docs_end_with_project_tracker(self) -> None:
        for doc_name in ("BENCHMARK_TRIAGE_REPORT.md", "POST_IMPLEMENTATION_VERIFICATION_REPORT.md", "TECHNICAL_ANALYSIS_EVIDENCE_SETUP_RESEARCH.md", "PROOF_METRICS_DASHBOARD.md"):
            text = (ROOT / "docs" / doc_name).read_text(encoding="utf-8")
            tracker_index = text.rfind("Project Finish Tracker")
            safety_index = text.rfind("Safety boundary: tracker items")

            self.assertGreater(tracker_index, 0, doc_name)
            self.assertGreater(safety_index, tracker_index, doc_name)
            self.assertIn("Post-Implementation Verification", text[tracker_index:])
            self.assertIn("Technical Analysis evidence setup admission", text[tracker_index:])
            self.assertIn("Paper-to-live proof gate", text[tracker_index:])
            self.assertIn("Market Specialist Desk registry", text[tracker_index:])


if __name__ == "__main__":
    unittest.main()
