from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "src" / "pages" / "ShadowModePage.jsx"
CLIENT = ROOT / "frontend" / "src" / "api" / "client.js"


class ShadowModeFrontendStaticTests(unittest.TestCase):
    def test_shadow_mode_page_renders_proof_gate_and_record_readiness(self) -> None:
        source = PAGE.read_text(encoding="utf-8")

        self.assertIn("Shadow Mode Proof Gate", source)
        self.assertIn("Same-opportunity coverage", source)
        self.assertIn("Human contract coverage", source)
        self.assertIn("System contract coverage", source)
        self.assertIn("Cost/risk coverage", source)
        self.assertIn("Human vs System Validation Plan", source)
        self.assertIn("manual-review only", source)
        self.assertIn("Shadow Record Readiness", source)
        self.assertIn("does not place, route, approve, or configure trades", source)

    def test_shadow_mode_fallback_contains_proof_fields_and_safety(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")

        self.assertIn("FALLBACK_SHADOW_MODE", source)
        self.assertIn("proof_summary", source)
        self.assertIn("shadow_validation_plan", source)
        self.assertIn("shadow_proof_ready", source)
        self.assertIn("claim_permissions", source)
        self.assertIn("same_opportunity_coverage", source)
        self.assertIn("system_decision_quality_delta", source)
        self.assertIn("Does not change broker routes.", source)


if __name__ == "__main__":
    unittest.main()
