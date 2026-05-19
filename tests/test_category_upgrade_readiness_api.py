from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.core.config import settings


def _report() -> dict:
    return {
        "status": "in_progress",
        "generated_at": "2026-05-09T00:00:00+00:00",
        "summary": {
            "gate_count": 9,
            "passed_gate_count": 1,
            "blocked_gate_count": 0,
            "ready_category_count": 0,
            "category_count": 6,
            "top_blockers": [],
            "priority_backlog": [{"key": "data_completeness_hardening"}],
            "highest_priority_build": "Safety verification and proof gates.",
        },
        "gates": [{"key": "safety_intact", "label": "Gate 1: Safety intact", "status": "passed", "passed": True, "blocking": False}],
        "categories": [{"key": "retail_trading_bot", "label": "Retail trading bot", "status": "in_progress", "current_estimated_readiness": "9/10"}],
        "category_progress": [{"category_key": "retail_trading_bot", "planning_progress_to_10_pct": 50.0}],
        "documented_scope_coverage": {"records": [], "requirement_count": 0, "complete_count": 0, "all_documented_scope_added": False},
        "backlog": [{"key": "data_completeness_hardening", "sequence": 2, "state": "next"}],
        "claims_to_avoid": ["proven_alpha"],
        "safety_notes": ["Read-only readiness evaluator. Does not affect trading.", "Does not place orders."],
        "research_only": True,
        "read_only": True,
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "can_change_broker_routes": False,
        "can_bypass_risk_gates": False,
        "can_clear_kill_switch": False,
        "can_change_ranking_weights": False,
        "can_grant_ai_order_authority": False,
        "mutation": "none",
    }


class CategoryUpgradeReadinessApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app())
        self.prefix = settings.api_prefix.rstrip("/")

    def test_category_upgrade_read_endpoints_are_read_only(self) -> None:
        with patch("backend.routers.readiness.get_category_upgrade_readiness_summary", return_value=_report()):
            for path in ("/category-upgrade", "/category-upgrade/proof-gates", "/category-upgrade/proof-chain", "/category-upgrade/backlog", "/category-upgrade/support-export"):
                response = self.client.get(f"{self.prefix}/readiness{path}")
                self.assertEqual(response.status_code, 200)
                data = response.json()["data"]
                self.assertTrue(data["research_only"])
                self.assertFalse(data["can_submit_orders"])
                self.assertFalse(data["can_submit_live_orders"])
                self.assertFalse(data["can_change_broker_routes"])
                self.assertFalse(data["can_bypass_risk_gates"])
                self.assertFalse(data["can_clear_kill_switch"])
                self.assertFalse(data["can_change_ranking_weights"])
                self.assertFalse(data["can_grant_ai_order_authority"])

    def test_category_upgrade_proof_chain_returns_read_only_records(self) -> None:
        with patch("backend.routers.readiness.get_category_upgrade_readiness_summary", return_value=_report()):
            response = self.client.get(f"{self.prefix}/readiness/category-upgrade/proof-chain")

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["summary"]["stage_count"], 9)
        self.assertTrue(data["records"])
        self.assertTrue(all(row["research_only"] for row in data["records"]))
        self.assertTrue(all(row["read_only"] for row in data["records"]))
        self.assertTrue(all(row["execution_mutation"] is False for row in data["records"]))
        self.assertTrue(all(row["broker_route_mutation"] is False for row in data["records"]))
        self.assertTrue(all(row["risk_gate_mutation"] is False for row in data["records"]))
        self.assertTrue(all(row["ranking_mutation"] is False for row in data["records"]))

    def test_category_upgrade_support_export_write_is_sanitized_metadata_only(self) -> None:
        export_result = {
            "status": "written",
            "artifact_reference": "runtime-exports/category-upgrade-readiness/test/category_upgrade_readiness_report.json",
            "artifact_name": "category_upgrade_readiness_report.json",
            "sanitized": True,
            "path_exposed_in_payload": False,
            "research_only": True,
            "read_only": True,
            "paper_route_only": True,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "can_change_broker_routes": False,
            "can_bypass_risk_gates": False,
            "can_clear_kill_switch": False,
            "can_change_ranking_weights": False,
            "can_grant_ai_order_authority": False,
            "mutation": "none",
        }
        with (
            patch("backend.routers.readiness.get_category_upgrade_readiness_summary", return_value=_report()),
            patch("backend.routers.readiness.write_category_upgrade_readiness_export", return_value=export_result),
        ):
            response = self.client.post(f"{self.prefix}/readiness/category-upgrade/support-export")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["status"], "written")
        self.assertTrue(data["sanitized"])
        self.assertFalse(data["path_exposed_in_payload"])
        self.assertFalse(data["can_submit_orders"])
        self.assertFalse(data["can_change_broker_routes"])
        self.assertFalse(data["can_bypass_risk_gates"])


if __name__ == "__main__":
    unittest.main()
