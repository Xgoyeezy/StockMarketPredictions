from __future__ import annotations

import shutil
import unittest

from hft.backtest.engine import ReplayBacktestEngine, ReplayRunConfig
from hft.execution.simulator import LatencyProfile
from hft.market_data.schemas import MarketEvent
from hft.risk.limits import HFTLimitConfig
from hft.strategies.market_making import InventoryAwareMarketMakingStrategy
from tests._workspace_tmp import reset_tmp_dir


def sample_events() -> list[MarketEvent]:
    return [
        MarketEvent(100, 100, 1, "e1", "test", "XNAS", "AAPL", "add", "buy", 100.00, 50.0),
        MarketEvent(100, 100, 2, "e2", "test", "XNAS", "AAPL", "add", "sell", 100.02, 50.0),
        MarketEvent(110, 110, 3, "e3", "test", "XNAS", "AAPL", "trade", "sell", 100.00, 40.0, trade_id="t1"),
        MarketEvent(120, 120, 4, "e4", "test", "XNAS", "AAPL", "trade", "buy", 100.02, 40.0, trade_id="t2"),
        MarketEvent(130, 130, 5, "e5", "test", "XNAS", "AAPL", "trade", "sell", 100.00, 40.0, trade_id="t3"),
    ]


class ReplayDeterminismTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = str(reset_tmp_dir("replay_determinism"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_once(self):
        engine = ReplayBacktestEngine(base_dir=self.tmpdir)
        config = ReplayRunConfig(
            seed=7,
            symbol="AAPL",
            strategy_name="inventory_aware_market_making",
            latency_profile=LatencyProfile(
                "fixed",
                {
                    "strategy_compute_ns": 1,
                    "order_submit_ns": 1,
                    "exchange_ack_ns": 1,
                    "fill_ns": 1,
                },
            ),
            risk_limits=HFTLimitConfig(),
            fee_model={"maker_rebate_per_share": 0.001, "taker_fee_per_share": 0.002, "cancel_cost_per_order": 0.0001},
        )
        return engine.run(events=sample_events(), strategy=InventoryAwareMarketMakingStrategy(), config=config)

    def test_same_seed_same_results(self) -> None:
        left = self.run_once()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        self.tmpdir = str(reset_tmp_dir("replay_determinism"))
        right = self.run_once()

        self.assertEqual(left.metrics, right.metrics)
        self.assertEqual(left.event_log, right.event_log)
        self.assertEqual(
            [(fill.order_id, fill.price, fill.quantity, fill.fill_timestamp) for fill in left.fills],
            [(fill.order_id, fill.price, fill.quantity, fill.fill_timestamp) for fill in right.fills],
        )
        self.assertIn("simulated_pnl", left.bridge_export)
        self.assertIn("inventory_summary", left.bridge_export)
        self.assertIn("risk_summary", left.bridge_export)
