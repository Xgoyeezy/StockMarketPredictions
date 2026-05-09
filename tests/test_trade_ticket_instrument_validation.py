import unittest

import pandas as pd

from backend.schemas import OpenTradeRequest
from backend.services.exceptions import ValidationServiceError
from backend.services.execution.mappers import build_alpaca_option_order_payload
from backend.services.trade_service import (
    coerce_preview_trade_request,
    _apply_equity_plan_override,
    _build_capital_preservation_snapshot,
    _build_equity_position_preview,
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

    def test_chart_range_horizon_is_clamped_for_trade_ticket(self):
        request = self._option_request(horizon=600)

        self.assertEqual(request.horizon, 50)

    def test_preview_payload_sanitizes_stale_draft_values(self):
        request = coerce_preview_trade_request(
            {
                "ticker": "spy",
                "interval": "bad",
                "horizon": 600,
                "instrument_type": "stock",
                "live_price": 0,
                "account_size": 0,
                "risk_percent": 250,
                "order_type": "bad",
                "time_in_force": "DAY + AH",
                "limit_price": 0,
                "max_open_positions": 0,
            }
        )

        self.assertEqual(request.ticker, "SPY")
        self.assertEqual(request.interval, "5m")
        self.assertEqual(request.horizon, 50)
        self.assertEqual(request.instrument_type, "equity")
        self.assertIsNone(request.live_price)
        self.assertEqual(request.account_size, 100000.0)
        self.assertEqual(request.risk_percent, 100.0)
        self.assertEqual(request.order_type, "market")
        self.assertEqual(request.time_in_force, "day_ext")
        self.assertIsNone(request.limit_price)
        self.assertIsNone(request.max_open_positions)

    def test_equity_position_preview_caps_whole_share_sizing_by_ticket_notional(self):
        report = {
            "trade_decision": "VALID TRADE",
            "forecast": {"regime_strength_score": 0.7},
            "option_plan": {
                "invalidation_price": 99.0,
                "expected_underlying_target": 102.0,
            },
        }

        position = _build_equity_position_preview(
            report,
            live_price=100.0,
            account_size=100000.0,
            risk_percent=1.0,
            fractional_shares_only=False,
            max_notional_per_trade=7500.0,
        )

        self.assertEqual(position["suggested_contracts"], 75.0)
        self.assertEqual(position["total_position_cost"], 7500.0)
        self.assertTrue(position["affordable"])

    def test_equity_plan_override_aligns_direction_with_supplied_target_and_stop(self):
        request = OpenTradeRequest(
            ticker="SPY",
            instrument_type="equity",
            live_price=100.0,
            target_price=103.0,
            invalidation_price=98.0,
        )
        report = {
            "verdict": "BEARISH",
            "trade_decision": "PASS",
            "reject_reason": "Conviction too weak.",
            "option_plan": {},
        }

        updated = _apply_equity_plan_override(report, request)

        self.assertEqual(updated["verdict"], "BULLISH")
        self.assertEqual(updated["trade_decision"], "VALID TRADE")
        self.assertEqual(updated["reject_reason"], "")
        self.assertEqual(updated["option_plan"]["directional_thesis"], "long")
        self.assertEqual(updated["option_plan"]["expected_underlying_target"], 103.0)
        self.assertEqual(updated["option_plan"]["invalidation_price"], 98.0)

    def test_capital_preservation_loss_streak_resets_each_trading_day(self):
        today_noon_et = pd.Timestamp.now(tz="America/New_York").normalize() + pd.Timedelta(hours=12)
        yesterday_et = today_noon_et - pd.Timedelta(days=1)
        two_days_ago_et = today_noon_et - pd.Timedelta(days=2)

        closed_trades = pd.DataFrame(
            [
                {"ticker": "SPY", "closed_at": two_days_ago_et.tz_convert("UTC").isoformat(), "realized_pnl": -20.0},
                {"ticker": "QQQ", "closed_at": yesterday_et.tz_convert("UTC").isoformat(), "realized_pnl": -15.0},
                {"ticker": "AAPL", "closed_at": today_noon_et.tz_convert("UTC").isoformat(), "realized_pnl": 0.0},
            ]
        )

        snapshot = _build_capital_preservation_snapshot(
            pd.DataFrame(),
            pd.DataFrame(),
            closed_trades,
        )

        self.assertEqual(snapshot["consecutive_losses"], 0)
        self.assertEqual(snapshot["today_closed_trades"], 1)

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
