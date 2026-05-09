from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.exceptions import ForbiddenError
from backend.services.execution.router import ExecutionRouter


class ExecutionRouterTests(unittest.TestCase):
    def test_broker_execution_blocked_never_calls_adapter(self) -> None:
        router = ExecutionRouter()
        request = SimpleNamespace(execution_intent="broker_paper", instrument_type="equity")

        with patch("backend.services.execution.router.get_billing_entitlements", return_value={"items": []}), patch(
            "backend.services.execution.router.get_execution_adapter_for"
        ) as get_adapter:
            with self.assertRaises(ForbiddenError):
                router.resolve_for_open_trade(request=request, db=object(), current_user=object())
            get_adapter.assert_not_called()

    def test_options_routes_to_tradier_when_configured(self) -> None:
        router = ExecutionRouter()
        request = SimpleNamespace(execution_intent="broker_paper", instrument_type="listed_option")

        fake_settings = SimpleNamespace(
            broker_mode="broker_paper",
            paper_broker_provider="alpaca_paper",
            options_broker_provider="tradier",
            execution_adapter="desk",
            alpaca_live_trading_enabled=False,
        )

        with patch("backend.services.execution.router.settings", fake_settings), patch(
            "backend.services.execution.router.get_billing_entitlements",
            return_value={"items": [{"key": "broker_execution", "enabled": True}]},
        ), patch("backend.services.execution.router.get_execution_adapter_for") as get_adapter:
            get_adapter.return_value = SimpleNamespace(adapter_name="tradier_paper")
            adapter, decision = router.resolve_for_open_trade(request=request, db=object(), current_user=object())
            self.assertEqual(adapter.adapter_name, "tradier_paper")
            self.assertEqual(decision.adapter_key, "tradier_paper")

    def test_options_routes_to_internal_when_configured(self) -> None:
        router = ExecutionRouter()
        request = SimpleNamespace(execution_intent="broker_paper", instrument_type="listed_option")

        fake_settings = SimpleNamespace(
            broker_mode="broker_paper",
            paper_broker_provider="internal_paper",
            options_broker_provider="internal",
            execution_adapter="desk",
            alpaca_live_trading_enabled=False,
        )

        with patch("backend.services.execution.router.settings", fake_settings), patch(
            "backend.services.execution.router.get_billing_entitlements",
            return_value={"items": [{"key": "broker_execution", "enabled": True}]},
        ), patch("backend.services.execution.router.get_execution_adapter_for") as get_adapter:
            get_adapter.return_value = SimpleNamespace(adapter_name="internal_paper")
            adapter, decision = router.resolve_for_open_trade(request=request, db=object(), current_user=object())
            self.assertEqual(adapter.adapter_name, "internal_paper")
            self.assertEqual(decision.adapter_key, "internal_paper")
