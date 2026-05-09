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
from backend.services import automation_replay_lab_service, notes_service, trade_automation_service


FIXED_NOW = datetime(2026, 4, 24, 21, 30, tzinfo=timezone.utc)


class AutomationReplayLabServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="replay-lab-test", name="Replay Lab Test", status="active")
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
                "account_size": 100000.0,
                "daily_profit_target_dollars": 1000.0,
                "daily_profit_target_pct": 1.0,
                "daily_loss_budget_pct": 0.5,
                "replay_lab_enabled": True,
                "replay_lab_auto_review_enabled": True,
                "replay_lab_window_sessions": 20,
                "replay_lab_min_trades": 3,
                "replay_lab_apply_to_live": False,
                "replay_lab_max_recommended_setting_changes": 3,
                "cycle_entry_rank_limit": 2,
                "min_edge_to_cost_ratio": 2.5,
                "max_spread_bps": 25.0,
            }
        )
        state["runtime"]["paper_broker_reconciliation_last_report"] = {"status": "clean"}
        state["runtime"]["accuracy_calibration_last_report"] = {
            "status": "calibrated",
            "decision_pnl_accuracy": 72.0,
            "calibrated_expectancy": 80.0,
        }
        return state

    def _closed_trades(self, tenant: Tenant, pnl_values: list[float], *, profile_key: str = "personal_paper") -> pd.DataFrame:
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
                    "position_cost": 1000.0,
                    "portfolio_rank": 1 if index % 2 else 2,
                    "accuracy_edge_to_cost_ratio": 4.0,
                    "accuracy_spread_bps": 3.0,
                    "slippage_bps": 2.0,
                    "accuracy_pattern_key": "mega_cap|equity|regular",
                    "closed_at": "2026-04-24T18:00:00+00:00",
                }
            )
        return pd.DataFrame(rows)

    def _patch_notes(self):
        notes_dir = tempfile.TemporaryDirectory()
        self.addCleanup(notes_dir.cleanup)
        notes_path = Path(notes_dir.name) / "notes.json"
        notes_path.write_text("[]", encoding="utf-8")
        return patch.object(notes_service, "NOTES_PATH", notes_path), notes_path

    def test_action_schema_accepts_replay_lab_review(self) -> None:
        request = OrganizationTradeAutomationActionRequest(action="run_replay_lab_review")
        self.assertEqual(request.action, "run_replay_lab_review")

    def test_low_sample_is_advisory_collecting(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["settings"]["replay_lab_min_trades"] = 10

        report = automation_replay_lab_service.build_replay_lab_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=self._closed_trades(tenant, [50.0, -10.0]),
            now=FIXED_NOW,
        )

        self.assertEqual(report["status"], "collecting")
        self.assertEqual(report["sample_count"], 2)
        self.assertTrue(report["warnings"])
        self.assertIn("sample_collection", {item["field"] for item in report["recommendations"]})

    def test_clean_replay_can_recommend_bounded_capacity(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = automation_replay_lab_service.build_replay_lab_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=self._closed_trades(tenant, [120.0, 90.0, 75.0, 60.0]),
            now=FIXED_NOW,
        )

        self.assertEqual(report["status"], "clean")
        directions = {item["direction"] for item in report["recommendations"]}
        self.assertIn("consider_small_increase", directions)
        self.assertLessEqual(len(report["recommendations"]), 3)

    def test_stress_failures_prefer_risk_reduction(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = automation_replay_lab_service.build_replay_lab_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=self._closed_trades(tenant, [10.0, -40.0, 15.0, -25.0]),
            now=FIXED_NOW,
        )

        self.assertIn(report["status"], {"blocked", "warning"})
        directions = {item["direction"] for item in report["recommendations"]}
        self.assertTrue(any(str(direction).startswith("tighten") for direction in directions))
        self.assertNotIn("consider_small_increase", directions)

    def test_large_notional_stress_failure_is_mitigated_with_cap_recommendation(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["settings"]["max_notional_per_trade"] = 25000.0
        rows = []
        for index in range(12):
            rows.append(
                {
                    "trade_id": f"thin-edge-{index}",
                    "ticker": "SPY",
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": "personal_paper",
                    "realized_pnl": 16.0,
                    "position_cost": 25000.0,
                    "fill_slippage_bps": 2.0,
                    "setup_score": 55.0 + index,
                    "closed_at": "2026-04-24T18:00:00+00:00",
                }
            )

        report = automation_replay_lab_service.build_replay_lab_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=pd.DataFrame(rows),
            now=FIXED_NOW,
        )

        self.assertEqual(report["status"], "warning")
        self.assertFalse(report["blockers"])
        self.assertIn("stress_failures_mitigated", {item["key"] for item in report["warnings"]})
        fields = {item["field"]: item for item in report["recommendations"]}
        self.assertIn("max_notional_per_trade", fields)
        self.assertTrue(fields["max_notional_per_trade"]["direction"].startswith("reduce_to_"))
        recommended_cap = float(fields["max_notional_per_trade"]["direction"].removeprefix("reduce_to_"))
        self.assertLess(recommended_cap, state["settings"]["max_notional_per_trade"])
        self.assertGreater(report["data_quality"]["realized_edge_bps"], 0)
        self.assertEqual(report["data_quality"]["missing_spread_count"], 12)

    def test_stress_safe_cap_does_not_ratchetdown_after_recommended_cap_is_applied(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["settings"]["max_notional_per_trade"] = 10000.0
        rows = []
        for index in range(12):
            rows.append(
                {
                    "trade_id": f"thin-edge-applied-{index}",
                    "ticker": "SPY",
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": "personal_paper",
                    "realized_pnl": 16.0,
                    "position_cost": 25000.0,
                    "fill_slippage_bps": 2.0,
                    "setup_score": 55.0 + index,
                    "closed_at": "2026-04-24T18:00:00+00:00",
                }
            )

        report = automation_replay_lab_service.build_replay_lab_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=pd.DataFrame(rows),
            now=FIXED_NOW,
        )

        cap_scenario = next(
            item for item in report["settings_sensitivity"] if item["scenario"] == "notional_cap_stress_safe"
        )
        self.assertGreaterEqual(cap_scenario["recommended_cap"], state["settings"]["max_notional_per_trade"])
        self.assertNotIn("max_notional_per_trade", {item["field"] for item in report["recommendations"]})

    def test_actual_trade_telemetry_fields_are_used_when_available(self) -> None:
        _, tenant = self._db()
        state = self._state()
        rows = pd.DataFrame(
            [
                {
                    "trade_id": "telemetry-1",
                    "ticker": "QQQ",
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": "personal_paper",
                    "realized_pnl": 30.0,
                    "position_cost": 1000.0,
                    "contract_spread_pct": 0.0025,
                    "fill_slippage_bps": -3.5,
                    "setup_score": 70.0,
                    "closed_at": "2026-04-24T18:00:00+00:00",
                },
                {
                    "trade_id": "telemetry-2",
                    "ticker": "QQQ",
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": "personal_paper",
                    "realized_pnl": 24.0,
                    "position_cost": 1000.0,
                    "contract_spread_pct": 0.001,
                    "fill_slippage_bps": 1.0,
                    "setup_score": 68.0,
                    "closed_at": "2026-04-24T18:00:00+00:00",
                },
                {
                    "trade_id": "telemetry-3",
                    "ticker": "QQQ",
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": "personal_paper",
                    "realized_pnl": 18.0,
                    "position_cost": 1000.0,
                    "contract_spread_pct": 0.0015,
                    "fill_slippage_bps": 2.0,
                    "setup_score": 66.0,
                    "closed_at": "2026-04-24T18:00:00+00:00",
                },
            ]
        )

        report = automation_replay_lab_service.build_replay_lab_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=rows,
            now=FIXED_NOW,
        )

        self.assertEqual(report["data_quality"]["spread_coverage"], 1.0)
        self.assertEqual(report["data_quality"]["slippage_coverage"], 1.0)
        best_pattern = report["best_patterns"][0]
        self.assertIsNotNone(best_pattern["average_slippage_bps"])

    def test_manual_review_persists_note_audit_and_leaves_settings_unchanged(self) -> None:
        db, tenant = self._db()
        state = self._state()
        before_settings = dict(state["settings"])
        notes_patch, notes_path = self._patch_notes()

        with (
            notes_patch,
            patch.object(
                automation_replay_lab_service.sdm,
                "read_closed_trades",
                return_value=self._closed_trades(tenant, [120.0, 90.0, 75.0]),
            ),
        ):
            report = automation_replay_lab_service.run_replay_lab_review(
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
                .filter(AuditEvent.event_type == "trade_automation.replay_lab_reviewed")
                .count()
            )

        self.assertEqual(state["settings"], before_settings)
        self.assertEqual(state["runtime"]["replay_lab_last_report"]["status"], report["status"])
        self.assertEqual(len(notes), 1)
        self.assertIn("replay-lab", notes[0]["tags"])
        self.assertIn("what-if", notes[0]["tags"])
        self.assertIn("skipped", notes[0]["body"].lower())
        self.assertEqual(audit_count, 1)

    def test_live_profile_not_applicable_when_live_scope_disabled(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = automation_replay_lab_service.build_replay_lab_report(
            tenant=tenant,
            state=state,
            profile_key="personal_live",
            owned_closed=self._closed_trades(tenant, [500.0, 300.0, 200.0], profile_key="personal_live"),
            now=FIXED_NOW,
        )

        self.assertEqual(report["status"], "not_applicable")
        self.assertFalse(report["apply_to_live"])


if __name__ == "__main__":
    unittest.main()
