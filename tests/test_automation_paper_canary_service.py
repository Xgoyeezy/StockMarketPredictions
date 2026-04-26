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
from backend.services import automation_paper_canary_service, notes_service, trade_automation_service


FIXED_NOW = datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc)
SCHEDULED_NOW = datetime(2026, 4, 24, 20, 20, tzinfo=timezone.utc)
BEFORE_CLOSE_NOW = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
SESSION_DAYS = ["2026-04-24", "2026-04-23", "2026-04-22"]


class AutomationPaperCanaryServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="paper-canary-test", name="Paper Canary Test", status="active")
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
                "ai_daily_review_enabled": True,
            }
        )
        state["runtime"].update(
            {
                "last_cycle_at": "2026-04-24T14:55:00+00:00",
                "last_success_at": "2026-04-24T14:55:00+00:00",
                "current_route_reconciliation_status": "clean",
                "ledger_snapshot_consistency": "consistent",
                "ai_daily_journal": {
                    day: {
                        "session_day": day,
                        "observations": [{"tone": "good", "action_type": "open_trade"}],
                        "review": {"status": "reviewed", "session_day": day, "note_id": f"daily-{day}"},
                        "note_id": f"daily-{day}",
                    }
                    for day in SESSION_DAYS
                },
                "ai_review_history": [
                    {"status": "reviewed", "session_day": SESSION_DAYS[0], "applied_changes": []}
                ],
                "state_control_last_evaluation": {
                    "state": "healthy",
                    "score": 91,
                    "session_day": SESSION_DAYS[0],
                    "evaluated_at": "2026-04-24T14:55:00+00:00",
                    "note_id": "state-2026-04-24",
                },
                "state_control_shadow_last_report": {
                    "status": "pass",
                    "session_day": SESSION_DAYS[0],
                    "evaluated_at": "2026-04-24T14:56:00+00:00",
                    "scenario_count": 6,
                    "failed_count": 0,
                    "worst_state": "halt",
                    "note_id": "shadow-2026-04-24",
                },
                "state_control_shadow_report_history": [
                    {
                        "at": f"{day}T14:56:00+00:00",
                        "session_day": day,
                        "status": "pass",
                        "scenario_count": 6,
                        "failed_count": 0,
                        "worst_state": "halt",
                        "note_id": f"shadow-{day}",
                    }
                    for day in SESSION_DAYS
                ],
                "paper_broker_reconciliation_history": [
                    {
                        "at": f"{day}T20:15:00+00:00",
                        "session_day": day,
                        "status": "clean",
                        "matched_count": 1,
                        "blocker_count": 0,
                        "warning_count": 0,
                        "note_id": f"paper-broker-{day}",
                        "run_source": "scheduled",
                    }
                    for day in SESSION_DAYS
                ],
                "paper_order_lifecycle_soak_history": [
                    {
                        "at": f"{day}T20:18:00+00:00",
                        "session_day": day,
                        "status": "completed",
                        "terminal_state": "canceled",
                        "broker_order_id": f"broker-entry-{day}",
                        "blocker_count": 0,
                        "warning_count": 0,
                        "note_id": f"paper-lifecycle-{day}",
                    }
                    for day in SESSION_DAYS
                ],
                "paper_order_lifecycle_canary_last_report": {
                    "status": "ready",
                    "session_day": SESSION_DAYS[0],
                    "evaluated_at": "2026-04-24T20:22:00+00:00",
                    "clean_session_count": 3,
                    "required_clean_sessions": 3,
                    "window_session_count": 3,
                    "latest_soak_status": "completed",
                    "latest_terminal_state": "canceled",
                    "latest_reconciliation_status": "clean",
                    "related_note_id": "lifecycle-canary-2026-04-24",
                    "note_id": "lifecycle-canary-2026-04-24",
                    "blockers": [],
                },
            }
        )
        return state

    def _closed_frame(self, tenant: Tenant) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "trade_id": f"trade-{day}",
                    "ticker": "SPY",
                    "closed_at": f"{day}T15:10:00+00:00",
                    "realized_pnl": 45.0,
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": "personal_paper",
                }
                for day in SESSION_DAYS
            ]
        )

    def _seed_notes(self, *, missing: set[tuple[str, str]] | None = None) -> None:
        missing = missing or set()
        for day in SESSION_DAYS:
            tag_base = ["automation-ai", "profile-personal_paper", f"session-{day}"]
            if (day, "daily") not in missing:
                notes_service.create_note(
                    title=f"Daily {day}",
                    body="Daily AI review",
                    tags=[*tag_base, "daily-review"],
                    owner="automation-ai",
                    note_type="risk_review",
                )
            if (day, "state") not in missing:
                notes_service.create_note(
                    title=f"State {day}",
                    body="State control: Healthy",
                    tags=[*tag_base, "state-control"],
                    owner="automation-ai",
                    note_type="risk_review",
                )
            if (day, "shadow") not in missing:
                notes_service.create_note(
                    title=f"Shadow {day}",
                    body="Shadow validation passed",
                    tags=[*tag_base, "state-control", "shadow-validation"],
                    owner="automation-ai",
                    note_type="risk_review",
                )
            if (day, "paper_broker") not in missing:
                notes_service.create_note(
                    title=f"Paper broker {day}",
                    body="Paper broker reconciliation clean",
                    tags=[*tag_base, "paper-broker", "reconciliation"],
                    owner="automation-ai",
                    note_type="risk_review",
                )
            if (day, "paper_lifecycle") not in missing:
                notes_service.create_note(
                    title=f"Paper lifecycle {day}",
                    body="Paper order lifecycle soak completed",
                    tags=[*tag_base, "paper-broker", "order-lifecycle-soak"],
                    owner="automation-ai",
                    note_type="risk_review",
                )

    def _seed_events(self, db, tenant: Tenant, *, slippage_bps: float = 8.0, status: str = "filled") -> None:
        for day in SESSION_DAYS:
            db.add(
                OrderEventRecord(
                    tenant_id=tenant.id,
                    trade_id=f"trade-{day}",
                    ticker="SPY",
                    event_key="order.filled",
                    status=status,
                    detail="Paper fill recorded.",
                    payload_json={
                        "automation_profile_key": "personal_paper",
                        "automation_cycle_id": f"cycle-{day}",
                        "slippage_bps": slippage_bps,
                    },
                    created_at=datetime.fromisoformat(f"{day}T15:05:00+00:00"),
                )
            )
        db.commit()

    def _run_canary(self, db, tenant, state, *, notes_missing=None, slippage_bps=8.0, event_status="filled"):
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_paper_canary_service.sdm, "read_closed_trades", return_value=self._closed_frame(tenant)),
            ):
                self._seed_notes(missing=notes_missing)
                self._seed_events(db, tenant, slippage_bps=slippage_bps, status=event_status)
                report = automation_paper_canary_service.run_paper_canary_review(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    rollout_readiness={"allows_live_rollout": False},
                    now=FIXED_NOW,
                )
                db.commit()
                notes = json.loads(notes_path.read_text(encoding="utf-8"))
        return report, notes

    def test_action_schema_accepts_paper_canary_review(self) -> None:
        payload = OrganizationTradeAutomationActionRequest(action="run_paper_canary_review")

        self.assertEqual(payload.action, "run_paper_canary_review")

    def test_clean_paper_sessions_mark_canary_ready_and_write_note(self) -> None:
        db, tenant = self._db()
        state = self._state()

        report, notes = self._run_canary(db, tenant, state)

        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["clean_session_count"], 3)
        self.assertEqual(report["required_clean_sessions"], 3)
        self.assertEqual(report["shadow_pass_rate"], 1.0)
        self.assertFalse(report["blockers"])
        canary_notes = [note for note in notes if "paper-canary" in note.get("tags", [])]
        self.assertEqual(len(canary_notes), 1)
        self.assertIn("Settings changed during window: no", canary_notes[0]["body"])
        self.assertEqual(state["runtime"]["paper_canary_last_report"]["status"], "ready")
        self.assertEqual(state["runtime"]["paper_canary_last_report"]["run_source"], "manual")
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.paper_canary_reviewed", audit_types)

    def test_missing_required_notes_block_canary(self) -> None:
        db, tenant = self._db()
        state = self._state()

        report, _notes = self._run_canary(db, tenant, state, notes_missing={(SESSION_DAYS[1], "state")})

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("state_control_missing", blocker_keys)

    def test_failed_shadow_validation_blocks_canary(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["state_control_shadow_report_history"][1]["status"] = "fail"
        state["runtime"]["state_control_shadow_report_history"][1]["failed_count"] = 1

        report, _notes = self._run_canary(db, tenant, state)

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("shadow_validation_failed", blocker_keys)

    def test_hard_state_halt_blocks_canary(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["state_control_last_evaluation"]["state"] = "halt"

        report, _notes = self._run_canary(db, tenant, state)

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("state_control_halt", blocker_keys)

    def test_slippage_above_canary_limit_blocks_canary(self) -> None:
        db, tenant = self._db()
        state = self._state()

        report, _notes = self._run_canary(db, tenant, state, slippage_bps=55.0)

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("slippage_blocked", blocker_keys)

    def test_paper_broker_reconciliation_mismatch_blocks_canary(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["paper_broker_reconciliation_history"][1].update(
            {
                "status": "blocked",
                "blocker_count": 1,
                "blockers": [
                    {
                        "key": "orphan_broker_order",
                        "detail": "Broker-paper order is missing locally.",
                    }
                ],
            }
        )

        report, _notes = self._run_canary(db, tenant, state)

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("paper_broker_reconciliation_blocked", blocker_keys)

    def test_paper_order_lifecycle_soak_blocker_blocks_canary(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["paper_order_lifecycle_soak_history"][1].update(
            {
                "status": "blocked",
                "blocker_count": 1,
                "blockers": [
                    {
                        "key": "reconciliation_blocked",
                        "detail": "Lifecycle terminal state does not reconcile.",
                    }
                ],
            }
        )

        report, _notes = self._run_canary(db, tenant, state)

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("paper_order_lifecycle_soak_blocked", blocker_keys)

    def test_paper_order_lifecycle_canary_not_ready_blocks_canary(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["paper_order_lifecycle_canary_last_report"].update(
            {
                "status": "collecting",
                "clean_session_count": 2,
                "required_clean_sessions": 3,
            }
        )

        report, _notes = self._run_canary(db, tenant, state)

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("paper_order_lifecycle_canary_not_ready", blocker_keys)
        self.assertEqual(report["paper_order_lifecycle_canary"]["status"], "collecting")

    def test_action_path_persists_report_without_orders_or_setting_changes(self) -> None:
        db, tenant = self._db()
        state = self._state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()
        before_settings = json.loads(json.dumps(state["settings"], sort_keys=True))
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="paper-canary",
            user_id="paper-canary",
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
                patch.object(automation_paper_canary_service, "_utc_now", return_value=FIXED_NOW),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=self._closed_frame(tenant)),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(
                    trade_automation_service,
                    "_build_personal_account_summary",
                    return_value={
                        "provider": "alpaca_paper",
                        "label": "Paper account",
                        "connected": False,
                        "status": "unavailable",
                    },
                ),
                patch.object(trade_automation_service, "get_latest_trade_automation_equity_snapshot", return_value=None),
                patch.object(trade_automation_service, "open_trade_from_request") as open_trade_mock,
                patch.object(trade_automation_service, "sync_pending_orders_from_broker") as sync_orders_mock,
                patch.object(trade_automation_service, "_manage_automation_positions") as manage_positions_mock,
            ):
                self._seed_notes()
                self._seed_events(db, tenant)
                snapshot = trade_automation_service.run_tenant_trade_automation_action(
                    db,
                    current_user=current_user,
                    request=OrganizationTradeAutomationActionRequest(action="run_paper_canary_review"),
                )
                notes = json.loads(notes_path.read_text(encoding="utf-8"))

        open_trade_mock.assert_not_called()
        sync_orders_mock.assert_not_called()
        manage_positions_mock.assert_not_called()
        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertEqual(after_state["settings"], before_settings)
        self.assertEqual(snapshot["paper_canary"]["status"], "ready")
        self.assertEqual(snapshot["paper_canary"]["run_source"], "manual")
        self.assertEqual(snapshot["paper_canary"]["clean_session_count"], 3)
        self.assertTrue(snapshot["paper_canary"]["related_note_id"])
        self.assertTrue(snapshot["available_actions"]["can_run_paper_canary_review"])
        canary_notes = [note for note in notes if "paper-canary" in note.get("tags", [])]
        self.assertEqual(len(canary_notes), 1)
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.paper_canary_reviewed", audit_types)

    def test_scheduled_runner_runs_once_after_post_close_for_paper_profile(self) -> None:
        db, tenant = self._db()
        state = self._state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(
                    trade_automation_service,
                    "get_trade_summary",
                    return_value={"rollout_readiness": {"allows_live_rollout": False}},
                ),
                patch.object(automation_paper_canary_service.sdm, "read_closed_trades", return_value=self._closed_frame(tenant)),
            ):
                self._seed_notes()
                self._seed_events(db, tenant)
                summary = trade_automation_service.run_trade_automation_paper_canary_reviews(
                    db,
                    now=SCHEDULED_NOW,
                )
                second_summary = trade_automation_service.run_trade_automation_paper_canary_reviews(
                    db,
                    now=SCHEDULED_NOW,
                )
                notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["reviewed"], 1)
        self.assertEqual(summary["session_day"], "2026-04-24")
        self.assertEqual(second_summary["reviewed"], 0)
        self.assertEqual(second_summary["skipped"], 1)
        self.assertEqual(second_summary["items"][0]["reason"], "already_reviewed_for_session")
        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        canary = after_state["runtime"]["paper_canary_last_report"]
        self.assertEqual(canary["status"], "ready")
        self.assertEqual(canary["run_source"], "scheduled")
        self.assertEqual(after_state["runtime"]["paper_canary_last_scheduled_session_day"], "2026-04-24")
        self.assertTrue(str(after_state["runtime"]["paper_canary_next_eligible_run_at"]).startswith("2026-04-27"))
        canary_notes = [note for note in notes if "paper-canary" in note.get("tags", [])]
        self.assertEqual(len(canary_notes), 1)
        self.assertIn("Run source: scheduled", canary_notes[0]["body"])

    def test_scheduled_runner_skips_before_close_buffer(self) -> None:
        db, tenant = self._db()
        state = self._state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()

        summary = trade_automation_service.run_trade_automation_paper_canary_reviews(
            db,
            now=BEFORE_CLOSE_NOW,
        )

        self.assertEqual(summary["reviewed"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["items"][0]["reason"], "review_window_not_open")
        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertFalse(after_state["runtime"].get("paper_canary_last_report"))

    def test_scheduled_runner_skips_when_auto_review_disabled(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["settings"]["paper_canary_auto_review_enabled"] = False
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()

        summary = trade_automation_service.run_trade_automation_paper_canary_reviews(
            db,
            now=SCHEDULED_NOW,
        )

        self.assertEqual(summary["reviewed"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["items"][0]["reason"], "paper_canary_auto_review_disabled")

    def test_scheduled_runner_ignores_live_profile(self) -> None:
        db, tenant = self._db()
        paper_state = self._state()
        paper_state["settings"]["paper_canary_auto_review_enabled"] = False
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        live_state = self._state()
        live_state["settings"]["execution_intent"] = "broker_live"
        trade_automation_service._write_trade_automation_state(tenant, live_state, profile_key="personal_live")
        db.commit()

        summary = trade_automation_service.run_trade_automation_paper_canary_reviews(
            db,
            now=SCHEDULED_NOW,
        )

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["reviewed"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["items"][0]["profile_key"], "personal_paper")
        live_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_live")
        self.assertFalse(live_after["runtime"].get("paper_canary_last_report"))


if __name__ == "__main__":
    unittest.main()
