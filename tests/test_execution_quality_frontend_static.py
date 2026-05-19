from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ExecutionQualityFrontendStaticTests(unittest.TestCase):
    def test_page_renders_execution_proof_gate_and_client_fallback(self) -> None:
        page = (ROOT / "frontend" / "src" / "pages" / "ExecutionQualityPage.jsx").read_text(encoding="utf-8")
        client = (ROOT / "frontend" / "src" / "api" / "client.js").read_text(encoding="utf-8")

        self.assertIn("Execution Proof Gate", page)
        self.assertIn("Execution Quality Hardening Plan", page)
        self.assertIn("Cost evidence coverage", page)
        self.assertIn("Candidate-route linkage", page)
        self.assertIn("Execution Record Readiness", page)
        self.assertIn("Blocked claims", page)
        self.assertIn("Human paper-route review only", page)
        self.assertIn("proof_summary", client)
        self.assertIn("execution_quality_hardening_plan", client)
        self.assertIn("execution_proof_ready", client)
        self.assertIn("execution_quality_hardening_status", client)
        self.assertIn("execution_requirements_total", client)
        self.assertIn("Cost evidence capture", client)
        self.assertIn("automatic_execution_mutation", client)
        self.assertIn("Does not change broker routes.", client)


if __name__ == "__main__":
    unittest.main()
