from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DataCompletenessFrontendStaticTests(unittest.TestCase):
    def test_page_renders_proof_field_coverage_and_client_fallback(self) -> None:
        page = (ROOT / "frontend" / "src" / "pages" / "DataCompletenessPage.jsx").read_text(encoding="utf-8")
        client = (ROOT / "frontend" / "src" / "api" / "client.js").read_text(encoding="utf-8")

        self.assertIn("Proof Field Coverage", page)
        self.assertIn("Data Cleanup Plan", page)
        self.assertIn("Forward returns, baselines, actuals, costs, regimes, and reward fields", page)
        self.assertIn("Ordered manual evidence cleanup tasks", page)
        self.assertIn("Safe next action", page)
        self.assertIn("This does not infer or fabricate missing data.", page)
        self.assertIn("Requires rewardable candidates, benchmark fields, and proof-field coverage", page)
        self.assertIn("data_cleanup_plan", client)
        self.assertIn("Missing forward returns", client)
        self.assertIn("Missing baselines", client)
        self.assertIn("Missing execution evidence", client)
        self.assertIn("proof_field_coverage", client)
        self.assertIn("proof_field_ready", client)
        self.assertIn("cleanup_plan_status", client)
        self.assertIn("Proof-field coverage is research-only", client)


if __name__ == "__main__":
    unittest.main()
