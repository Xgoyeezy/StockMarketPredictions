from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.saas import AuditEvent, OrderEventRecord, Tenant
from backend.schemas import OrganizationTradeAutomationActionRequest
from backend.services import automation_exit_execution_watchdog_service, notes_service, trade_automation_service


FIXED_NOW = datetime(2026, 4, 24, 18, 30, tzinfo=timezone.utc)


class AutomationExitExecutionWatchdogServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="exit-watchdog-test", name="Exit Watchdog Test", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        return db, tenant

    def _state(self, *, requested_at: datetime | None = None) -> dict:
        requested_at = requested_at or (FIXED_NOW - timedelta(seconds=30))
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["profile_key"] = "personal_paper"
        state["settings"].update(
            {
                "exit_watchdog_enabled": True,
                "exit_watchdog_apply_to_live": False,
                "exit_watchdog_max_confirmation_seconds": 60,
                "exit_watchdog_max_partial_minutes": 5,
                "exit_watchdog_block_entries_on_unconfirmed_exit": True,
            }
        )
        state["runtime"]["loss_containment_last_report"] = {
            "status": "action_required",
            "evaluated_at": requested_at.isoformat(),
            "defensive_actions": [
                {
                    "trade_id": "exit-1",
                    "order_id": "order-1",
                    "ticker": "SPY",
                    "action": "EXIT FULLY NOW",
                    "reason": "position_loss_r_breach",
                    "auto_close_eligible": True,
                }
            ],
        }
        return state

    def _patch_notes(self):
        notes_dir = tempfile.TemporaryDirectory()
        self.addCleanup(notes_dir.cleanup)
        notes_path = Path(notes_dir.name) / "notes.json"
        notes_path.write_text("[]", encoding="utf-8")
        return patch.object(notes_service, "NOTES_PATH", notes_path), notes_path

    def _closed_trades(self, tenant: Tenant, rows: list[dict], profile_key: str = "personal_paper") -> pd.DataFrame:
        normalized = []
        for row in rows:
            normalized.append(
                {
                    "trade_id": row.get("trade_id", "exit-1"),
                    "order_id": row.get("order_id", "order-1"),
                    "ticker": row.get("ticker", "SPY"),
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": profile_key,
                    "status": row.get("status", "closed"),
                    "closed_at": row.get("closed_at", FIXED_NOW.isoformat()),
                }
            )
        return pd.DataFrame(normalized)

    def _event(self, **overrides) -> dict:
        event = {
            "trade_id": "exit-1",
            "ticker": "SPY",
            "event_key": "order.closed",
            "status": "closed",
            "created_at": FIXED_NOW.isoformat(),
        }
        event.update(overrides)
        return event

    def _report(
        self,
        tenant: Tenant,
        state: dict,
        *,
        order_events: list[dict] | None = None,
        closed_rows: list[dict] | None = None,
        profile_key: str = "personal_paper",
        now: datetime = FIXED_NOW,
    ) -> dict:
        return automation_exit_execution_watchdog_service.build_exit_watchdog_report(
            tenant=tenant,
            state=state,
            profile_key=profile_key,
            owned_closed=self._closed_trades(tenant, closed_rows or [], profile_key=profile_key),
            order_events=order_events or [],
            option_exit_events=[],
            now=now,
            run_source="test",
        )

    def test_action_schema_accepts_exit_watchdog_review(self) -> None:
        request = OrganizationTradeAutomationActionRequest(action="run_exit_watchdog_review")
        self.assertEqual(request.action, "run_exit_watchdog_review")

    def test_defensive_exit_with_matching_order_closed_reports_clean(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = self._report(tenant, state, order_events=[self._event()])

        self.assertEqual(report["status"], "clean")
        self.assertFalse(report["entries_blocked"])
        self.assertEqual(report["confirmed_exit_count"], 1)
        self.assertEqual(report["pending_exit_count"], 0)

    def test_defensive_exit_with_closed_ledger_reports_clean(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = self._report(tenant, state, closed_rows=[{"trade_id": "exit-1", "order_id": "order-1"}])

        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["confirmed_exit_count"], 1)

    def test_partially_closed_exit_stays_watch_before_timeout(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = self._report(
            tenant,
            state,
            order_events=[self._event(event_key="order.partially_closed", status="partially_closed")],
        )

        self.assertEqual(report["status"], "watch")
        self.assertFalse(report["entries_blocked"])
        self.assertEqual(report["pending_exit_count"], 1)
        self.assertIn("exit_partial", {item["key"] for item in report["warnings"]})

    def test_missing_terminal_evidence_after_timeout_blocks_entries(self) -> None:
        _, tenant = self._db()
        state = self._state(requested_at=FIXED_NOW - timedelta(seconds=75))

        report = self._report(tenant, state)

        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["entries_blocked"])
        self.assertTrue(report["manual_action_required"])
        self.assertTrue(report["reconciliation_required"])
        self.assertEqual(report["state_control_signal"], "de_risk")
        self.assertEqual(report["stuck_exit_count"], 1)
        self.assertIn("stuck", {item["key"] for item in report["blockers"]})
        required_items = {item["key"] for item in report["manual_rescue_checklist"] if item["status"] == "required"}
        self.assertIn("verify_broker_terminal_state", required_items)
        self.assertIn("run_broker_local_reconciliation", required_items)

    def test_failed_exit_event_produces_halt_evidence(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = self._report(
            tenant,
            state,
            order_events=[self._event(event_key="order.close_failed", status="failed")],
        )

        self.assertEqual(report["status"], "halt")
        self.assertTrue(report["entries_blocked"])
        self.assertTrue(report["manual_action_required"])
        self.assertEqual(report["state_control_signal"], "halt")
        self.assertEqual(report["failed_exit_count"], 1)
        self.assertIn("exit_failed", {item["key"] for item in report["blockers"]})

    def test_live_profile_remains_advisory_when_live_scope_disabled(self) -> None:
        _, tenant = self._db()
        state = self._state(requested_at=FIXED_NOW - timedelta(seconds=75))

        report = self._report(tenant, state, profile_key="personal_live")

        self.assertEqual(report["status"], "not_applicable")
        self.assertFalse(report["entries_blocked"])
        self.assertIn("live_scope_advisory_only", {item["key"] for item in report["warnings"]})

    def test_manual_review_persists_note_audit_and_leaves_settings_unchanged(self) -> None:
        db, tenant = self._db()
        state = self._state()
        before_settings = dict(state["settings"])
        db.add(
            OrderEventRecord(
                tenant_id=tenant.id,
                trade_id="exit-1",
                ticker="SPY",
                event_key="order.closed",
                status="closed",
                route_state="closed",
                book_state="flat",
                payload_json={"trade": {"trade_id": "exit-1", "order_id": "order-1"}},
                created_at=FIXED_NOW,
            )
        )
        db.commit()
        notes_patch, notes_path = self._patch_notes()

        with (
            notes_patch,
            patch.object(automation_exit_execution_watchdog_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
        ):
            report = automation_exit_execution_watchdog_service.run_exit_watchdog_review(
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
                .filter(AuditEvent.event_type == "trade_automation.exit_watchdog_reviewed")
                .count()
            )

        self.assertEqual(report["status"], "clean")
        self.assertEqual(state["settings"], before_settings)
        self.assertEqual(state["runtime"]["exit_watchdog_last_report"]["status"], "clean")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["owner"], "automation-ai")
        self.assertEqual(notes[0]["note_type"], "risk_review")
        self.assertIn("exit-watchdog", notes[0]["tags"])
        self.assertIn("loss-containment", notes[0]["tags"])
        self.assertIn("Exit evaluations", notes[0]["body"])
        self.assertEqual(audit_count, 1)

    def test_manual_blocked_review_runs_read_only_reconciliation_and_writes_rescue_note(self) -> None:
        db, tenant = self._db()
        state = self._state(requested_at=FIXED_NOW - timedelta(seconds=75))
        notes_patch, notes_path = self._patch_notes()

        reconciliation_report = {
            "status": "clean",
            "checked_at": FIXED_NOW.isoformat(),
            "broker_available": True,
            "ledger_consistency": "consistent",
            "related_note_id": "paper-recon-note",
            "blockers": [],
            "warnings": [],
        }
        with (
            notes_patch,
            patch.object(automation_exit_execution_watchdog_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(
                automation_exit_execution_watchdog_service.automation_paper_broker_reconciliation_service,
                "run_paper_broker_reconciliation",
                return_value=reconciliation_report,
            ) as reconciliation_mock,
        ):
            report = automation_exit_execution_watchdog_service.run_exit_watchdog_review(
                db,
                tenant=tenant,
                state=state,
                profile_key="personal_paper",
                now=FIXED_NOW,
            )
            db.commit()
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        reconciliation_mock.assert_called_once()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["escalation_reconciliation"]["status"], "clean")
        self.assertTrue(report["manual_action_required"])
        self.assertIn("Manual rescue checklist", notes[0]["body"])
        self.assertIn("Escalation reconciliation", notes[0]["body"])

    def test_action_path_persists_snapshot_note_and_does_not_place_orders(self) -> None:
        db, tenant = self._db()
        state = self._state()
        before_settings = json.loads(json.dumps(state["settings"], sort_keys=True))
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.add(
            OrderEventRecord(
                tenant_id=tenant.id,
                trade_id="exit-1",
                ticker="SPY",
                event_key="order.closed",
                status="closed",
                route_state="closed",
                book_state="flat",
                payload_json={"trade": {"trade_id": "exit-1", "order_id": "order-1"}},
                created_at=FIXED_NOW,
            )
        )
        db.commit()
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="exit-watchdog",
            user_id="exit-watchdog",
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
            patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(automation_exit_execution_watchdog_service, "_utc_now", return_value=FIXED_NOW),
            patch.object(trade_automation_service, "open_trade_from_request") as open_trade_mock,
            patch.object(trade_automation_service, "sync_pending_orders_from_broker") as sync_orders_mock,
        ):
            snapshot = trade_automation_service.run_tenant_trade_automation_action(
                db,
                current_user=current_user,
                request=OrganizationTradeAutomationActionRequest(action="run_exit_watchdog_review"),
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        open_trade_mock.assert_not_called()
        sync_orders_mock.assert_not_called()
        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertEqual(after_state["settings"], before_settings)
        self.assertEqual(snapshot["exit_execution_watchdog"]["status"], "clean")
        self.assertEqual(len(notes), 1)
        self.assertIn("exit-watchdog", notes[0]["tags"])


if __name__ == "__main__":
    unittest.main()
