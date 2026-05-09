from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from backend.services.alpaca_paper_readiness_service import (
    build_alpaca_paper_readiness_snapshot,
    classify_alpaca_error,
)


class AlpacaPaperReadinessServiceTests(unittest.TestCase):
    def test_classifies_retryable_and_non_retryable_errors(self) -> None:
        self.assertEqual(classify_alpaca_error(429)["category"], "rate_limited")
        self.assertTrue(classify_alpaca_error(503)["retryable"])
        self.assertFalse(classify_alpaca_error(403)["retryable"])
        self.assertEqual(classify_alpaca_error(message="insufficient buying power")["category"], "account_capacity")

    def test_readiness_snapshot_never_exposes_secrets_and_reports_local_books(self) -> None:
        pending = pd.DataFrame(
            [
                {"order_id": "ord-1", "broker_order_id": "brk-1", "broker_status": "new", "broker_client_order_id": "client-1"},
                {"order_id": "ord-2", "broker_status": "rejected", "broker_client_order_id": "client-2", "rejection_reason": "market closed"},
            ]
        )
        open_trades = pd.DataFrame([{"order_id": "ord-3", "broker_status": "filled", "broker_client_order_id": "client-3"}])
        closed = pd.DataFrame([{"order_id": "ord-4", "broker_status": "filled", "broker_client_order_id": "client-4"}])

        with (
            patch("backend.services.alpaca_paper_readiness_service.sdm.read_pending_orders", return_value=pending),
            patch("backend.services.alpaca_paper_readiness_service.sdm.read_open_trades", return_value=open_trades),
            patch("backend.services.alpaca_paper_readiness_service.sdm.read_closed_trades", return_value=closed),
        ):
            payload = build_alpaca_paper_readiness_snapshot(
                account_summary={"equity": 100000.0, "buying_power": 400000.0}
            )

        self.assertEqual(payload["provider"], "alpaca")
        self.assertEqual(payload["mode"], "paper")
        self.assertFalse(payload["credentials"]["secrets_exposed"])
        self.assertEqual(payload["reconciliation"]["pending_count"], 2)
        self.assertEqual(payload["reconciliation"]["missing_broker_order_id_count"], 1)
        self.assertEqual(payload["rejected_order_normalization"]["rejected_count"], 1)
        self.assertTrue(payload["position_sync_guard"]["enabled"])
        self.assertEqual(payload["account_heartbeat"]["buying_power"], 400000.0)
        self.assertTrue(payload["account_heartbeat"]["buying_power_is_ceiling"])

    def test_closed_history_duplicates_do_not_block_active_duplicate_guard(self) -> None:
        pending = pd.DataFrame()
        open_trades = pd.DataFrame()
        closed = pd.DataFrame(
            [
                {"order_id": "entry-1", "broker_status": "filled", "broker_client_order_id": "client-1"},
                {"order_id": "entry-1", "broker_status": "filled", "broker_client_order_id": "client-1"},
            ]
        )

        with (
            patch("backend.services.alpaca_paper_readiness_service.sdm.read_pending_orders", return_value=pending),
            patch("backend.services.alpaca_paper_readiness_service.sdm.read_open_trades", return_value=open_trades),
            patch("backend.services.alpaca_paper_readiness_service.sdm.read_closed_trades", return_value=closed),
        ):
            payload = build_alpaca_paper_readiness_snapshot(
                account_summary={"equity": 100000.0, "buying_power": 400000.0}
            )

        guard = payload["duplicate_client_order_id_guard"]
        self.assertEqual(guard["status"], "ready")
        self.assertEqual(guard["duplicate_count"], 0)
        self.assertEqual(guard["historical_duplicate_count"], 1)


if __name__ == "__main__":
    unittest.main()
