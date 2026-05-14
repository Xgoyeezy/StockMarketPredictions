from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from backend.services.evidence_edge_analytics import build_evidence_edge_report


class EvidenceEdgeAnalyticsServiceTests(unittest.TestCase):
    def _empty_frame(self) -> pd.DataFrame:
        return pd.DataFrame()

    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")

    def _build_report(self, root: Path, closed_trades: pd.DataFrame | None = None) -> dict:
        return build_evidence_edge_report(
            tenant_slug="test-tenant",
            root=root,
            open_trades=self._empty_frame(),
            closed_trades=closed_trades if closed_trades is not None else self._empty_frame(),
            pending_orders=self._empty_frame(),
        )

    def test_empty_evidence_state_returns_research_only_empty_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._build_report(Path(tmp))

        self.assertEqual(report["summary"]["candidate_count"], 0)
        self.assertEqual(report["summary"]["data_status"], "empty")
        self.assertTrue(report["research_only"])
        self.assertFalse(report["can_submit_orders"])
        self.assertFalse(report["can_submit_live_orders"])
        self.assertEqual(report["mutation"], "none")
        self.assertEqual(report["recommended_ranking_adjustments"][0]["type"], "insufficient_data")

    def test_blocker_effectiveness_and_missed_winner_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [
                    {
                        "candidate_lifecycle_id": "cand-allowed",
                        "ticker": "AAPL",
                        "desk_key": "intraday_momentum",
                        "final_state": "eligible",
                        "opportunity_type": "opening_breakout",
                        "opportunity_score": 92,
                        "regime": "trend_day",
                        "forward_return_30m_pct": 0.6,
                        "ai_verdict": "approve_evidence",
                    },
                    {
                        "candidate_lifecycle_id": "cand-good-block",
                        "ticker": "MSFT",
                        "desk_key": "intraday_momentum",
                        "final_state": "rejected_or_waiting",
                        "blocker": "stale_quote",
                        "opportunity_type": "opening_breakout",
                        "opportunity_score": 88,
                        "regime": "trend_day",
                        "forward_return_30m_pct": -0.4,
                    },
                    {
                        "candidate_lifecycle_id": "cand-false-block",
                        "ticker": "NVDA",
                        "desk_key": "fast_scalper",
                        "final_state": "rejected_or_waiting",
                        "blocker": "weak_opportunity_score",
                        "opportunity_type": "vwap_reclaim",
                        "opportunity_score": 58,
                        "regime": "trend_day",
                        "forward_return_30m_pct": 0.8,
                    },
                ],
            )
            report = self._build_report(root)

        blocker_map = {item["blocker"]: item for item in report["blocker_effectiveness"]}
        self.assertEqual(report["summary"]["candidate_count"], 3)
        self.assertEqual(report["summary"]["allowed_count"], 1)
        self.assertEqual(report["summary"]["blocked_count"], 2)
        self.assertEqual(report["summary"]["missed_move_count"], 1)
        self.assertEqual(blocker_map["stale_quote"]["estimated_blocker_value"], 0.4)
        self.assertEqual(blocker_map["weak_opportunity_score"]["false_block_rate"], 1.0)
        self.assertEqual(blocker_map["weak_opportunity_score"]["recommendation"], "review_blocker")

    def test_missing_fields_are_reported_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [
                    {
                        "candidate_lifecycle_id": "cand-missing",
                        "ticker": "AMD",
                        "desk_key": "stat_arb",
                        "final_state": "rejected_or_waiting",
                        "blocker": "missing_deep_confirmation",
                    }
                ],
            )
            report = self._build_report(root)

        self.assertEqual(report["summary"]["candidate_count"], 1)
        self.assertIn("forward_returns", report["summary"]["missing_fields"])
        self.assertIn("regime", report["summary"]["missing_fields"])
        self.assertEqual(report["blocker_effectiveness"][0]["confidence_bucket"], "insufficient")

    def test_setup_engine_regime_and_score_bucket_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [
                    {
                        "candidate_lifecycle_id": "cand-breakout",
                        "ticker": "AAPL",
                        "desk_key": "intraday_momentum",
                        "final_state": "eligible",
                        "opportunity_type": "opening_breakout",
                        "opportunity_score": 95,
                        "regime": "trend_day",
                        "forward_return_30m_pct": 1.0,
                    },
                    {
                        "candidate_lifecycle_id": "cand-vwap",
                        "ticker": "TSLA",
                        "desk_key": "fast_scalper",
                        "final_state": "eligible",
                        "opportunity_type": "vwap_reclaim",
                        "opportunity_score": 55,
                        "regime": "range_day",
                        "forward_return_30m_pct": -0.5,
                    },
                ],
            )
            report = self._build_report(root)

        setups = {item["setup_type"]: item for item in report["setup_forward_return_stats"]}
        engines = {item["engine"]: item for item in report["engine_forward_return_stats"]}
        regimes = {item["regime"]: item for item in report["regime_forward_return_stats"]}
        buckets = {item["score_bucket"]: item for item in report["score_bucket_outcomes"]}
        self.assertEqual(setups["opening_breakout"]["average_forward_return_pct"], 1.0)
        self.assertEqual(engines["fast_scalper"]["average_forward_return_pct"], -0.5)
        self.assertIn("trend_day", regimes)
        self.assertEqual(buckets["90_100"]["observed_outcome_count"], 1)
        self.assertEqual(buckets["40_59"]["observed_outcome_count"], 1)

    def test_recommendation_generation_and_paper_trade_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [
                    {
                        "candidate_lifecycle_id": f"cand-breakout-{index}",
                        "ticker": "AAPL",
                        "desk_key": "intraday_momentum",
                        "final_state": "eligible",
                        "opportunity_type": "opening_breakout",
                        "opportunity_score": 95,
                        "regime": "trend_day",
                        "forward_return_30m_pct": 1.0,
                    }
                    for index in range(5)
                ],
            )
            closed = pd.DataFrame(
                [
                    {
                        "ticker": "QQQ",
                        "strategy_desk_key": "macro",
                        "automation_entry_reason": "ranked_candidate",
                        "alpha_score": 70,
                        "validation_sample_bucket": "range_day",
                        "realized_pnl": 50,
                        "position_cost": 1000,
                        "automation_execution_intent": "broker_paper",
                    }
                ]
            )
            report = self._build_report(root, closed_trades=closed)

        self.assertEqual(report["summary"]["candidate_count"], 6)
        self.assertTrue(any(item["type"] == "increase_rank_weight" for item in report["recommended_ranking_adjustments"]))
        self.assertTrue(report["top_positive_features"])
        self.assertTrue(report["paper_route_only"])

    def test_simulation_evidence_is_excluded_from_live_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root / "runtime-exports" / "candidate-lifecycle" / "2026-05-05" / "test-tenant.jsonl",
                [
                    {
                        "candidate_lifecycle_id": "sim-only",
                        "ticker": "SPY",
                        "simulation_evidence": True,
                        "final_state": "eligible",
                        "forward_return_30m_pct": 99.0,
                    },
                    {
                        "candidate_lifecycle_id": "sim-pool",
                        "ticker": "QQQ",
                        "evidence_pool": "simulation_evidence",
                        "final_state": "eligible",
                        "forward_return_30m_pct": 99.0,
                    }
                ],
            )
            self._write_jsonl(
                root / "runtime-exports" / "simulation-evidence" / "2026-05-05" / "test-tenant.jsonl",
                [{"ticker": "SPY", "scenario_probability": 0.9, "counts_toward_live_million": False}],
            )
            report = self._build_report(root)

        self.assertEqual(report["summary"]["candidate_count"], 0)
        self.assertEqual(report["summary"]["source_counts"]["simulation_evidence_rows_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
