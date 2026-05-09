from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AiCommitteeFrontendStaticTests(unittest.TestCase):
    def test_page_route_nav_and_client_exports_exist(self) -> None:
        page = (ROOT / "frontend" / "src" / "pages" / "AICommitteePage.jsx").read_text(encoding="utf-8")
        app = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
        nav = (ROOT / "frontend" / "src" / "utils" / "navigationModel.js").read_text(encoding="utf-8")
        client = (ROOT / "frontend" / "src" / "api" / "client.js").read_text(encoding="utf-8")

        self.assertIn("AI Committee", page)
        self.assertIn("Research only. Does not affect trading.", page)
        self.assertIn("Agents cannot place orders.", page)
        self.assertIn("Agents cannot change broker routes.", page)
        self.assertIn("Agents cannot bypass risk gates.", page)
        self.assertIn("Agents cannot change ranking weights automatically.", page)
        self.assertIn("Agents cannot clear kill switches.", page)
        self.assertIn("Research Proposal Queue", page)
        self.assertIn("10/10 Readiness Backlog", page)
        self.assertIn("External Review And LLM Status", page)
        self.assertIn("Human review metadata", page)
        self.assertIn("AICommitteePage", app)
        self.assertIn('path="/ai-committee"', app)
        self.assertIn("'/ai-committee'", nav)
        self.assertIn("getAiAgentsSummary", client)
        self.assertIn("getAiAgentsLlmStatus", client)
        self.assertIn("getAiAgentsReadinessBacklog", client)
        self.assertIn("getAiAgentsExternalReview", client)
        self.assertIn("getAiAgentProposals", client)
        self.assertIn("createAiAgentProposal", client)
        self.assertIn("decideAiAgentProposal", client)
        self.assertIn("runAiAgentsCommittee", client)
        self.assertIn("runAiDeskAgent", client)


if __name__ == "__main__":
    unittest.main()
