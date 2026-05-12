from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.services.candidate_outcome_stamping_service import (
    build_evidence_outcomes_report,
    build_outcome_record,
    build_price_timeline,
    candidate_horizons,
    due_lifecycle_rows,
    enrich_candidate_lifecycle_row,
    load_lifecycle_rows,
    load_outcome_rows,
    stamp_due_candidate_outcomes,
)
from backend.services.evidence_reward_engine import build_evidence_reward_report


class CandidateOutcomeStampingServiceTests(unittest.TestCase):
    def _empty_frame(self) -> pd.DataFrame:
        return pd.DataFrame()

    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")

    def _candidate(self, **overrides) -> dict:
        row = {
            "tenant_slug": "test-tenant",
            "candidate_lifecycle_id": "cand-main",
            "ticker": "AAPL",
            "scan_time": "2026-05-05T14:00:00+00:00",
            "prediction_created_at": "2026-05-05T14:00:00+00:00",
            "predicted_direction": "bullish",
            "prediction_horizon_minutes": 15,
            "predicted_target_pct": 0.5,
            "invalidation_level": 98.0,
            "confidence": 0.72,
            "engine": "intraday_momentum",
            "setup_type": "vwap_reclaim",
            "score": 91,
            "regime": "trend_day",
            "spread_at_signal": 8.0,
            "slippage_estimate_bps": 3.0,
            "route": "broker_paper",
            "price_at_signal": 100.0,
            "previous_close": 99.0,
            "sector": "technology",
            "final_state": "eligible",
            "paper_route_only": True,
        }
        row.update(overrides)
        return row

    def _price_row(self, candidate_id: str, ticker: str, timestamp: str, price: float, **overrides) -> dict:
        row = self._candidate(
            candidate_lifecycle_id=candidate_id,
            ticker=ticker,
            symbol=ticker,
            scan_time=timestamp,
            prediction_created_at=timestamp,
            price_at_signal=price,
            reference_price=price,
            prediction_horizon_minutes=5,
        )
        row.update(overrides)
        return row

    def _fixture_rows(self) -> list[dict]:
        return [
            self._candidate(),
            self._price_row("aapl-5", "AAPL", "2026-05-05T14:05:00+00:00", 100.8),
            self._price_row("aapl-15", "AAPL", "2026-05-05T14:15:00+00:00", 101.5),
            self._price_row("spy-0", "SPY", "2026-05-05T14:00:00+00:00", 500.0),
            self._price_row("spy-15", "SPY", "2026-05-05T14:15:00+00:00", 501.0),
            self._price_row("qqq-0", "QQQ", "2026-05-05T14:00:00+00:00", 450.0),
            self._price_row("qqq-15", "QQQ", "2026-05-05T14:15:00+00:00", 449.1),
            self._price_row("xlk-0", "XLK", "2026-05-05T14:00:00+00:00", 210.0),
            self._price_row("xlk-15", "XLK", "2026-05-05T14:15:00+00:00", 211.05),
            self._price_row("msft-0", "MSFT", "2026-05-05T14:00:00+00:00", 200.0, candidate_lifecycle_id="cand-peer"),
            self._price_row("msft-15", "MSFT", "2026-05-05T14:15:00+00:00", 201.0),
        ]

    def test_lifecycle_row_without_mature_horizon_stays_pending(self) -> None:
        row = enrich_candidate_lifecycle_row(self._candidate())
        due = due_lifecycle_rows([row], now=datetime(2026, 5, 5, 14, 4, tzinfo=timezone.utc))

        self.assertEqual(candidate_horizons(row), [5, 15, 30])
        self.assertEqual(due, [])

    def test_mature_horizons_stamp_forward_returns_and_baselines(self) -> None:
        rows = [enrich_candidate_lifecycle_row(row) for row in self._fixture_rows()]
        timeline = build_price_timeline(rows)
        record = build_outcome_record(
            rows[0],
            horizon_minutes=15,
            price_timeline=timeline,
            lifecycle_rows=rows,
        )

        self.assertTrue(record["available"])
        self.assertEqual(record["actual_forward_return"], 1.5)
        self.assertEqual(record["spy_forward_return"], 0.2)
        self.assertEqual(record["qqq_forward_return"], -0.2)
        self.assertEqual(record["sector_etf_forward_return"], 0.5)
        self.assertIsNotNone(record["random_candidate_forward_return"])
        self.assertEqual(record["baseline_forward_return"], record["random_candidate_forward_return"])
        self.assertEqual(record["simple_vwap_reclaim_forward_return"], 1.5)
        self.assertEqual(record["previous_close_drift_forward_return"], 2.525253)
        self.assertEqual(record["spread_at_signal"], 8.0)
        self.assertEqual(record["slippage_bps"], 3.0)

    def test_missing_price_data_does_not_fabricate_returns(self) -> None:
        rows = [enrich_candidate_lifecycle_row(self._candidate())]
        record = build_outcome_record(
            rows[0],
            horizon_minutes=15,
            price_timeline=build_price_timeline(rows),
            lifecycle_rows=rows,
        )

        self.assertFalse(record["available"])
        self.assertIn("closed_horizon_price", record["missing_fields"])
        self.assertIsNone(record.get("actual_forward_return"))

    def test_missing_sector_data_disables_only_sector_baseline(self) -> None:
        rows = [enrich_candidate_lifecycle_row({**row, "sector": None}) for row in self._fixture_rows()]
        record = build_outcome_record(
            rows[0],
            horizon_minutes=15,
            price_timeline=build_price_timeline(rows),
            lifecycle_rows=rows,
        )

        self.assertTrue(record["available"])
        self.assertIsNone(record["sector_etf_forward_return"])
        self.assertIn("sector", record["baseline_missing_fields"]["sector_etf_forward_return"])
        self.assertIsNotNone(record["baseline_forward_return"])

    def test_stamp_due_is_append_only_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lifecycle_path = root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl"
            self._write_jsonl(lifecycle_path, self._fixture_rows())
            before = lifecycle_path.read_text(encoding="utf-8")

            first = stamp_due_candidate_outcomes(
                tenant_slug="test-tenant",
                root=root,
                now=datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc),
            )
            second = stamp_due_candidate_outcomes(
                tenant_slug="test-tenant",
                root=root,
                now=datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc),
            )
            outcome_rows = load_outcome_rows(root, "test-tenant")

            self.assertGreater(first["summary"]["written_count"], 0)
            self.assertEqual(second["summary"]["written_count"], 0)
            self.assertEqual(len({row["idempotency_key"] for row in outcome_rows}), len(outcome_rows))
            self.assertEqual(lifecycle_path.read_text(encoding="utf-8"), before)

    def test_unavailable_diagnostic_is_not_rewritten_until_evidence_improves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lifecycle_path = root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl"
            self._write_jsonl(lifecycle_path, [self._candidate()])

            first = stamp_due_candidate_outcomes(
                tenant_slug="test-tenant",
                root=root,
                now=datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc),
            )
            second = stamp_due_candidate_outcomes(
                tenant_slug="test-tenant",
                root=root,
                now=datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc),
            )
            outcome_rows = load_outcome_rows(root, "test-tenant")

            self.assertEqual(first["summary"]["available_count"], 0)
            self.assertEqual(first["summary"]["written_count"], 3)
            self.assertEqual(second["summary"]["written_count"], 0)
            self.assertEqual(second["summary"]["skipped_unavailable_duplicate_count"], 3)
            self.assertEqual(len(outcome_rows), 3)

    def test_unavailable_diagnostic_does_not_block_later_available_stamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl", self._fixture_rows())
            unavailable = {
                "candidate_lifecycle_id": "cand-main",
                "horizon_minutes": 15,
                "available": False,
                "actual_forward_return": None,
                "baseline_forward_return": None,
                "missing_fields": ["actual_forward_return", "baseline_forward_return"],
                "idempotency_key": "cand-main|15|candidate_outcome_baseline_v1",
                "generated_at": "2026-05-05T14:20:00+00:00",
            }
            self._write_jsonl(root / "runtime-exports" / "candidate-outcomes" / "2026-05-05" / "test-tenant.jsonl", [unavailable])

            report = stamp_due_candidate_outcomes(
                tenant_slug="test-tenant",
                root=root,
                now=datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc),
            )
            outcome_rows = load_outcome_rows(root, "test-tenant")
            available_rows = [
                row
                for row in outcome_rows
                if row.get("candidate_lifecycle_id") == "cand-main"
                and row.get("horizon_minutes") == 15
                and row.get("available")
            ]

            self.assertGreaterEqual(report["summary"]["superseded_unavailable_count"], 1)
            self.assertTrue(available_rows)
            self.assertEqual(available_rows[-1]["actual_forward_return"], 1.5)
            self.assertIsNotNone(available_rows[-1]["baseline_forward_return"])

    def test_simulation_evidence_is_ignored_for_real_time_observed_rewardability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [{**self._candidate(), "simulation_evidence": True}],
            )
            report = stamp_due_candidate_outcomes(
                tenant_slug="test-tenant",
                root=root,
                now=datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(report["summary"]["candidate_lifecycle_rows"], 0)
            self.assertEqual(report["summary"]["written_count"], 0)

    def test_evidence_reward_merges_candidate_outcome_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lifecycle = self._candidate(actual_forward_return=None, baseline_forward_return=None)
            outcome = {
                "candidate_lifecycle_id": "cand-main",
                "horizon_minutes": 15,
                "available": True,
                "actual_forward_return": 1.2,
                "actual_forward_return_observed_at": "2026-05-05T14:15:00+00:00",
                "baseline_forward_return": 0.2,
                "random_candidate_forward_return": 0.2,
                "spy_forward_return": 0.1,
                "max_adverse_excursion": -0.1,
                "hit_target": True,
                "hit_invalidation": False,
                "time_to_target_minutes": 10,
                "slippage_bps": 3.0,
                "spread_at_signal": 8.0,
                "idempotency_key": "cand-main|15|candidate_outcome_baseline_v1",
            }
            self._write_jsonl(root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl", [lifecycle])
            self._write_jsonl(root / "runtime-exports" / "candidate-outcomes" / "2026-05-05" / "test-tenant.jsonl", [outcome])

            report = build_evidence_reward_report(
                tenant_slug="test-tenant",
                root=root,
                open_trades=self._empty_frame(),
                closed_trades=self._empty_frame(),
                pending_orders=self._empty_frame(),
            )

            self.assertEqual(report["summary"]["rewardable_count"], 1)
            self.assertEqual(report["summary"]["source_counts"]["candidate_outcome_rows"], 1)
            self.assertEqual(report["candidate_rows"][0]["prediction_contract_status"], "rewardable")

    def test_evidence_outcomes_report_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl", self._fixture_rows())
            report = build_evidence_outcomes_report(
                tenant_slug="test-tenant",
                root=root,
                now=datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc),
            )

            self.assertTrue(report["research_only"])
            self.assertTrue(report["paper_only"])
            self.assertFalse(report["can_submit_orders"])
            self.assertFalse(report["can_submit_live_orders"])
            self.assertIn("summary", report)
            self.assertIn("records", report)
            self.assertIn("aggregations", report)
            self.assertIn("safety_notes", report)

    def test_api_routes_return_required_shape(self) -> None:
        client = TestClient(create_app())
        fixture = {
            "status": "empty",
            "generated_at": "2026-05-05T15:00:00+00:00",
            "research_only": True,
            "paper_only": True,
            "summary": {},
            "records": [],
            "aggregations": {},
            "warnings": [],
            "missing_fields": {},
            "safety_notes": ["Research only. Does not affect trading."],
            "can_submit_orders": False,
            "can_submit_live_orders": False,
        }
        with (
            patch("backend.routers.evidence_outcomes.cached_research_report", side_effect=lambda **kwargs: kwargs["builder"]()),
            patch("backend.routers.evidence_outcomes.get_evidence_outcomes_summary", return_value=fixture),
            patch("backend.routers.evidence_outcomes.get_evidence_outcomes_due", return_value=fixture),
            patch("backend.routers.evidence_outcomes.get_evidence_outcomes_records", return_value=fixture),
            patch("backend.routers.evidence_outcomes.post_evidence_outcomes_stamp_due", return_value=fixture),
        ):
            for method, path in (
                ("get", "/api/evidence-outcomes/summary"),
                ("get", "/api/evidence-outcomes/due"),
                ("get", "/api/evidence-outcomes/records"),
                ("post", "/api/evidence-outcomes/stamp-due"),
            ):
                response = getattr(client, method)(path)
                self.assertEqual(response.status_code, 200)
                data = response.json()["data"]
                self.assertTrue(data["research_only"])
                self.assertTrue(data["paper_only"])
                self.assertFalse(data["can_submit_orders"])
                self.assertFalse(data["can_submit_live_orders"])
                self.assertIn("summary", data)
                self.assertIn("records", data)
                self.assertIn("warnings", data)
                self.assertIn("missing_fields", data)
                self.assertIn("safety_notes", data)

    def test_service_contains_no_trading_mutation_calls(self) -> None:
        source = Path("backend/services/candidate_outcome_stamping_service.py").read_text(encoding="utf-8")
        forbidden_calls = (
            "submit_order(",
            "place_order(",
            "clear_kill_switch(",
            "set_broker_route(",
            "update_ranking_weight(",
            "enable_live_trading(",
            "bypass_risk_gate(",
        )
        for call in forbidden_calls:
            self.assertNotIn(call, source)


if __name__ == "__main__":
    unittest.main()
