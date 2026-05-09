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
    automation_paper_evidence_service,
    automation_replay_lab_service,
    notes_service,
    trade_automation_service,
)


FIXED_NOW = datetime(2026, 4, 24, 18, 30, tzinfo=timezone.utc)


class AutomationPaperEvidenceServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="paper-evidence-test", name="Paper Evidence Test", status="active")
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
                "account_size": 100000.0,
                "execution_intent": "broker_paper",
                "require_edge_fields": True,
                "require_liquidity_fields": True,
                "max_notional_per_trade": 10000.0,
                "max_total_open_notional": 20000.0,
                "max_spread_bps": 20.0,
                "min_edge_to_cost_ratio": 3.0,
                "risk_percent": 0.25,
                "accuracy_calibration_min_samples": 1,
                "paper_evidence_collection_enabled": True,
                "paper_evidence_auto_review_enabled": True,
            }
        )
        return state

    def _patch_notes(self):
        notes_dir = tempfile.TemporaryDirectory()
        self.addCleanup(notes_dir.cleanup)
        notes_path = Path(notes_dir.name) / "notes.json"
        notes_path.write_text("[]", encoding="utf-8")
        return patch.object(notes_service, "NOTES_PATH", notes_path), notes_path

    def _candidate_history(self) -> list[dict]:
        return [
            {
                "at": "2026-04-24T14:00:00+00:00",
                "ticker": "SPY",
                "selected": True,
                "auto_entry_eligible": True,
                "expected_edge_bps": 55.0,
                "edge_to_cost_ratio": 4.4,
                "spread_bps": 2.0,
                "liquidity": 50_000_000.0,
                "rank": 1,
                "daily_objective_expected_pnl": 55.0,
                "session_bucket": "regular",
            },
            {
                "at": "2026-04-24T14:00:00+00:00",
                "ticker": "QQQ",
                "selected": False,
                "auto_entry_eligible": False,
                "expected_edge_bps": 40.0,
                "edge_to_cost_ratio": 3.2,
                "spread_bps": 3.0,
                "liquidity": 40_000_000.0,
                "rank": 2,
                "daily_objective_expected_pnl": 40.0,
                "session_bucket": "regular",
                "reject_reason": "rank_limit",
            },
        ]

    def _closed_trades(self, tenant: Tenant) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "trade_id": "trade-1",
                    "ticker": "SPY",
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": "personal_paper",
                    "realized_pnl": 35.0,
                    "position_cost": 5000.0,
                    "accuracy_pattern_key": "index|equity|regular|bullish",
                    "accuracy_forecast_direction": "BULLISH",
                    "accuracy_forecast_confidence": 0.70,
                    "accuracy_expected_edge_bps": 55.0,
                    "accuracy_edge_to_cost_ratio": 4.4,
                    "accuracy_spread_bps": 2.0,
                    "slippage_bps": 1.5,
                    "closed_at": "2026-04-24T17:00:00+00:00",
                }
            ]
        )

    def test_action_schema_accepts_paper_evidence_review(self) -> None:
        request = OrganizationTradeAutomationActionRequest(action="run_paper_evidence_review")
        self.assertEqual(request.action, "run_paper_evidence_review")

    def test_missing_edge_or_spread_candidates_are_rejected_before_entry(self) -> None:
        state = self._state()
        rows = [
            {
                "ticker": "MISS",
                "trade_decision": "VALID TRADE",
                "verdict": "BULLISH",
                "ranking_score": 95.0,
                "setup_score": 95.0,
                "execution_score": 95.0,
                "portfolio_score": 95.0,
                "auto_entry_eligible": True,
                "average_dollar_volume": 50_000_000.0,
                "close": 100.0,
            }
        ]

        ranked = trade_automation_service._rank_automation_candidates(
            state=state,
            rows=rows,
            now=FIXED_NOW,
            current_equity=100000.0,
        )

        self.assertEqual(len(ranked), 1)
        self.assertFalse(ranked[0]["auto_entry_eligible"])
        self.assertIn(ranked[0]["reject_reason"], {"missing_spread", "missing_edge"})
        self.assertEqual(ranked[0]["candidate_telemetry_blocker"], ranked[0]["reject_reason"])

    def test_full_candidate_telemetry_is_recorded_for_selected_and_rejected_history(self) -> None:
        state = self._state()
        rows = [
            {
                "ticker": "SPY",
                "trade_decision": "VALID TRADE",
                "verdict": "BULLISH",
                "ranking_score": 95.0,
                "setup_score": 95.0,
                "execution_score": 95.0,
                "portfolio_score": 95.0,
                "auto_entry_eligible": True,
                "expected_edge_bps": 55.0,
                "edge_to_cost_ratio": 4.4,
                "spread_bps": 2.0,
                "average_dollar_volume": 50_000_000.0,
                "close": 100.0,
            },
            {
                "ticker": "QQQ",
                "trade_decision": "VALID TRADE",
                "verdict": "BULLISH",
                "ranking_score": 90.0,
                "setup_score": 90.0,
                "execution_score": 90.0,
                "portfolio_score": 90.0,
                "auto_entry_eligible": True,
                "expected_edge_bps": 40.0,
                "edge_to_cost_ratio": 3.5,
                "spread_bps": 3.0,
                "average_dollar_volume": 40_000_000.0,
                "close": 100.0,
            },
        ]

        ranked = trade_automation_service._rank_automation_candidates(
            state=state,
            rows=rows,
            now=FIXED_NOW,
            current_equity=100000.0,
        )
        trade_automation_service.automation_accuracy_calibration_service.record_candidate_snapshot(
            state,
            candidates=ranked,
            now=FIXED_NOW,
            cycle_id="cycle-1",
        )
        trade_automation_service.automation_accuracy_calibration_service.record_selected_candidate(
            state,
            candidate=ranked[0],
            now=FIXED_NOW,
            cycle_id="cycle-1",
        )

        history = state["runtime"]["accuracy_candidate_history"]
        self.assertGreaterEqual(len(history), 3)
        latest_selected = history[0]
        self.assertTrue(latest_selected["selected"])
        self.assertGreater(latest_selected["expected_edge_bps"], 0)
        self.assertGreater(latest_selected["edge_to_cost_ratio"], 0)
        self.assertGreater(latest_selected["spread_bps"], 0)
        self.assertGreater(latest_selected["liquidity"], 0)
        self.assertEqual(latest_selected["session_bucket"], "regular")
        self.assertGreater(latest_selected["daily_objective_expected_pnl"], 0)

    def test_paper_evidence_review_runs_objective_and_accuracy_reviews(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["accuracy_candidate_history"] = self._candidate_history()
        before_settings = dict(state["settings"])
        notes_patch, notes_path = self._patch_notes()
        closed = self._closed_trades(tenant)

        with (
            notes_patch,
            patch.object(automation_paper_evidence_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(automation_paper_evidence_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(automation_paper_evidence_service.sdm, "read_closed_trades", return_value=closed),
            patch.object(automation_paper_evidence_service.automation_daily_objective_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(automation_paper_evidence_service.automation_daily_objective_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(automation_paper_evidence_service.automation_daily_objective_service.sdm, "read_closed_trades", return_value=closed),
            patch.object(automation_paper_evidence_service.automation_daily_objective_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
            patch.object(automation_paper_evidence_service.automation_accuracy_calibration_service.sdm, "read_closed_trades", return_value=closed),
        ):
            report = automation_paper_evidence_service.run_paper_evidence_review(
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
                .filter(AuditEvent.event_type == "trade_automation.paper_evidence_reviewed")
                .count()
            )

        self.assertEqual(state["settings"], before_settings)
        self.assertNotEqual(state["runtime"]["daily_objective_last_report"]["status"], "not_run")
        self.assertNotEqual(state["runtime"]["accuracy_calibration_last_report"]["status"], "not_run")
        self.assertEqual(report["candidate_count"], 2)
        self.assertEqual(report["edge_coverage_pct"], 100.0)
        self.assertEqual(report["spread_coverage_pct"], 100.0)
        self.assertTrue(report["note_coverage"])
        self.assertTrue(any("paper-evidence" in item["tags"] for item in notes))
        self.assertEqual(audit_count, 1)

    def test_incomplete_candidate_telemetry_blocks_paper_evidence_quality(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["runtime"]["accuracy_candidate_history"] = [
            {
                "at": "2026-04-24T14:00:00+00:00",
                "ticker": "SPY",
                "selected": False,
                "spread_bps": 2.0,
                "liquidity": 50_000_000.0,
                "rank": 1,
            }
        ]

        report = automation_paper_evidence_service.build_paper_evidence_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_open=pd.DataFrame(),
            owned_pending=pd.DataFrame(),
            owned_closed=pd.DataFrame(),
            now=FIXED_NOW,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("edge_telemetry_incomplete", {item["key"] for item in report["blockers"]})

    def test_replay_lab_uses_fresh_candidate_and_trade_telemetry_without_missing_field_warnings(self) -> None:
        _, tenant = self._db()
        state = self._state()
        state["settings"]["replay_lab_min_trades"] = 1
        state["runtime"]["paper_broker_reconciliation_last_report"] = {"status": "clean"}
        state["runtime"]["accuracy_calibration_last_report"] = {
            "status": "calibrated",
            "decision_pnl_accuracy": 72.0,
            "calibrated_expectancy": 35.0,
        }
        state["runtime"]["accuracy_candidate_history"] = self._candidate_history()

        report = automation_replay_lab_service.build_replay_lab_report(
            tenant=tenant,
            state=state,
            profile_key="personal_paper",
            owned_closed=self._closed_trades(tenant),
            now=FIXED_NOW,
        )

        warning_keys = {item.get("key") for item in report.get("warnings") or []}
        self.assertNotIn("edge_telemetry_missing", warning_keys)
        self.assertNotIn("spread_telemetry_missing", warning_keys)
