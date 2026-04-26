import unittest

from backend.schemas import OpenTradeRequest
from backend.services.exceptions import ValidationServiceError
from backend.services.execution.mappers import build_alpaca_option_order_payload
from backend.services.trade_service import (
    _normalize_option_strategy,
    _validate_instrument_strategy_request,
)


class TradeTicketInstrumentValidationTests(unittest.TestCase):
    def _option_request(self, **overrides):
        payload = {
            "ticker": "SPY",
            "instrument_type": "listed_option",
            "option_strategy": "long_option",
            "option_right": "call",
            "contract_symbol": "SPY260515C00500000",
            "contract_expiration": "2026-05-15",
            "contract_strike": 500,
            "order_type": "limit",
            "time_in_force": "day",
            "limit_price": 1.25,
        }
        payload.update(overrides)
        return OpenTradeRequest(**payload)

    def test_long_option_payload_maps_to_alpaca_single_leg_buy(self):
        request = self._option_request()

        _validate_instrument_strategy_request(request)
        payload = build_alpaca_option_order_payload(
            request,
            contract_symbol=request.contract_symbol,
            quantity=2,
            client_order_id="ticket-test",
            side="buy",
        )

        self.assertEqual(payload["symbol"], "SPY260515C00500000")
        self.assertEqual(payload["qty"], "2")
        self.assertEqual(payload["side"], "buy")
        self.assertEqual(payload["type"], "limit")
        self.assertEqual(payload["time_in_force"], "day")
        self.assertEqual(payload["limit_price"], "1.25")

    def test_single_leg_alias_stays_backward_compatible(self):
        self.assertEqual(_normalize_option_strategy("single_leg"), "long_option")
        request = self._option_request(option_strategy="single_leg")

        _validate_instrument_strategy_request(request)

    def test_short_premium_is_review_only(self):
        request = self._option_request(option_strategy="short_premium", broker_side="sell")

        with self.assertRaisesRegex(ValidationServiceError, "Short premium"):
            _validate_instrument_strategy_request(request)
        with self.assertRaisesRegex(ValidationServiceError, "long-option single-leg"):
            build_alpaca_option_order_payload(
                request,
                contract_symbol=request.contract_symbol,
                quantity=1,
                side="sell",
            )

    def test_vertical_spread_requires_multi_leg_routing(self):
        request = self._option_request(option_strategy="vertical_spread")

        with self.assertRaisesRegex(ValidationServiceError, "Vertical spread"):
            _validate_instrument_strategy_request(request)
        with self.assertRaisesRegex(ValidationServiceError, "long-option single-leg"):
            build_alpaca_option_order_payload(
                request,
                contract_symbol=request.contract_symbol,
                quantity=1,
                side="buy",
            )

    def test_long_option_sell_side_is_rejected(self):
        request = self._option_request(broker_side="sell")

        with self.assertRaisesRegex(ValidationServiceError, "buy-to-open"):
            _validate_instrument_strategy_request(request)
        with self.assertRaisesRegex(ValidationServiceError, "buy-to-open"):
            build_alpaca_option_order_payload(
                request,
                contract_symbol=request.contract_symbol,
                quantity=1,
                side="sell",
            )

    def test_long_option_sell_to_close_payload_is_allowed_for_close_path(self):
        request = self._option_request()

        payload = build_alpaca_option_order_payload(
            request,
            contract_symbol=request.contract_symbol,
            quantity=1,
            side="sell",
            position_effect="close",
        )

        self.assertEqual(payload["symbol"], "SPY260515C00500000")
        self.assertEqual(payload["qty"], "1")
        self.assertEqual(payload["side"], "sell")
        self.assertEqual(payload["type"], "limit")
        self.assertEqual(payload["time_in_force"], "day")

    def test_long_option_buy_to_close_is_rejected(self):
        request = self._option_request()

        with self.assertRaisesRegex(ValidationServiceError, "buy-to-open and sell-to-close"):
            build_alpaca_option_order_payload(
                request,
                contract_symbol=request.contract_symbol,
                quantity=1,
                side="buy",
                position_effect="close",
            )


if __name__ == "__main__":
    unittest.main()
