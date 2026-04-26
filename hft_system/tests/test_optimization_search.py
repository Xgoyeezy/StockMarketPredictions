from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from hft.market_data.schemas import MarketEvent
from hft.optimization.search import optimize_market_making
from hft.optimization.types import OptimizationRunConfig
from hft.risk.limits import HFTLimitConfig


def build_session_events(session_id: str, *, day_offset: int) -> list[MarketEvent]:
    base_ts = 1_700_000_000_000_000_000 + (day_offset * 86_400_000_000_000)
    events: list[MarketEvent] = []
    sequence = 1
    mid_price = 100.0 + (day_offset * 0.10)
    for step in range(10):
        ts = base_ts + (step * 100_000_000)
        bid = round(mid_price - 0.01, 4)
        ask = round(mid_price + 0.01, 4)
        trade_side = "buy" if step % 2 == 0 else "sell"
        trade_price = ask if trade_side == "buy" else bid
        trade_size = 60.0 if trade_side == "buy" else 45.0
        events.extend(
            [
                MarketEvent(ts, ts, sequence, f"{session_id}-bid-{step}", "test", "XNAS", "AAPL", "add", "buy", bid, 150.0),
                MarketEvent(ts + 1, ts + 1, sequence + 1, f"{session_id}-ask-{step}", "test", "XNAS", "AAPL", "add", "sell", ask, 150.0),
                MarketEvent(
                    ts + 2,
                    ts + 2,
                    sequence + 2,
                    f"{session_id}-trade-{step}",
                    "test",
                    "XNAS",
                    "AAPL",
                    "trade",
                    trade_side,
                    trade_price,
                    trade_size,
                    trade_id=f"{session_id}-t-{step}",
                ),
            ]
        )
        sequence += 3
        mid_price += 0.015 if trade_side == "buy" else -0.010
    return events


class OptimizationSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="hft_opt_")
        self.session_events = {
            f"session-{index:02d}": build_session_events(f"session-{index:02d}", day_offset=index)
            for index in range(5)
        }
        self.config = OptimizationRunConfig(
            seed=11,
            random_candidates=4,
            top_candidates=2,
            refinement_rounds=1,
            refinement_radius=0.10,
            train_sessions=2,
            validation_sessions=1,
            holdout_sessions=1,
            horizons_ns=(100_000_000, 200_000_000),
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_search_is_deterministic_and_emits_artifacts(self) -> None:
        left = optimize_market_making(
            session_events=self.session_events,
            base_dir=self.tmpdir,
            config=self.config,
            risk_limits=HFTLimitConfig(),
            fee_model={},
        )
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        self.tmpdir = tempfile.mkdtemp(prefix="hft_opt_")
        right = optimize_market_making(
            session_events=self.session_events,
            base_dir=self.tmpdir,
            config=self.config,
            risk_limits=HFTLimitConfig(),
            fee_model={},
        )

        self.assertEqual(left.run_id, right.run_id)
        self.assertEqual(left.champion.parameters, right.champion.parameters)
        self.assertAlmostEqual(left.champion.validation_score, right.champion.validation_score)
        self.assertAlmostEqual(left.champion.holdout_score, right.champion.holdout_score)
        self.assertEqual(left.champion_report.accepted, right.champion_report.accepted)

        output_dir = right.output_dir
        required_files = [
            "optimization_summary.json",
            "split_manifest.json",
            "calibration_artifacts.json",
            "config_candidates.json",
            "fold_metrics.json",
            "selected_champion.json",
            "holdout_report.json",
            "bridge_export.json",
            "champion_report.html",
        ]
        for name in required_files:
            self.assertTrue((Path(output_dir) / name).exists(), name)

        summary = json.loads((Path(output_dir) / "optimization_summary.json").read_text(encoding="utf-8"))
        self.assertIn("baseline_vs_champion", summary)
        self.assertIn("feature_importance", summary)
        self.assertIn("parameter_sensitivity", summary)
        self.assertIn("read_only_bridge", summary)
