from __future__ import annotations

import shutil
import tempfile
import unittest
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from hft.backtest.engine import ReplayBacktestEngine, ReplayRunConfig
from hft.execution.simulator import ExecutionSimulator
from hft.latency.model import LatencyProfile
from hft.latency.model import LatencyModel
from hft.market_data.schemas import MarketEvent
from hft.market_data.sessions import is_extended_session
from hft.risk.checks import HFTRiskEngine
from hft.risk.limits import HFTLimitConfig
from hft.strategies.market_making import InventoryAwareMarketMakingStrategy


def sample_equity_events() -> list[MarketEvent]:
    return [
        MarketEvent(100, 100, 1, "b1", "sip", "XNAS", "AAPL", "add", "buy", 100.00, 500.0),
        MarketEvent(100, 100, 2, "a1", "sip", "XNAS", "AAPL", "add", "sell", 100.02, 500.0),
        MarketEvent(110, 110, 3, "t1", "sip", "XNAS", "AAPL", "trade", "sell", 100.00, 200.0, trade_id="t1"),
        MarketEvent(120, 120, 4, "t2", "sip", "XNAS", "AAPL", "trade", "buy", 100.02, 200.0, trade_id="t2"),
        MarketEvent(130, 130, 5, "t3", "sip", "XNAS", "AAPL", "trade", "sell", 100.00, 250.0, trade_id="t3"),
    ]


class EquityReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="hft_equity_replay_")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_common_stock_replay_produces_orders_fills_and_bridge(self) -> None:
        engine = ReplayBacktestEngine(base_dir=self.tmpdir)
        config = ReplayRunConfig(
            seed=13,
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
        result = engine.run(events=sample_equity_events(), strategy=InventoryAwareMarketMakingStrategy(), config=config)

        self.assertEqual(result.symbol, "AAPL")
        self.assertGreaterEqual(len(result.orders), 1)
        self.assertGreaterEqual(len(result.fills), 1)
        self.assertIn("simulated_pnl", result.bridge_export)
        self.assertIn("inventory_pnl", result.bridge_export["simulated_pnl"])
        self.assertIn("slippage", result.bridge_export["simulated_pnl"])

    def test_hft_events_and_orders_preserve_pre_and_after_hours_sessions(self) -> None:
        pre_market_ns = int(datetime(2026, 4, 24, 8, 0, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1_000_000_000)
        after_hours_ns = int(datetime(2026, 4, 24, 17, 0, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1_000_000_000)
        event = MarketEvent(pre_market_ns, pre_market_ns, 1, "pre-1", "sip", "XNAS", "AAPL", "trade", "buy", 100.0, 100.0)
        simulator = ExecutionSimulator(
            risk_engine=HFTRiskEngine(limits=HFTLimitConfig()),
            latency_model=LatencyModel(LatencyProfile("fixed", {"order_submit_ns": 1, "exchange_ack_ns": 1}), random.Random(1)),
        )
        order = simulator.create_limit_order(
            symbol="AAPL",
            strategy_name="hft_prepost",
            side="buy",
            price=100.0,
            quantity=10.0,
            decision_timestamp=after_hours_ns,
            quote_width=0.02,
        )

        self.assertEqual(event.session, "pre_market")
        self.assertTrue(is_extended_session(event.session))
        self.assertEqual(order.metadata["session"], "after_hours")
        self.assertTrue(order.metadata["extended_hours"])


if __name__ == "__main__":
    unittest.main()
