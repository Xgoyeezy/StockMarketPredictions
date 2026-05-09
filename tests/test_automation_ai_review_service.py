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
from backend.services import automation_ai_review_service, notes_service, trade_automation_service


class AutomationAiReviewServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="ai-review-test", name="AI Review Test", status="active")
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
                "ai_daily_review_enabled": True,
                "ai_auto_adjust_enabled": True,
                "ai_adjust_live_enabled": True,
                "ai_review_min_trades": 2,
                "ai_max_daily_setting_changes": 4,
                "ai_max_step_pct": 20.0,
                "risk_percent": 0.50,
                "max_daily_entries": 3,
                "cooldown_minutes": 20,
                "order_type": "market",
            }
        )
        state["runtime"].update(
            {
                "last_action": {"type": "stand_down", "detail": "Candidate failed edge checks."},
                "last_decision": {
                    "decision": "stand_down",
                    "reason": "edge_cost_ratio_too_low",
                    "detail": "Edge/cost was too low.",
                },
                "last_rejection": {
                    "reason": "edge_cost_ratio_too_low",
                    "detail": "Edge/cost was too low.",
                },
            }
        )
        return state

    def _closed_trades(self, tenant: Tenant, profile_key: str = "personal_paper") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "trade_id": "loss-1",
                    "ticker": "SPY",
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": profile_key,
                    "realized_pnl": -80.0,
                    "closed_at": "2026-04-24T18:00:00+00:00",
                },
                {
                    "trade_id": "loss-2",
                    "ticker": "QQQ",
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": tenant.id,
                    "automation_profile_key": profile_key,
                    "realized_pnl": -20.0,
                    "closed_at": "2026-04-24T19:30:00+00:00",
                },
            ]
        )

    def test_normalized_state_includes_ai_and_risk_controls(self) -> None:
        state = trade_automation_service._normalize_trade_automation_profile_state({})

        self.assertTrue(state["settings"]["ai_daily_review_enabled"])
        self.assertTrue(state["settings"]["ai_auto_adjust_enabled"])
        self.assertTrue(state["settings"]["ai_adjust_live_enabled"])
        self.assertEqual(state["settings"]["ai_review_min_trades"], 3)
        self.assertTrue(state["settings"]["ai_evidence_review_enabled"])
        self.assertEqual(state["settings"]["ai_evidence_review_mode"], "shadow_review")
        self.assertEqual(state["settings"]["ai_evidence_min_confidence"], 0.7)
        self.assertEqual(state["settings"]["ai_evidence_max_candidates_per_cycle"], 12)
        self.assertEqual(state["settings"]["max_spread_bps"], 25.0)
        self.assertFalse(state["settings"]["require_edge_fields"])
        self.assertIn("ai_daily_journal", state["runtime"])

    def test_ai_evidence_referee_approves_strong_confirmed_candidate(self) -> None:
        state = self._state()
        candidate = {
            "ticker": "SPY",
            "trade_decision": "VALID TRADE",
            "ranking_tier": "opportunity_capture",
            "opportunity_score": 91.0,
            "opportunity_type": "opening_range_breakout",
            "rapid_confirmed": True,
            "relative_volume": 2.1,
            "spread_bps": 6.0,
            "execution_score": 86.0,
            "portfolio_score": 84.0,
            "deep_score": 88.0,
            "edge_to_cost_ratio": 8.0,
        }

        review = automation_ai_review_service.review_trade_candidate_evidence(
            candidate,
            settings_state=state["settings"],
            state=state,
            now=datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(review["status"], "reviewed")
        self.assertEqual(review["verdict"], "approve_evidence")
        self.assertGreaterEqual(review["confidence"], 0.7)
        self.assertEqual(review["reason_codes"], [])
        self.assertGreaterEqual(review["review_latency_ms"], 0.0)
        self.assertFalse(review["can_override_risk_gates"])

    def test_ai_evidence_referee_waits_for_missing_confirmation(self) -> None:
        state = self._state()
        candidate = {
            "ticker": "QQQ",
            "trade_decision": "VALID TRADE",
            "ranking_tier": "opportunity_capture",
            "opportunity_score": 88.0,
            "relative_volume": 2.0,
            "spread_bps": 5.0,
            "execution_score": 80.0,
            "portfolio_score": 82.0,
            "deep_analysis_status": "deep_analysis_pending",
            "deep_analysis_cache_fresh": False,
        }

        review = automation_ai_review_service.review_trade_candidate_evidence(
            candidate,
            settings_state=state["settings"],
            state=state,
            now=datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(review["verdict"], "wait_for_confirmation")
        self.assertIn("fresh_deep_or_rapid_confirmation", review["missing_evidence"])
        self.assertIn("confirmation_incomplete", review["reason_codes"])

    def test_ai_evidence_referee_rejects_hard_safety_blocker(self) -> None:
        state = self._state()
        state["settings"]["kill_switch"] = True
        candidate = {
            "ticker": "AAPL",
            "trade_decision": "VALID TRADE",
            "ranking_tier": "opportunity_capture",
            "opportunity_score": 95.0,
            "rapid_confirmed": True,
            "relative_volume": 2.4,
            "spread_bps": 4.0,
            "execution_score": 90.0,
            "portfolio_score": 90.0,
        }

        review = automation_ai_review_service.review_trade_candidate_evidence(
            candidate,
            settings_state=state["settings"],
            state=state,
            now=datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(review["verdict"], "reject_evidence")
        self.assertIn("kill_switch", review["hard_blockers"])
        self.assertIn("hard_safety_lock", review["reason_codes"])

    def test_shadow_evidence_overlay_does_not_mutate_auto_entry(self) -> None:
        state = self._state()
        candidate = {
            "ticker": "MSFT",
            "auto_entry_eligible": True,
            "trade_decision": "VALID TRADE",
            "ranking_tier": "opportunity_capture",
            "opportunity_score": 88.0,
            "relative_volume": 2.0,
            "spread_bps": 5.0,
            "execution_score": 80.0,
            "portfolio_score": 82.0,
        }

        reviewed = automation_ai_review_service.apply_ai_evidence_review_candidate_overlay(
            [candidate],
            state=state,
            now=datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(reviewed[0]["auto_entry_eligible"])
        self.assertIn("ai_evidence_review", reviewed[0])

    def test_ai_evidence_report_aggregates_reason_codes_and_missed_reviews(self) -> None:
        state = self._state()
        candidates = [
            {
                "ticker": "SPY",
                "eligible": False,
                "blocker": "stale_quote",
                "opportunity_capture": {"score": 91.0},
                "rapid_confirmed": True,
                "relative_volume": 2.1,
                "spread_bps": 5.0,
            },
            {
                "ticker": "QQQ",
                "eligible": False,
                "opportunity_capture": {"score": 40.0},
                "spread_bps": 5.0,
            },
        ]
        reviews = [
            automation_ai_review_service.review_trade_candidate_evidence(
                candidate,
                settings_state=state["settings"],
                state=state,
                now=datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc),
            )
            for candidate in candidates
        ]

        report = automation_ai_review_service.build_ai_evidence_review_report(
            reviews,
            candidates=candidates,
            now=datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc),
        )
        missed = automation_ai_review_service.build_missed_trade_ai_review(
            [{**candidate, "ai_evidence_review": review} for candidate, review in zip(candidates, reviews)],
            now=datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(report["total_count"], 2)
        self.assertIn("reason_code_counts", report)
        self.assertTrue(report["paper_assist"]["dry_run_only"])
        self.assertFalse(report["paper_assist"]["can_override_risk_gates"])
        self.assertGreaterEqual(missed["reviewed_count"], 1)
        self.assertIn("rows", missed)

    def test_daily_observation_creates_and_reuses_single_note(self) -> None:
        _, tenant = self._db()
        state = self._state()
        fixed_now = datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with patch.object(notes_service, "NOTES_PATH", notes_path):
                first = automation_ai_review_service.capture_trade_automation_ai_observation(
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                    cycle_id="cycle-1",
                )
                second = automation_ai_review_service.capture_trade_automation_ai_observation(
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                    cycle_id="cycle-2",
                )
                notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(first["session_day"], "2026-04-24")
        self.assertEqual(second["summary"]["observation_count"], 2)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["note_type"], "risk_review")
        self.assertEqual(notes[0]["owner"], "automation-ai")
        self.assertIn("automation-ai", notes[0]["tags"])
        self.assertIn("daily-review", notes[0]["tags"])

    def test_review_applies_bounded_paper_adjustments_and_audits(self) -> None:
        db, tenant = self._db()
        state = self._state()
        fixed_now = datetime(2026, 4, 24, 20, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_ai_review_service.sdm, "read_closed_trades", return_value=self._closed_trades(tenant)),
                patch.object(
                    automation_ai_review_service.sdm,
                    "journal_probability_calibration_summary",
                    return_value={"resolved_count": 0, "average_error": None, "calibration_scope": "insufficient"},
                ),
            ):
                review = automation_ai_review_service.run_trade_automation_ai_review(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                    live_route_allowed=True,
                )
                db.commit()
                notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(review["status"], "reviewed")
        self.assertEqual(review["session_day"], "2026-04-24")
        self.assertTrue(review["applied_changes"])
        self.assertLess(state["settings"]["risk_percent"], 0.50)
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0]["completed"])
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.ai_reviewed", audit_types)
        self.assertIn("trade_automation.ai_adjusted", audit_types)

    def test_live_review_respects_live_gate_and_skips_changes(self) -> None:
        db, tenant = self._db()
        state = self._state()
        fixed_now = datetime(2026, 4, 24, 20, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(
                    automation_ai_review_service.sdm,
                    "read_closed_trades",
                    return_value=self._closed_trades(tenant, "personal_live"),
                ),
                patch.object(
                    automation_ai_review_service.sdm,
                    "journal_probability_calibration_summary",
                    return_value={"resolved_count": 0, "average_error": None, "calibration_scope": "insufficient"},
                ),
            ):
                review = automation_ai_review_service.run_trade_automation_ai_review(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_live",
                    now=fixed_now,
                    live_route_allowed=False,
                )

        self.assertEqual(review["status"], "reviewed")
        self.assertEqual(review["applied_changes"], [])
        self.assertTrue(review["skipped_changes"])
        self.assertEqual(state["settings"]["risk_percent"], 0.50)
        self.assertEqual(review["skipped_changes"][0]["skip_reason"], "Live rollout gate is not cleared.")

    def test_review_runs_once_per_session_day_without_force(self) -> None:
        db, tenant = self._db()
        state = self._state()
        fixed_now = datetime(2026, 4, 24, 20, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(automation_ai_review_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            ):
                first = automation_ai_review_service.run_trade_automation_ai_review(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                )
                second = automation_ai_review_service.run_trade_automation_ai_review(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=fixed_now,
                )

        self.assertEqual(first["status"], "reviewed")
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["reason"], "already_reviewed")


if __name__ == "__main__":
    unittest.main()
