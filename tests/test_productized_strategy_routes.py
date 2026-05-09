from __future__ import annotations

import unittest

from tests.productized_control_plane_test_support import build_test_client, build_test_context, clear_test_overrides


class ProductizedStrategyRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = build_test_context(slug="productized-routes-test", plan_key="professional")
        self.client = build_test_client(self.context)

    def tearDown(self) -> None:
        clear_test_overrides()
        self.context.close()

    def _create_strategy(self) -> str:
        response = self.client.post(
            "/api/strategies",
            json={
                "name": "Systematic Equities v1",
                "desk_key": "systematic-equities-v1",
                "description": "Paper-first momentum strategy",
                "allocation_cap": 25000,
                "symbols": ["SPY", "QQQ", "AAPL"],
                "mode": "paper",
                "risk_profile": {"max_daily_loss": 500, "max_order_notional": 5000},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["ok"])
        return payload["data"]["strategy"]["id"]

    def test_strategy_crud_versions_actions_and_metrics_are_enveloped(self) -> None:
        strategy_id = self._create_strategy()

        for method, path, body in [
            ("get", "/api/strategies", None),
            ("get", f"/api/strategies/{strategy_id}", None),
            ("patch", f"/api/strategies/{strategy_id}", {"description": "Updated"}),
            ("post", f"/api/strategies/{strategy_id}/versions", {"name": "Revision 2"}),
            ("get", f"/api/strategies/{strategy_id}/versions", None),
            ("post", f"/api/strategies/{strategy_id}/start", {"deployment_mode": "paper"}),
            ("post", f"/api/strategies/{strategy_id}/stop", {}),
            ("post", f"/api/strategies/{strategy_id}/promote", {"from_stage": "draft", "to_stage": "paper"}),
            ("post", f"/api/strategies/{strategy_id}/rollback", {}),
            ("get", f"/api/strategies/{strategy_id}/runs", None),
            ("get", f"/api/strategies/{strategy_id}/metrics", None),
        ]:
            request = getattr(self.client, method)
            response = request(path, json=body) if body is not None and method != "get" else request(path)
            self.assertEqual(response.status_code, 200, f"{method.upper()} {path}: {response.text}")
            self.assertTrue(response.json()["ok"])

    def test_automation_risk_audit_and_execution_routes_are_enveloped(self) -> None:
        strategy_id = self._create_strategy()

        route_calls = [
            ("get", "/api/automation/status", None),
            ("get", f"/api/automation/strategies/{strategy_id}/status", None),
            ("post", f"/api/automation/strategies/{strategy_id}/paper/start", {"deployment_mode": "paper"}),
            ("post", f"/api/automation/strategies/{strategy_id}/paper/stop", {}),
            ("post", f"/api/automation/strategies/{strategy_id}/live/request", {"deployment_mode": "live"}),
            ("post", f"/api/automation/strategies/{strategy_id}/kill", {"reason": "test"}),
            ("get", "/api/automation/events", None),
            ("post", "/api/risk/policies", {"strategy_id": strategy_id, "scope": "strategy", "status": "active", "max_order_notional": 5000}),
            ("get", "/api/risk/policies", None),
            ("post", "/api/risk/check", {"strategy_id": strategy_id, "symbol": "AAPL", "instrument_type": "equity", "side": "buy", "quantity": 1, "expected_notional": 1000}),
            ("get", "/api/risk/events", None),
            ("post", "/api/risk/kill-switch", {"strategy_id": strategy_id, "reason": "test"}),
            ("post", "/api/risk/kill-switch/clear", {"strategy_id": strategy_id, "reason": "test"}),
            ("get", "/api/audit/events", None),
            ("post", "/api/audit/export", {"export_type": "audit_bundle"}),
            ("get", "/api/execution-analytics/summary", None),
            ("get", f"/api/execution-analytics/strategies/{strategy_id}", None),
            ("get", "/api/live/status", None),
            ("get", "/api/live/kill-switch", None),
            ("get", "/api/live/orders", None),
            ("get", "/api/orgs/paper-execution-router", None),
            ("get", "/api/orgs/internal-broker-router", None),
        ]

        for method, path, body in route_calls:
            request = getattr(self.client, method)
            response = request(path, json=body) if body is not None and method != "get" else request(path)
            self.assertEqual(response.status_code, 200, f"{method.upper()} {path}: {response.text}")
            self.assertTrue(response.json()["ok"])

    def test_live_status_does_not_expose_native_broker_flag(self) -> None:
        response = self.client.get("/api/live/status")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["ok"])
        flags = payload["data"]["feature_flags"]
        self.assertIn("live_trading", flags)
        self.assertIn("managed_advisory", flags)
        self.assertNotIn("proprietary_broker", flags)


if __name__ == "__main__":
    unittest.main()
