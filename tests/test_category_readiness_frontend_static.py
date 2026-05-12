from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CategoryReadinessFrontendStaticTests(unittest.TestCase):
    def test_page_route_nav_and_client_exports_exist(self) -> None:
        page = (ROOT / "frontend" / "src" / "pages" / "CategoryReadinessPage.jsx").read_text(encoding="utf-8")
        app = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
        nav = (ROOT / "frontend" / "src" / "utils" / "navigationModel.js").read_text(encoding="utf-8")
        client = (ROOT / "frontend" / "src" / "api" / "client.js").read_text(encoding="utf-8")

        self.assertIn("10/10 Category Readiness", page)
        self.assertIn("Read-only readiness evaluator. Does not affect trading.", page)
        self.assertIn("Does not place orders.", page)
        self.assertIn("Does not change broker routes.", page)
        self.assertIn("Does not bypass risk gates.", page)
        self.assertIn("Does not clear kill switches.", page)
        self.assertIn("Does not change ranking weights automatically.", page)
        self.assertIn("Does not grant AI order authority.", page)
        self.assertIn("No proof of alpha", page)
        self.assertIn("Proof decides priority", page)
        self.assertIn("deferred expansion items", page)
        self.assertIn("Proof Chain", page)
        self.assertIn("Next safe action", page)
        self.assertIn("Do not build/claim yet", page)
        self.assertIn("Sanitized Support Export", page)
        self.assertIn("CategoryReadinessPage", app)
        self.assertIn('path="/category-readiness"', app)
        self.assertIn("'/category-readiness'", nav)
        self.assertIn("getCategoryUpgradeReadiness", client)
        self.assertIn("getCategoryUpgradeProofGates", client)
        self.assertIn("getCategoryUpgradeProofChain", client)
        self.assertIn("getCategoryUpgradeBacklog", client)
        self.assertIn("getCategoryUpgradeSupportExport", client)
        self.assertIn("writeCategoryUpgradeSupportExport", client)


if __name__ == "__main__":
    unittest.main()
