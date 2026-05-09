from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.saas import AuditEvent, OrderEventRecord, Tenant
from backend.services import automation_state_control_service, notes_service, trade_automation_service


class AutomationStateControlServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="state-control-test", name="State Control Test", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        return db, tenant

    def _state(self) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "enabled": True,
                "armed": True,
                "state_control_enabled": True,
                "state_control_auto_throttle_enabled": True,
                "state_control_auto_halt_enabled": True,
                "risk_percent": 0.50,
                "max_notional_per_trade": 2500.0,
                "max_total_open_notional": 5000.0,
                "cycle_entry_rank_limit": 3,
                "allow_pyramiding": True,
                "max_spread_bps": 25.0,
                "order_type": "market",
                "max_consecutive_losses": 2,
            }
        )
        state["runtime"].update(
            {
                "last_cycle_at": "2026-04-24T14:00:00+00:00",
                "last_success_at": "2026-04-24T14:00:00+00:00",
                "error_streak": 0,
                "ledger_snapshot_consistency": "consistent",
                "current_route_reconciliation_status": "clean",
                "mark_to_market_coverage_status": "complete",
                "current_route_sample_status": "sufficient",
            }
        )
        return state

    def _closed_losses(self, tenant: Tenant, profile_key: str = "personal_paper") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "trade_id": "loss-1",
                    "ticker": "SPY",
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": profile_key,
                    "realized_pnl": -300.0,
                    "closed_at": "2026-04-24T16:00:00+00:00",
                },
                {
                    "trade_id": "loss-2",
                    "ticker": "QQQ",
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": profile_key,
                    "realized_pnl": -250.0,
                    "closed_at": "2026-04-24T17:00:00+00:00",
                },
            ]
        )

    def test_normalized_state_includes_state_control_defaults(self) -> None:
        state = trade_automation_service._normalize_trade_automation_profile_state({})

        self.assertTrue(state["settings"]["state_control_enabled"])
        self.assertTrue(state["settings"]["state_control_auto_throttle_enabled"])
        self.assertTrue(state["settings"]["state_control_auto_halt_enabled"])
        self.assertEqual(state["settings"]["state_control_watch_score"], 75.0)
        self.assertEqual(state["settings"]["state_control_derisk_score"], 55.0)
        self.assertEqual(state["settings"]["state_control_halt_score"], 30.0)
        self.assertEqual(state["settings"]["state_control_recovery_cycles"], 2)
        self.assertIn("state_control_last_evaluation", state["runtime"])

    def test_healthy_review_creates_daily_note_and_audit(self) -> None:
        db, tenant = self._db()
        state = self._state()
        fixed_now = datetime(2026, 4, 24, 14, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_state_control_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(
                    automation_state_control_service.sdm,
                    "journal_probability_calibration_summary",
                    return_value={"resolved_count": 0, "average_error": None},
                ),
            ):
                review = automation_state_control_service.evaluate_trade_automation_state_control(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                )
                db.commit()
                notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(review["status"], "reviewed")
        self.assertEqual(review["state"], "healthy")
        self.assertGreaterEqual(review["score"], 75.0)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["owner"], "automation-ai")
        self.assertEqual(notes[0]["note_type"], "risk_review")
        self.assertIn("state-control", notes[0]["tags"])
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.state_reviewed", audit_types)

    def test_loss_and_slippage_review_throttles_without_rewriting_baseline(self) -> None:
        db, tenant = self._db()
        state = self._state()
        fixed_now = datetime(2026, 4, 24, 18, 0, tzinfo=timezone.utc)
        db.add(
            OrderEventRecord(
                tenant_id=tenant.id,
                ticker="SPY",
                event_key="order.filled",
                status="filled",
                payload_json={
                    "automation_profile_key": "personal_paper",
                    "automation_cycle_id": "cycle-1",
                    "slippage_bps": 45.0,
                },
                created_at=fixed_now,
            )
        )
        db.commit()
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_state_control_service.sdm, "read_closed_trades", return_value=self._closed_losses(tenant)),
                patch.object(automation_state_control_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(
                    automation_state_control_service.sdm,
                    "journal_probability_calibration_summary",
                    return_value={"resolved_count": 5, "average_error": 0.15},
                ),
            ):
                review = automation_state_control_service.evaluate_trade_automation_state_control(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                )

        self.assertIn(review["state"], {"watch", "de_risk"})
        self.assertTrue(review["active_overrides"])
        self.assertEqual(state["settings"]["risk_percent"], 0.50)

        de_risk_snapshot = {
            "state": "de_risk",
            "enabled": True,
            "auto_throttle_enabled": True,
            "triggered_signals": [{"component": "execution_quality", "signal": "average_slippage_drift"}],
        }
        effective = automation_state_control_service.apply_state_control_overlay(state["settings"], de_risk_snapshot)
        self.assertEqual(effective["risk_percent"], 0.25)
        self.assertEqual(effective["cycle_entry_rank_limit"], 1)
        self.assertFalse(effective["allow_pyramiding"])
        self.assertEqual(effective["order_type"], "limit")
        self.assertEqual(state["settings"]["risk_percent"], 0.50)
        self.assertEqual(state["settings"]["cycle_entry_rank_limit"], 3)

    def test_hard_fault_halts_sets_kill_switch_and_stays_manual(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["ledger_snapshot_consistency"] = "inconsistent"
        fixed_now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_state_control_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            ):
                first = automation_state_control_service.evaluate_trade_automation_state_control(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                )
                state["runtime"]["ledger_snapshot_consistency"] = "consistent"
                second = automation_state_control_service.evaluate_trade_automation_state_control(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                )
                db.commit()

        self.assertEqual(first["state"], "halt")
        self.assertTrue(state["settings"]["kill_switch"])
        self.assertFalse(state["settings"]["armed"])
        self.assertEqual(second["state"], "halt")
        self.assertTrue(second["manual_action_required"])
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.state_halted", audit_types)

    def test_stale_loss_containment_action_does_not_rehalt_after_position_reconciles(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["loss_containment_last_report"] = {
            "status": "action_required",
            "entries_blocked": True,
            "open_position_count": 1,
            "evaluated_at": "2026-04-24T18:55:00+00:00",
            "defensive_actions": [
                {
                    "ticker": "AAPL",
                    "action": "EXIT FULLY NOW",
                    "auto_close_eligible": True,
                }
            ],
        }
        state["runtime"]["current_route_latest_event_at"] = "2026-04-24T18:56:00+00:00"
        fixed_now = datetime(2026, 4, 24, 19, 1, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_state_control_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            ):
                review = automation_state_control_service.evaluate_trade_automation_state_control(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                )

        signals = {item.get("signal") for item in review.get("triggered_signals") or []}
        self.assertIn("stale_loss_containment_report", signals)
        self.assertNotIn("loss_containment_breach", signals)
        self.assertNotEqual(review["state"], "halt")
        self.assertFalse(state["settings"]["kill_switch"])
        self.assertTrue(state["settings"]["armed"])

    def test_confirmed_route_reconciliation_orphan_halts_and_sets_kill_switch(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["current_route_reconciliation_status"] = "orphaned"
        state["runtime"]["current_route_orphan_order_event_count"] = 2
        fixed_now = datetime(2026, 4, 24, 19, 10, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_state_control_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            ):
                review = automation_state_control_service.evaluate_trade_automation_state_control(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                )

        signals = {item.get("signal") for item in review.get("triggered_signals") or []}
        self.assertEqual(review["state"], "halt")
        self.assertIn("route_reconciliation_failed", signals)
        self.assertTrue(state["settings"]["kill_switch"])
        self.assertFalse(state["settings"]["armed"])

    def test_advisory_route_issues_without_orphans_do_not_auto_halt(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["current_route_reconciliation_status"] = "issues_present"
        state["runtime"]["current_route_orphan_order_event_count"] = 0
        fixed_now = datetime(2026, 4, 24, 19, 12, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_state_control_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            ):
                review = automation_state_control_service.evaluate_trade_automation_state_control(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                )

        signals = {item.get("signal") for item in review.get("triggered_signals") or []}
        self.assertIn("route_reconciliation_review", signals)
        self.assertNotIn("route_reconciliation_failed", signals)
        self.assertNotEqual(review["state"], "halt")
        self.assertFalse(state["settings"]["kill_switch"])
        self.assertTrue(state["settings"]["armed"])

    def test_exit_watchdog_block_feeds_derisk_signal_without_live_gate_mutation(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["exit_watchdog_last_report"] = {
            "status": "blocked",
            "entries_blocked": True,
            "pending_exit_count": 0,
            "stuck_exit_count": 1,
            "failed_exit_count": 0,
            "manual_action_required": True,
        }
        before_settings = json.loads(json.dumps(state["settings"], sort_keys=True))
        fixed_now = datetime(2026, 4, 24, 18, 30, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_state_control_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(automation_state_control_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(
                    automation_state_control_service.sdm,
                    "journal_probability_calibration_summary",
                    return_value={"resolved_count": 0, "average_error": None},
                ),
            ):
                review = automation_state_control_service.evaluate_trade_automation_state_control(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                )

        signals = {item.get("signal") for item in review.get("triggered_signals") or []}
        self.assertIn("exit_watchdog_unconfirmed_exit", signals)
        self.assertIn(review["state"], {"de_risk", "halt"})
        self.assertEqual(state["settings"].get("broker_live_gate_open"), before_settings.get("broker_live_gate_open"))
        self.assertEqual(state["settings"].get("enabled"), before_settings.get("enabled"))


if __name__ == "__main__":
    unittest.main()
