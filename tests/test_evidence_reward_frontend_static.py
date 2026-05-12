from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "src" / "pages" / "EvidenceRewardPage.jsx"
CLIENT = ROOT / "frontend" / "src" / "api" / "client.js"


class EvidenceRewardFrontendStaticTests(unittest.TestCase):
    def test_evidence_reward_page_renders_cleanup_plan_and_boundaries(self) -> None:
        source = PAGE.read_text(encoding="utf-8")

        self.assertIn("Evidence Reward Cleanup Plan", source)
        self.assertIn("rewardability, blocker value, after-cost context", source)
        self.assertIn("Manual Ranking Review", source)
        self.assertIn("never change ranking weights automatically", source)

    def test_evidence_reward_fallback_contains_cleanup_plan_and_safety(self) -> None:
        source = CLIENT.read_text(encoding="utf-8")

        self.assertIn("FALLBACK_EVIDENCE_REWARD", source)
        self.assertIn("evidence_reward_cleanup_plan", source)
        self.assertIn("claim_permissions", source)
        self.assertIn("automatic_ranking_mutation", source)
        self.assertIn("live_trading_readiness", source)
        self.assertIn("Does not change broker routes.", source)


if __name__ == "__main__":
    unittest.main()
