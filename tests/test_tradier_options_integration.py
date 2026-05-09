from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from backend.schemas import OpenTradeRequest


def _settings(**overrides):
    values = {
        "broker_mode": "external_brokers",
        "paper_broker_provider": "broker_paper",
        "options_broker_provider": "tradier",
        "options_data_provider": "tradier",
        "options_quote_max_age_seconds": 30,
        "options_max_spread_pct": 0.15,
        "options_min_volume": 25,
        "options_min_open_interest": 100,
        "options_min_dte_days": 7,
        "options_max_dte_days": 45,
        "options_max_premium_risk_pct": 1.0,
        "options_max_open_positions": 4,
        "tradier_paper_token": "paper-token",
        "tradier_paper_account_id": "PAPER123",
        "tradier_live_token": "live-token",
        "tradier_live_account_id": "LIVE123",
        "alpaca_api_key_id": "alpaca-key",
        "alpaca_api_secret_key": "alpaca-secret",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TradierOptionsIntegrationTests(unittest.TestCase):
    def test_tradier_quote_normalization_can_satisfy_options_gate(self) -> None:
        from backend.services import options_automation_service as service
        from backend.services.execution.tradier_client import normalize_tradier_option_contract

        scan_started_at = datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc)
        row = normalize_tradier_option_contract(
            {
                "symbol": "SPY260515C00500000",
                "option_type": "call",
                "expiration_date": "2026-05-15",
                "strike": 500,
                "bid": 1.20,
                "ask": 1.28,
                "last": 1.24,
                "volume": 350,
                "open_interest": 1200,
                "bid_date": int(scan_started_at.timestamp() * 1000),
            }
        )

        with patch.object(service, "settings", _settings()):
            candidate = service._extract_contract_candidate(
                ticker="SPY",
                row=row,
                underlying_price=500.0,
                required_right="call",
                feed="tradier_realtime",
                scan_started_at=scan_started_at,
            )

        self.assertIsNotNone(candidate)
        self.assertTrue(candidate["ready_to_execute"])
        self.assertEqual(candidate["source"], "tradier_options_chain")
        self.assertEqual(candidate["bid"], 1.20)
        self.assertEqual(candidate["ask"], 1.28)
        self.assertEqual(candidate["volume"], 350)
        self.assertEqual(candidate["open_interest"], 1200)

    def test_broker_paper_routes_equities_to_alpaca_and_options_to_tradier(self) -> None:
        from backend.services import trade_service

        def fake_adapter(name):
            return SimpleNamespace(adapter_name=name)

        with (
            patch.object(trade_service, "settings", _settings(options_broker_provider="tradier")),
            patch.object(trade_service, "get_execution_adapter_for", side_effect=fake_adapter) as adapter_mock,
        ):
            option_adapter, option_name, _ = trade_service._resolve_execution_adapter_for_open_request(
                OpenTradeRequest(
                    ticker="SPY",
                    instrument_type="listed_option",
                    execution_intent="broker_paper",
                    contract_symbol="SPY260515C00500000",
                    option_strategy="long_option",
                    option_right="call",
                    contract_expiration="2026-05-15",
                    contract_strike=500,
                    order_type="limit",
                    time_in_force="day",
                    limit_price=1.25,
                )
            )
            equity_adapter, equity_name, _ = trade_service._resolve_execution_adapter_for_open_request(
                OpenTradeRequest(
                    ticker="SPY",
                    instrument_type="equity",
                    execution_intent="broker_paper",
                    requested_quantity=1,
                    order_type="market",
                    time_in_force="day",
                )
            )

        self.assertEqual(option_adapter.adapter_name, "tradier_paper")
        self.assertEqual(option_name, "tradier_paper")
        self.assertEqual(equity_adapter.adapter_name, "alpaca_paper")
        self.assertEqual(equity_name, "alpaca_paper")
        self.assertEqual([call.args[0] for call in adapter_mock.call_args_list], ["tradier_paper", "alpaca_paper"])

    def test_missing_tradier_credentials_block_options_readiness_only(self) -> None:
        from scripts.check_options_paper_readiness import classify_readiness

        summary = classify_readiness(
            feed="opra",
            options_data_provider="tradier",
            options_broker_provider="tradier",
            use_sandbox=False,
            paper_keys_present=True,
            tradier_live_keys_present=False,
            tradier_paper_keys_present=False,
            opra_probe={},
            indicative_probe={},
            backend_running=True,
        )

        self.assertEqual(summary["status"], "blocked")
        self.assertEqual(summary["broker_code"], "tradier_live_credentials_missing")
        self.assertIn("TRADIER_LIVE_TOKEN", summary["next_action"])

    def test_balance_rollup_handles_alpaca_tradier_and_combined(self) -> None:
        from backend.services import broker_balance_service as service

        alpaca_client = SimpleNamespace(
            get_account=lambda: {"equity": "1000", "cash": "400", "buying_power": "800"}
        )
        tradier_client = SimpleNamespace(
            get_account_balances=lambda: {
                "balances": {
                    "total_equity": "2000",
                    "total_cash": "600",
                    "margin": {"stock_buying_power": "1400", "option_buying_power": "900"},
                }
            }
        )

        with (
            patch.object(service, "settings", _settings()),
            patch.object(service, "build_alpaca_paper_client", return_value=alpaca_client),
            patch.object(service, "build_tradier_paper_client", return_value=tradier_client),
        ):
            snapshot = service.get_paper_broker_balance_snapshot()

        self.assertEqual(snapshot["alpaca_paper"]["equity"], 1000.0)
        self.assertEqual(snapshot["tradier_paper"]["option_buying_power"], 900.0)
        self.assertEqual(snapshot["combined_paper"]["equity"], 3000.0)
        self.assertEqual(snapshot["combined_paper"]["cash"], 1000.0)
        self.assertEqual(snapshot["routing"]["equities"], "alpaca")
        self.assertEqual(snapshot["routing"]["options"], "tradier")


if __name__ == "__main__":
    unittest.main()
