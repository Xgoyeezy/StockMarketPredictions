from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProofMetricsFrontendStaticTests(unittest.TestCase):
    def test_page_route_nav_client_and_safety_copy_exist(self) -> None:
        page = (ROOT / "frontend" / "src" / "pages" / "ProofMetricsPage.jsx").read_text(encoding="utf-8")
        app = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
        nav = (ROOT / "frontend" / "src" / "utils" / "navigationModel.js").read_text(encoding="utf-8")
        client = (ROOT / "frontend" / "src" / "api" / "client.js").read_text(encoding="utf-8")

        self.assertIn("Proof Metrics", page)
        self.assertIn("Aggregates proof gaps", page)
        self.assertIn("Read-only visibility", page)
        self.assertIn("No proof of alpha", page)
        self.assertIn("No live-trading readiness", page)
        self.assertIn("Does not place orders.", page)
        self.assertIn("Does not change broker routes.", page)
        self.assertIn("Does not bypass risk gates.", page)
        self.assertIn("Does not clear kill switches.", page)
        self.assertIn("FinishTrackerSection", page)
        self.assertIn("Gate Groups", page)
        self.assertIn("Plan blockers", page)
        self.assertIn("No proof plan attached", page)
        self.assertIn("Source Reports", page)
        self.assertIn("Deferred Scope", page)
        self.assertIn("getProofMetricsSummary", page)
        self.assertIn("ProofMetricsPage", app)
        self.assertIn('path="/proof-metrics"', app)
        self.assertIn("'/proof-metrics'", nav)
        self.assertIn("FALLBACK_PROOF_METRICS_DASHBOARD", client)
        self.assertIn("proof_plan_open_items", client)
        self.assertIn("getProofMetricsSummary", client)
        self.assertIn("'/proof-metrics/summary'", client)


if __name__ == "__main__":
    unittest.main()
