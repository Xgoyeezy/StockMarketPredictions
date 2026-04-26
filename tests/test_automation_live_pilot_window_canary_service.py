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
    automation_live_pilot_window_canary_service,
    automation_live_pilot_window_service,
    notes_service,
    trade_automation_service,
)


FIXED_NOW = datetime(2026, 4, 24, 21, 10, tzinfo=timezone.utc)
BEFORE_CLOSE_NOW = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
SESSION_DAYS = ["2026-04-24", "2026-04-23", "2026-04-22"]


class AutomationLivePilotWindowCanaryServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="live-window-canary-test", name="Live Window Canary Test", status="active")
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
                "live_pilot_window_canary_enabled": True,
                "live_pilot_window_canary_auto_review_enabled": True,
                "live_pilot_window_canary_window_sessions": 5,
                "live_pilot_window_canary_required_clean_sessions": 3,
            }
        )
        state["runtime"].update(
            {
                "live_pilot_readiness_last_report": {
                    "status": "ready_to_request_approval",
                    "evaluated_at": "2026-04-24T20:35:00+00:00",
                    "broker_live_gate_status": "open",
                    "safety_lock_status": "clear",
                    "blockers": [],
                    "warnings": [],
                    "related_note_id": "live-readiness-latest",
                },
                "live_pilot_readiness_history": [
                    {
                        "at": f"{day}T20:35:00+00:00",
                        "session_day": day,
                        "status": "ready_to_request_approval",
                        "broker_live_gate_status": "open",
                        "safety_lock_status": "clear",
                        "blocker_count": 0,
                        "warning_count": 0,
                        "note_id": f"live-readiness-{day}",
                    }
                    for day in SESSION_DAYS
                ],
                "live_pilot_window_history": [
                    {
                        "at": f"{day}T20:55:00+00:00",
                        "checked_at": f"{day}T20:55:00+00:00",
                        "session_day": day,
                        "status": "completed",
                        "terminal_state": "canceled",
                        "broker_order_id": f"live-window-broker-{day}",
                        "local_order_id": f"live-window-local-{day}",
                        "local_trade_id": f"live-window-trade-{day}",
                        "reconciliation_status": "clean",
                        "notional_cap": 50.0,
                        "estimated_notional": 0.50,
                        "selected_candidate": {
                            "ticker": "SPY",
                            "portfolio_rank": 1,
                            "alpha_score": 92,
                            "execution_score": 88,
                            "edge_to_cost_ratio": 3.4,
                            "auto_entry_eligible": True,
                        },
                        "position_evidence": {"slippage_bps": 6.0},
                        "exit_evidence": {"realized_pnl": 0.0},
                        "cancel_evidence": {"canceled": True},
                        "blockers": [],
                        "warnings": [],
                        "note_id": f"live-window-{day}",
                    }
                    for day in SESSION_DAYS
                ],
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

    def _seed_notes(self, *, missing_window: set[str] | None = None) -> None:
        missing_window = missing_window or set()
        for day in SESSION_DAYS:
            tag_base = ["automation-ai", "profile-personal_paper", f"session-{day}"]
            if day not in missing_window:
                notes_service.create_note(
                    title=f"Supervised live pilot {day}",
                    body="Supervised live pilot terminal state was recorded",
                    tags=[*tag_base, "supervised-live-pilot", "live-pilot-readiness"],
                    owner="automation-ai",
                    note_type="risk_review",
                )
            notes_service.create_note(
                title=f"Live readiness {day}",
                body="Live pilot readiness clean",
                tags=[*tag_base, "live-pilot-readiness"],
                owner="automation-ai",
                note_type="risk_review",
            )

    def _seed_submit_events(self, db, tenant, *, days: list[str] | None = None, order_type: str = "limit") -> None:
        for day in days or SESSION_DAYS:
            db.add(
                OrderEventRecord(
                    tenant_id=tenant.id,
                    trade_id=f"live-window-trade-{day}",
                    ticker="SPY",
                    event_key="order.submitted",
                    status="submitted",
                    order_type=order_type,
                    detail="Supervised live window submit evidence.",
                    payload_json={
                        "automation_profile_key": "personal_live",
                        "live_pilot_window_id": f"live-window-{day}",
                        "request": {"order_type": order_type},
                    },
                    created_at=datetime.fromisoformat(f"{day}T20:50:00+00:00"),
                )
            )
            db.add(
                OrderEventRecord(
                    tenant_id=tenant.id,
                    trade_id=f"live-window-trade-{day}",
                    ticker="SPY",
                    event_key="order.canceled",
                    status="canceled",
                    order_type="limit",
                    detail="Supervised live window cancel evidence.",
                    payload_json={
                        "automation_profile_key": "personal_live",
                        "live_pilot_window_id": f"live-window-{day}",
                    },
                    created_at=datetime.fromisoformat(f"{day}T20:55:00+00:00"),
                )
            )
        db.commit()

    def _run_canary(
        self,
        db,
        tenant,
        paper_state,
        live_state=None,
        *,
        missing_window_notes=None,
        seed_events: bool = True,
        now=FIXED_NOW,
    ):
        if seed_events:
            self._seed_submit_events(db, tenant)
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with patch.object(notes_service, "NOTES_PATH", notes_path):
                self._seed_notes(missing_window=missing_window_notes)
                report = automation_live_pilot_window_canary_service.run_live_pilot_window_canary_review(
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

    def test_action_schema_accepts_live_pilot_window_canary_review(self) -> None:
        payload = OrganizationTradeAutomationActionRequest(action="run_live_pilot_window_canary_review")

        self.assertEqual(payload.action, "run_live_pilot_window_canary_review")

    def test_clean_supervised_live_window_sessions_mark_canary_ready_and_write_note(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()

        report, notes = self._run_canary(db, tenant, paper_state)

        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["clean_session_count"], 3)
        self.assertEqual(report["required_clean_sessions"], 3)
        self.assertFalse(report["blockers"])
        self.assertEqual(report["latest_window_status"], "completed")
        self.assertEqual(report["latest_terminal_state"], "canceled")
        self.assertEqual(report["candidate_evidence"]["ticker"], "SPY")
        canary_notes = [note for note in notes if "supervised-live-pilot-canary" in note.get("tags", [])]
        self.assertEqual(len(canary_notes), 1)
        self.assertIn("does not place, cancel, or close orders", canary_notes[0]["body"])
        self.assertEqual(paper_state["runtime"]["live_pilot_window_canary_last_report"]["status"], "ready")
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.live_pilot_window_canary_reviewed", audit_types)

    def test_missing_window_evidence_blocks_canary(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["live_pilot_window_history"] = []

        report, _notes = self._run_canary(
            db,
            tenant,
            paper_state,
            missing_window_notes=set(SESSION_DAYS),
            seed_events=False,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("live_window_missing", {item["key"] for item in report["blockers"]})

    def test_unresolved_working_order_blocks_canary(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["live_pilot_window_history"][0].update(
            {
                "status": "entered",
                "terminal_state": "working",
                "reconciliation_status": "working",
            }
        )

        report, _notes = self._run_canary(db, tenant, paper_state)

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("live_window_not_completed", blocker_keys)
        self.assertIn("live_reconciliation_warning", {item["key"] for item in report["warnings"]})

    def test_closed_terminal_state_counts_as_clean(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        for item in paper_state["runtime"]["live_pilot_window_history"]:
            item["terminal_state"] = "closed"
            item["cancel_evidence"] = {}
            item["exit_evidence"] = {"closed": True, "realized_pnl": 0.0}

        report, _notes = self._run_canary(db, tenant, paper_state)

        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["latest_terminal_state"], "closed")

    def test_reconciliation_mismatch_missing_note_and_non_limit_entry_block_canary(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["live_pilot_window_history"][0]["reconciliation_status"] = "blocked"

        report, _notes = self._run_canary(
            db,
            tenant,
            paper_state,
            missing_window_notes={"2026-04-24"},
            seed_events=False,
        )
        db.query(OrderEventRecord).delete()
        self._seed_submit_events(db, tenant, days=["2026-04-24"], order_type="market")
        report, _notes = self._run_canary(
            db,
            tenant,
            paper_state,
            missing_window_notes={"2026-04-24"},
            seed_events=False,
        )

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("live_reconciliation_blocked", blocker_keys)
        self.assertIn("live_window_note_missing", blocker_keys)
        self.assertIn("non_limit_entry_detected", blocker_keys)

    def test_active_live_kill_switch_blocks_canary(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()

        report, _notes = self._run_canary(db, tenant, paper_state, live_state=self._live_state(kill_switch=True))

        self.assertEqual(report["status"], "blocked")
        self.assertIn("kill_switch_active", {item["key"] for item in report["blockers"]})

    def test_candidate_rejection_blocks_canary(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["live_pilot_window_history"][0]["selected_candidate"].update(
            {"auto_entry_eligible": False, "reject_reason": "Candidate failed the gate."}
        )

        report, _notes = self._run_canary(db, tenant, paper_state)

        self.assertEqual(report["status"], "blocked")
        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertIn("candidate_not_auto_eligible", blocker_keys)
        self.assertIn("candidate_rejected", blocker_keys)

    def test_action_path_persists_report_without_order_or_setting_mutation(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state()
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        trade_automation_service._write_trade_automation_state(tenant, live_state, profile_key="personal_live")
        self._seed_submit_events(db, tenant)
        db.commit()
        before_paper_settings = json.loads(json.dumps(paper_state["settings"], sort_keys=True))
        before_live_settings = json.loads(json.dumps(live_state["settings"], sort_keys=True))
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="live-window-canary",
            user_id="live-window-canary",
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
                patch.object(automation_live_pilot_window_service, "prepare_live_pilot_window") as prepare_mock,
                patch.object(automation_live_pilot_window_service, "run_live_pilot_window_entry") as entry_mock,
                patch.object(automation_live_pilot_window_service, "run_live_pilot_window_exit") as exit_mock,
                patch.object(trade_automation_service, "open_trade_from_request") as open_trade_mock,
                patch.object(trade_automation_service, "sync_pending_orders_from_broker") as sync_orders_mock,
            ):
                self._seed_notes()
                snapshot = trade_automation_service.run_tenant_trade_automation_action(
                    db,
                    current_user=current_user,
                    request=OrganizationTradeAutomationActionRequest(action="run_live_pilot_window_canary_review"),
                )
                notes = json.loads(notes_path.read_text(encoding="utf-8"))

        prepare_mock.assert_not_called()
        entry_mock.assert_not_called()
        exit_mock.assert_not_called()
        open_trade_mock.assert_not_called()
        sync_orders_mock.assert_not_called()
        paper_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        live_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_live")
        self.assertEqual(paper_after["settings"], before_paper_settings)
        self.assertEqual(live_after["settings"], before_live_settings)
        self.assertEqual(snapshot["live_pilot_window_canary"]["status"], "ready")
        self.assertTrue(snapshot["live_pilot_window_canary"]["related_note_id"])
        self.assertTrue(snapshot["available_actions"]["can_run_live_pilot_window_canary_review"])
        self.assertEqual(len([note for note in notes if "supervised-live-pilot-canary" in note.get("tags", [])]), 1)

    def test_scheduler_runs_review_once_after_close(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        trade_automation_service._write_trade_automation_state(tenant, self._live_state(), profile_key="personal_live")
        self._seed_submit_events(db, tenant)
        db.commit()

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_live_pilot_window_service, "run_live_pilot_window_entry") as entry_mock,
                patch.object(automation_live_pilot_window_service, "run_live_pilot_window_exit") as exit_mock,
            ):
                self._seed_notes()
                summary = trade_automation_service.run_trade_automation_live_pilot_window_canary_reviews(
                    db,
                    now=FIXED_NOW,
                )
                second_summary = trade_automation_service.run_trade_automation_live_pilot_window_canary_reviews(
                    db,
                    now=FIXED_NOW,
                )

        entry_mock.assert_not_called()
        exit_mock.assert_not_called()
        self.assertEqual(summary["reviewed"], 1)
        self.assertEqual(second_summary["reviewed"], 0)
        self.assertEqual(second_summary["skipped"], 1)
        self.assertEqual(second_summary["items"][0]["reason"], "already_reviewed_for_session")
        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertEqual(after_state["runtime"]["live_pilot_window_canary_last_report"]["run_source"], "scheduled")

    def test_scheduler_skips_before_close_buffer_and_when_disabled(self) -> None:
        db, tenant = self._db()
        state = self._paper_state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()

        before_summary = trade_automation_service.run_trade_automation_live_pilot_window_canary_reviews(
            db,
            now=BEFORE_CLOSE_NOW,
        )
        state["settings"]["live_pilot_window_canary_auto_review_enabled"] = False
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()
        disabled_summary = trade_automation_service.run_trade_automation_live_pilot_window_canary_reviews(
            db,
            now=FIXED_NOW,
        )

        self.assertEqual(before_summary["reviewed"], 0)
        self.assertEqual(before_summary["items"][0]["reason"], "review_window_not_open")
        self.assertEqual(disabled_summary["reviewed"], 0)
        self.assertEqual(disabled_summary["items"][0]["reason"], "live_pilot_window_canary_auto_review_disabled")

    def test_scheduler_ignores_live_profile(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["settings"]["live_pilot_window_canary_enabled"] = False
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        live_state = self._paper_state()
        live_state["settings"]["execution_intent"] = "broker_live"
        trade_automation_service._write_trade_automation_state(tenant, live_state, profile_key="personal_live")
        db.commit()

        summary = trade_automation_service.run_trade_automation_live_pilot_window_canary_reviews(db, now=FIXED_NOW)

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["reviewed"], 0)
        self.assertEqual(summary["items"][0]["profile_key"], "personal_paper")
        live_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_live")
        self.assertFalse(live_after["runtime"].get("live_pilot_window_canary_last_report"))


if __name__ == "__main__":
    unittest.main()
