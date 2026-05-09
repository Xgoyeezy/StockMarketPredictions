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
from backend.models.saas import AuditEvent, Tenant
from backend.schemas import OrganizationTradeAutomationActionRequest
from backend.services import (
    automation_limited_live_rollout_canary_service,
    notes_service,
    trade_automation_service,
)


FIXED_NOW = datetime(2026, 4, 24, 21, 20, tzinfo=timezone.utc)


class AutomationLimitedLiveRolloutCanaryServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="limited-live-canary-test", name="Limited Live Canary Test", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        return db, tenant

    def _session_days(self) -> list[str]:
        return ["2026-04-24", "2026-04-23", "2026-04-22", "2026-04-21", "2026-04-20"]

    def _promotion(self, *, status: str = "ready_to_request_limited_live_rollout") -> dict:
        return {
            "status": status,
            "evaluated_at": "2026-04-24T20:45:00+00:00",
            "broker_live_gate_status": "open",
            "safety_lock_status": "clear",
            "blockers": [],
            "warnings": [],
            "related_note_id": "promotion-note",
        }

    def _rollout_report(
        self,
        session_day: str,
        *,
        order_type: str = "limit",
        notional: float = 10.0,
        note_id: str | None = "rollout-note",
        slippage_bps: float | None = None,
    ) -> dict:
        order = {
            "ticker": "SPY",
            "order_id": f"local-{session_day}",
            "broker_order_id": f"broker-{session_day}",
            "broker_status": "filled",
            "order_type": order_type,
            "limit_price": 400.0,
            "notional": notional,
            "slippage_bps": slippage_bps,
            "route_family": "limited_live_rollout",
        }
        return {
            "status": "active",
            "session_day": session_day,
            "evaluated_at": f"{session_day}T21:00:00+00:00",
            "rollout_id": f"rollout-{session_day}",
            "rollout_active": True,
            "caps": {
                "max_notional": 100.0,
                "max_session_orders": 1,
                "duration_minutes": 60,
                "require_limit": True,
            },
            "consumed_order_count": 1,
            "candidate_order_evidence": {
                "candidate": {"ticker": "SPY", "portfolio_rank": 1},
                "orders": [order],
                "reconciliation_status": "clean",
            },
            "selected_candidate": {"ticker": "SPY", "portfolio_rank": 1},
            "blockers": [],
            "warnings": [],
            "note_id": note_id,
            "related_note_id": note_id,
        }

    def _paper_state(self, reports: list[dict] | None = None, *, promotion_status: str = "ready_to_request_limited_live_rollout") -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "enabled": False,
                "armed": False,
                "kill_switch": False,
                "execution_intent": "broker_paper",
                "limited_live_rollout_canary_enabled": True,
                "limited_live_rollout_canary_auto_review_enabled": True,
                "limited_live_rollout_canary_window_sessions": 5,
                "limited_live_rollout_canary_required_clean_sessions": 3,
                "limited_live_rollout_canary_stale_after_days": 2,
            }
        )
        reports = list(reports or [self._rollout_report(day) for day in self._session_days()[:3]])
        state["runtime"].update(
            {
                "live_pilot_promotion_report_last_report": self._promotion(status=promotion_status),
                "limited_live_rollout_gate_last_report": reports[0] if reports else {},
                "limited_live_rollout_gate_history": reports[1:],
                "limited_live_rollout_gate_approval": {"status": "consumed", "approval_id": "approval-1"},
                "limited_live_rollout_gate_allowance": {
                    "status": "active",
                    "rollout_id": "rollout-current",
                    "active": True,
                    "session_day": "2026-04-24",
                    "consumed_order_count": 1,
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

    def _patch_io(self):
        notes_dir = tempfile.TemporaryDirectory()
        self.addCleanup(notes_dir.cleanup)
        notes_path = Path(notes_dir.name) / "notes.json"
        notes_path.write_text("[]", encoding="utf-8")
        return (
            patch.object(notes_service, "NOTES_PATH", notes_path),
            patch.object(automation_limited_live_rollout_canary_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(automation_limited_live_rollout_canary_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            notes_path,
        )

    def test_action_schema_accepts_limited_live_rollout_canary(self) -> None:
        self.assertEqual(
            OrganizationTradeAutomationActionRequest(action="run_limited_live_rollout_canary_review").action,
            "run_limited_live_rollout_canary_review",
        )

    def test_ready_three_of_five_sessions_records_note_and_audit(self) -> None:
        db, tenant = self._db()
        state = self._paper_state()
        patches = self._patch_io()
        *context_patches, notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2]:
            report = automation_limited_live_rollout_canary_service.run_limited_live_rollout_canary_review(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=self._live_state(),
                now=FIXED_NOW,
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "ready_for_operator_review")
        self.assertEqual(report["clean_session_count"], 3)
        self.assertEqual(report["consumed_order_count"], 3)
        self.assertFalse(report["baseline_settings_mutated"])
        self.assertFalse(report["allowance_mutated"])
        self.assertEqual(len([note for note in notes if "limited-live-rollout-canary" in note.get("tags", [])]), 1)
        self.assertIn(
            "trade_automation.limited_live_rollout_canary_reviewed",
            [row.event_type for row in db.query(AuditEvent).all()],
        )

    def test_cap_non_limit_missing_note_and_lock_blockers(self) -> None:
        db, tenant = self._db()
        bad_reports = [
            self._rollout_report("2026-04-24", notional=150.0),
            self._rollout_report("2026-04-23", order_type="market"),
            self._rollout_report("2026-04-22", note_id=None),
        ]
        state = self._paper_state(bad_reports)
        patches = self._patch_io()
        *context_patches, _notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2]:
            report = automation_limited_live_rollout_canary_service.run_limited_live_rollout_canary_review(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=self._live_state(kill_switch=True),
                now=FIXED_NOW,
            )

        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertEqual(report["status"], "blocked")
        self.assertIn("notional_cap_breached", blocker_keys)
        self.assertIn("non_limit_order_evidence", blocker_keys)
        self.assertIn("limited_live_note_missing", blocker_keys)
        self.assertIn("kill_switch_active", blocker_keys)

    def test_warning_only_evidence_does_not_report_ready(self) -> None:
        db, tenant = self._db()
        reports = [self._rollout_report(day, slippage_bps=75.0) for day in self._session_days()[:3]]
        state = self._paper_state(reports)
        patches = self._patch_io()
        *context_patches, _notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2]:
            report = automation_limited_live_rollout_canary_service.run_limited_live_rollout_canary_review(
                db,
                tenant=tenant,
                paper_state=state,
                live_state=self._live_state(),
                now=FIXED_NOW,
            )

        self.assertEqual(report["status"], "warning")
        self.assertFalse(report["blockers"])
        self.assertTrue(any(item["key"] == "slippage_warning" for item in report["warnings"]))

    def test_action_path_persists_metadata_without_order_or_allowance_mutation(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state()
        before_settings = json.loads(json.dumps(paper_state["settings"], sort_keys=True))
        before_allowance = json.loads(json.dumps(paper_state["runtime"]["limited_live_rollout_gate_allowance"], sort_keys=True))
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        trade_automation_service._write_trade_automation_state(tenant, live_state, profile_key="personal_live")
        db.commit()
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="limited-live-canary",
            user_id="limited-live-canary",
            permissions=("tenant.manage_support",),
        )
        patches = self._patch_io()
        *context_patches, notes_path = patches

        with (
            context_patches[0],
            context_patches[1],
            context_patches[2],
            patch.object(trade_automation_service, "_resolve_tenant_for_current_user", return_value=tenant),
            patch.object(trade_automation_service, "_resolve_user_for_current_user", return_value=None),
            patch.object(trade_automation_service, "get_trade_summary", return_value={"rollout_readiness": {"allows_live_rollout": True}}),
            patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service, "_build_personal_account_summary", return_value={"status": "unavailable"}),
            patch.object(trade_automation_service, "get_latest_trade_automation_equity_snapshot", return_value=None),
            patch.object(automation_limited_live_rollout_canary_service, "_utc_now", return_value=FIXED_NOW),
            patch.object(trade_automation_service, "open_trade_from_request") as open_trade_mock,
            patch.object(trade_automation_service, "sync_pending_orders_from_broker") as sync_orders_mock,
        ):
            snapshot = trade_automation_service.run_tenant_trade_automation_action(
                db,
                current_user=current_user,
                request=OrganizationTradeAutomationActionRequest(action="run_limited_live_rollout_canary_review"),
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        open_trade_mock.assert_not_called()
        sync_orders_mock.assert_not_called()
        paper_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertEqual(paper_after["settings"], before_settings)
        self.assertEqual(paper_after["runtime"]["limited_live_rollout_gate_allowance"], before_allowance)
        self.assertEqual(snapshot["limited_live_rollout_canary"]["status"], "ready_for_operator_review")
        self.assertEqual(len([note for note in notes if "limited-live-rollout-canary" in note.get("tags", [])]), 1)

    def test_scheduled_review_once_disabled_and_already_reviewed(self) -> None:
        db, tenant = self._db()
        state = self._paper_state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        trade_automation_service._write_trade_automation_state(tenant, self._live_state(), profile_key="personal_live")
        db.commit()
        patches = self._patch_io()
        *context_patches, _notes_path = patches

        with (
            context_patches[0],
            context_patches[1],
            context_patches[2],
            patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
        ):
            first = trade_automation_service.run_trade_automation_limited_live_rollout_canary_reviews(
                db,
                now=FIXED_NOW,
            )
            second = trade_automation_service.run_trade_automation_limited_live_rollout_canary_reviews(
                db,
                now=FIXED_NOW + timedelta(minutes=1),
            )

        self.assertEqual(first["reviewed"], 1)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(second["items"][0]["reason"], "already_reviewed_for_session")

    def test_scheduled_review_disabled_skip(self) -> None:
        db, tenant = self._db()
        state = self._paper_state()
        state["settings"]["limited_live_rollout_canary_auto_review_enabled"] = False
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()

        result = trade_automation_service.run_trade_automation_limited_live_rollout_canary_reviews(
            db,
            now=FIXED_NOW,
        )

        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["items"][0]["reason"], "limited_live_rollout_canary_auto_review_disabled")


if __name__ == "__main__":
    unittest.main()
