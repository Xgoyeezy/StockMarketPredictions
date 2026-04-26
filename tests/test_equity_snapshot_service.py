from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from backend.services import equity_snapshot_service as ess


class EquitySnapshotServiceTests(unittest.TestCase):
    def test_record_trade_automation_equity_snapshot_builds_mark_to_market_fields(self) -> None:
        tenant = SimpleNamespace(id="tenant-1", slug="alpha-desk")
        state = {
            "settings": {"account_size": 100000.0},
            "runtime": {
                "last_cycle_at": "2026-04-20T17:30:00+00:00",
                "cycle_count": 4,
                "success_count": 3,
                "error_count": 1,
                "rejection_count": 2,
                "last_action": {"cycle_id": "cycle-1"},
            },
        }
        monitored_open = pd.DataFrame(
            [
                {
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": "tenant-1",
                    "ticker": "SPY",
                    "position_cost": 10000.0,
                    "unrealized_pnl": 500.0,
                }
            ]
        )
        pending_orders = pd.DataFrame(
            [
                {
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": "tenant-1",
                    "ticker": "QQQ",
                    "quantity": 50.0,
                    "limit_price": 400.0,
                }
            ]
        )
        closed_trades = pd.DataFrame(
            [
                {
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": "tenant-1",
                    "realized_pnl": 200.0,
                }
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "equity_snapshots.csv"
            snapshot = ess.record_trade_automation_equity_snapshot(
                tenant=tenant,
                state=state,
                cycle_at="2026-04-20T17:30:00+00:00",
                cycle_id="cycle-1",
                monitored_open=monitored_open,
                pending_orders=pending_orders,
                closed_trades=closed_trades,
                file_path=file_path,
            )

            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["tenant_slug"], "alpha-desk")
            self.assertEqual(snapshot["cycle_id"], "cycle-1")
            self.assertEqual(snapshot["active_trade_count"], 1)
            self.assertEqual(snapshot["pending_order_count"], 1)
            self.assertEqual(snapshot["cash_estimate"], 90200.0)
            self.assertEqual(snapshot["long_market_value"], 10500.0)
            self.assertEqual(snapshot["pending_notional"], 20000.0)
            self.assertEqual(snapshot["equity"], 100700.0)

            written = ess.read_equity_snapshots(file_path)
            self.assertEqual(len(written.index), 1)
            self.assertEqual(float(written.iloc[0]["equity"]), 100700.0)

    def test_record_trade_automation_equity_snapshot_replaces_same_cycle(self) -> None:
        tenant = SimpleNamespace(id="tenant-1", slug="alpha-desk")
        state = {
            "settings": {"account_size": 100000.0},
            "runtime": {
                "last_cycle_at": "2026-04-20T17:30:00+00:00",
                "cycle_count": 4,
                "success_count": 3,
                "error_count": 1,
                "rejection_count": 2,
                "last_action": {"cycle_id": "cycle-1"},
            },
        }
        first_open = pd.DataFrame(
            [
                {
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": "tenant-1",
                    "ticker": "SPY",
                    "position_cost": 10000.0,
                    "unrealized_pnl": 500.0,
                }
            ]
        )
        second_open = pd.DataFrame(
            [
                {
                    "automation_origin": "trade_automation",
                    "automation_tenant_id": "tenant-1",
                    "ticker": "SPY",
                    "position_cost": 10000.0,
                    "unrealized_pnl": 900.0,
                }
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "equity_snapshots.csv"
            ess.record_trade_automation_equity_snapshot(
                tenant=tenant,
                state=state,
                cycle_at="2026-04-20T17:30:00+00:00",
                cycle_id="cycle-1",
                monitored_open=first_open,
                pending_orders=pd.DataFrame(),
                closed_trades=pd.DataFrame(),
                file_path=file_path,
            )
            ess.record_trade_automation_equity_snapshot(
                tenant=tenant,
                state=state,
                cycle_at="2026-04-20T17:30:00+00:00",
                cycle_id="cycle-1",
                monitored_open=second_open,
                pending_orders=pd.DataFrame(),
                closed_trades=pd.DataFrame(),
                file_path=file_path,
            )

            written = ess.read_equity_snapshots(file_path)
            self.assertEqual(len(written.index), 1)
            self.assertEqual(float(written.iloc[0]["equity"]), 100900.0)

    def test_get_latest_trade_automation_equity_snapshot_returns_latest_cycle(self) -> None:
        tenant = SimpleNamespace(id="tenant-1", slug="alpha-desk")
        state = {
            "settings": {"account_size": 100000.0},
            "runtime": {
                "last_cycle_at": "2026-04-20T17:30:00+00:00",
                "cycle_count": 4,
                "success_count": 3,
                "error_count": 1,
                "rejection_count": 2,
                "last_action": {"cycle_id": "cycle-2"},
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "equity_snapshots.csv"
            ess.record_trade_automation_equity_snapshot(
                tenant=tenant,
                state=state,
                cycle_at="2026-04-20T17:30:00+00:00",
                cycle_id="cycle-1",
                monitored_open=pd.DataFrame(),
                pending_orders=pd.DataFrame(),
                closed_trades=pd.DataFrame(),
                file_path=file_path,
            )
            ess.record_trade_automation_equity_snapshot(
                tenant=tenant,
                state=state,
                cycle_at="2026-04-20T17:35:00+00:00",
                cycle_id="cycle-2",
                monitored_open=pd.DataFrame(),
                pending_orders=pd.DataFrame(),
                closed_trades=pd.DataFrame(),
                file_path=file_path,
            )

            latest = ess.get_latest_trade_automation_equity_snapshot(
                tenant_id="tenant-1",
                tenant_slug="alpha-desk",
                file_path=file_path,
            )

        self.assertIsNotNone(latest)
        self.assertEqual(latest["cycle_id"], "cycle-2")
        self.assertEqual(latest["cycle_at"], "2026-04-20T17:35:00+00:00")


if __name__ == "__main__":
    unittest.main()
