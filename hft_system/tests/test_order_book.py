from __future__ import annotations

import unittest

from hft.market_data.schemas import MarketEvent
from hft.order_book.book import OrderBook


class OrderBookTest(unittest.TestCase):
    def test_reconstructs_book_and_trade_flow(self) -> None:
        book = OrderBook("AAPL")
        events = [
            MarketEvent(1, 2, 1, "e1", "test", "XNAS", "AAPL", "add", "buy", 100.0, 10.0),
            MarketEvent(1, 2, 2, "e2", "test", "XNAS", "AAPL", "add", "sell", 101.0, 12.0),
            MarketEvent(1, 2, 3, "e3", "test", "XNAS", "AAPL", "add", "buy", 99.5, 20.0),
            MarketEvent(2, 3, 4, "e4", "test", "XNAS", "AAPL", "trade", "sell", 100.0, 5.0, trade_id="t1"),
        ]
        for event in events:
            book.apply_event(event)

        self.assertEqual(book.get_top_of_book(), (100.0, 101.0))
        self.assertAlmostEqual(book.get_mid_price(), 100.5)
        self.assertAlmostEqual(book.get_spread(), 1.0)
        depth = book.get_depth(2)
        self.assertEqual(depth["bids"][0].size, 5.0)
        self.assertEqual(len(book.snapshot().recent_trade_flow), 1)
