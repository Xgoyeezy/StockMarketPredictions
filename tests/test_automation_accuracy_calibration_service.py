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
from backend.services import automation_accuracy_calibration_service, notes_service, trade_automation_service


FIXED_NOW = datetime(2026, 4, 24, 18, 30, tzinfo=timezone.utc)


class AutomationAccuracyCalibrationServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="accuracy-calibration-test", name="Accuracy Calibration Test", status="active")
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
                "accuracy_calibration_enabled": True,
                "accuracy_calibration_apply_to_live": False,
                "accuracy_calibration_min_samples": 2,
                "accuracy_calibration_stale_after_sessions": 5,
                "accuracy_calibration_max_candidate_penalty": 25.0,
                "account_size": 100000.0,
            }
        )
        return state

    def _patch_notes(self):
        notes_dir = tempfile.TemporaryDirectory()
        self.addCleanup(notes_dir.cleanup)
        notes_path = Path(notes_dir.name) / "notes.json"
        notes_path.write_text("[]", encoding="utf-8")
        return patch.object(notes_service, "NOTES_PATH", notes_path), notes_path

    def _closed_trades(self, tenant: Tenant, rows: list[dict], profile_key: str = "personal_paper") -> pd.DataFrame:
        normalized = []
        for index, row in enumerate(rows, start=1):
            normalized.append(
                {
                    "trade_id": f"trade-{index}",
                    "ticker": row.get("ticker", "SPY"),
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": profile_key,
                    "automation_instrument_type": row.get("instrument_type", "equity"),
                    "accuracy_pattern_key": row.get("pattern_key", "index|equity|regular|bullish"),
                    "accuracy_forecast_direction": row.get("direction", "BULLISH"),
                    "accuracy_forecast_confidence": row.get("confidence", 0.72),
                    "accuracy_expected_edge_bps": row.get("expected_edge_bps", 40.0),
                    "position_cost": row.get("position_cost", 5000.0),
                    "realized_pnl": row.get("realized_pnl", 0.0),
                    "slippage_bps": row.get("slippage_bps", 4.0),
                    "closed_at": "2026-04-24T17:00:00+00:00",
                }
            )
        return pd.DataFrame(normalized)

    def test_action_schema_accepts_accuracy_calibration_review(self) -> None:
        request = OrganizationTradeAutomationActionRequest(action="run_accuracy_calibration_review")
        self.assertEqual(request.action, "run_accuracy_calibration_review")

    def test_profitable_clean_selected_candidates_improve_accuracy(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = automation_accuracy_calibration_service.build_accuracy_calibration_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=self._closed_trades(
                tenant,
                [
                    {"realized_pnl": 120.0, "confidence": 0.78, "slippage_bps": 3.0},
                    {"realized_pnl": 90.0, "confidence": 0.70, "slippage_bps": 5.0},
                ],
            ),
            now=FIXED_NOW,
        )

        self.assertEqual(report["status"], "calibrated")
        self.assertGreater(report["decision_pnl_accuracy"], 65.0)
        self.assertGreater(report["calibrated_expectancy"], 0.0)

    def test_directionally_correct_but_cost_negative_reduces_accuracy(self) -> None:
        _, tenant = self._db()
        state = self._state()

        report = automation_accuracy_calibration_service.build_accuracy_calibration_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=self._closed_trades(
                tenant,
                [
                    {"realized_pnl": -12.0, "confidence": 0.76, "slippage_bps": 35.0},
                    {"realized_pnl": -8.0, "confidence": 0.68, "slippage_bps": 28.0},
                ],
            ),
            now=FIXED_NOW,
        )

        self.assertEqual(report["status"], "weak")
        self.assertLess(report["decision_pnl_accuracy"], 45.0)
        self.assertLess(report["calibrated_expectancy"], 0.0)

    def test_overconfident_losing_pattern_penalizes_similar_candidates(self) -> None:
        _, tenant = self._db()
        state = self._state()
        report = automation_accuracy_calibration_service.build_accuracy_calibration_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=self._closed_trades(
                tenant,
                [
                    {"realized_pnl": -100.0, "confidence": 0.92, "pattern_key": "tech|equity|regular|bullish"},
                    {"realized_pnl": -60.0, "confidence": 0.88, "pattern_key": "tech|equity|regular|bullish"},
                ],
            ),
            now=FIXED_NOW,
        )
        state["runtime"]["accuracy_calibration_last_report"] = report

        annotated = automation_accuracy_calibration_service.apply_accuracy_calibration_candidate_overlay(
            [
                {
                    "ticker": "WEAK",
                    "portfolio_score": 80.0,
                    "execution_score": 80.0,
                    "proxy_correlation_bucket": "tech",
                    "automation_instrument_type": "equity",
                    "session_label": "regular",
                    "verdict": "BULLISH",
                    "confidence": 0.90,
                    "expected_edge_bps": 35.0,
                }
            ],
            state=state,
            current_equity=100000.0,
        )

        self.assertGreater(annotated[0]["accuracy_candidate_penalty"], 0.0)
        self.assertLess(annotated[0]["accuracy_calibrated_score"], 80.0)

    def test_rejected_candidate_history_records_missed_opportunity_signal(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["runtime"]["accuracy_candidate_history"] = [
            {
                "at": "2026-04-24T16:00:00+00:00",
                "selected": False,
                "auto_entry_eligible": True,
                "ticker": "MISS",
                "expected_pnl": 80.0,
            },
            {
                "at": "2026-04-24T16:01:00+00:00",
                "selected": True,
                "auto_entry_eligible": True,
                "ticker": "TAKEN",
                "expected_pnl": 20.0,
            },
        ]

        report = automation_accuracy_calibration_service.build_accuracy_calibration_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=self._closed_trades(
                tenant,
                [
                    {"realized_pnl": 15.0, "confidence": 0.60},
                    {"realized_pnl": 5.0, "confidence": 0.58},
                ],
            ),
            now=FIXED_NOW,
        )

        self.assertEqual(report["missed_opportunity_count"], 1)
        self.assertLess(report["selected_vs_rejected_delta"], 0.0)

    def test_low_sample_count_is_advisory_and_does_not_penalize_candidates(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["settings"]["accuracy_calibration_min_samples"] = 5
        report = automation_accuracy_calibration_service.build_accuracy_calibration_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=self._closed_trades(tenant, [{"realized_pnl": -100.0, "confidence": 0.95}]),
            now=FIXED_NOW,
        )
        state["runtime"]["accuracy_calibration_last_report"] = report

        annotated = automation_accuracy_calibration_service.apply_accuracy_calibration_candidate_overlay(
            [{"ticker": "TEST", "portfolio_score": 70.0, "execution_score": 70.0}],
            state=state,
            current_equity=100000.0,
        )

        self.assertEqual(report["status"], "collecting")
        self.assertEqual(annotated[0]["accuracy_candidate_penalty"], 0.0)

    def test_manual_review_persists_note_audit_and_leaves_settings_unchanged(self) -> None:
        db, tenant = self._db()
        state = self._state()
        before_settings = dict(state["settings"])
        notes_patch, notes_path = self._patch_notes()

        with (
            notes_patch,
            patch.object(
                automation_accuracy_calibration_service.sdm,
                "read_closed_trades",
                return_value=self._closed_trades(
                    tenant,
                    [
                        {"realized_pnl": 40.0, "confidence": 0.65},
                        {"realized_pnl": -10.0, "confidence": 0.72},
                    ],
                ),
            ),
        ):
            report = automation_accuracy_calibration_service.run_accuracy_calibration_review(
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
                .filter(AuditEvent.event_type == "trade_automation.accuracy_calibrated")
                .count()
            )

        self.assertEqual(state["settings"], before_settings)
        self.assertEqual(state["runtime"]["accuracy_calibration_last_report"]["status"], report["status"])
        self.assertEqual(len(notes), 1)
        self.assertIn("accuracy-calibration", notes[0]["tags"])
        self.assertIn("decision-pnl", notes[0]["tags"])
        self.assertEqual(audit_count, 1)

    def test_live_profile_not_changed_when_scope_disabled(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["settings"]["accuracy_calibration_apply_to_live"] = False

        report = automation_accuracy_calibration_service.build_accuracy_calibration_report(
            tenant=tenant,
            state=state,
            profile_key="personal_live",
            owned_closed=self._closed_trades(
                tenant,
                [{"realized_pnl": -200.0, "confidence": 0.9}],
                profile_key="personal_live",
            ),
            now=FIXED_NOW,
        )

        self.assertEqual(report["status"], "not_applicable")
        self.assertFalse(report["apply_to_live"])


if __name__ == "__main__":
    unittest.main()
