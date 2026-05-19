from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "src" / "pages" / "ResearchPromotionPage.jsx"
CLIENT = ROOT / "frontend" / "src" / "api" / "client.js"


class ResearchPromotionFrontendStaticTests(unittest.TestCase):
    def test_research_promotion_page_renders_proof_gate_and_record_readiness(self) -> None:
        source = PAGE.read_text(encoding="utf-8")

        self.assertIn("Research Promotion Proof Gate", source)
        self.assertIn("Promotion traceability", source)
        self.assertIn("Benchmark linkage", source)
        self.assertIn("Walk-forward linkage", source)
        self.assertIn("Execution linkage", source)
        self.assertIn("Manual review records", source)
        self.assertIn("Research Promotion Cleanup Plan", source)
        self.assertIn("metadata-only safety", source)
        self.assertIn("Promotion Record Readiness", source)
        self.assertIn("does not place, route, approve, or configure trades", source)

    def test_research_promotion_fallback_contains_proof_fields_and_safety(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")

        self.assertIn("FALLBACK_RESEARCH_PROMOTION_PROOF", source)
        self.assertIn("proof_summary", source)
        self.assertIn("research_promotion_cleanup_plan", source)
        self.assertIn("promotion_proof_ready", source)
        self.assertIn("claim_permissions", source)
        self.assertIn("promotion_traceability_coverage", source)
        self.assertIn("manual_review_record_count", source)
        self.assertIn("writes_execution_config: false", source)
        self.assertIn("Does not change broker routes.", source)


if __name__ == "__main__":
    unittest.main()
