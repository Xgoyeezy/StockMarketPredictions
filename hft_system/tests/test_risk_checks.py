from __future__ import annotations

import unittest

from hft.execution.order_state import SimulatedOrder
from hft.risk.checks import HFTRiskEngine
from hft.risk.kill_switch import KillSwitchRegistry
from hft.risk.limits import GlobalRiskLimits, HFTLimitConfig, SymbolRiskLimits


class RiskEngineTest(unittest.TestCase):
    def build_engine(self) -> HFTRiskEngine:
        limits = HFTLimitConfig(
            global_limits=GlobalRiskLimits(max_notional_exposure=10_000.0, max_outstanding_orders=2),
            symbol_limits={"AAPL": SymbolRiskLimits(max_order_size=50.0, max_inventory=100.0)},
        )
        return HFTRiskEngine(limits=limits, kill_switches=KillSwitchRegistry())

    def test_rejects_oversized_order(self) -> None:
        engine = self.build_engine()
        order = SimulatedOrder(
            order_id="o1",
            symbol="AAPL",
            strategy_name="mm",
            side="buy",
            price=100.0,
            quantity=75.0,
            decision_timestamp=1,
            send_timestamp=1,
            exchange_receive_timestamp=1,
            ack_timestamp=1,
            quote_width=0.02,
        )
        decision = engine.validate_order(order)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "max_order_size")

    def test_symbol_kill_switch_blocks_order(self) -> None:
        engine = self.build_engine()
        engine.kill_switches.disable_symbol("AAPL", "manual disable")
        order = SimulatedOrder(
            order_id="o1",
            symbol="AAPL",
            strategy_name="mm",
            side="buy",
            price=100.0,
            quantity=10.0,
            decision_timestamp=1,
            send_timestamp=1,
            exchange_receive_timestamp=1,
            ack_timestamp=1,
            quote_width=0.02,
        )
        decision = engine.validate_order(order)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "symbol_disabled")

    def test_market_state_disabled_blocks_order(self) -> None:
        limits = HFTLimitConfig(
            global_limits=GlobalRiskLimits(max_notional_exposure=10_000.0, max_outstanding_orders=2),
            symbol_limits={"AAPL": SymbolRiskLimits(max_order_size=50.0, max_inventory=100.0, market_state_enabled=False)},
        )
        engine = HFTRiskEngine(limits=limits, kill_switches=KillSwitchRegistry())
        order = SimulatedOrder(
            order_id="o2",
            symbol="AAPL",
            strategy_name="mm",
            side="buy",
            price=100.0,
            quantity=10.0,
            decision_timestamp=1,
            send_timestamp=1,
            exchange_receive_timestamp=1,
            ack_timestamp=1,
            quote_width=0.02,
        )
        decision = engine.validate_order(order)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "market_state_disabled")

    def test_kill_switch_trips_on_fill_anomaly(self) -> None:
        engine = self.build_engine()
        engine.state.fill_anomaly_score = engine.limits.global_limits.max_fill_anomaly_score + 0.1
        decision = engine.should_kill_strategy()
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "fill_anomaly")
