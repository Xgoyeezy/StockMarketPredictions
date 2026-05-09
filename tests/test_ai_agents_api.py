from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.core.config import settings


def _run_payload(role: str = "portfolio_manager") -> dict:
    return {
        "status": "ready",
        "generated_at": "2026-05-09T00:00:00+00:00",
        "research_only": True,
        "authority_level": "research_only",
        "summary": {"agent_role": role},
        "record": {"memo_id": "memo_test", "agent_role": role, "research_only": True, "safety_notes": ["Research only. Does not affect trading."]},
        "warnings": [],
        "missing_fields": [],
        "safety_notes": ["Research only. Does not affect trading."],
        "memos_created": ["memo_test"],
        "agents_run": [role],
        "agents_skipped": [],
        "llm_available": False,
        "fallback_used": True,
        "safety_checks_passed": True,
        "execution_mutation": False,
        "broker_route_mutation": False,
        "risk_gate_mutation": False,
        "ranking_mutation": False,
    }


class AiAgentsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app())
        self.prefix = settings.api_prefix.rstrip("/")

    def test_get_summary_roles_memos_committee_and_safety(self) -> None:
        with (
            patch("backend.routers.ai_agents.get_ai_agents_summary", return_value={"status": "ready", "research_only": True, "summary": {"memo_count": 0}}),
            patch("backend.routers.ai_agents.list_agent_roles", return_value={"status": "ready", "research_only": True, "records": [{"role_name": "portfolio_manager"}]}),
            patch("backend.routers.ai_agents.list_agent_memos", return_value={"status": "ready", "research_only": True, "records": []}),
            patch("backend.routers.ai_agents.get_latest_committee_report", return_value={"status": "empty", "research_only": True, "record": None}),
            patch("backend.routers.ai_agents.get_ai_agents_safety", return_value={"status": "ready", "research_only": True, "safety_notes": ["Research only. Does not affect trading."]}),
        ):
            for path in ("/summary", "/roles", "/memos", "/committee/latest", "/safety"):
                response = self.client.get(f"{self.prefix}/ai-agents{path}")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["ok"])
                self.assertTrue(response.json()["data"]["research_only"])

    def test_extended_read_endpoints_are_research_only(self) -> None:
        with (
            patch("backend.routers.ai_agents.get_ai_agents_llm_status", return_value={"status": "ready", "research_only": True, "summary": {"fallback_used": True}}),
            patch("backend.routers.ai_agents.get_readiness_backlog", return_value={"status": "ready", "research_only": True, "records": [{"item_id": "data_completeness_hardening"}]}),
            patch("backend.routers.ai_agents.get_external_review_plan", return_value={"status": "ready", "research_only": True, "records": [{"review_id": "external_security_review"}]}),
            patch("backend.routers.ai_agents.list_agent_proposals", return_value={"status": "ready", "research_only": True, "records": []}),
        ):
            for path in ("/llm-status", "/readiness-backlog", "/external-review", "/proposals"):
                response = self.client.get(f"{self.prefix}/ai-agents{path}")
                self.assertEqual(response.status_code, 200)
                data = response.json()["data"]
                self.assertTrue(data["research_only"])

    def test_get_memo_by_id(self) -> None:
        with patch("backend.routers.ai_agents.get_agent_memo", return_value={"memo_id": "memo_1", "research_only": True, "safety_notes": []}):
            response = self.client.get(f"{self.prefix}/ai-agents/memos/memo_1")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["record"]["memo_id"], "memo_1")
        self.assertEqual(data["authority_level"], "research_only")

    def test_post_run_role_committee_and_desk_are_research_only(self) -> None:
        with (
            patch("backend.routers.ai_agents.run_role_agent", return_value=_run_payload("portfolio_manager")),
            patch("backend.routers.ai_agents.run_investment_committee", return_value=_run_payload("investment_committee")),
            patch("backend.routers.ai_agents.run_desk_agent", return_value=_run_payload("macro_trend")),
        ):
            requests = (
                ("post", f"{self.prefix}/ai-agents/run-role/portfolio_manager"),
                ("post", f"{self.prefix}/ai-agents/run-committee"),
                ("post", f"{self.prefix}/ai-agents/run-desk/macro_trend"),
            )
            for method, path in requests:
                with self.subTest(path=path):
                    response = getattr(self.client, method)(path)
                    self.assertEqual(response.status_code, 200)
                    data = response.json()["data"]
                    self.assertTrue(data["research_only"])
                    self.assertEqual(data["authority_level"], "research_only")
                    self.assertFalse(data["execution_mutation"])
                    self.assertFalse(data["broker_route_mutation"])
                    self.assertFalse(data["risk_gate_mutation"])
                    self.assertFalse(data["ranking_mutation"])
                    self.assertTrue(data["safety_checks_passed"])

    def test_proposal_write_endpoints_are_metadata_only(self) -> None:
        create_payload = {
            "status": "ready",
            "research_only": True,
            "authority_level": "research_only",
            "record": {"proposal_id": "proposal_test", "can_apply_automatically": False},
            "execution_mutation": False,
            "broker_route_mutation": False,
            "risk_gate_mutation": False,
            "ranking_mutation": False,
        }
        decision_payload = {
            "status": "ready",
            "research_only": True,
            "authority_level": "research_only",
            "record": {"proposal_id": "proposal_test", "decision": "approved_for_research"},
            "execution_mutation": False,
            "broker_route_mutation": False,
            "risk_gate_mutation": False,
            "ranking_mutation": False,
        }
        with (
            patch("backend.routers.ai_agents.create_agent_proposal", return_value=create_payload),
            patch("backend.routers.ai_agents.decide_agent_proposal", return_value=decision_payload),
        ):
            created = self.client.post(f"{self.prefix}/ai-agents/proposals", json={"title": "Human research follow-up"})
            decided = self.client.post(f"{self.prefix}/ai-agents/proposals/proposal_test/decision", json={"decision": "approved_for_research"})
        for response in (created, decided):
            self.assertEqual(response.status_code, 200)
            data = response.json()["data"]
            self.assertTrue(data["research_only"])
            self.assertEqual(data["authority_level"], "research_only")
            self.assertFalse(data["execution_mutation"])
            self.assertFalse(data["broker_route_mutation"])
            self.assertFalse(data["risk_gate_mutation"])
            self.assertFalse(data["ranking_mutation"])


if __name__ == "__main__":
    unittest.main()
