from __future__ import annotations

import unittest

from hft.execution.order_state import OrderLifecycleState
from hft.live.paper_execution import AlpacaPaperExecutionAdapter, PaperOrderIntent


class PaperExecutionAdapterTest(unittest.TestCase):
    def test_submit_order_rejects_unvalidated_intent_without_network(self) -> None:
        adapter = AlpacaPaperExecutionAdapter(api_key_id="key", api_secret_key="secret")
        intent = PaperOrderIntent(
            order_id="paper-1",
            symbol="AAPL",
            strategy_name="inventory_aware_market_making",
            side="buy",
            price=100.0,
            quantity=10.0,
            decision_timestamp=1,
            quote_width=0.02,
        )

        report = adapter.submit_order(intent)

        self.assertFalse(report.accepted)
        self.assertEqual(report.state, OrderLifecycleState.REJECTED)
        self.assertIn("risk_not_passed", report.reason)

    def test_report_mapping_uses_cached_intent_fields(self) -> None:
        adapter = AlpacaPaperExecutionAdapter(api_key_id="key", api_secret_key="secret")
        intent = PaperOrderIntent(
            order_id="paper-2",
            symbol="AAPL",
            strategy_name="inventory_aware_market_making",
            side="buy",
            price=100.0,
            quantity=10.0,
            decision_timestamp=10,
            quote_width=0.02,
            risk_checked=True,
            risk_reason="allowed",
        )

        report = adapter._report_from_alpaca_order(
            {
                "id": "broker-1",
                "client_order_id": "paper-2",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "10",
                "filled_qty": "10",
                "limit_price": "100.0",
                "filled_avg_price": "99.99",
                "status": "filled",
                "submitted_at": "2026-04-24T14:00:00Z",
                "updated_at": "2026-04-24T14:00:01Z",
                "filled_at": "2026-04-24T14:00:01Z",
            },
            intent=intent,
            sent_ns=11,
        )

        self.assertTrue(report.accepted)
        self.assertEqual(report.state, OrderLifecycleState.FILLED)
        self.assertEqual(report.strategy_name, "inventory_aware_market_making")
        self.assertEqual(report.average_fill_price, 99.99)


if __name__ == "__main__":
    unittest.main()
