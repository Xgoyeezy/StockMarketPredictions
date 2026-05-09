from __future__ import annotations

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
    automation_transaction_cost_calibration_service,
    notes_service,
    trade_automation_service,
)


FIXED_NOW = datetime(2026, 4, 24, 21, 30, tzinfo=timezone.utc)


class AutomationTransactionCostCalibrationServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="cost-calibration-test", name="Cost Calibration Test", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        return db, tenant

    def _state(self) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["profile_key"] = "personal_paper"
        state["settings"].update(
            {
                "transaction_cost_calibration_enabled": True,
                "transaction_cost_calibration_auto_review_enabled": True,
                "transaction_cost_calibration_apply_to_live": False,
                "transaction_cost_calibration_min_samples": 3,
                "transaction_cost_calibration_stale_after_sessions": 5,
                "transaction_cost_calibration_max_candidate_penalty": 20,
            }
        )
        return state

    def _closed_trades(
        self,
        tenant: Tenant,
        errors: list[float],
        *,
        profile_key: str = "personal_paper",
        pnl: float = 25.0,
    ) -> pd.DataFrame:
        rows = []
        for index, error in enumerate(errors, start=1):
            rows.append(
                {
                    "trade_id": f"trade-{index}",
                    "ticker": "SPY" if index % 2 else "QQQ",
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": profile_key,
                    "realized_pnl": pnl if error < 20 else -abs(pnl),
                    "position_cost": 1000.0,
                    "accuracy_expected_edge_bps": 35.0,
                    "accuracy_estimated_cost_bps": 5.0,
                    "slippage_bps": 5.0 + error,
                    "accuracy_spread_bps": 4.0,
                    "average_dollar_volume": 100_000_000.0,
                    "accuracy_pattern_key": "mega_cap|equity|regular|bullish",
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

    def test_action_schema_accepts_transaction_cost_review(self) -> None:
        request = OrganizationTradeAutomationActionRequest(action="run_transaction_cost_calibration_review")
        self.assertEqual(request.action, "run_transaction_cost_calibration_review")

    def test_low_sample_collects_without_fake_confidence(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["settings"]["transaction_cost_calibration_min_samples"] = 5

        report = automation_transaction_cost_calibration_service.build_transaction_cost_calibration_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=self._closed_trades(tenant, [1.0, 2.0]),
            now=FIXED_NOW,
        )

        self.assertEqual(report["status"], "collecting")
        self.assertEqual(report["sample_count"], 2)
        self.assertIn("sample_collection", {item["field"] for item in report["recommendations"]})

    def test_clean_fills_create_calibrated_report(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = automation_transaction_cost_calibration_service.build_transaction_cost_calibration_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=self._closed_trades(tenant, [0.5, 1.0, 1.5, 0.0]),
            now=FIXED_NOW,
        )

        self.assertEqual(report["status"], "calibrated")
        self.assertGreaterEqual(report["sample_count"], 3)
        self.assertFalse(report["blockers"])

    def test_poor_cost_drift_blocks_and_penalizes_candidates(self) -> None:
        _, tenant = self._db()
        state = self._state()
        report = automation_transaction_cost_calibration_service.build_transaction_cost_calibration_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=self._closed_trades(tenant, [35.0, 40.0, 30.0], pnl=-20.0),
            now=FIXED_NOW,
        )
        state["runtime"]["transaction_cost_calibration_last_report"] = report

        scored = automation_transaction_cost_calibration_service.score_transaction_cost_candidate(
            {
                "ticker": "SPY",
                "portfolio_score": 80.0,
                "expected_edge_bps": 40.0,
                "estimated_cost_bps": 6.0,
                "setup_bucket": "mega_cap|equity|regular|bullish",
                "average_dollar_volume": 100_000_000.0,
            },
            state=state,
            current_equity=100000.0,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertGreater(scored["transaction_cost_candidate_penalty"], 0)
        self.assertLess(scored["transaction_cost_adjusted_expected_edge_bps"], 40.0)

    def test_live_profile_is_advisory_when_live_scope_disabled(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["profile_key"] = "personal_live"

        report = automation_transaction_cost_calibration_service.build_transaction_cost_calibration_report(
            tenant=tenant,
            state=state,
            profile_key="personal_live",
            owned_closed=self._closed_trades(tenant, [1.0, 1.0, 1.0], profile_key="personal_live"),
            now=FIXED_NOW,
        )
        scored = automation_transaction_cost_calibration_service.score_transaction_cost_candidate(
            {"ticker": "SPY", "portfolio_score": 80.0},
            state=state,
        )

        self.assertEqual(report["status"], "not_applicable")
        self.assertEqual(scored, {})

    def test_manual_review_persists_metadata_note_and_audit_without_settings_mutation(self) -> None:
        db, tenant = self._db()
        state = self._state()
        before_settings = dict(state["settings"])
        notes_patch, _ = self._patch_notes()

        with notes_patch, patch.object(
            automation_transaction_cost_calibration_service.sdm,
            "read_closed_trades",
            return_value=self._closed_trades(tenant, [0.5, 1.0, 1.5, 0.0]),
        ):
            report = automation_transaction_cost_calibration_service.run_transaction_cost_calibration_review(
                db,
                tenant=tenant,
                state=state,
                profile_key="personal_paper",
                actor=None,
                now=FIXED_NOW,
                run_source="manual",
            )
            db.commit()

        self.assertEqual(state["settings"], before_settings)
        self.assertEqual(report["status"], "calibrated")
        self.assertTrue(state["runtime"].get("transaction_cost_calibration_last_note_id"))
        events = db.query(AuditEvent).all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "trade_automation.transaction_cost_calibrated")


if __name__ == "__main__":
    unittest.main()
