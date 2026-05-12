from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "src" / "pages" / "ForecastValidationPage.jsx"
CLIENT = ROOT / "frontend" / "src" / "api" / "client.js"


class ForecastValidationFrontendStaticTests(unittest.TestCase):
    def test_forecast_validation_page_renders_hardening_plan(self) -> None:
        source = PAGE.read_text(encoding="utf-8")

        self.assertIn("Forecast Validation Hardening Plan", source)
        self.assertIn("actual paths", source)
        self.assertIn("Forecast Validation never grants trading authority", source)
        self.assertIn("does not alter execution, broker routes, risk gates, or ranking weights", source)

    def test_forecast_validation_fallback_contains_hardening_plan_and_safety(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")

        self.assertIn("FALLBACK_FORECAST_VALIDATION", source)
        self.assertIn("forecast_validation_hardening_plan", source)
        self.assertIn("claim_permissions", source)
        self.assertIn("automatic_ranking_mutation", source)
        self.assertIn("live_trading_readiness", source)
        self.assertIn("Does not change broker routes.", source)


if __name__ == "__main__":
    unittest.main()
