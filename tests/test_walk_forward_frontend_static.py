from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WalkForwardFrontendStaticTests(unittest.TestCase):
    def test_page_renders_walk_forward_proof_gate_and_client_fallback(self) -> None:
        page = (ROOT / "frontend" / "src" / "pages" / "WalkForwardExperimentsPage.jsx").read_text(encoding="utf-8")
        client = (ROOT / "frontend" / "src" / "api" / "client.js").read_text(encoding="utf-8")

        self.assertIn("Walk-Forward Proof Gate", page)
        self.assertIn("Walk-Forward Validation Plan", page)
        self.assertIn("frozen out-of-sample experiments", page)
        self.assertIn("Repeatability and live-readiness claims stay blocked", page)
        self.assertIn("No lookahead", page)
        self.assertIn("After-cost support", page)
        self.assertIn("Blocked claims", page)
        self.assertIn("Human research review only", page)
        self.assertIn("Record Readiness", page)
        self.assertIn("proof_summary", client)
        self.assertIn("walk_forward_validation_plan", client)
        self.assertIn("walk_forward_proof_ready", client)
        self.assertIn("walk_forward_validation_status", client)
        self.assertIn("Create and freeze an experiment snapshot", client)
        self.assertIn("live_trading_readiness", client)
        self.assertIn("walk_forward_requirements_total", client)


if __name__ == "__main__":
    unittest.main()
