from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.saas import AuditEvent, Tenant
from backend.schemas import OrganizationTradeAutomationActionRequest
from backend.services import automation_loss_containment_service, notes_service, trade_automation_service


FIXED_NOW = datetime(2026, 4, 24, 18, 30, tzinfo=timezone.utc)


class AutomationLossContainmentServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="loss-containment-test", name="Loss Containment Test", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        return db, tenant

    def _state(self) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["profile_key"] = "personal_paper"
        state["settings"].update(
            {
                "loss_containment_enabled": True,
                "loss_containment_apply_to_live": False,
                "loss_containment_auto_close_paper": True,
                "loss_containment_auto_close_live": False,
                "loss_containment_max_open_heat_pct": 0.35,
                "loss_containment_max_position_loss_r": 0.50,
                "loss_containment_max_position_mae_pct": 0.35,
                "loss_containment_profit_protect_trigger_r": 0.75,
                "loss_containment_profit_protect_floor_r": 0.15,
                "loss_containment_time_stop_minutes": 45,
                "loss_containment_stale_quote_seconds": 120,
                "account_size": 100000.0,
                "risk_percent": 0.5,
            }
        )
        return state

    def _patch_notes(self):
        notes_dir = tempfile.TemporaryDirectory()
        self.addCleanup(notes_dir.cleanup)
        notes_path = Path(notes_dir.name) / "notes.json"
        notes_path.write_text("[]", encoding="utf-8")
        return patch.object(notes_service, "NOTES_PATH", notes_path), notes_path

    def _open_trades(self, tenant: Tenant, rows: list[dict], profile_key: str = "personal_paper") -> pd.DataFrame:
        normalized = []
        for index, row in enumerate(rows, start=1):
            normalized.append(
                {
                    "trade_id": row.get("trade_id", f"trade-{index}"),
                    "order_id": row.get("order_id", f"order-{index}"),
                    "ticker": row.get("ticker", "SPY"),
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": profile_key,
                    "instrument_type": row.get("instrument_type", "equity"),
                    "suggested_contracts": row.get("suggested_contracts", 10),
                    "entry_price": row.get("entry_price", 100.0),
                    "live_price_at_open": row.get("entry_price", 100.0),
                    "position_cost": row.get("position_cost", 1000.0),
                    "opened_at": row.get("opened_at", "2026-04-24T17:30:00+00:00"),
                }
            )
        return pd.DataFrame(normalized)

    def _monitored(self, tenant: Tenant, rows: list[dict], profile_key: str = "personal_paper") -> pd.DataFrame:
        normalized = []
        for index, row in enumerate(rows, start=1):
            normalized.append(
                {
                    "trade_id": row.get("trade_id", f"trade-{index}"),
                    "order_id": row.get("order_id", f"order-{index}"),
                    "ticker": row.get("ticker", "SPY"),
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": profile_key,
                    "instrument_type": row.get("instrument_type", "equity"),
                    "unrealized_pnl": row.get("unrealized_pnl", 0.0),
                    "quote_age_seconds": row.get("quote_age_seconds", 10.0),
                    "max_adverse_excursion_pct": row.get("max_adverse_excursion_pct", 0.0),
                    "max_favorable_excursion_r": row.get("max_favorable_excursion_r", 0.0),
                    "current_underlying": row.get("current_underlying", 100.0),
                    "current_contract_mid": row.get("current_contract_mid", 100.0),
                    "monitor_action": row.get("monitor_action", "HOLD"),
                }
            )
        return pd.DataFrame(normalized)

    def _report(
        self,
        tenant: Tenant,
        state: dict,
        open_rows: list[dict],
        monitor_rows: list[dict],
        *,
        profile_key: str = "personal_paper",
    ) -> dict:
        return automation_loss_containment_service.build_loss_containment_report(
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            owned_open=self._open_trades(tenant, open_rows, profile_key=profile_key),
            owned_pending=pd.DataFrame(),
            monitored_open=self._monitored(tenant, monitor_rows, profile_key=profile_key),
            effective_funds=100000.0,
            now=FIXED_NOW,
        )

    def test_action_schema_accepts_loss_containment_review(self) -> None:
        request = OrganizationTradeAutomationActionRequest(action="run_loss_containment_review")
        self.assertEqual(request.action, "run_loss_containment_review")

    def test_healthy_open_paper_positions_keep_entries_allowed(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = self._report(
            tenant,
            state,
            [{"trade_id": "healthy-1", "order_id": "healthy-order"}],
            [{"trade_id": "healthy-1", "order_id": "healthy-order", "unrealized_pnl": 85.0}],
        )

        self.assertEqual(report["status"], "clean")
        self.assertFalse(report["entries_blocked"])
        self.assertEqual(report["defensive_actions"], [])

    def test_open_heat_over_budget_blocks_new_paper_entries(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["settings"]["risk_percent"] = 2.0

        report = self._report(
            tenant,
            state,
            [{"trade_id": "heat-1", "order_id": "heat-order", "opened_at": "2026-04-24T18:20:00+00:00"}],
            [{"trade_id": "heat-1", "order_id": "heat-order", "unrealized_pnl": -400.0}],
        )

        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["entries_blocked"])
        self.assertGreaterEqual(report["open_heat_pct"], 0.35)
        self.assertIn("open_heat_breach", {item["key"] for item in report["blockers"]})

    def test_position_beyond_loss_r_triggers_paper_defensive_close_action(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = self._report(
            tenant,
            state,
            [{"trade_id": "loss-1", "order_id": "loss-order"}],
            [{"trade_id": "loss-1", "order_id": "loss-order", "unrealized_pnl": -275.0}],
        )

        self.assertEqual(report["status"], "action_required")
        self.assertTrue(report["entries_blocked"])
        self.assertEqual(report["defensive_actions"][0]["reason"], "position_loss_r_breach")
        self.assertTrue(report["defensive_actions"][0]["auto_close_eligible"])
        actions = automation_loss_containment_service.build_forced_exit_actions(report)
        self.assertEqual(actions["trade_id:loss-1"], "EXIT FULLY NOW")

    def test_profit_giveback_after_winner_triggers_protection(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = self._report(
            tenant,
            state,
            [{"trade_id": "giveback-1", "order_id": "giveback-order"}],
            [
                {
                    "trade_id": "giveback-1",
                    "order_id": "giveback-order",
                    "unrealized_pnl": 50.0,
                    "max_favorable_excursion_r": 0.80,
                }
            ],
        )

        self.assertEqual(report["status"], "action_required")
        self.assertEqual(report["defensive_actions"][0]["reason"], "profit_protect_giveback")

    def test_stale_quote_blocks_new_entries_and_records_warning_evidence(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = self._report(
            tenant,
            state,
            [{"trade_id": "stale-1", "order_id": "stale-order"}],
            [{"trade_id": "stale-1", "order_id": "stale-order", "unrealized_pnl": 25.0, "quote_age_seconds": 180.0}],
        )

        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["entries_blocked"])
        self.assertIn("stale_quote", {item["key"] for item in report["blockers"]})

    def test_live_profile_is_advisory_when_live_scope_disabled(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["settings"]["loss_containment_apply_to_live"] = False

        report = self._report(
            tenant,
            state,
            [{"trade_id": "live-loss", "order_id": "live-order"}],
            [{"trade_id": "live-loss", "order_id": "live-order", "unrealized_pnl": -500.0}],
            profile_key="personal_live",
        )

        self.assertEqual(report["status"], "not_applicable")
        self.assertFalse(report["entries_blocked"])
        self.assertFalse(report["defensive_actions"][0]["auto_close_eligible"])
        self.assertIn("live_scope_advisory_only", {item["key"] for item in report["warnings"]})

    def test_manual_review_persists_note_audit_and_leaves_settings_unchanged(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["__actual_funds"] = 100000.0
        before_settings = dict(state["settings"])
        open_frame = self._open_trades(tenant, [{"trade_id": "review-1", "order_id": "review-order"}])
        monitor_frame = self._monitored(
            tenant,
            [{"trade_id": "review-1", "order_id": "review-order", "unrealized_pnl": -275.0}],
        )
        notes_patch, notes_path = self._patch_notes()

        with (
            notes_patch,
            patch.object(automation_loss_containment_service.sdm, "read_open_trades", return_value=open_frame),
            patch.object(automation_loss_containment_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(automation_loss_containment_service.sdm, "monitor_open_trades", return_value=monitor_frame),
        ):
            report = automation_loss_containment_service.run_loss_containment_review(
                db,
                tenant=tenant,
                state=state,
                profile_key="personal_paper",
                now=FIXED_NOW,
            )
            db.commit()
            notes = json.loads(notes_path.read_text(encoding="utf-8"))
            audit_count = (
                db.query(AuditEvent)
                .filter(AuditEvent.event_type == "trade_automation.loss_containment_reviewed")
                .count()
            )

        self.assertEqual(report["status"], "action_required")
        self.assertEqual(state["settings"], before_settings)
        self.assertEqual(state["runtime"]["loss_containment_last_report"]["status"], "action_required")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["owner"], "automation-ai")
        self.assertEqual(notes[0]["note_type"], "risk_review")
        self.assertIn("loss-containment", notes[0]["tags"])
        self.assertIn("defensive-exit", notes[0]["tags"])
        self.assertIn("Defensive actions", notes[0]["body"])
        self.assertEqual(audit_count, 1)

    def test_action_path_persists_snapshot_note_and_does_not_place_orders(self) -> None:
        db, tenant = self._db()
        state = self._state()
        before_settings = json.loads(json.dumps(state["settings"], sort_keys=True))
        open_frame = self._open_trades(tenant, [{"trade_id": "action-1", "order_id": "action-order"}])
        monitor_frame = self._monitored(
            tenant,
            [{"trade_id": "action-1", "order_id": "action-order", "unrealized_pnl": -275.0}],
        )
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="loss-containment",
            user_id="loss-containment",
            permissions=("tenant.manage_support",),
        )
        notes_patch, notes_path = self._patch_notes()

        with (
            notes_patch,
            patch.object(trade_automation_service, "_resolve_tenant_for_current_user", return_value=tenant),
            patch.object(trade_automation_service, "_resolve_user_for_current_user", return_value=None),
            patch.object(trade_automation_service, "get_trade_summary", return_value={"rollout_readiness": {}}),
            patch.object(trade_automation_service, "_build_personal_account_summary", return_value={"status": "unavailable"}),
            patch.object(trade_automation_service, "get_latest_trade_automation_equity_snapshot", return_value=None),
            patch.object(trade_automation_service.sdm, "read_open_trades", return_value=open_frame),
            patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=monitor_frame),
            patch.object(trade_automation_service, "open_trade_from_request") as open_trade_mock,
            patch.object(trade_automation_service, "sync_pending_orders_from_broker") as sync_orders_mock,
        ):
            snapshot = trade_automation_service.run_tenant_trade_automation_action(
                db,
                current_user=current_user,
                request=OrganizationTradeAutomationActionRequest(action="run_loss_containment_review"),
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        open_trade_mock.assert_not_called()
        sync_orders_mock.assert_not_called()
        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertEqual(after_state["settings"], before_settings)
        self.assertEqual(snapshot["loss_containment"]["status"], "action_required")
        self.assertEqual(len(notes), 1)
        self.assertIn("loss-containment", notes[0]["tags"])


if __name__ == "__main__":
    unittest.main()
