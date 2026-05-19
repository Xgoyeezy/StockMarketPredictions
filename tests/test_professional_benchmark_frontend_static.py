from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProfessionalBenchmarkFrontendStaticTests(unittest.TestCase):
    def test_page_renders_benchmark_proof_gate_and_client_fallback(self) -> None:
        page = (ROOT / "frontend" / "src" / "pages" / "ProfessionalBenchmarkPage.jsx").read_text(encoding="utf-8")
        client = (ROOT / "frontend" / "src" / "api" / "client.js").read_text(encoding="utf-8")

        self.assertIn("Benchmark Proof Gate", page)
        self.assertIn("Benchmark Hardening Plan", page)
        self.assertIn("baseline-relative edge, score-bucket lift, after-cost reward", page)
        self.assertIn("This is not an alpha or performance claim.", page)
        self.assertIn("Blocked claims", page)
        self.assertIn("Human research review only", page)
        self.assertIn("After-cost edge", page)
        self.assertIn("Safe next action", page)
        self.assertIn("proof_summary", client)
        self.assertIn("benchmark_hardening_plan", client)
        self.assertIn("benchmark_proof_ready", client)
        self.assertIn("benchmark_hardening_status", client)
        self.assertIn("Rewardable sample and data quality", client)
        self.assertIn("live_trading_readiness", client)
        self.assertIn("timeout: 90000", client)
        self.assertIn("Do not claim proven alpha", client)


if __name__ == "__main__":
    unittest.main()
