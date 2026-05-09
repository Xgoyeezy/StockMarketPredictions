from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.models.saas import DecisionReplayEvent, StrategyDesk, TradeDecision
from tests.productized_control_plane_test_support import build_test_client, build_test_context, clear_test_overrides


class AuditReplayApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = build_test_context(slug="audit-replay-api-test", plan_key="professional")
        self.client = build_test_client(self.context)

    def tearDown(self) -> None:
        clear_test_overrides()
        self.context.close()

    def _seed_decision(self) -> TradeDecision:
        db = self.context.db
        tenant = self.context.tenant
        strategy = StrategyDesk(tenant_id=tenant.id, desk_key="audit-alpha", name="Audit Alpha", config_json={"symbols": ["AAPL"]})
        db.add(strategy)
        db.flush()
        decision = TradeDecision(
            tenant_id=tenant.id,
            strategy_desk_id=strategy.id,
            symbol="AAPL",
            instrument_type="equity",
            side="buy",
            quantity=10,
            confidence_score=0.81,
            decision_status="approved",
            decision_reason="momentum and volume confirmation",
            signal_snapshot_json={"setup_score": 78.2},
            risk_snapshot_json={"max_order_notional": 5000, "passed": True},
            readiness_snapshot_json={"score": 84, "status": "validated"},
            market_snapshot_json={"spread_bps": 4.1},
            broker_snapshot_json={"provider": "alpaca", "paper": True},
            decision_hash="decision-aapl-1",
        )
        db.add(decision)
        db.flush()
        db.add_all(
            [
                DecisionReplayEvent(
                    tenant_id=tenant.id,
                    trade_decision_id=decision.id,
                    sequence_number=2,
                    event_type="risk_checked",
                    event_time=datetime(2026, 4, 27, 13, 59, 55, tzinfo=timezone.utc),
                    payload_json={"passed": True},
                ),
                DecisionReplayEvent(
                    tenant_id=tenant.id,
                    trade_decision_id=decision.id,
                    sequence_number=1,
                    event_type="signal_generated",
                    event_time=datetime(2026, 4, 27, 13, 59, 50, tzinfo=timezone.utc),
                    payload_json={"symbol": "AAPL"},
                ),
            ]
        )
        db.commit()
        return decision

    def test_trade_audit_replay_is_ordered_and_enveloped(self) -> None:
        decision = self._seed_decision()

        response = self.client.get(f"/api/audit/trades/{decision.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertEqual(data["decision"]["signal_snapshot"]["setup_score"], 78.2)
        self.assertTrue(data["decision"]["risk_snapshot"]["passed"])
        self.assertEqual(data["decision"]["readiness_snapshot"]["status"], "validated")
        self.assertEqual(data["decision"]["broker_snapshot"]["provider"], "alpaca")
        self.assertEqual([item["sequence_number"] for item in data["replay"]], [1, 2])

    def test_audit_export_returns_queued_job(self) -> None:
        response = self.client.post("/api/audit/export", json={"export_type": "audit_bundle"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["export"]["status"], "queued")


if __name__ == "__main__":
    unittest.main()
