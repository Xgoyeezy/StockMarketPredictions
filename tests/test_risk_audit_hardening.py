from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timezone

from backend.models.saas import AuditEvent, AuditExport, DecisionReplayEvent, RiskEvent, RiskPolicy, StrategyDesk, TradeDecision
from backend.services import risk_audit_hardening as hardening
from backend.services.risk_audit_hardening import build_risk_audit_hardening_report
from tests.productized_control_plane_test_support import build_test_client, build_test_context, clear_test_overrides


def _policy(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "policy-1",
        "scope": "tenant",
        "status": "active",
        "max_daily_loss": 500.0,
        "max_order_notional": 5000.0,
        "max_open_positions": 5,
    }
    payload.update(overrides)
    return payload


def _risk_event(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "risk-event-1",
        "event_type": "risk.check_failed",
        "severity": "high",
        "action_taken": "blocked",
        "payload": {"symbol": "AAPL", "expected_notional": 10000},
        "created_at": "2026-05-10T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def _audit_event(event_type: str = "risk.kill_switch_activated", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "audit-event-1",
        "event_type": event_type,
        "actor_email": "operator@example.test",
        "payload": {"reason": "operator review", "affected_count": 1, "export_type": "audit_bundle"},
        "created_at": "2026-05-10T12:01:00Z",
    }
    payload.update(overrides)
    return payload


def _trade_replay(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "decision-1",
        "risk_snapshot": {"passed": True},
        "readiness_snapshot": {"status": "validated"},
        "market_snapshot": {"spread_bps": 4.1},
        "broker_snapshot": {"provider": "alpaca", "paper": True},
        "replay_event_count": 2,
    }
    payload.update(overrides)
    return payload


class RiskAuditHardeningTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_test_overrides()

    def test_report_blocks_claims_when_evidence_is_missing(self) -> None:
        report = build_risk_audit_hardening_report(generated_at="2026-05-10T00:00:00Z")
        plan = report["risk_audit_hardening_plan"]
        by_key = {row["key"]: row for row in plan["items"]}

        self.assertEqual(report["status"], "blocked_by_evidence")
        self.assertEqual(plan["status"], "blocked_by_evidence")
        self.assertEqual(plan["summary"]["top_hardening_item"], "Active risk policy evidence")
        self.assertGreaterEqual(plan["summary"]["critical_open_items"], 3)
        self.assertEqual(by_key["active_risk_policy"]["status"], "no_records")
        self.assertEqual(by_key["read_only_governance_boundary"]["status"], "ready")
        self.assertIn("risk_gate_authority_claim", plan["summary"]["blocked_claims"])
        self.assertFalse(plan["summary"]["claim_permissions"]["cautious_internal_risk_audit_review"])
        self.assertFalse(report["summary"]["claim_permissions"]["live_trading_readiness"])
        self.assertFalse(any(row["changes_broker_routes"] for row in plan["items"]))
        self.assertFalse(any(row["clears_kill_switch"] for row in plan["items"]))
        self.assertFalse(any(row["changes_execution"] for row in plan["items"]))
        self.assertFalse(any(row["changes_order_submission"] for row in plan["items"]))
        self.assertFalse(any(row["changes_risk_gates"] for row in plan["items"]))
        self.assertFalse(any(row["changes_ranking_weights"] for row in plan["items"]))
        self.assertFalse(any(row["can_change_broker_routes"] for row in plan["items"]))
        self.assertFalse(any(row["can_bypass_risk_gates"] for row in plan["items"]))
        self.assertFalse(any(row["can_change_ranking_weights"] for row in plan["items"]))
        self.assertFalse(any(row["can_grant_ai_order_authority"] for row in plan["items"]))
        for action in plan["safe_next_actions"]:
            self.assertFalse(action["changes_execution"])
            self.assertFalse(action["changes_order_submission"])
            self.assertFalse(action["changes_broker_routes"])
            self.assertFalse(action["changes_risk_gates"])
            self.assertFalse(action["changes_ranking_weights"])
            self.assertFalse(action["clears_kill_switch"])
            self.assertFalse(action["can_change_broker_routes"])
            self.assertFalse(action["can_bypass_risk_gates"])
            self.assertFalse(action["can_change_ranking_weights"])
            self.assertFalse(action["can_grant_ai_order_authority"])

    def test_report_allows_internal_review_only_when_hardening_evidence_is_complete(self) -> None:
        report = build_risk_audit_hardening_report(
            risk_policies=[_policy()],
            risk_events=[_risk_event()],
            audit_events=[
                _audit_event("risk.kill_switch_activated"),
                _audit_event("audit.export_queued", id="audit-event-2", payload={"export_type": "audit_bundle"}),
            ],
            audit_exports=[{"id": "export-1", "status": "queued", "export_type": "audit_bundle", "file_path": None}],
            trade_replays=[_trade_replay()],
            safety_summary={"record_count": 1, "status": "ready"},
            generated_at="2026-05-10T00:00:00Z",
        )
        plan = report["risk_audit_hardening_plan"]

        self.assertEqual(plan["status"], "ready_for_human_review")
        self.assertTrue(plan["summary"]["claim_permissions"]["cautious_internal_risk_audit_review"])
        self.assertFalse(plan["summary"]["claim_permissions"]["risk_gate_authority_claim"])
        self.assertFalse(plan["summary"]["claim_permissions"]["kill_switch_clearance"])
        self.assertFalse(plan["summary"]["claim_permissions"]["live_trading_readiness"])
        self.assertEqual(plan["summary"]["open_item_count"], 0)
        self.assertTrue(all(row["manual_review_only"] for row in plan["items"]))

    def test_report_sanitizes_secret_keys_and_raw_local_paths(self) -> None:
        report = build_risk_audit_hardening_report(
            risk_policies=[_policy(api_key="secret-value")],
            risk_events=[_risk_event(payload={"path": r"D:\private\raw.log", "account_id": "acct-123"})],
            audit_events=[_audit_event(payload={"reason": "operator review", "token": "token-123"})],
            audit_exports=[{"id": "export-1", "status": "queued", "export_type": "audit_bundle", "file_path": r"C:\raw\audit.json"}],
            trade_replays=[_trade_replay(broker_snapshot={"provider": "alpaca", "account_id": "acct-456"})],
            safety_summary={"records": [{"local_path": r"D:\runtime\ledger.json", "password": "pw"}]},
            generated_at="2026-05-10T00:00:00Z",
        )

        rendered = str(report)
        self.assertNotIn("secret-value", rendered)
        self.assertNotIn("token-123", rendered)
        self.assertNotIn("acct-123", rendered)
        self.assertNotIn("acct-456", rendered)
        self.assertNotIn("D:\\private\\raw.log", rendered)
        self.assertNotIn("C:\\raw\\audit.json", rendered)
        self.assertNotIn("D:\\runtime\\ledger.json", rendered)
        self.assertIn("[redacted]", rendered)
        self.assertIn("[local_path_redacted]", rendered)

    def test_api_response_shape_and_kill_switch_audit_event(self) -> None:
        context = build_test_context(slug="risk-audit-hardening-test", plan_key="professional")
        client = build_test_client(context)
        db = context.db
        tenant = context.tenant
        user = context.user
        strategy = StrategyDesk(tenant_id=tenant.id, desk_key="risk-audit-alpha", name="Risk Audit Alpha", config_json={"symbols": ["AAPL"]})
        db.add(strategy)
        db.flush()
        db.add(
            RiskPolicy(
                tenant_id=tenant.id,
                scope="tenant",
                status="active",
                max_daily_loss=500,
                max_order_notional=5000,
                max_open_positions=5,
                allowed_symbols_json={"items": []},
                blocked_symbols_json={"items": []},
                allowed_instruments_json={"items": ["equity"]},
                config_json={},
            )
        )
        db.add(
            RiskEvent(
                tenant_id=tenant.id,
                strategy_desk_id=strategy.id,
                event_type="risk.check_failed",
                severity="high",
                action_taken="blocked",
                breached_rule="max_order_notional",
                payload_json={"symbol": "AAPL", "expected_notional": 10000},
            )
        )
        db.add(AuditEvent(tenant_id=tenant.id, user_id=user.id, event_type="audit.export_queued", actor_email=user.email, payload_json={"export_type": "audit_bundle"}))
        db.add(AuditExport(tenant_id=tenant.id, requested_by_user_id=user.id, status="queued", export_type="audit_bundle", file_path=None))
        decision = TradeDecision(
            tenant_id=tenant.id,
            strategy_desk_id=strategy.id,
            symbol="AAPL",
            instrument_type="equity",
            side="buy",
            quantity=10,
            confidence_score=0.81,
            decision_status="approved",
            decision_reason="risk audit hardening seed",
            signal_snapshot_json={"setup_score": 78.2},
            risk_snapshot_json={"passed": True},
            readiness_snapshot_json={"status": "validated"},
            market_snapshot_json={"spread_bps": 4.1},
            broker_snapshot_json={"provider": "alpaca", "paper": True},
            decision_hash="risk-audit-decision-1",
        )
        db.add(decision)
        db.flush()
        db.add(
            DecisionReplayEvent(
                tenant_id=tenant.id,
                trade_decision_id=decision.id,
                sequence_number=1,
                event_type="risk_checked",
                event_time=datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc),
                payload_json={"passed": True},
            )
        )
        db.commit()

        kill_response = client.post("/api/risk/kill-switch", json={"reason": "risk audit hardening test"})
        self.assertEqual(kill_response.status_code, 200)
        kill_payload = kill_response.json()["data"]
        self.assertTrue(kill_payload["active"])
        self.assertTrue(kill_payload["status"]["active"])
        self.assertEqual(kill_payload["status"]["active_strategy_count"], 1)

        status_response = client.get("/api/risk/kill-switch")
        self.assertEqual(status_response.status_code, 200)
        status_data = status_response.json()["data"]
        self.assertTrue(status_data["active"])
        self.assertEqual(status_data["latest_event"]["event_type"], "risk.kill_switch_activated")
        self.assertFalse(status_data["can_submit_orders"])
        self.assertFalse(status_data["can_bypass_risk_gates"])

        events_response = client.get("/api/risk/events")
        self.assertEqual(events_response.status_code, 200)
        self.assertIn("risk.kill_switch_activated", [row["event_type"] for row in events_response.json()["data"]["items"]])

        clear_response = client.post("/api/risk/kill-switch/clear", json={"reason": "risk audit hardening test clear"})
        self.assertEqual(clear_response.status_code, 200)
        clear_data = clear_response.json()["data"]
        self.assertFalse(clear_data["active"])
        self.assertFalse(clear_data["status"]["active"])
        self.assertEqual(clear_data["status"]["latest_event"]["event_type"], "risk.kill_switch_cleared")

        response = client.get("/api/risk/audit-hardening")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertTrue(data["research_only"])
        self.assertTrue(data["audit_only"])
        self.assertFalse(data["can_submit_orders"])
        self.assertFalse(data["can_submit_live_orders"])
        self.assertFalse(data["can_bypass_risk_gates"])
        self.assertFalse(data["can_clear_kill_switch"])
        self.assertFalse(data["can_change_broker_routes"])
        self.assertFalse(data["can_change_ranking_weights"])
        self.assertFalse(data["can_grant_ai_order_authority"])
        self.assertIn("risk_audit_hardening_plan", data)
        self.assertIn("finish_tracker", data)
        self.assertGreaterEqual(data["risk_audit_hardening_plan"]["metrics"]["kill_switch_audit_event_count"], 1)
        self.assertIn("risk.kill_switch_cleared", [row["event_type"] for row in data["audit_events"]])
        context.close()

    def test_service_contains_no_execution_broker_risk_or_ranking_mutation_calls(self) -> None:
        source = inspect.getsource(hardening)
        forbidden_calls = (
            "place_order(",
            "submit_order(",
            "clear_kill_switch(",
            "set_broker_route(",
            "update_ranking_weight(",
            "update_risk_config(",
            "enable_live_trading(",
            "route_order(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
