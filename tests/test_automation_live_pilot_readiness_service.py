from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.saas import AuditEvent, Tenant
from backend.schemas import OrganizationTradeAutomationActionRequest
from backend.services import (
    automation_live_pilot_readiness_service,
    automation_paper_order_lifecycle_soak_service,
    notes_service,
    trade_automation_service,
)


FIXED_NOW = datetime(2026, 4, 24, 20, 35, tzinfo=timezone.utc)
STALE_NOW = datetime(2026, 4, 29, 20, 35, tzinfo=timezone.utc)


class AutomationLivePilotReadinessServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="live-readiness-test", name="Live Readiness Test", status="active")
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
                "enabled": True,
                "armed": True,
                "kill_switch": False,
                "execution_intent": "broker_paper",
            }
        )
        state["runtime"].update(
            {
                "paper_canary_last_report": {
                    "status": "ready",
                    "evaluated_at": "2026-04-24T20:20:00+00:00",
                    "clean_session_count": 3,
                    "required_clean_sessions": 3,
                    "note_coverage": {"covered": 3, "required": 3, "ratio": 1},
                    "blockers": [],
                    "related_note_id": "paper-canary-note",
                },
                "paper_order_lifecycle_canary_last_report": {
                    "status": "ready",
                    "evaluated_at": "2026-04-24T20:22:00+00:00",
                    "clean_session_count": 3,
                    "required_clean_sessions": 3,
                    "note_coverage": {"covered": 3, "required": 3, "ratio": 1},
                    "blockers": [],
                    "related_note_id": "lifecycle-canary-note",
                },
                "paper_broker_reconciliation_last_report": {
                    "status": "clean",
                    "checked_at": "2026-04-24T20:15:00+00:00",
                    "matched_count": 4,
                    "blockers": [],
                    "related_note_id": "paper-broker-note",
                },
                "state_control_state": "healthy",
                "state_control_halt_active": False,
                "state_control_last_evaluation": {
                    "state": "healthy",
                    "score": 91,
                    "evaluated_at": "2026-04-24T20:10:00+00:00",
                    "note_id": "state-note",
                },
                "state_control_shadow_last_report": {
                    "status": "pass",
                    "evaluated_at": "2026-04-24T20:12:00+00:00",
                    "scenario_count": 6,
                    "failed_count": 0,
                    "note_id": "shadow-note",
                },
            }
        )
        return state

    def _live_state(self) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state(
            {
                "settings": {
                    "enabled": False,
                    "armed": False,
                    "kill_switch": False,
                    "execution_intent": "broker_live",
                },
                "runtime": {},
            }
        )
        state["settings"]["execution_intent"] = "broker_live"
        return state

    def _settings_patch(self, *, credentials=True, live_enabled=True):
        return patch.object(
            automation_live_pilot_readiness_service,
            "settings",
            SimpleNamespace(
                alpaca_api_key_id="paper-key" if credentials else "",
                alpaca_api_secret_key="paper-secret" if credentials else "",
                alpaca_live_api_key_id="live-key" if credentials else "",
                alpaca_live_api_secret_key="live-secret" if credentials else "",
                alpaca_live_trading_enabled=live_enabled,
            ),
        )

    def _run_review(
        self,
        db,
        tenant,
        paper_state,
        live_state=None,
        *,
        rollout_allows_live=True,
        credentials=True,
        live_enabled=True,
        now=FIXED_NOW,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                self._settings_patch(credentials=credentials, live_enabled=live_enabled),
            ):
                report = automation_live_pilot_readiness_service.run_live_pilot_readiness_review(
                    db,
                    tenant=tenant,
                    paper_state=paper_state,
                    live_state=live_state or self._live_state(),
                    rollout_readiness={"allows_live_rollout": rollout_allows_live},
                    now=now,
                )
                db.commit()
                notes = json.loads(notes_path.read_text(encoding="utf-8"))
        return report, notes

    def test_action_schema_accepts_live_pilot_readiness_review(self) -> None:
        payload = OrganizationTradeAutomationActionRequest(action="run_live_pilot_readiness_review")

        self.assertEqual(payload.action, "run_live_pilot_readiness_review")

    def test_ready_evidence_marks_ready_and_writes_note(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()

        report, notes = self._run_review(db, tenant, paper_state)

        self.assertEqual(report["status"], "ready_to_request_approval")
        self.assertEqual(report["broker_live_gate_status"], "open")
        self.assertEqual(report["safety_lock_status"], "clear")
        self.assertFalse(report["blockers"])
        readiness_notes = [note for note in notes if "live-pilot-readiness" in note.get("tags", [])]
        self.assertEqual(len(readiness_notes), 1)
        self.assertIn("does not place live orders", readiness_notes[0]["body"])
        self.assertEqual(paper_state["runtime"]["live_pilot_readiness_last_report"]["status"], "ready_to_request_approval")
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.live_pilot_readiness_reviewed", audit_types)

    def test_blocked_paper_canary_blocks_readiness(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["paper_canary_last_report"]["status"] = "blocked"

        report, _notes = self._run_review(db, tenant, paper_state)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("paper_canary_not_ready", {item["key"] for item in report["blockers"]})

    def test_blocked_lifecycle_canary_blocks_readiness(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["paper_order_lifecycle_canary_last_report"]["status"] = "blocked"

        report, _notes = self._run_review(db, tenant, paper_state)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("lifecycle_canary_not_ready", {item["key"] for item in report["blockers"]})

    def test_stale_evidence_warns_without_blocking_clean_status(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()

        report, _notes = self._run_review(db, tenant, paper_state, now=STALE_NOW)

        self.assertEqual(report["status"], "warning")
        self.assertIn("paper_canary_stale", {item["key"] for item in report["warnings"]})

    def test_live_gate_locked_blocks_readiness(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()

        report, _notes = self._run_review(db, tenant, paper_state, rollout_allows_live=False)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("broker_live_gate_locked", {item["key"] for item in report["blockers"]})

    def test_missing_live_config_blocks_readiness(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()

        report, _notes = self._run_review(db, tenant, paper_state, credentials=False, live_enabled=False)

        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("live_broker_credentials_missing", blocker_keys)
        self.assertIn("live_broker_disabled", blocker_keys)

    def test_active_kill_switch_blocks_readiness(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state()
        live_state["settings"]["kill_switch"] = True

        report, _notes = self._run_review(db, tenant, paper_state, live_state=live_state)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("safety_lock_active", {item["key"] for item in report["blockers"]})

    def test_state_control_halt_blocks_readiness(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["state_control_last_evaluation"]["state"] = "halt"

        report, _notes = self._run_review(db, tenant, paper_state)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("state_control_halt", {item["key"] for item in report["blockers"]})

    def test_action_path_persists_metadata_and_leaves_live_safety_unchanged(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state()
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        trade_automation_service._write_trade_automation_state(tenant, live_state, profile_key="personal_live")
        db.commit()
        before_paper_settings = json.loads(json.dumps(paper_state["settings"], sort_keys=True))
        before_live_settings = json.loads(json.dumps(live_state["settings"], sort_keys=True))
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="live-readiness",
            user_id="live-readiness",
            permissions=("tenant.manage_support",),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                self._settings_patch(credentials=True, live_enabled=True),
                patch.object(
                    trade_automation_service,
                    "get_trade_summary",
                    return_value={"rollout_readiness": {"allows_live_rollout": True}},
                ),
                patch.object(
                    trade_automation_service,
                    "_build_personal_account_summary",
                    return_value={"connected": False, "status": "unavailable"},
                ),
                patch.object(trade_automation_service, "open_trade_from_request") as open_trade_mock,
                patch.object(trade_automation_service, "sync_pending_orders_from_broker") as sync_orders_mock,
                patch.object(automation_paper_order_lifecycle_soak_service, "run_paper_order_lifecycle_soak") as soak_mock,
            ):
                snapshot = trade_automation_service.run_tenant_trade_automation_action(
                    db,
                    current_user=current_user,
                    request=OrganizationTradeAutomationActionRequest(action="run_live_pilot_readiness_review"),
                )
                notes = json.loads(notes_path.read_text(encoding="utf-8"))

        open_trade_mock.assert_not_called()
        sync_orders_mock.assert_not_called()
        soak_mock.assert_not_called()
        after_paper_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        after_live_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_live")
        self.assertEqual(after_paper_state["settings"], before_paper_settings)
        self.assertEqual(after_live_state["settings"], before_live_settings)
        self.assertEqual(snapshot["live_pilot_readiness"]["status"], "ready_to_request_approval")
        self.assertTrue(snapshot["live_pilot_readiness"]["related_note_id"])
        self.assertTrue(snapshot["available_actions"]["can_run_live_pilot_readiness_review"])
        self.assertEqual(len([note for note in notes if "live-pilot-readiness" in note.get("tags", [])]), 1)
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.live_pilot_readiness_reviewed", audit_types)


if __name__ == "__main__":
    unittest.main()
