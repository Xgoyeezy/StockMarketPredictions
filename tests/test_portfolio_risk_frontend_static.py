from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "src" / "pages" / "PortfolioRiskPage.jsx"
CLIENT = ROOT / "frontend" / "src" / "api" / "client.js"


class PortfolioRiskFrontendStaticTests(unittest.TestCase):
    def test_portfolio_risk_page_renders_proof_gate_and_record_readiness(self) -> None:
        source = PAGE.read_text(encoding="utf-8")

        self.assertIn("Portfolio Risk Proof Gate", source)
        self.assertIn("Portfolio Risk Cleanup Plan", source)
        self.assertIn("Portfolio risk coverage", source)
        self.assertIn("Factor coverage", source)
        self.assertIn("Liquidity coverage", source)
        self.assertIn("Blocked claims", source)
        self.assertIn("Internal review", source)
        self.assertIn("Portfolio Risk Record Readiness", source)
        self.assertIn("does not loosen gates or change risk limits", source)

    def test_portfolio_risk_fallback_contains_proof_fields_and_safety(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")

        self.assertIn("FALLBACK_PORTFOLIO_RISK_INTELLIGENCE", source)
        self.assertIn("proof_summary", source)
        self.assertIn("portfolio_risk_cleanup_plan", source)
        self.assertIn("portfolio_risk_proof_ready", source)
        self.assertIn("portfolio_risk_cleanup_status", source)
        self.assertIn("portfolio_risk_coverage", source)
        self.assertIn("writes_risk_limits: false", source)
        self.assertIn("Does not change broker routes.", source)


if __name__ == "__main__":
    unittest.main()
