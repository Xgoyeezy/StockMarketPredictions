from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
REPORT_PAGES = (
    "AICommitteePage.jsx",
    "CategoryReadinessPage.jsx",
    "DataCompletenessPage.jsx",
    "EvidenceEdgePage.jsx",
    "EvidenceOutcomesPage.jsx",
    "EvidenceRewardPage.jsx",
    "ExecutionQualityPage.jsx",
    "ForecastValidationPage.jsx",
    "PortfolioRiskPage.jsx",
    "ProfessionalBenchmarkPage.jsx",
    "ResearchPromotionPage.jsx",
    "ScoreCalibrationPage.jsx",
    "ShadowModePage.jsx",
    "WalkForwardExperimentsPage.jsx",
)


class ProjectFinishTrackerFrontendStaticTests(unittest.TestCase):
    def test_report_pages_end_with_finish_tracker_section(self) -> None:
        for page_name in REPORT_PAGES:
            page = (ROOT / "frontend" / "src" / "pages" / page_name).read_text(encoding="utf-8")
            tracker_index = page.rfind("<FinishTrackerSection")
            last_section_index = page.rfind("</SectionCard>")

            self.assertIn("import FinishTrackerSection", page, page_name)
            self.assertGreater(tracker_index, 0, page_name)
            self.assertGreater(tracker_index, last_section_index, page_name)

    def test_shared_component_and_client_fallback_exist(self) -> None:
        component = (ROOT / "frontend" / "src" / "components" / "FinishTrackerSection.jsx").read_text(encoding="utf-8")
        client = (ROOT / "frontend" / "src" / "api" / "client.js").read_text(encoding="utf-8")

        self.assertIn("Project Finish Tracker", component)
        self.assertIn("No live-trading authorization", component)
        self.assertIn("Next safe action", component)
        self.assertIn("next_safe_action", component)
        self.assertIn("FALLBACK_FINISH_TRACKER", client)
        self.assertIn("project_finish_tracker_v2", client)
        self.assertIn("proof_first_rule", client)
        self.assertIn("paper_to_live_gate", client)
        self.assertIn("technical_analysis_evidence_setup_admission", client)
        self.assertIn("future_market_specialist_desks", client)
        self.assertGreaterEqual(client.count("finish_tracker: FALLBACK_FINISH_TRACKER"), len(REPORT_PAGES) - 1)


if __name__ == "__main__":
    unittest.main()
