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
from backend.models.saas import AuditEvent, Tenant
from backend.schemas import OrganizationTradeAutomationActionRequest
from backend.services import (
    automation_limited_live_safety_ladder_service,
    notes_service,
    trade_automation_service,
)


FIXED_NOW = datetime(2026, 4, 24, 21, 20, tzinfo=timezone.utc)


class AutomationLimitedLiveSafetyLadderServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="limited-live-ladder-test", name="Limited Live Ladder Test", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        return db, tenant

    def _patch_notes(self):
        notes_dir = tempfile.TemporaryDirectory()
        self.addCleanup(notes_dir.cleanup)
        notes_path = Path(notes_dir.name) / "notes.json"
        notes_path.write_text("[]", encoding="utf-8")
        return patch.object(notes_service, "NOTES_PATH", notes_path), notes_path

    def _paper_state(self) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "enabled": False,
                "armed": False,
                "kill_switch": False,
                "execution_intent": "broker_paper",
                "limited_live_next_tier_cap_canary_enabled": True,
                "limited_live_next_tier_cap_canary_auto_review_enabled": True,
                "limited_live_next_tier_cap_canary_window_sessions": 5,
                "limited_live_next_tier_cap_canary_required_clean_sessions": 3,
                "limited_live_next_tier_cap_canary_stale_after_days": 2,
                "limited_live_higher_cap_report_enabled": True,
                "limited_live_higher_cap_report_auto_review_enabled": True,
                "limited_live_higher_cap_required_clean_sessions": 3,
                "limited_live_higher_cap_stale_after_days": 2,
                "limited_live_higher_cap_target_max_notional": 1000.0,
                "limited_live_operator_checklist_required": True,
                "limited_live_rollout_enabled": True,
                "limited_live_cap_expansion_enabled": True,
                "limited_live_next_tier_cap_enabled": True,
                "limited_live_rollout_max_notional": 100.0,
                "limited_live_cap_expansion_max_notional": 250.0,
                "limited_live_next_tier_cap_max_notional": 500.0,
            }
        )
        state["runtime"].update(
            {
                "state_control_state": "healthy",
                "limited_live_rollout_gate_allowance": {
                    "status": "active",
                    "active": True,
                    "session_day": "2026-04-24",
                    "expires_at": "2026-04-24T22:20:00+00:00",
                },
                "limited_live_cap_expansion_gate_allowance": {
                    "status": "active",
                    "active": True,
                    "session_day": "2026-04-24",
                    "expires_at": "2026-04-24T22:20:00+00:00",
                },
                "limited_live_next_tier_cap_gate_allowance": {
                    "status": "active",
                    "active": True,
                    "session_day": "2026-04-24",
                    "expires_at": "2026-04-24T22:20:00+00:00",
                },
            }
        )
        return state

    def _live_state(self, *, kill_switch: bool = False) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "enabled": False,
                "armed": False,
                "kill_switch": kill_switch,
                "execution_intent": "broker_live",
            }
        )
        return state

    def _gate_report(self, day: str) -> dict:
        return {
            "status": "active",
            "session_day": day,
            "evaluated_at": f"{day}T21:00:00+00:00",
            "consumed_order_count": 1,
            "next_tier_cap_active": True,
            "next_max_notional": 500.0,
            "blockers": [],
            "warnings": [],
            "note_id": f"gate-{day}",
            "related_note_id": f"gate-{day}",
        }

    def _reconciliation_report(self, day: str) -> dict:
        return {
            "status": "clean",
            "session_day": day,
            "evaluated_at": f"{day}T21:05:00+00:00",
            "checked_at": f"{day}T21:05:00+00:00",
            "reconciliation_status": "clean",
            "broker_gate_status": "open",
            "safety_lock_status": "clear",
            "state_control_status": "healthy",
            "blockers": [],
            "warnings": [],
            "note_id": f"recon-{day}",
            "related_note_id": f"recon-{day}",
        }

    def _closeout_report(self, day: str) -> dict:
        return {
            "status": "clean",
            "session_day": day,
            "evaluated_at": f"{day}T21:10:00+00:00",
            "terminal_state": "complete",
            "reconciliation_status": "clean",
            "pnl_summary": {"sample_count": 1, "realized_pnl": 1.25},
            "slippage_summary": {"sample_count": 1, "average_abs_bps": 5.0, "worst_abs_bps": 5.0},
            "blockers": [],
            "warnings": [],
            "note_id": f"closeout-{day}",
            "related_note_id": f"closeout-{day}",
        }

    def _seed_three_clean_sessions(self, state: dict) -> list[str]:
        days = ["2026-04-24", "2026-04-23", "2026-04-22"]
        state["runtime"]["limited_live_next_tier_cap_gate_last_report"] = self._gate_report(days[0])
        state["runtime"]["limited_live_next_tier_cap_gate_history"] = [self._gate_report(day) for day in days[1:]]
        state["runtime"]["limited_live_broker_reconciliation_last_report"] = self._reconciliation_report(days[0])
        state["runtime"]["limited_live_broker_reconciliation_history"] = [
            self._reconciliation_report(day) for day in days[1:]
        ]
        state["runtime"]["limited_live_session_closeout_last_report"] = self._closeout_report(days[0])
        state["runtime"]["limited_live_session_closeout_history"] = [self._closeout_report(day) for day in days[1:]]
        return days

    def _seed_notes(self, days: list[str]) -> None:
        for day in days:
            for tag in (
                "limited-live-next-tier-cap-gate",
                "limited-live-broker-reconciliation",
                "limited-live-session-closeout",
            ):
                notes_service.create_note(
                    title=f"{tag} {day}",
                    body="test",
                    tags=[
                        "automation-ai",
                        tag,
                        "limited-live-rollout",
                        "profile-personal_paper",
                        f"session-{day}",
                    ],
                    owner="automation-ai",
                    note_type="risk_review",
                )

    def test_action_schema_accepts_ladder_actions(self) -> None:
        for action in (
            "run_limited_live_next_tier_cap_canary_review",
            "run_limited_live_broker_reconciliation",
            "run_limited_live_session_closeout",
            "run_limited_live_higher_cap_report",
            "submit_limited_live_operator_checklist",
        ):
            self.assertEqual(OrganizationTradeAutomationActionRequest(action=action).action, action)

    def test_reconciliation_blocks_uncontrolled_live_order_and_writes_note(self) -> None:
        db, tenant = self._db()
        state = self._paper_state()
        pending = pd.DataFrame(
            [
                {
                    "ticker": "SPY",
                    "automation_profile_key": "personal_live",
                    "automation_execution_intent": "broker_live",
                    "order_type": "limit",
                    "notional": 50.0,
                    "created_at": "2026-04-24T20:30:00+00:00",
                }
            ]
        )
        notes_patch, notes_path = self._patch_notes()
        with (
            notes_patch,
            patch.object(automation_limited_live_safety_ladder_service.sdm, "read_pending_orders", return_value=pending),
            patch.object(automation_limited_live_safety_ladder_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(automation_limited_live_safety_ladder_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
        ):
            report = automation_limited_live_safety_ladder_service.run_limited_live_broker_reconciliation(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=self._live_state(),
                now=FIXED_NOW,
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "blocked")
        self.assertIn("uncontrolled_live_exposure", {item["key"] for item in report["blockers"]})
        self.assertEqual(len([note for note in notes if "limited-live-broker-reconciliation" in note.get("tags", [])]), 1)
        self.assertIn(
            "trade_automation.limited_live_broker_reconciled",
            [row.event_type for row in db.query(AuditEvent).all()],
        )

    def test_session_closeout_hard_fault_disables_runtime_allowances(self) -> None:
        db, tenant = self._db()
        state = self._paper_state()
        state["runtime"]["limited_live_broker_reconciliation_last_report"] = {
            "status": "blocked",
            "session_day": "2026-04-24",
            "blockers": [{"key": "broker_mismatch"}],
        }
        notes_patch, _notes_path = self._patch_notes()
        with (
            notes_patch,
            patch.object(automation_limited_live_safety_ladder_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(automation_limited_live_safety_ladder_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(automation_limited_live_safety_ladder_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
        ):
            report = automation_limited_live_safety_ladder_service.run_limited_live_session_closeout(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=self._live_state(),
                now=FIXED_NOW,
            )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(state["runtime"]["limited_live_rollout_gate_allowance"]["status"], "disabled")
        self.assertEqual(state["runtime"]["limited_live_cap_expansion_gate_allowance"]["status"], "disabled")
        self.assertEqual(state["runtime"]["limited_live_next_tier_cap_gate_allowance"]["status"], "disabled")

    def test_next_tier_canary_ready_with_three_clean_sessions_and_notes(self) -> None:
        db, tenant = self._db()
        state = self._paper_state()
        days = self._seed_three_clean_sessions(state)
        notes_patch, _notes_path = self._patch_notes()
        with (
            notes_patch,
            patch.object(automation_limited_live_safety_ladder_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(automation_limited_live_safety_ladder_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(automation_limited_live_safety_ladder_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
        ):
            self._seed_notes(days)
            report = automation_limited_live_safety_ladder_service.run_limited_live_next_tier_cap_canary_review(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=self._live_state(),
                now=FIXED_NOW,
            )

        self.assertEqual(report["status"], "ready_for_operator_review")
        self.assertEqual(report["clean_session_count"], 3)
        self.assertFalse(report["blockers"])

    def test_higher_cap_report_requires_operator_checklist_then_passes(self) -> None:
        db, tenant = self._db()
        state = self._paper_state()
        days = self._seed_three_clean_sessions(state)
        notes_patch, _notes_path = self._patch_notes()
        with (
            notes_patch,
            patch.object(automation_limited_live_safety_ladder_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(automation_limited_live_safety_ladder_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(automation_limited_live_safety_ladder_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
        ):
            self._seed_notes(days)
            canary = automation_limited_live_safety_ladder_service.run_limited_live_next_tier_cap_canary_review(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=self._live_state(),
                now=FIXED_NOW,
            )
            blocked = automation_limited_live_safety_ladder_service.run_limited_live_higher_cap_report(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=self._live_state(),
                now=FIXED_NOW,
            )
            checklist = automation_limited_live_safety_ladder_service.submit_limited_live_operator_checklist(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=self._live_state(),
                now=FIXED_NOW,
            )
            ready = automation_limited_live_safety_ladder_service.run_limited_live_higher_cap_report(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=self._live_state(),
                now=FIXED_NOW,
            )

        self.assertEqual(canary["status"], "ready_for_operator_review")
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("operator_checklist_missing", {item["key"] for item in blocked["blockers"]})
        self.assertEqual(checklist["status"], "submitted")
        self.assertEqual(ready["status"], "ready_to_request_higher_cap")
        self.assertEqual(ready["recommended_next_max_notional"], 1000.0)


if __name__ == "__main__":
    unittest.main()

