from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.schemas import OpenTradeRequest
from backend.services.execution.legitimate_brokerage_paper_adapter import LegitimateBrokeragePaperExecutionAdapter
from backend.services.execution.provider_registry import get_execution_adapter_for
from backend.services.execution.types import SubmitOrderResult


def _settings(**overrides):
    values = {
        "legitimate_brokerage_api_url": "http://127.0.0.1:8001",
        "legitimate_brokerage_api_key": "test-key",
        "legitimate_brokerage_account_id": "acc_paper",
        "legitimate_brokerage_timeout_seconds": 2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FallbackAdapter:
    adapter_name = "internal_paper"

    def submit_order(self, **kwargs):
        return SubmitOrderResult(
            position_opened=True,
            record={"fallback": True},
            pending_order=None,
            broker_name="internal_paper",
            broker_order_id="internal-1",
            broker_status="filled",
            broker_response={"fallback": True},
        )


class LegitimateBrokeragePaperAdapterTests(unittest.TestCase):
    def test_provider_registry_resolves_legitimate_brokerage_paper(self) -> None:
        adapter = get_execution_adapter_for("legitimate_brokerage_paper")

        self.assertEqual(adapter.adapter_name, "legitimate_brokerage_paper")

    def test_equity_payload_maps_to_internal_paper_brokerage_order(self) -> None:
        request = OpenTradeRequest(
            ticker="aapl",
            instrument_type="equity",
            requested_quantity=2,
            order_type="limit",
            limit_price=189.25,
            execution_intent="broker_paper",
        )

        with patch("backend.services.execution.legitimate_brokerage_paper_adapter.settings", _settings()):
            payload = LegitimateBrokeragePaperExecutionAdapter.build_order_payload(
                request=request,
                position={"suggested_contracts": 2},
                order_id="local-order-1",
            )

        self.assertEqual(payload["account_id"], "acc_paper")
        self.assertEqual(payload["client_order_id"], "local-order-1")
        self.assertEqual(payload["symbol"], "AAPL")
        self.assertEqual(payload["asset_class"], "equity")
        self.assertEqual(payload["side"], "buy")
        self.assertEqual(payload["order_type"], "limit")
        self.assertEqual(payload["execution_mode"], "paper")
        self.assertEqual(payload["execution_route"], "internal_paper")

    def test_submit_order_records_filled_brokerage_order(self) -> None:
        adapter = LegitimateBrokeragePaperExecutionAdapter()
        request = OpenTradeRequest(
            ticker="AAPL",
            instrument_type="equity",
            requested_quantity=1,
            order_type="market",
            execution_intent="broker_paper",
        )
        broker_order = {
            "id": "ord_brokerage_1",
            "client_order_id": "local-order-1",
            "status": "filled",
            "quantity": 1,
            "average_fill_price": 190.0,
        }

        with (
            patch("backend.services.execution.legitimate_brokerage_paper_adapter.settings", _settings()),
            patch.object(adapter, "_request", return_value=broker_order) as request_call,
            patch("backend.services.execution.legitimate_brokerage_paper_adapter.sdm.open_trade_record", return_value={"order_id": "local-order-1"}),
            patch("backend.services.execution.legitimate_brokerage_paper_adapter.sdm.append_open_trade") as append_open,
        ):
            result = adapter.submit_order(
                request=request,
                report={"ticker": "AAPL"},
                live_price=190.0,
                position={"suggested_contracts": 1},
                trade_id="trade-1",
                order_id="local-order-1",
                order_ticket={"order_id": "local-order-1", "instrument_type": "equity"},
            )

        self.assertTrue(result.position_opened)
        self.assertEqual(result.broker_name, "legitimate_brokerage_paper")
        self.assertEqual(result.broker_order_id, "ord_brokerage_1")
        request_call.assert_called_once()
        append_open.assert_called_once()

    def test_connection_failure_falls_back_to_internal_paper(self) -> None:
        adapter = LegitimateBrokeragePaperExecutionAdapter(fallback=_FallbackAdapter())
        request = OpenTradeRequest(
            ticker="AAPL",
            instrument_type="equity",
            requested_quantity=1,
            order_type="market",
            execution_intent="broker_paper",
        )

        with (
            patch("backend.services.execution.legitimate_brokerage_paper_adapter.settings", _settings()),
            patch.object(adapter, "_request", side_effect=ConnectionError("down")),
        ):
            result = adapter.submit_order(
                request=request,
                report={"ticker": "AAPL"},
                live_price=190.0,
                position={"suggested_contracts": 1},
                trade_id="trade-1",
                order_id="local-order-1",
                order_ticket={"order_id": "local-order-1", "instrument_type": "equity"},
            )

        self.assertEqual(result.broker_name, "internal_paper")
        self.assertEqual(result.broker_order_id, "internal-1")


if __name__ == "__main__":
    unittest.main()
