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
from backend.services import (
    automation_state_control_shadow_service,
    automation_state_control_service,
    notes_service,
    trade_automation_service,
)


class AutomationStateControlShadowServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="state-shadow-test", name="State Shadow Test", status="active")
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
                "kill_switch": False,
                "execution_intent": "broker_paper",
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
                "max_error_streak": 3,
            }
        )
        state["runtime"].update(
            {
                "last_cycle_at": "2026-04-24T14:00:00+00:00",
                "last_success_at": "2026-04-24T14:00:00+00:00",
                "error_streak": 0,
                "ledger_snapshot_consistency": "consistent",
                "current_route_reconciliation_status": "clean",
                "current_route_orphan_order_event_count": 0,
                "mark_to_market_coverage_status": "complete",
                "current_route_sample_status": "sufficient",
                "last_rejection": None,
                "last_guardrail": None,
                "last_path_evaluations": [],
            }
        )
        return state

    def _run_shadow(self, db, tenant, state, *, profile_key="personal_paper"):
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
                report = automation_state_control_shadow_service.run_state_control_shadow_validation(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key=profile_key,
                    now=fixed_now,
                )
                db.commit()
                notes = json.loads(notes_path.read_text(encoding="utf-8"))
        return report, notes

    def test_action_schema_accepts_shadow_validation(self) -> None:
        payload = OrganizationTradeAutomationActionRequest(action="run_state_control_shadow_validation")

        self.assertEqual(payload.action, "run_state_control_shadow_validation")

    def test_shadow_validation_runs_scenarios_and_preserves_baseline(self) -> None:
        db, tenant = self._db()
        state = self._state()
        before_settings = json.loads(json.dumps(state["settings"], sort_keys=True))
        before_runtime = {
            key: json.loads(json.dumps(value, sort_keys=True))
            for key, value in state["runtime"].items()
            if not key.startswith("state_control_shadow_")
        }

        report, notes = self._run_shadow(db, tenant, state)

        self.assertEqual(state["settings"], before_settings)
        after_runtime = {
            key: json.loads(json.dumps(value, sort_keys=True))
            for key, value in state["runtime"].items()
            if not key.startswith("state_control_shadow_")
        }
        self.assertEqual(after_runtime, before_runtime)
        self.assertFalse(state["settings"]["kill_switch"])
        self.assertEqual(report["scenario_count"], 6)
        self.assertEqual(report["status"], "pass")
        scenario_ids = {item["id"] for item in report["scenarios"]}
        self.assertEqual(
            scenario_ids,
            {
                "healthy_baseline",
                "loss_drawdown_weakness",
                "slippage_spread_weakness",
                "hard_operational_fault",
                "recovery_hysteresis",
                "live_profile_safety",
            },
        )

        scenarios = {item["id"]: item for item in report["scenarios"]}
        self.assertEqual(scenarios["healthy_baseline"]["state"], "healthy")
        self.assertIn(scenarios["loss_drawdown_weakness"]["state"], {"watch", "de_risk"})
        self.assertTrue(scenarios["loss_drawdown_weakness"]["active_overrides"])
        self.assertIn(scenarios["slippage_spread_weakness"]["state"], {"watch", "de_risk"})
        self.assertTrue(
            any(item.get("field") == "order_type" for item in scenarios["slippage_spread_weakness"]["active_overrides"])
        )
        self.assertEqual(scenarios["hard_operational_fault"]["state"], "halt")
        self.assertTrue(scenarios["hard_operational_fault"]["safety_lock_expected"])
        self.assertIn(
            "recovery_hold",
            {item.get("signal") for item in scenarios["recovery_hysteresis"]["signals"]},
        )
        self.assertFalse(scenarios["live_profile_safety"]["forbidden_actions_allowed"])

        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note["owner"], "automation-ai")
        self.assertEqual(note["note_type"], "risk_review")
        self.assertIn("shadow-validation", note["tags"])
        self.assertIn("Expected setting/effective overrides", note["body"])
        self.assertEqual(state["runtime"]["state_control_shadow_last_report"]["status"], "pass")
        self.assertEqual(state["runtime"]["state_control_shadow_last_note_id"], note["id"])

        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.state_shadow_validated", audit_types)

    def test_shadow_snapshot_surfaces_latest_report(self) -> None:
        db, tenant = self._db()
        state = self._state()

        report, _notes = self._run_shadow(db, tenant, state)
        snapshot = automation_state_control_shadow_service.build_shadow_validation_snapshot(state)

        self.assertEqual(snapshot["status"], report["status"])
        self.assertEqual(snapshot["scenario_count"], 6)
        self.assertEqual(snapshot["worst_state"], report["worst_state"])
        self.assertTrue(snapshot["scenarios"])

    def test_trade_automation_action_runs_shadow_validation_without_orders_or_setting_changes(self) -> None:
        db, tenant = self._db()
        state = self._state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()
        persisted_before = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        before_settings = json.loads(json.dumps(persisted_before["settings"], sort_keys=True))
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="demo-trader",
            user_id="demo-trader",
            permissions=("tenant.manage_support",),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(trade_automation_service, "_resolve_tenant_for_current_user", return_value=tenant),
                patch.object(trade_automation_service, "_resolve_user_for_current_user", return_value=None),
                patch.object(
                    trade_automation_service,
                    "get_trade_summary",
                    return_value={"rollout_readiness": {"allows_live_rollout": False}},
                ),
                patch.object(
                    trade_automation_service,
                    "_build_personal_account_summary",
                    return_value={
                        "provider": "alpaca_paper",
                        "label": "Paper account",
                        "connected": False,
                        "status": "unavailable",
                        "equity": None,
                        "cash": None,
                        "portfolio_value": None,
                        "buying_power": None,
                    },
                ),
                patch.object(trade_automation_service, "get_latest_trade_automation_equity_snapshot", return_value=None),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(
                    trade_automation_service.sdm,
                    "journal_probability_calibration_summary",
                    return_value={"resolved_count": 0, "average_error": None},
                ),
                patch.object(trade_automation_service, "open_trade_from_request") as open_trade_mock,
                patch.object(trade_automation_service, "sync_pending_orders_from_broker") as sync_orders_mock,
                patch.object(trade_automation_service, "_manage_automation_positions") as manage_positions_mock,
            ):
                snapshot = trade_automation_service.run_tenant_trade_automation_action(
                    db,
                    current_user=current_user,
                    request=OrganizationTradeAutomationActionRequest(action="run_state_control_shadow_validation"),
                )
                notes = json.loads(notes_path.read_text(encoding="utf-8"))

        open_trade_mock.assert_not_called()
        sync_orders_mock.assert_not_called()
        manage_positions_mock.assert_not_called()
        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertEqual(after_state["settings"], before_settings)
        self.assertEqual(snapshot["profile_key"], "personal_paper")
        self.assertEqual(snapshot["control_plane"]["shadow_validation"]["status"], "pass")
        self.assertEqual(snapshot["control_plane"]["shadow_validation"]["scenario_count"], 6)
        self.assertTrue(snapshot["control_plane"]["shadow_validation"]["note_id"])
        self.assertTrue(snapshot["available_actions"]["can_run_state_control_shadow_validation"])
        self.assertEqual(after_state["runtime"]["state_control_shadow_last_report"]["status"], "pass")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["id"], snapshot["control_plane"]["shadow_validation"]["note_id"])
        self.assertIn("shadow-validation", notes[0]["tags"])
        self.assertIn("Expected setting/effective overrides", notes[0]["body"])
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.state_shadow_validated", audit_types)


if __name__ == "__main__":
    unittest.main()
