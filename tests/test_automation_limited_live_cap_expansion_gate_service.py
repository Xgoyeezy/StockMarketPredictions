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
    automation_limited_live_cap_expansion_gate_service,
    notes_service,
    trade_automation_service,
)


FIXED_NOW = datetime(2026, 4, 24, 21, 20, tzinfo=timezone.utc)


class AutomationLimitedLiveCapExpansionGateServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="cap-expansion-gate-test", name="Cap Expansion Gate Test", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        return db, tenant

    def _paper_state(self, *, enabled: bool = True, report_status: str = "ready_to_request_cap_expansion") -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "enabled": False,
                "armed": False,
                "kill_switch": False,
                "execution_intent": "broker_paper",
                "limited_live_rollout_enabled": True,
                "limited_live_rollout_max_notional": 100.0,
                "limited_live_rollout_max_session_orders": 1,
                "limited_live_cap_expansion_enabled": enabled,
                "limited_live_cap_expansion_max_notional": 250.0,
                "limited_live_cap_expansion_duration_minutes": 60,
                "limited_live_cap_expansion_approval_ttl_minutes": 10,
                "limited_live_cap_expansion_max_session_orders": 1,
                "limited_live_cap_expansion_require_limit": True,
                "limited_live_cap_expansion_auto_expand_enabled": False,
            }
        )
        state["runtime"].update(
            {
                "limited_live_cap_expansion_report_last_report": {
                    "status": report_status,
                    "evaluated_at": "2026-04-24T21:00:00+00:00",
                    "stale_after_days": 2,
                    "current_max_notional": 100.0,
                    "recommended_next_max_notional": 250.0,
                    "broker_live_gate_status": "open",
                    "safety_lock_status": "clear",
                    "state_control_status": "healthy",
                    "blockers": [],
                    "warnings": [],
                    "related_note_id": "cap-report-note",
                },
                "limited_live_rollout_canary_last_report": {
                    "status": "ready_for_operator_review",
                    "blockers": [],
                    "warnings": [],
                    "related_note_id": "rollout-canary-note",
                },
                "limited_live_rollout_gate_last_report": {
                    "status": "active",
                    "blockers": [],
                    "warnings": [],
                    "rollout_active": True,
                    "related_note_id": "rollout-note",
                },
                "limited_live_rollout_gate_allowance": {
                    "rollout_id": "rollout-1",
                    "status": "active",
                    "active": True,
                    "session_day": "2026-04-24",
                    "expires_at": "2026-04-24T22:20:00+00:00",
                    "max_notional": 100.0,
                    "max_session_orders": 1,
                    "require_limit": True,
                    "caps": {"max_notional": 100.0, "max_session_orders": 1, "require_limit": True},
                    "consumed_order_count": 0,
                    "consumed_orders": [],
                },
            }
        )
        return state

    def _live_state(self, *, kill_switch: bool = False) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "enabled": True,
                "armed": True,
                "kill_switch": kill_switch,
                "execution_intent": "broker_live",
            }
        )
        return state

    def _rollout(self) -> dict:
        return {"allows_live_rollout": True, "status": "open", "basis": "test open"}

    def _patch_io(self):
        notes_dir = tempfile.TemporaryDirectory()
        self.addCleanup(notes_dir.cleanup)
        notes_path = Path(notes_dir.name) / "notes.json"
        notes_path.write_text("[]", encoding="utf-8")
        return (
            patch.object(notes_service, "NOTES_PATH", notes_path),
            patch.object(
                automation_limited_live_cap_expansion_gate_service,
                "settings",
                SimpleNamespace(
                    alpaca_live_trading_enabled=True,
                    alpaca_live_api_key_id="live-key",
                    alpaca_live_api_secret_key="live-secret",
                    alpaca_api_key_id="",
                    alpaca_api_secret_key="",
                ),
            ),
            patch.object(automation_limited_live_cap_expansion_gate_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(automation_limited_live_cap_expansion_gate_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            notes_path,
        )

    def test_action_schema_accepts_limited_live_cap_expansion_actions(self) -> None:
        self.assertEqual(
            OrganizationTradeAutomationActionRequest(action="prepare_limited_live_cap_expansion").action,
            "prepare_limited_live_cap_expansion",
        )
        self.assertEqual(
            OrganizationTradeAutomationActionRequest(action="activate_limited_live_cap_expansion").action,
            "activate_limited_live_cap_expansion",
        )
        self.assertEqual(
            OrganizationTradeAutomationActionRequest(action="rollback_limited_live_cap_expansion").action,
            "rollback_limited_live_cap_expansion",
        )

    def test_disabled_setting_blocks_prepare_without_allowance(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state(enabled=False)
        patches = self._patch_io()
        *context_patches, notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2], context_patches[3]:
            report = automation_limited_live_cap_expansion_gate_service.prepare_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "blocked")
        self.assertIn("limited_live_cap_expansion_disabled", {item["key"] for item in report["blockers"]})
        self.assertFalse(paper_state["runtime"].get("limited_live_cap_expansion_gate_allowance"))
        self.assertEqual(len([note for note in notes if "limited-live-cap-expansion-gate" in note.get("tags", [])]), 1)

    def test_clean_prepare_writes_short_lived_approval_note_and_audit(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        before_settings = json.loads(json.dumps(paper_state["settings"], sort_keys=True))
        patches = self._patch_io()
        *context_patches, notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2], context_patches[3]:
            report = automation_limited_live_cap_expansion_gate_service.prepare_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "approved")
        self.assertEqual(report["approval_state"], "approved")
        self.assertEqual(float(report["expanded_max_notional"]), 250.0)
        self.assertEqual(paper_state["settings"], before_settings)
        self.assertEqual(paper_state["runtime"]["limited_live_cap_expansion_gate_approval"]["status"], "approved")
        self.assertFalse(paper_state["runtime"].get("limited_live_cap_expansion_gate_allowance"))
        self.assertIn("Prepare only creates short-lived approval", notes[0]["body"])
        self.assertIn(
            "trade_automation.limited_live_cap_expansion_prepared",
            [row.event_type for row in db.query(AuditEvent).all()],
        )

    def test_activate_requires_fresh_approval_and_creates_runtime_only_allowance(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        patches = self._patch_io()
        *context_patches, _notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2], context_patches[3]:
            blocked = automation_limited_live_cap_expansion_gate_service.activate_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )
            automation_limited_live_cap_expansion_gate_service.prepare_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )
            report = automation_limited_live_cap_expansion_gate_service.activate_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                rollout_readiness=self._rollout(),
                now=FIXED_NOW + timedelta(minutes=1),
            )

        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("approval_missing", {item["key"] for item in blocked["blockers"]})
        self.assertEqual(report["status"], "active")
        allowance = paper_state["runtime"]["limited_live_cap_expansion_gate_allowance"]
        self.assertEqual(allowance["status"], "active")
        self.assertEqual(allowance["max_session_orders"], 1)
        self.assertLessEqual(float(allowance["max_notional"]), 250.0)
        self.assertEqual(paper_state["settings"]["enabled"], False)
        self.assertIn(
            "trade_automation.limited_live_cap_expansion_activated",
            [row.event_type for row in db.query(AuditEvent).all()],
        )

    def test_entry_gate_preserves_base_cap_until_expansion_is_active(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        base_gate = {
            "allowed": True,
            "rollout_id": "rollout-1",
            "max_notional": 100.0,
            "remaining_order_count": 1,
            "max_session_orders": 1,
        }
        patches = self._patch_io()
        *context_patches, _notes_path = patches

        before_activation = automation_limited_live_cap_expansion_gate_service.evaluate_limited_live_cap_expansion_entry_gate(
            paper_state,
            base_gate_result=base_gate,
            now=FIXED_NOW + timedelta(minutes=1),
            order_type="limit",
            estimated_notional=150.0,
        )

        with context_patches[0], context_patches[1], context_patches[2], context_patches[3]:
            automation_limited_live_cap_expansion_gate_service.prepare_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )
            automation_limited_live_cap_expansion_gate_service.activate_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                rollout_readiness=self._rollout(),
                now=FIXED_NOW + timedelta(minutes=1),
            )

        expanded = automation_limited_live_cap_expansion_gate_service.evaluate_limited_live_cap_expansion_entry_gate(
            paper_state,
            base_gate_result=base_gate,
            now=FIXED_NOW + timedelta(minutes=2),
            order_type="limit",
            estimated_notional=150.0,
        )
        too_large = automation_limited_live_cap_expansion_gate_service.evaluate_limited_live_cap_expansion_entry_gate(
            paper_state,
            base_gate_result=base_gate,
            now=FIXED_NOW + timedelta(minutes=2),
            order_type="limit",
            estimated_notional=251.0,
        )
        market = automation_limited_live_cap_expansion_gate_service.evaluate_limited_live_cap_expansion_entry_gate(
            paper_state,
            base_gate_result=base_gate,
            now=FIXED_NOW + timedelta(minutes=2),
            order_type="market",
            estimated_notional=150.0,
        )

        self.assertEqual(before_activation["reason"], "limited_live_cap_expansion_required")
        self.assertTrue(expanded["allowed"])
        self.assertEqual(float(expanded["max_notional"]), 250.0)
        self.assertEqual(too_large["reason"], "limited_live_cap_expansion_notional_cap")
        self.assertEqual(market["reason"], "limited_live_cap_expansion_limit_required")

    def test_recording_order_consumption_exhausts_expansion_gate_and_updates_note(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        base_gate = {"allowed": True, "rollout_id": "rollout-1", "max_notional": 100.0}
        patches = self._patch_io()
        *context_patches, notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2], context_patches[3]:
            automation_limited_live_cap_expansion_gate_service.prepare_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )
            automation_limited_live_cap_expansion_gate_service.activate_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                rollout_readiness=self._rollout(),
                now=FIXED_NOW + timedelta(minutes=1),
            )
            automation_limited_live_cap_expansion_gate_service.record_limited_live_cap_expansion_order_use(
                db,
                tenant=tenant,
                state=paper_state,
                now=FIXED_NOW + timedelta(minutes=2),
                order_evidence={"ticker": "SPY", "order_id": "local-1", "broker_order_id": "broker-1"},
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        exhausted = automation_limited_live_cap_expansion_gate_service.evaluate_limited_live_cap_expansion_entry_gate(
            paper_state,
            base_gate_result=base_gate,
            now=FIXED_NOW + timedelta(minutes=3),
            order_type="limit",
        )
        self.assertEqual(exhausted["reason"], "limited_live_cap_expansion_order_cap_exhausted")
        self.assertEqual(paper_state["runtime"]["limited_live_cap_expansion_gate_allowance"]["consumed_order_count"], 1)
        self.assertIn("Consumed orders: 1", notes[0]["body"])

    def test_manual_rollback_disables_runtime_allowance(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        patches = self._patch_io()
        *context_patches, _notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2], context_patches[3]:
            automation_limited_live_cap_expansion_gate_service.prepare_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )
            automation_limited_live_cap_expansion_gate_service.activate_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=self._live_state(),
                rollout_readiness=self._rollout(),
                now=FIXED_NOW + timedelta(minutes=1),
            )
            report = automation_limited_live_cap_expansion_gate_service.rollback_limited_live_cap_expansion(
                db,
                tenant=tenant,
                paper_state=paper_state,
                now=FIXED_NOW + timedelta(minutes=2),
            )

        self.assertEqual(report["status"], "rolled_back")
        self.assertFalse(paper_state["runtime"]["limited_live_cap_expansion_gate_allowance"]["active"])

    def test_action_path_persists_metadata_without_order_path_or_settings_mutation(self) -> None:
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
            auth_subject="cap-expansion-gate",
            user_id="cap-expansion-gate",
            permissions=("tenant.manage_support",),
        )
        patches = self._patch_io()
        *context_patches, notes_path = patches

        with (
            context_patches[0],
            context_patches[1],
            context_patches[2],
            context_patches[3],
            patch.object(trade_automation_service, "_resolve_tenant_for_current_user", return_value=tenant),
            patch.object(trade_automation_service, "_resolve_user_for_current_user", return_value=None),
            patch.object(trade_automation_service, "get_trade_summary", return_value={"rollout_readiness": self._rollout()}),
            patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service, "_build_personal_account_summary", return_value={"status": "unavailable"}),
            patch.object(trade_automation_service, "get_latest_trade_automation_equity_snapshot", return_value=None),
            patch.object(trade_automation_service, "open_trade_from_request") as open_trade_mock,
            patch.object(trade_automation_service, "sync_pending_orders_from_broker") as sync_orders_mock,
            patch.object(automation_limited_live_cap_expansion_gate_service, "_utc_now", return_value=FIXED_NOW),
        ):
            prepared = trade_automation_service.run_tenant_trade_automation_action(
                db,
                current_user=current_user,
                request=OrganizationTradeAutomationActionRequest(action="prepare_limited_live_cap_expansion"),
            )
            activated = trade_automation_service.run_tenant_trade_automation_action(
                db,
                current_user=current_user,
                request=OrganizationTradeAutomationActionRequest(action="activate_limited_live_cap_expansion"),
            )
            rolled_back = trade_automation_service.run_tenant_trade_automation_action(
                db,
                current_user=current_user,
                request=OrganizationTradeAutomationActionRequest(action="rollback_limited_live_cap_expansion"),
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        open_trade_mock.assert_not_called()
        sync_orders_mock.assert_not_called()
        paper_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        live_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_live")
        self.assertEqual(paper_after["settings"], before_paper_settings)
        self.assertEqual(live_after["settings"], before_live_settings)
        self.assertEqual(prepared["limited_live_cap_expansion_gate"]["status"], "approved")
        self.assertEqual(activated["limited_live_cap_expansion_gate"]["status"], "active")
        self.assertEqual(rolled_back["limited_live_cap_expansion_gate"]["status"], "rolled_back")
        self.assertEqual(len([note for note in notes if "limited-live-cap-expansion-gate" in note.get("tags", [])]), 1)


if __name__ == "__main__":
    unittest.main()
