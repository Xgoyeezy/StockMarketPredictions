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
from backend.models.saas import AuditEvent, OrderEventRecord, Tenant
from backend.schemas import OrganizationTradeAutomationActionRequest
from backend.services import (
    automation_live_pilot_expansion_service,
    automation_live_pilot_promotion_report_service,
    automation_live_pilot_soak_service,
    automation_live_pilot_window_service,
    notes_service,
    trade_automation_service,
)


FIXED_NOW = datetime(2026, 4, 24, 21, 10, tzinfo=timezone.utc)
BEFORE_CLOSE_NOW = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)


class AutomationLivePilotPromotionReportServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="live-promotion-test", name="Live Promotion Test", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        return db, tenant

    def _paper_state(self) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "enabled": False,
                "armed": False,
                "kill_switch": False,
                "execution_intent": "broker_paper",
                "live_pilot_promotion_report_enabled": True,
                "live_pilot_promotion_report_auto_review_enabled": True,
                "live_pilot_promotion_required_window_clean_sessions": 3,
                "live_pilot_promotion_stale_after_days": 2,
            }
        )
        state["runtime"].update(
            {
                "paper_canary_last_report": {
                    "status": "ready",
                    "evaluated_at": "2026-04-24T20:00:00+00:00",
                    "blockers": [],
                    "warnings": [],
                    "related_note_id": "paper-canary-note",
                },
                "paper_broker_reconciliation_last_report": {
                    "status": "clean",
                    "checked_at": "2026-04-24T20:05:00+00:00",
                    "ledger_consistency": "consistent",
                    "blockers": [],
                    "warnings": [],
                    "related_note_id": "paper-broker-note",
                },
                "live_pilot_readiness_last_report": {
                    "status": "ready_to_request_approval",
                    "evaluated_at": "2026-04-24T20:10:00+00:00",
                    "broker_live_gate_status": "open",
                    "safety_lock_status": "clear",
                    "blockers": [],
                    "warnings": [],
                    "related_note_id": "live-readiness-note",
                },
                "live_pilot_soak_last_report": {
                    "status": "completed",
                    "checked_at": "2026-04-24T20:15:00+00:00",
                    "terminal_state": "canceled",
                    "reconciliation_status": "clean",
                    "related_note_id": "live-soak-note",
                },
                "live_pilot_canary_last_report": {
                    "status": "ready",
                    "evaluated_at": "2026-04-24T20:20:00+00:00",
                    "clean_session_count": 3,
                    "required_clean_sessions": 3,
                    "blockers": [],
                    "warnings": [],
                    "related_note_id": "live-canary-note",
                },
                "live_pilot_expansion_last_report": {
                    "status": "completed",
                    "checked_at": "2026-04-24T20:25:00+00:00",
                    "terminal_state": "canceled",
                    "reconciliation_status": "clean",
                    "related_note_id": "live-expansion-note",
                },
                "live_pilot_expansion_canary_last_report": {
                    "status": "ready",
                    "evaluated_at": "2026-04-24T20:30:00+00:00",
                    "clean_session_count": 3,
                    "required_clean_sessions": 3,
                    "blockers": [],
                    "warnings": [],
                    "related_note_id": "live-expansion-canary-note",
                },
                "live_pilot_window_last_report": {
                    "status": "completed",
                    "checked_at": "2026-04-24T20:35:00+00:00",
                    "terminal_state": "canceled",
                    "broker_order_id": "broker-window-latest",
                    "local_order_id": "local-window-latest",
                    "reconciliation_status": "clean",
                    "related_note_id": "live-window-note",
                },
                "live_pilot_window_canary_last_report": {
                    "status": "ready",
                    "evaluated_at": "2026-04-24T20:40:00+00:00",
                    "clean_session_count": 3,
                    "required_clean_sessions": 3,
                    "window_session_count": 3,
                    "broker_live_gate_status": "open",
                    "safety_lock_status": "clear",
                    "pnl_summary": {"sample_count": 3, "realized_pnl": 0.12},
                    "slippage_summary": {"sample_count": 3, "average_abs_bps": 6.5, "worst_abs_bps": 12.0},
                    "blockers": [],
                    "warnings": [],
                    "related_note_id": "live-window-canary-note",
                },
            }
        )
        return state

    def _live_state(self, *, enabled: bool = False, armed: bool = False, kill_switch: bool = False) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "enabled": enabled,
                "armed": armed,
                "kill_switch": kill_switch,
                "execution_intent": "broker_live",
            }
        )
        return state

    def _run_report(self, db, tenant, paper_state, live_state=None, *, now=FIXED_NOW):
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with patch.object(notes_service, "NOTES_PATH", notes_path):
                report = automation_live_pilot_promotion_report_service.run_live_pilot_promotion_report(
                    db,
                    tenant=tenant,
                    paper_state=paper_state,
                    live_state=live_state or self._live_state(),
                    profile_key="personal_paper",
                    now=now,
                )
                db.commit()
                notes = json.loads(notes_path.read_text(encoding="utf-8"))
        return report, notes

    def test_action_schema_accepts_live_pilot_promotion_report(self) -> None:
        payload = OrganizationTradeAutomationActionRequest(action="run_live_pilot_promotion_report")

        self.assertEqual(payload.action, "run_live_pilot_promotion_report")

    def test_clean_evidence_marks_ready_and_writes_note(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()

        report, notes = self._run_report(db, tenant, paper_state)

        self.assertEqual(report["status"], "ready_to_request_limited_live_rollout")
        self.assertFalse(report["blockers"])
        self.assertEqual(report["clean_session_progress"]["clean"], 3)
        self.assertEqual(report["broker_live_gate_status"], "open")
        promotion_notes = [note for note in notes if "live-pilot-promotion" in note.get("tags", [])]
        self.assertEqual(len(promotion_notes), 1)
        self.assertIn("does not place, cancel, or close orders", promotion_notes[0]["body"])
        self.assertEqual(paper_state["runtime"]["live_pilot_promotion_report_last_report"]["status"], "ready_to_request_limited_live_rollout")
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.live_pilot_promotion_reviewed", audit_types)

    def test_blocked_paper_or_broker_evidence_blocks_report(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["paper_canary_last_report"].update({"status": "blocked", "blockers": [{"key": "paper"}]})
        paper_state["runtime"]["paper_broker_reconciliation_last_report"].update({"status": "blocked", "blockers": [{"key": "broker"}]})

        report, _notes = self._run_report(db, tenant, paper_state)

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("paper_canary_blocked", blocker_keys)
        self.assertIn("paper_broker_reconciliation_blocked", blocker_keys)

    def test_live_readiness_locked_gate_and_active_kill_switch_block(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["live_pilot_readiness_last_report"].update(
            {
                "status": "blocked",
                "broker_live_gate_status": "locked",
                "safety_lock_status": "locked",
                "blockers": [{"key": "gate"}],
            }
        )

        report, _notes = self._run_report(db, tenant, paper_state, live_state=self._live_state(kill_switch=True))

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("live_pilot_readiness_blocked", blocker_keys)
        self.assertIn("broker_live_gate_not_open", blocker_keys)
        self.assertIn("safety_lock_active", blocker_keys)
        self.assertIn("kill_switch_active", blocker_keys)

    def test_supervised_window_canary_below_required_blocks(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["live_pilot_window_canary_last_report"]["clean_session_count"] = 2

        report, _notes = self._run_report(db, tenant, paper_state)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("supervised_window_clean_sessions_low", {item["key"] for item in report["blockers"]})

    def test_stale_evidence_and_missing_notes_block(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["live_pilot_canary_last_report"].update(
            {
                "evaluated_at": "2026-04-20T20:00:00+00:00",
                "related_note_id": None,
            }
        )

        report, _notes = self._run_report(db, tenant, paper_state)

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("live_pilot_canary_stale", blocker_keys)
        self.assertIn("live_pilot_canary_note_missing", blocker_keys)

    def test_slippage_block_and_negative_pnl_warning(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["live_pilot_window_canary_last_report"]["slippage_summary"] = {
            "sample_count": 3,
            "average_abs_bps": 65.0,
            "worst_abs_bps": 125.0,
        }
        paper_state["runtime"]["live_pilot_window_canary_last_report"]["pnl_summary"] = {
            "sample_count": 3,
            "realized_pnl": -1.25,
        }

        report, _notes = self._run_report(db, tenant, paper_state)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("slippage_blocked", {item["key"] for item in report["blockers"]})
        self.assertIn("negative_live_pilot_pnl", {item["key"] for item in report["warnings"]})

    def test_warning_only_requires_operator_review(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["live_pilot_readiness_last_report"].update(
            {
                "status": "warning",
                "warnings": [{"key": "metadata"}],
                "broker_live_gate_status": "open",
                "safety_lock_status": "clear",
            }
        )

        report, _notes = self._run_report(db, tenant, paper_state)

        self.assertEqual(report["status"], "needs_operator_review")
        self.assertFalse(report["blockers"])
        self.assertIn("live_pilot_readiness_warning", {item["key"] for item in report["warnings"]})

    def test_action_path_persists_report_without_live_order_or_setting_mutation(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state()
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        trade_automation_service._write_trade_automation_state(tenant, live_state, profile_key="personal_live")
        db.add(
            OrderEventRecord(
                tenant_id=tenant.id,
                trade_id="live-window-trade",
                ticker="SPY",
                event_key="order.submitted",
                status="submitted",
                order_type="limit",
                payload_json={"automation_profile_key": "personal_live", "live_pilot_window_id": "window"},
                created_at=datetime(2026, 4, 24, 20, 35, tzinfo=timezone.utc),
            )
        )
        db.commit()
        before_paper_settings = json.loads(json.dumps(paper_state["settings"], sort_keys=True))
        before_live_settings = json.loads(json.dumps(live_state["settings"], sort_keys=True))
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="live-promotion",
            user_id="live-promotion",
            permissions=("tenant.manage_support",),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(trade_automation_service, "_resolve_tenant_for_current_user", return_value=tenant),
                patch.object(trade_automation_service, "_resolve_user_for_current_user", return_value=None),
                patch.object(trade_automation_service, "get_trade_summary", return_value={"rollout_readiness": {}}),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service, "_build_personal_account_summary", return_value={"status": "unavailable"}),
                patch.object(trade_automation_service, "get_latest_trade_automation_equity_snapshot", return_value=None),
                patch.object(automation_live_pilot_soak_service, "run_live_pilot_soak") as live_soak_mock,
                patch.object(automation_live_pilot_expansion_service, "run_live_pilot_expansion") as expansion_mock,
                patch.object(automation_live_pilot_window_service, "run_live_pilot_window_entry") as entry_mock,
                patch.object(automation_live_pilot_window_service, "run_live_pilot_window_exit") as exit_mock,
                patch.object(trade_automation_service, "open_trade_from_request") as open_trade_mock,
                patch.object(trade_automation_service, "sync_pending_orders_from_broker") as sync_orders_mock,
            ):
                snapshot = trade_automation_service.run_tenant_trade_automation_action(
                    db,
                    current_user=current_user,
                    request=OrganizationTradeAutomationActionRequest(action="run_live_pilot_promotion_report"),
                )
                notes = json.loads(notes_path.read_text(encoding="utf-8"))

        live_soak_mock.assert_not_called()
        expansion_mock.assert_not_called()
        entry_mock.assert_not_called()
        exit_mock.assert_not_called()
        open_trade_mock.assert_not_called()
        sync_orders_mock.assert_not_called()
        paper_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        live_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_live")
        self.assertEqual(paper_after["settings"], before_paper_settings)
        self.assertEqual(live_after["settings"], before_live_settings)
        self.assertEqual(snapshot["live_pilot_promotion_report"]["status"], "ready_to_request_limited_live_rollout")
        self.assertTrue(snapshot["available_actions"]["can_run_live_pilot_promotion_report"])
        self.assertEqual(len([note for note in notes if "live-pilot-promotion" in note.get("tags", [])]), 1)

    def test_scheduler_runs_once_and_records_manual_source_separately(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        trade_automation_service._write_trade_automation_state(tenant, self._live_state(), profile_key="personal_live")
        db.commit()

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_live_pilot_window_service, "run_live_pilot_window_entry") as entry_mock,
                patch.object(automation_live_pilot_window_service, "run_live_pilot_window_exit") as exit_mock,
            ):
                summary = trade_automation_service.run_trade_automation_live_pilot_promotion_reports(
                    db,
                    now=FIXED_NOW,
                )
                second_summary = trade_automation_service.run_trade_automation_live_pilot_promotion_reports(
                    db,
                    now=FIXED_NOW,
                )
                after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
                manual_report = automation_live_pilot_promotion_report_service.run_live_pilot_promotion_report(
                    db,
                    tenant=tenant,
                    paper_state=after_state,
                    live_state=self._live_state(),
                    profile_key="personal_paper",
                    now=FIXED_NOW,
                    run_source="manual",
                )

        entry_mock.assert_not_called()
        exit_mock.assert_not_called()
        self.assertEqual(summary["reviewed"], 1)
        self.assertEqual(second_summary["reviewed"], 0)
        self.assertEqual(second_summary["items"][0]["reason"], "already_reviewed_for_session")
        self.assertEqual(manual_report["run_source"], "manual")

    def test_scheduler_skips_before_close_buffer_and_when_disabled(self) -> None:
        db, tenant = self._db()
        state = self._paper_state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()

        before_summary = trade_automation_service.run_trade_automation_live_pilot_promotion_reports(
            db,
            now=BEFORE_CLOSE_NOW,
        )
        state["settings"]["live_pilot_promotion_report_auto_review_enabled"] = False
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()
        disabled_summary = trade_automation_service.run_trade_automation_live_pilot_promotion_reports(
            db,
            now=FIXED_NOW,
        )

        self.assertEqual(before_summary["reviewed"], 0)
        self.assertEqual(before_summary["items"][0]["reason"], "review_window_not_open")
        self.assertEqual(disabled_summary["reviewed"], 0)
        self.assertEqual(disabled_summary["items"][0]["reason"], "live_pilot_promotion_report_auto_review_disabled")


if __name__ == "__main__":
    unittest.main()
