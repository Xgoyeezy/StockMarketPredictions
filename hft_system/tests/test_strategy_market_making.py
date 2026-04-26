from __future__ import annotations

import unittest

from hft.features.microstructure import MicrostructureFeatureSnapshot
from hft.inventory.inventory import InventoryState
from hft.strategies.cross_venue_arbitrage import CrossVenueArbitrageStrategy
from hft.strategies.market_making import InventoryAwareMarketMakingStrategy


class MarketMakingStrategyTest(unittest.TestCase):
    def test_inventory_skew_reduces_buy_aggression_and_can_disable_one_side(self) -> None:
        strategy = InventoryAwareMarketMakingStrategy()
        features = MicrostructureFeatureSnapshot(
            timestamp_ns=1,
            symbol="AAPL",
            spread=0.02,
            mid_price=100.0,
            mid_price_movement=0.0,
            order_book_imbalance=0.1,
            queue_imbalance=0.1,
            trade_imbalance=0.0,
            short_term_volatility=0.001,
            quote_update_rate=5.0,
            cancel_rate=1.0,
            trade_arrival_rate=2.0,
            depth_imbalance=0.1,
            price_impact_estimate=0.01,
            adverse_selection_estimate=0.0,
            metadata={},
        )
        flat = InventoryState("AAPL", 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0)
        long_inventory = InventoryState("AAPL", 90.0, 100.0, 0.0, 0.0, 0, 0.9, -90.0)

        flat_quotes = strategy.generate_quotes(features, flat)
        long_quotes = strategy.generate_quotes(features, long_inventory)

        self.assertEqual(len(flat_quotes), 2)
        self.assertEqual(len(long_quotes), 1)
        self.assertEqual(long_quotes[0].side, "sell")
        self.assertLess(flat_quotes[1].price, 100.5)


class CrossVenueArbitrageStrategyTest(unittest.TestCase):
    def test_reports_theoretical_and_executable_edge_with_haircuts(self) -> None:
        strategy = CrossVenueArbitrageStrategy(enabled=True, venue_fee_bps=0.5, latency_haircut_bps=0.75)
        opportunity = strategy.analyze_opportunity(
            {
                "XNYS": {"symbol": "AAPL", "bid": 100.10, "ask": 100.12, "timestamp_ns": 1_000},
                "BATS": {"symbol": "AAPL", "bid": 100.20, "ask": 100.22, "timestamp_ns": 1_000},
            }
        )

        self.assertIsNotNone(opportunity)
        assert opportunity is not None
        self.assertEqual(opportunity.buy_venue, "XNYS")
        self.assertEqual(opportunity.sell_venue, "BATS")
        self.assertGreater(opportunity.theoretical_edge_bps, opportunity.executable_edge_bps)
        self.assertAlmostEqual(opportunity.fee_haircut_bps, 1.0)
