from __future__ import annotations

import unittest

from hft.live.market_data import AlpacaEquityMarketDataAdapter


class LiveMarketDataAdapterTest(unittest.TestCase):
    def test_feed_health_accepts_top_level_snapshot_payload(self) -> None:
        class StubAdapter(AlpacaEquityMarketDataAdapter):
            def _request_snapshots(self, symbols):
                from hft.live.http import HttpJsonResponse

                return HttpJsonResponse(
                    reachable=True,
                    status_code=200,
                    payload={"AAPL": {"latestQuote": {"bp": 100.0, "ap": 100.02}}},
                    message=None,
                )

        adapter = StubAdapter(api_key_id="key", api_secret_key="secret")
        health = adapter.check_feed(["AAPL"])

        self.assertTrue(health.ok)
        self.assertEqual(health.symbol_count, 1)

    def test_snapshot_payload_becomes_ordered_bbo_and_trade_events(self) -> None:
        adapter = AlpacaEquityMarketDataAdapter(api_key_id="key", api_secret_key="secret")
        events = adapter._parse_snapshot_payload(
            {
                "AAPL": {
                    "latestQuote": {
                        "bp": 100.00,
                        "bs": 10,
                        "ap": 100.02,
                        "as": 12,
                        "bx": "XNAS",
                        "ax": "XNAS",
                        "t": "2026-04-24T14:00:00Z",
                    },
                    "latestTrade": {
                        "p": 100.02,
                        "s": 5,
                        "x": "XNAS",
                        "i": "trade-1",
                        "t": "2026-04-24T14:00:00Z",
                    },
                }
            },
            fetched_at_ns=1_746_100_000_000_000_000,
        )

        self.assertEqual([event.event_type for event in events], ["bbo", "bbo", "trade"])
        self.assertEqual([event.side for event in events[:2]], ["buy", "sell"])
        self.assertEqual(events[-1].side, "buy")
        self.assertEqual(events[-1].symbol, "AAPL")


if __name__ == "__main__":
    unittest.main()
