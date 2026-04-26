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
    automation_paper_order_lifecycle_canary_service,
    automation_paper_order_lifecycle_soak_service,
    notes_service,
    trade_automation_service,
)


FIXED_NOW = datetime(2026, 4, 24, 20, 25, tzinfo=timezone.utc)
BEFORE_CLOSE_NOW = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
SESSION_DAYS = ["2026-04-24", "2026-04-23", "2026-04-22"]


class AutomationPaperOrderLifecycleCanaryServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="lifecycle-canary-test", name="Lifecycle Canary Test", status="active")
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
                "paper_order_lifecycle_canary_enabled": True,
                "paper_order_lifecycle_auto_submit_enabled": False,
                "paper_order_lifecycle_window_sessions": 5,
                "paper_order_lifecycle_required_clean_sessions": 3,
            }
        )
        state["runtime"].update(
            {
                "paper_broker_reconciliation_history": [
                    {
                        "at": f"{day}T20:15:00+00:00",
                        "session_day": day,
                        "status": "clean",
                        "matched_count": 1,
                        "blockers": [],
                        "warnings": [],
                        "note_id": f"paper-broker-{day}",
                    }
                    for day in SESSION_DAYS
                ],
                "paper_order_lifecycle_soak_history": [
                    {
                        "at": f"{day}T20:18:00+00:00",
                        "checked_at": f"{day}T20:18:00+00:00",
                        "session_day": day,
                        "status": "completed",
                        "terminal_state": "canceled",
                        "broker_order_id": f"broker-entry-{day}",
                        "local_order_id": f"local-order-{day}",
                        "reconciliation_status": "clean",
                        "blockers": [],
                        "warnings": [],
                        "note_id": f"paper-lifecycle-{day}",
                    }
                    for day in SESSION_DAYS
                ],
            }
        )
        return state

    def _seed_notes(self, *, missing: set[tuple[str, str]] | None = None) -> None:
        missing = missing or set()
        for day in SESSION_DAYS:
            tag_base = ["automation-ai", "paper-broker", "profile-personal_paper", f"session-{day}"]
            if (day, "soak") not in missing:
                notes_service.create_note(
                    title=f"Lifecycle soak {day}",
                    body="Paper order lifecycle soak completed",
                    tags=[*tag_base, "order-lifecycle-soak"],
                    owner="automation-ai",
                    note_type="risk_review",
                )
            if (day, "reconciliation") not in missing:
                notes_service.create_note(
                    title=f"Paper broker {day}",
                    body="Paper broker reconciliation clean",
                    tags=[*tag_base, "reconciliation"],
                    owner="automation-ai",
                    note_type="risk_review",
                )

    def _run_canary(self, db, tenant, state, *, notes_missing=None):
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_paper_order_lifecycle_canary_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(automation_paper_order_lifecycle_canary_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(automation_paper_order_lifecycle_canary_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            ):
                self._seed_notes(missing=notes_missing)
                report = automation_paper_order_lifecycle_canary_service.run_paper_order_lifecycle_canary_review(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=FIXED_NOW,
                )
                db.commit()
                notes = json.loads(notes_path.read_text(encoding="utf-8"))
        return report, notes

    def test_action_schema_accepts_lifecycle_canary_review(self) -> None:
        payload = OrganizationTradeAutomationActionRequest(action="run_paper_order_lifecycle_canary_review")

        self.assertEqual(payload.action, "run_paper_order_lifecycle_canary_review")

    def test_clean_lifecycle_sessions_mark_canary_ready_and_write_note(self) -> None:
        db, tenant = self._db()
        state = self._state()

        report, notes = self._run_canary(db, tenant, state)

        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["clean_session_count"], 3)
        self.assertEqual(report["required_clean_sessions"], 3)
        self.assertFalse(report["blockers"])
        canary_notes = [note for note in notes if "order-lifecycle-canary" in note.get("tags", [])]
        self.assertEqual(len(canary_notes), 1)
        self.assertIn("Latest terminal state: canceled", canary_notes[0]["body"])
        self.assertEqual(state["runtime"]["paper_order_lifecycle_canary_last_report"]["status"], "ready")
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.paper_order_lifecycle_canary_reviewed", audit_types)

    def test_missing_soak_evidence_blocks_lifecycle_canary(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["paper_order_lifecycle_soak_history"] = []

        report, _notes = self._run_canary(
            db,
            tenant,
            state,
            notes_missing={(day, "soak") for day in SESSION_DAYS},
        )

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("lifecycle_soak_missing", blocker_keys)

    def test_warning_sessions_remain_clean_but_record_warning(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["paper_order_lifecycle_soak_history"][1]["warnings"] = [
            {"key": "slow_fill", "detail": "Lifecycle terminal state was confirmed slowly."}
        ]

        report, _notes = self._run_canary(db, tenant, state)

        self.assertEqual(report["status"], "ready")
        warning_keys = {item["key"] for item in report["warnings"]}
        self.assertIn("lifecycle_soak_warning", warning_keys)

    def test_blocked_reconciliation_blocks_lifecycle_canary(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["paper_broker_reconciliation_history"][1].update(
            {
                "status": "blocked",
                "blockers": [{"key": "position_mismatch", "detail": "Broker and local positions disagree."}],
            }
        )

        report, _notes = self._run_canary(db, tenant, state)

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("reconciliation_blocked", blocker_keys)

    def test_missing_lifecycle_note_blocks_lifecycle_canary(self) -> None:
        db, tenant = self._db()
        state = self._state()

        report, _notes = self._run_canary(db, tenant, state, notes_missing={(SESSION_DAYS[1], "soak")})

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("lifecycle_note_missing", blocker_keys)

    def test_action_path_persists_report_without_order_or_setting_mutation(self) -> None:
        db, tenant = self._db()
        state = self._state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()
        before_settings = json.loads(json.dumps(state["settings"], sort_keys=True))
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="lifecycle-canary",
            user_id="lifecycle-canary",
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
                patch.object(automation_paper_order_lifecycle_canary_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(automation_paper_order_lifecycle_canary_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(automation_paper_order_lifecycle_canary_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service, "_build_personal_account_summary", return_value={"status": "unavailable"}),
                patch.object(trade_automation_service, "get_latest_trade_automation_equity_snapshot", return_value=None),
                patch.object(automation_paper_order_lifecycle_soak_service, "run_paper_order_lifecycle_soak") as soak_mock,
                patch.object(trade_automation_service, "open_trade_from_request") as open_trade_mock,
                patch.object(trade_automation_service, "sync_pending_orders_from_broker") as sync_orders_mock,
            ):
                self._seed_notes()
                snapshot = trade_automation_service.run_tenant_trade_automation_action(
                    db,
                    current_user=current_user,
                    request=OrganizationTradeAutomationActionRequest(action="run_paper_order_lifecycle_canary_review"),
                )
                notes = json.loads(notes_path.read_text(encoding="utf-8"))

        soak_mock.assert_not_called()
        open_trade_mock.assert_not_called()
        sync_orders_mock.assert_not_called()
        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertEqual(after_state["settings"], before_settings)
        self.assertEqual(snapshot["paper_order_lifecycle_canary"]["status"], "ready")
        self.assertTrue(snapshot["paper_order_lifecycle_canary"]["related_note_id"])
        self.assertTrue(snapshot["available_actions"]["can_run_paper_order_lifecycle_canary_review"])
        self.assertEqual(len([note for note in notes if "order-lifecycle-canary" in note.get("tags", [])]), 1)

    def test_scheduler_runs_review_without_auto_submit_by_default(self) -> None:
        db, tenant = self._db()
        state = self._state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_paper_order_lifecycle_canary_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(automation_paper_order_lifecycle_canary_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(automation_paper_order_lifecycle_canary_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(automation_paper_order_lifecycle_soak_service, "run_paper_order_lifecycle_soak") as soak_mock,
            ):
                self._seed_notes()
                summary = trade_automation_service.run_trade_automation_paper_order_lifecycle_canary_reviews(
                    db,
                    now=FIXED_NOW,
                )
                second_summary = trade_automation_service.run_trade_automation_paper_order_lifecycle_canary_reviews(
                    db,
                    now=FIXED_NOW,
                )

        soak_mock.assert_not_called()
        self.assertEqual(summary["reviewed"], 1)
        self.assertEqual(summary["submitted"], 0)
        self.assertEqual(second_summary["reviewed"], 0)
        self.assertEqual(second_summary["skipped"], 1)
        self.assertEqual(second_summary["items"][0]["reason"], "already_reviewed_for_session")
        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertEqual(after_state["runtime"]["paper_order_lifecycle_canary_last_report"]["run_source"], "scheduled")

    def test_scheduler_skips_before_close_buffer(self) -> None:
        db, tenant = self._db()
        state = self._state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()

        summary = trade_automation_service.run_trade_automation_paper_order_lifecycle_canary_reviews(
            db,
            now=BEFORE_CLOSE_NOW,
        )

        self.assertEqual(summary["reviewed"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["items"][0]["reason"], "review_window_not_open")

    def test_scheduler_auto_submit_when_explicitly_enabled_and_not_repeated(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["settings"]["paper_order_lifecycle_auto_submit_enabled"] = True
        state["runtime"]["paper_order_lifecycle_soak_history"] = [
            item for item in state["runtime"]["paper_order_lifecycle_soak_history"] if item["session_day"] != SESSION_DAYS[0]
        ]
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()

        def _soak_side_effect(*args, **kwargs):
            target_state = kwargs["state"]
            report = {
                "checked_at": "2026-04-24T20:24:00+00:00",
                "session_day": SESSION_DAYS[0],
                "status": "completed",
                "terminal_state": "canceled",
                "broker_order_id": "broker-entry-2026-04-24",
                "local_order_id": "local-order-2026-04-24",
                "reconciliation_status": "clean",
                "note_id": "paper-lifecycle-2026-04-24",
                "blockers": [],
                "warnings": [],
            }
            target_state.setdefault("runtime", {})["paper_order_lifecycle_soak_last_report"] = report
            return report

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_paper_order_lifecycle_canary_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(automation_paper_order_lifecycle_canary_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(automation_paper_order_lifecycle_canary_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(automation_paper_order_lifecycle_soak_service, "run_paper_order_lifecycle_soak", side_effect=_soak_side_effect) as soak_mock,
            ):
                self._seed_notes()
                summary = trade_automation_service.run_trade_automation_paper_order_lifecycle_canary_reviews(
                    db,
                    now=FIXED_NOW,
                )
                second_summary = trade_automation_service.run_trade_automation_paper_order_lifecycle_canary_reviews(
                    db,
                    now=FIXED_NOW,
                )

        self.assertEqual(soak_mock.call_count, 1)
        self.assertEqual(summary["submitted"], 1)
        self.assertEqual(summary["items"][0]["auto_submit_status"], "completed")
        self.assertEqual(second_summary["items"][0]["reason"], "already_reviewed_for_session")

    def test_scheduler_ignores_live_profile(self) -> None:
        db, tenant = self._db()
        paper_state = self._state()
        paper_state["settings"]["paper_order_lifecycle_canary_enabled"] = False
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        live_state = self._state()
        live_state["settings"]["execution_intent"] = "broker_live"
        trade_automation_service._write_trade_automation_state(tenant, live_state, profile_key="personal_live")
        db.commit()

        summary = trade_automation_service.run_trade_automation_paper_order_lifecycle_canary_reviews(
            db,
            now=FIXED_NOW,
        )

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["reviewed"], 0)
        self.assertEqual(summary["items"][0]["profile_key"], "personal_paper")
        live_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_live")
        self.assertFalse(live_after["runtime"].get("paper_order_lifecycle_canary_last_report"))


if __name__ == "__main__":
    unittest.main()
