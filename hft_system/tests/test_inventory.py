from __future__ import annotations

import unittest

from hft.execution.order_state import SimulatedFill
from hft.inventory.inventory import InventoryManager


class InventoryManagerTest(unittest.TestCase):
    def test_fill_updates_realized_and_position(self) -> None:
        inventory = InventoryManager(symbol="AAPL", max_inventory=100.0)
        buy_fill = SimulatedFill("o1", "AAPL", "mm", "buy", 100.0, 10.0, 100)
        sell_fill = SimulatedFill("o2", "AAPL", "mm", "sell", 101.0, 5.0, 200)

        state_after_buy = inventory.update_from_fill(buy_fill)
        self.assertEqual(state_after_buy.position, 10.0)
        self.assertEqual(state_after_buy.realized_pnl, 0.0)

        state_after_sell = inventory.update_from_fill(sell_fill)
        self.assertEqual(state_after_sell.position, 5.0)
        self.assertAlmostEqual(state_after_sell.realized_pnl, 5.0)
