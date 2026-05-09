from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.schemas import InternalBrokerPaperOrderRequest
from backend.services import broker_balance_service, internal_broker_router_service, options_automation_service
from backend.services.internal_broker_router_service import InternalPaperBrokerRouterService


def _settings(**overrides):
    values = {
        "broker_mode": "internal_paper",
        "paper_broker_provider": "internal_paper",
        "options_broker_provider": "internal",
        "options_data_provider": "free_delayed",
        "licensed_realtime_options_data": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class InternalPaperBrokerRouterServiceTests(unittest.TestCase):
    def _service(self) -> InternalPaperBrokerRouterService:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        return InternalPaperBrokerRouterService(state_dir=Path(tmpdir.name))

    def _submit(self, service: InternalPaperBrokerRouterService, **overrides):
        payload = {
            "symbol": "SPY",
            "instrument_type": "equity",
            "side": "buy",
            "quantity": 2,
            "order_type": "limit",
            "limit_price": 500.0,
            "session": "regular",
            "execution_mode": "paper",
            "idempotency_key": "order-1",
        }
        payload.update(overrides)
        return service.submit_order(InternalBrokerPaperOrderRequest(**payload))

    def test_equity_paper_intent_routes_to_internal_simulator(self) -> None:
        service = self._service()
        result = self._submit(service)

        self.assertTrue(result["accepted"])
        self.assertEqual(result["route"]["broker_target"], "internal_paper")
        self.assertEqual(result["route"]["route_kind"], "internal_simulator")
        self.assertEqual(result["route"]["execution_venue"], "internal_simulator")
        self.assertEqual(result["order"]["state"], "acknowledged")

    def test_option_paper_intent_routes_to_internal_simulator(self) -> None:
        service = self._service()
        result = self._submit(
            service,
            symbol="SPY260515C00500000",
            instrument_type="listed_option",
            quantity=1,
            limit_price=1.25,
            idempotency_key="option-order-1",
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["route"]["broker_target"], "internal_paper")
        self.assertEqual(result["route"]["route_kind"], "internal_simulator")

    def test_external_credentials_do_not_change_internal_route(self) -> None:
        service = self._service()
        result = self._submit(service)

        self.assertTrue(result["accepted"])
        self.assertEqual(result["route"]["broker_target"], "internal_paper")
        self.assertEqual(result["route"]["route_kind"], "internal_simulator")

    def test_live_intent_is_rejected_in_v1(self) -> None:
        service = self._service()
        result = self._submit(service, execution_mode="live")

        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "live_routing_disabled")

    def test_duplicate_idempotency_key_does_not_duplicate_fill(self) -> None:
        service = self._service()
        first = self._submit(
            service,
            order_type="market",
            limit_price=None,
            reference_price=500.0,
            idempotency_key="duplicate-market",
        )
        second = self._submit(
            service,
            order_type="market",
            limit_price=None,
            reference_price=500.0,
            idempotency_key="duplicate-market",
        )

        self.assertTrue(first["accepted"])
        self.assertEqual(first["order"]["broker_order_id"], second["order"]["broker_order_id"])
        self.assertEqual(len(service.list_fills()), 1)
        self.assertEqual(second["order"]["filled_quantity"], 2)

    def test_extended_hours_market_order_is_rejected(self) -> None:
        service = self._service()
        result = self._submit(
            service,
            order_type="market",
            limit_price=None,
            reference_price=500.0,
            session="after_hours",
            extended_hours=True,
        )

        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "order_validation_failed")

    def test_kill_switch_blocks_new_paper_orders(self) -> None:
        service = self._service()
        service.kill_switch.trip_global("operator test")

        result = self._submit(service)

        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "global_kill_switch")

    def test_balance_rollup_is_internal_only(self) -> None:
        service = self._service()
        snapshot = service.snapshot()

        self.assertEqual(snapshot["balances"]["internal_simulated"]["equity"], 100000.0)
        self.assertEqual(snapshot["balances"]["combined_paper"]["equity"], 100000.0)
        self.assertEqual(snapshot["balances"]["combined_paper"]["cash"], 100000.0)
        self.assertEqual(snapshot["balances"]["alpaca_paper"]["status"], "disabled")
        self.assertEqual(snapshot["balances"]["tradier_paper"]["status"], "disabled")
        self.assertEqual(snapshot["broker_mode"], "internal_paper")
        self.assertEqual(snapshot["routing"]["live_routing_enabled"], False)
        self.assertFalse(snapshot["regulated_broker_dealer"])

    def test_broker_balance_snapshot_does_not_call_external_clients_in_internal_mode(self) -> None:
        with (
            patch.object(broker_balance_service, "settings", _settings()),
            patch.object(broker_balance_service, "build_alpaca_paper_client") as alpaca_client,
            patch.object(broker_balance_service, "build_tradier_paper_client") as tradier_client,
        ):
            snapshot = broker_balance_service.get_paper_broker_balance_snapshot()

        alpaca_client.assert_not_called()
        tradier_client.assert_not_called()
        self.assertEqual(snapshot["routing"]["broker_mode"], "internal_paper")
        self.assertEqual(snapshot["alpaca_paper"]["status"], "disabled")
        self.assertEqual(snapshot["tradier_paper"]["status"], "disabled")

    def test_free_delayed_options_data_blocks_automation_readiness(self) -> None:
        with patch.object(options_automation_service, "settings", _settings()):
            status = options_automation_service._derive_opra_entitlement_status(
                feed="free_delayed",
                blocked_reason=None,
            )
            reason = options_automation_service._options_data_blocked_reason(status)

        self.assertEqual(status, "licensed_realtime_options_feed_required")
        self.assertIn("Licensed real-time options data is required", reason)

    def test_service_singleton_is_thread_safe(self) -> None:
        original_service = internal_broker_router_service._SERVICE
        created: list[object] = []

        def factory() -> object:
            service = object()
            created.append(service)
            return service

        internal_broker_router_service._SERVICE = None
        try:
            with patch.object(internal_broker_router_service, "InternalPaperBrokerRouterService", side_effect=factory):
                with ThreadPoolExecutor(max_workers=8) as executor:
                    services = list(
                        executor.map(lambda _: internal_broker_router_service.get_internal_broker_router_service(), range(16))
                    )
        finally:
            internal_broker_router_service._SERVICE = original_service

        self.assertEqual(len(created), 1)
        self.assertEqual(len({id(service) for service in services}), 1)


if __name__ == "__main__":
    unittest.main()
