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
from backend.services import automation_daily_objective_service, notes_service, trade_automation_service


FIXED_NOW = datetime(2026, 4, 24, 18, 30, tzinfo=timezone.utc)


class AutomationDailyObjectiveServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="daily-objective-test", name="Daily Objective Test", status="active")
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
                "daily_objective_enabled": True,
                "daily_profit_target_pct": 1.0,
                "daily_profit_target_dollars": 1000.0,
                "daily_loss_budget_pct": 0.5,
                "daily_objective_apply_to_live": False,
                "account_size": 100000.0,
                "max_spread_bps": 25.0,
                "min_average_dollar_volume": 1_000_000.0,
            }
        )
        return state

    def _closed_trades(self, tenant: Tenant, pnl_values: list[float], profile_key: str = "personal_paper") -> pd.DataFrame:
        rows = []
        for index, pnl in enumerate(pnl_values, start=1):
            rows.append(
                {
                    "trade_id": f"trade-{index}",
                    "ticker": "SPY" if index % 2 else "QQQ",
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": profile_key,
                    "realized_pnl": pnl,
                    "closed_at": "2026-04-24T17:00:00+00:00",
                }
            )
        return pd.DataFrame(rows)

    def _patch_notes(self):
        notes_dir = tempfile.TemporaryDirectory()
        self.addCleanup(notes_dir.cleanup)
        notes_path = Path(notes_dir.name) / "notes.json"
        notes_path.write_text("[]", encoding="utf-8")
        return patch.object(notes_service, "NOTES_PATH", notes_path), notes_path

    def test_action_schema_accepts_daily_objective_review(self) -> None:
        request = OrganizationTradeAutomationActionRequest(action="run_daily_objective_review")
        self.assertEqual(request.action, "run_daily_objective_review")

    def test_target_reached_does_not_block_entries(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = automation_daily_objective_service.build_daily_objective_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_open=pd.DataFrame(),
            owned_pending=pd.DataFrame(),
            owned_closed=self._closed_trades(tenant, [1200.0]),
            effective_funds=100000.0,
            now=FIXED_NOW,
        )

        self.assertEqual(report["status"], "target_reached")
        self.assertTrue(report["target_reached"])
        self.assertFalse(report["entries_blocked"])
        self.assertGreaterEqual(report["target_progress_pct"], 100.0)

    def test_loss_budget_blocks_entries_writes_note_and_audit(self) -> None:
        db, tenant = self._db()
        state = self._state()
        notes_patch, notes_path = self._patch_notes()

        with notes_patch:
            report = automation_daily_objective_service.evaluate_daily_objective_entry_gate(
                db,
                tenant=tenant,
                state=state,
                profile_key="personal_paper",
                owned_open=pd.DataFrame(),
                owned_pending=pd.DataFrame(),
                owned_closed=self._closed_trades(tenant, [-525.0]),
                effective_funds=100000.0,
                now=FIXED_NOW,
            )
            db.commit()
            notes = json.loads(notes_path.read_text(encoding="utf-8"))
            audit_count = db.query(AuditEvent).filter(AuditEvent.event_type == "trade_automation.daily_objective_reviewed").count()

        self.assertEqual(report["status"], "loss_budget_locked")
        self.assertTrue(report["entries_blocked"])
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["owner"], "automation-ai")
        self.assertEqual(notes[0]["note_type"], "risk_review")
        self.assertIn("daily-objective", notes[0]["tags"])
        self.assertIn("return-target", notes[0]["tags"])
        self.assertEqual(audit_count, 1)

    def test_manual_review_persists_snapshot_and_leaves_settings_unchanged(self) -> None:
        db, tenant = self._db()
        state = self._state()
        before_settings = dict(state["settings"])
        notes_patch, notes_path = self._patch_notes()

        with (
            notes_patch,
            patch.object(automation_daily_objective_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(automation_daily_objective_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(automation_daily_objective_service.sdm, "read_closed_trades", return_value=self._closed_trades(tenant, [250.0])),
            patch.object(automation_daily_objective_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
        ):
            report = automation_daily_objective_service.run_daily_objective_review(
                db,
                tenant=tenant,
                state=state,
                profile_key="personal_paper",
                now=FIXED_NOW,
            )
            db.commit()
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "tracking")
        self.assertEqual(state["settings"], before_settings)
        self.assertEqual(state["runtime"]["daily_objective_last_report"]["status"], "tracking")
        self.assertEqual(len(notes), 1)
        self.assertIn("skipped", notes[0]["body"].lower())

    def test_candidate_overlay_favors_high_edge_low_spread_liquidity(self) -> None:
        state = self._state()
        state["profile_key"] = "personal_paper"
        state["runtime"]["daily_objective_last_report"] = {"target_gap": 1000.0}
        candidates = [
            {
                "ticker": "WEAK",
                "portfolio_score": 90.0,
                "execution_score": 90.0,
                "edge_to_cost_ratio": 0.8,
                "expected_edge_bps": 5.0,
                "spread_bps": 45.0,
                "average_dollar_volume": 200000.0,
                "projected_position_cost": 5000.0,
            },
            {
                "ticker": "CLEAN",
                "portfolio_score": 80.0,
                "execution_score": 82.0,
                "edge_to_cost_ratio": 5.0,
                "expected_edge_bps": 45.0,
                "spread_bps": 3.0,
                "average_dollar_volume": 10000000.0,
                "projected_position_cost": 5000.0,
            },
        ]

        annotated = automation_daily_objective_service.apply_daily_objective_candidate_overlay(
            candidates,
            state=state,
            current_equity=100000.0,
        )
        ranked = sorted(annotated, key=lambda item: item.get("daily_objective_score", 0.0), reverse=True)

        self.assertEqual(ranked[0]["ticker"], "CLEAN")
        self.assertGreater(ranked[0]["daily_objective_score"], ranked[1]["daily_objective_score"])

    def test_live_profile_unchanged_when_daily_objective_live_scope_disabled(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["settings"]["daily_objective_apply_to_live"] = False

        report = automation_daily_objective_service.build_daily_objective_report(
            tenant=tenant,
            state=state,
            profile_key="personal_live",
            owned_open=pd.DataFrame(),
            owned_pending=pd.DataFrame(),
            owned_closed=self._closed_trades(tenant, [-1000.0], profile_key="personal_live"),
            effective_funds=100000.0,
            now=FIXED_NOW,
        )

        self.assertEqual(report["status"], "not_applicable")
        self.assertFalse(report["apply_to_live"])


if __name__ == "__main__":
    unittest.main()
