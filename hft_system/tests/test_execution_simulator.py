from __future__ import annotations

import random
import unittest

from hft.execution.simulator import ExecutionSimulator, LatencyModel, LatencyProfile
from hft.market_data.schemas import MarketEvent
from hft.risk.checks import HFTRiskEngine
from hft.risk.limits import HFTLimitConfig


class ExecutionSimulatorTest(unittest.TestCase):
    def test_submitted_order_fills_after_trade_event(self) -> None:
        risk_engine = HFTRiskEngine(limits=HFTLimitConfig())
        latency = LatencyModel(
            profile=LatencyProfile("fixed", {"order_submit_ns": 1, "exchange_ack_ns": 1, "fill_ns": 1}),
            rng=random.Random(7),
        )
        simulator = ExecutionSimulator(risk_engine=risk_engine, latency_model=latency, seed=7)
        order = simulator.create_limit_order(
            symbol="AAPL",
            strategy_name="mm",
            side="buy",
            price=100.0,
            quantity=10.0,
            decision_timestamp=100,
            quote_width=0.02,
        )
        decision, placed = simulator.submit_order(order)
        self.assertTrue(decision.allowed)

        fills = simulator.process_market_event(
            MarketEvent(100, 200, 1, "e1", "test", "XNAS", "AAPL", "trade", "sell", 100.0, 100.0),
            best_bid=100.0,
            best_ask=100.01,
            visible_depth_by_price={100.0: 0.0},
        )
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].order_id, placed.order_id)
        self.assertEqual(fills[0].fill_timestamp, 201)

    def test_ack_latency_changes_fill_eligibility(self) -> None:
        risk_engine = HFTRiskEngine(limits=HFTLimitConfig())
        latency = LatencyModel(
            profile=LatencyProfile("fixed", {"order_submit_ns": 1, "exchange_ack_ns": 10, "fill_ns": 1}),
            rng=random.Random(11),
        )
        simulator = ExecutionSimulator(risk_engine=risk_engine, latency_model=latency, seed=11)
        order = simulator.create_limit_order(
            symbol="AAPL",
            strategy_name="mm",
            side="buy",
            price=100.0,
            quantity=10.0,
            decision_timestamp=100,
            quote_width=0.02,
        )
        _, placed = simulator.submit_order(order)

        early = simulator.process_market_event(
            MarketEvent(100, 105, 1, "e1", "test", "XNAS", "AAPL", "trade", "sell", 100.0, 100.0),
            best_bid=100.0,
            best_ask=100.01,
            visible_depth_by_price={100.0: 0.0},
        )
        late = simulator.process_market_event(
            MarketEvent(100, 111, 2, "e2", "test", "XNAS", "AAPL", "trade", "sell", 100.0, 100.0),
            best_bid=100.0,
            best_ask=100.01,
            visible_depth_by_price={100.0: 0.0},
        )

        self.assertEqual(placed.ack_timestamp, 111)
        self.assertEqual(len(early), 0)
        self.assertEqual(len(late), 1)
