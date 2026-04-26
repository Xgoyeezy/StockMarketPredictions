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
from backend.services import (
    automation_limited_live_cap_expansion_report_service,
    notes_service,
    trade_automation_service,
)


FIXED_NOW = datetime(2026, 4, 24, 21, 20, tzinfo=timezone.utc)
BEFORE_CLOSE_NOW = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)


class AutomationLimitedLiveCapExpansionReportServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="cap-expansion-test", name="Cap Expansion Test", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        return db, tenant

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

    def _canary(
        self,
        *,
        status: str = "ready_for_operator_review",
        clean: int = 3,
        related_note_id: str | None = "canary-note",
        pnl: float = 1.25,
        worst_slippage: float = 12.0,
    ) -> dict:
        return {
            "status": status,
            "evaluated_at": "2026-04-24T21:00:00+00:00",
            "clean_session_count": clean,
            "required_clean_sessions": 3,
            "window_session_count": 3,
            "latest_rollout_status": "active",
            "latest_terminal_state": "filled",
            "latest_reconciliation_status": "clean",
            "consumed_order_count": 3,
            "broker_live_gate_status": "open",
            "broker_gate_status": "open",
            "safety_lock_status": "clear",
            "promotion_status": "ready_to_request_limited_live_rollout",
            "pnl_summary": {"sample_count": 3, "realized_pnl": pnl},
            "slippage_summary": {
                "sample_count": 3,
                "average_abs_bps": min(worst_slippage, 25.0),
                "worst_abs_bps": worst_slippage,
            },
            "note_coverage": {"covered": 3 if related_note_id else 2, "required": 3, "ratio": 1.0 if related_note_id else 0.67},
            "blockers": [],
            "warnings": [],
            "related_note_id": related_note_id,
            "note_id": related_note_id,
        }

    def _gate(self) -> dict:
        return {
            "status": "active",
            "evaluated_at": "2026-04-24T21:05:00+00:00",
            "caps": {"max_notional": 100.0, "max_session_orders": 1, "require_limit": True},
            "consumed_order_count": 1,
            "blockers": [],
            "warnings": [],
            "related_note_id": "rollout-note",
        }

    def _paper_state(self) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "enabled": False,
                "armed": False,
                "kill_switch": False,
                "execution_intent": "broker_paper",
                "limited_live_rollout_max_notional": 100.0,
                "limited_live_cap_expansion_report_enabled": True,
                "limited_live_cap_expansion_report_auto_review_enabled": True,
                "limited_live_cap_expansion_required_clean_sessions": 3,
                "limited_live_cap_expansion_stale_after_days": 2,
                "limited_live_cap_expansion_target_max_notional": 250.0,
            }
        )
        state["runtime"].update(
            {
                "live_pilot_promotion_report_last_report": self._promotion(),
                "limited_live_rollout_canary_last_report": self._canary(),
                "limited_live_rollout_gate_last_report": self._gate(),
                "limited_live_rollout_gate_approval": {"status": "consumed", "approval_id": "approval-1"},
                "limited_live_rollout_gate_allowance": {
                    "status": "inactive",
                    "active": False,
                    "rollout_id": "rollout-current",
                    "session_day": "2026-04-24",
                    "consumed_order_count": 1,
                },
                "state_control_state": "healthy",
                "state_control_last_evaluation": {"state": "healthy", "score": 95.0},
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
            patch.object(
                automation_limited_live_cap_expansion_report_service.sdm,
                "read_pending_orders",
                return_value=pd.DataFrame(),
            ),
            patch.object(
                automation_limited_live_cap_expansion_report_service.sdm,
                "read_open_trades",
                return_value=pd.DataFrame(),
            ),
            notes_path,
        )

    def test_action_schema_accepts_cap_expansion_report(self) -> None:
        self.assertEqual(
            OrganizationTradeAutomationActionRequest(action="run_limited_live_cap_expansion_report").action,
            "run_limited_live_cap_expansion_report",
        )

    def test_ready_evidence_records_note_and_audit_without_mutation(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        before_settings = json.loads(json.dumps(paper_state["settings"], sort_keys=True))
        before_allowance = json.loads(json.dumps(paper_state["runtime"]["limited_live_rollout_gate_allowance"], sort_keys=True))
        patches = self._patch_io()
        *context_patches, notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2]:
            report = automation_limited_live_cap_expansion_report_service.run_limited_live_cap_expansion_report(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                now=FIXED_NOW,
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "ready_to_request_cap_expansion")
        self.assertEqual(report["recommended_next_max_notional"], 250.0)
        self.assertFalse(report["baseline_settings_mutated"])
        self.assertFalse(report["allowance_mutated"])
        self.assertEqual(paper_state["settings"], before_settings)
        self.assertEqual(paper_state["runtime"]["limited_live_rollout_gate_allowance"], before_allowance)
        self.assertEqual(len([note for note in notes if "limited-live-cap-expansion" in note.get("tags", [])]), 1)
        self.assertIn(
            "trade_automation.limited_live_cap_expansion_reviewed",
            [row.event_type for row in db.query(AuditEvent).all()],
        )

    def test_blockers_cover_canary_promotion_locks_state_control_and_risk(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        paper_state["runtime"]["limited_live_rollout_canary_last_report"] = self._canary(
            status="blocked",
            clean=2,
            related_note_id=None,
            pnl=-1.0,
            worst_slippage=125.0,
        )
        paper_state["runtime"]["limited_live_rollout_canary_last_report"]["blockers"] = [
            {"key": "notional_cap_breached", "detail": "cap breach"}
        ]
        paper_state["runtime"]["live_pilot_promotion_report_last_report"].update(
            {"status": "blocked", "blockers": [{"key": "promotion"}], "broker_live_gate_status": "locked"}
        )
        paper_state["runtime"]["state_control_state"] = "halt"
        patches = self._patch_io()
        *context_patches, _notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2]:
            report = automation_limited_live_cap_expansion_report_service.run_limited_live_cap_expansion_report(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(kill_switch=True),
                now=FIXED_NOW,
            )

        blocker_keys = {item["key"] for item in report["blockers"]}
        self.assertEqual(report["status"], "blocked")
        self.assertIn("limited_live_rollout_canary_not_ready", blocker_keys)
        self.assertIn("limited_live_clean_sessions_low", blocker_keys)
        self.assertIn("limited_live_rollout_canary_note_missing", blocker_keys)
        self.assertIn("promotion_report_not_ready", blocker_keys)
        self.assertIn("broker_live_gate_not_open", blocker_keys)
        self.assertIn("kill_switch_active", blocker_keys)
        self.assertIn("state_control_halt", blocker_keys)
        self.assertIn("slippage_blocked", blocker_keys)
        self.assertIn("negative_limited_live_pnl", blocker_keys)
        self.assertIn("notional_cap_breached", blocker_keys)

    def test_warning_only_requires_operator_review(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        canary = self._canary(status="warning", worst_slippage=75.0)
        canary["warnings"] = [{"key": "slippage_warning", "detail": "watch slippage"}]
        paper_state["runtime"]["limited_live_rollout_canary_last_report"] = canary
        patches = self._patch_io()
        *context_patches, _notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2]:
            report = automation_limited_live_cap_expansion_report_service.run_limited_live_cap_expansion_report(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                now=FIXED_NOW,
            )

        self.assertEqual(report["status"], "needs_operator_review")
        self.assertFalse(report["blockers"])
        self.assertIn("limited_live_rollout_canary_warning", {item["key"] for item in report["warnings"]})

    def test_action_path_persists_report_without_order_or_allowance_mutation(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state()
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        trade_automation_service._write_trade_automation_state(tenant, live_state, profile_key="personal_live")
        db.add(
            OrderEventRecord(
                tenant_id=tenant.id,
                trade_id="limited-rollout-trade",
                ticker="SPY",
                event_key="order.submitted",
                status="submitted",
                order_type="limit",
                payload_json={
                    "automation_profile_key": "personal_live",
                    "limited_live_rollout_id": "rollout",
                    "route_family": "limited_live_rollout",
                    "notional": 10.0,
                },
                created_at=datetime(2026, 4, 24, 20, 35, tzinfo=timezone.utc),
            )
        )
        db.commit()
        before_paper_settings = json.loads(json.dumps(paper_state["settings"], sort_keys=True))
        before_live_settings = json.loads(json.dumps(live_state["settings"], sort_keys=True))
        before_allowance = json.loads(json.dumps(paper_state["runtime"]["limited_live_rollout_gate_allowance"], sort_keys=True))
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="cap-expansion",
            user_id="cap-expansion",
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
            patch.object(trade_automation_service, "open_trade_from_request") as open_trade_mock,
            patch.object(trade_automation_service, "sync_pending_orders_from_broker") as sync_orders_mock,
        ):
            snapshot = trade_automation_service.run_tenant_trade_automation_action(
                db,
                current_user=current_user,
                request=OrganizationTradeAutomationActionRequest(action="run_limited_live_cap_expansion_report"),
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        open_trade_mock.assert_not_called()
        sync_orders_mock.assert_not_called()
        paper_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        live_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_live")
        self.assertEqual(paper_after["settings"], before_paper_settings)
        self.assertEqual(live_after["settings"], before_live_settings)
        self.assertEqual(paper_after["runtime"]["limited_live_rollout_gate_allowance"], before_allowance)
        self.assertEqual(snapshot["limited_live_cap_expansion_report"]["status"], "ready_to_request_cap_expansion")
        self.assertEqual(len([note for note in notes if "limited-live-cap-expansion" in note.get("tags", [])]), 1)

    def test_scheduler_runs_once_and_disabled_skip(self) -> None:
        db, tenant = self._db()
        state = self._paper_state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        trade_automation_service._write_trade_automation_state(tenant, self._live_state(), profile_key="personal_live")
        db.commit()
        patches = self._patch_io()
        *context_patches, _notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2]:
            first = trade_automation_service.run_trade_automation_limited_live_cap_expansion_reports(
                db,
                now=FIXED_NOW,
            )
            second = trade_automation_service.run_trade_automation_limited_live_cap_expansion_reports(
                db,
                now=FIXED_NOW + timedelta(minutes=1),
            )

        self.assertEqual(first["reviewed"], 1)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(second["items"][0]["reason"], "already_reviewed_for_session")

        disabled = self._paper_state()
        disabled["settings"]["limited_live_cap_expansion_report_auto_review_enabled"] = False
        trade_automation_service._write_trade_automation_state(tenant, disabled, profile_key="personal_paper")
        db.commit()
        disabled_result = trade_automation_service.run_trade_automation_limited_live_cap_expansion_reports(
            db,
            now=FIXED_NOW + timedelta(days=1),
        )
        self.assertEqual(disabled_result["skipped"], 1)
        self.assertEqual(disabled_result["items"][0]["reason"], "limited_live_cap_expansion_report_auto_review_disabled")

        before_close = trade_automation_service.run_trade_automation_limited_live_cap_expansion_reports(
            db,
            now=BEFORE_CLOSE_NOW,
        )
        self.assertEqual(before_close["reviewed"], 0)
        self.assertEqual(before_close["items"][0]["reason"], "limited_live_cap_expansion_report_auto_review_disabled")


if __name__ == "__main__":
    unittest.main()
