from __future__ import annotations

import unittest

import pandas as pd

from backend.services.risk_control_service import (
    DEFAULT_RISK_CONTROL_SETTINGS,
    effective_risk_percent,
    effective_total_notional_cap,
    evaluate_candidate_risk_controls,
    normalize_risk_control_settings,
)


class RiskControlServiceTests(unittest.TestCase):
    def test_default_settings_match_ranked_entry_profile(self) -> None:
        settings = normalize_risk_control_settings({})

        self.assertEqual(settings["max_gross_leverage"], 1.5)
        self.assertEqual(settings["max_single_position_pct"], 12.0)
        self.assertEqual(settings["max_correlated_bucket_pct"], 35.0)
        self.assertEqual(settings["min_edge_to_cost_ratio"], 2.5)
        self.assertTrue(settings["allow_pyramiding"])
        self.assertTrue(settings["require_liquidity_fields"])

    def test_effective_total_notional_uses_tighter_configured_cap(self) -> None:
        settings = {
            "account_size": 100000.0,
            "max_total_open_notional": 50000.0,
            "max_gross_leverage": 1.0,
        }

        self.assertEqual(effective_total_notional_cap(settings, equity=100000.0), 50000.0)

    def test_effective_total_notional_uses_leverage_when_tighter(self) -> None:
        settings = {
            "account_size": 100000.0,
            "max_total_open_notional": 500000.0,
            "max_gross_leverage": 1.0,
        }

        self.assertEqual(effective_total_notional_cap(settings, equity=95000.0), 95000.0)

    def test_drawdown_cut_reduces_risk_percent(self) -> None:
        settings = {
            "risk_percent": 0.50,
            "drawdown_size_cut_pct": 5.0,
            "risk_cut_multiplier": 0.5,
        }

        self.assertEqual(effective_risk_percent(settings, 4.9), 0.50)
        self.assertEqual(effective_risk_percent(settings, 5.0), 0.25)

    def test_candidate_blocks_single_position_cap(self) -> None:
        settings = {
            "account_size": 100000.0,
            "max_total_open_notional": 100000.0,
            "max_gross_leverage": 1.0,
            "max_notional_per_trade": 25000.0,
            "max_single_position_pct": 10.0,
            "max_correlated_bucket_pct": 30.0,
            "order_type": "market",
            **normalize_risk_control_settings({}),
        }
        candidate = {
            "ticker": "AAPL",
            "total_position_cost": 25000.0,
            "live_price": 200.0,
            "spread_bps": 8.0,
            "average_dollar_volume": 5_000_000_000.0,
            "edge_to_cost_ratio": 3.0,
        }
        session = {"phase": "regular_session", "minutes_to_close": 120}

        decision = evaluate_candidate_risk_controls(
            candidate,
            settings_state=settings,
            owned_open=pd.DataFrame(),
            owned_pending=pd.DataFrame(),
            session=session,
            current_equity=100000.0,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "single_position_cap")

    def test_candidate_blocks_correlated_bucket_cap(self) -> None:
        settings = {
            "account_size": 100000.0,
            "max_total_open_notional": 100000.0,
            "max_gross_leverage": 1.0,
            "max_notional_per_trade": 25000.0,
            "max_single_position_pct": 30.0,
            "max_correlated_bucket_pct": 25.0,
            "order_type": "limit",
            **normalize_risk_control_settings({}),
        }
        settings["max_correlated_bucket_pct"] = 25.0
        owned_open = pd.DataFrame([
            {"ticker": "NVDA", "position_cost": 20000.0},
        ])
        candidate = {
            "ticker": "AMD",
            "total_position_cost": 10000.0,
            "live_price": 250.0,
            "spread_bps": 9.0,
            "average_dollar_volume": 3_000_000_000.0,
            "edge_to_cost_ratio": 3.1,
        }
        session = {"phase": "regular_session", "minutes_to_close": 120}

        decision = evaluate_candidate_risk_controls(
            candidate,
            settings_state=settings,
            owned_open=owned_open,
            owned_pending=pd.DataFrame(),
            session=session,
            current_equity=100000.0,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "correlated_bucket_cap")

    def test_candidate_blocks_order_adv_cap(self) -> None:
        settings = {
            "account_size": 100000.0,
            "max_total_open_notional": 100000.0,
            "max_gross_leverage": 1.0,
            "max_notional_per_trade": 10000.0,
            "max_single_position_pct": 20.0,
            "max_correlated_bucket_pct": 30.0,
            "max_order_adv_pct": 1.0,
            "order_type": "market",
            **normalize_risk_control_settings({}),
        }
        settings["max_notional_per_trade"] = 25000.0
        settings["max_single_position_pct"] = 30.0
        settings["max_order_adv_pct"] = 1.0
        candidate = {
            "ticker": "MSFT",
            "total_position_cost": 25000.0,
            "live_price": 400.0,
            "spread_bps": 7.0,
            "average_dollar_volume": 2000000.0,
            "edge_to_cost_ratio": 3.2,
        }
        session = {"phase": "regular_session", "minutes_to_close": 120}

        decision = evaluate_candidate_risk_controls(
            candidate,
            settings_state=settings,
            owned_open=pd.DataFrame(),
            owned_pending=pd.DataFrame(),
            session=session,
            current_equity=100000.0,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "order_adv_cap")

    def test_candidate_blocks_intraday_volume_cap(self) -> None:
        settings = {
            "account_size": 100000.0,
            "max_total_open_notional": 100000.0,
            "max_gross_leverage": 1.0,
            "max_notional_per_trade": 10000.0,
            "max_single_position_pct": 20.0,
            "max_correlated_bucket_pct": 30.0,
            "max_order_adv_pct": 100.0,
            "max_intraday_volume_pct": 5.0,
            "order_type": "market",
            **normalize_risk_control_settings({}),
        }
        settings["max_order_adv_pct"] = 100.0
        candidate = {
            "ticker": "MSFT",
            "total_position_cost": 10000.0,
            "live_price": 400.0,
            "spread_bps": 7.0,
            "average_dollar_volume": 100000000.0,
            "average_1m_dollar_volume": 100000.0,
            "edge_to_cost_ratio": 3.0,
        }
        session = {"phase": "regular_session", "minutes_to_close": 120}

        decision = evaluate_candidate_risk_controls(
            candidate,
            settings_state=settings,
            owned_open=pd.DataFrame(),
            owned_pending=pd.DataFrame(),
            session=session,
            current_equity=100000.0,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "intraday_volume_cap")

    def test_candidate_blocks_edge_to_cost_floor(self) -> None:
        settings = {
            "account_size": 100000.0,
            "max_total_open_notional": 100000.0,
            "max_gross_leverage": DEFAULT_RISK_CONTROL_SETTINGS["max_gross_leverage"],
            "max_notional_per_trade": 10000.0,
            "order_type": "market",
            **normalize_risk_control_settings({}),
        }
        candidate = {
            "ticker": "AAPL",
            "total_position_cost": 5000.0,
            "live_price": 200.0,
            "spread_bps": 9.0,
            "average_dollar_volume": 2_000_000_000.0,
            "edge_to_cost_ratio": 1.9,
        }

        decision = evaluate_candidate_risk_controls(
            candidate,
            settings_state=settings,
            owned_open=pd.DataFrame(),
            owned_pending=pd.DataFrame(),
            session={"phase": "regular_session", "minutes_to_close": 120},
            current_equity=100000.0,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "edge_cost_ratio_too_low")


if __name__ == "__main__":
    unittest.main()
