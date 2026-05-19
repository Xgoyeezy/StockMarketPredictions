from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ScoreCalibrationFrontendStaticTests(unittest.TestCase):
    def test_page_renders_calibration_proof_gate_and_client_fallback(self) -> None:
        page = (ROOT / "frontend" / "src" / "pages" / "ScoreCalibrationPage.jsx").read_text(encoding="utf-8")
        client = (ROOT / "frontend" / "src" / "api" / "client.js").read_text(encoding="utf-8")

        self.assertIn("Calibration Proof Gate", page)
        self.assertIn("Score Calibration Hardening Plan", page)
        self.assertIn("after-cost lift", page)
        self.assertIn("Feature Readiness", page)
        self.assertIn("manual-review-only recommendations", page)
        self.assertIn("Blocked claims", page)
        self.assertIn("Human research review only", page)
        self.assertIn("proof_summary", client)
        self.assertIn("score_calibration_hardening_plan", client)
        self.assertIn("calibration_proof_ready", client)
        self.assertIn("score_calibration_hardening_status", client)
        self.assertIn("calibration_requirements_total", client)
        self.assertIn("Rewardable score sample", client)
        self.assertIn("public_score_quality_claim", client)
        self.assertIn("writes_ranking_config: false", client)


if __name__ == "__main__":
    unittest.main()
