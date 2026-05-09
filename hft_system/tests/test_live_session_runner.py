from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from hft.execution.order_state import OrderLifecycleState
from hft.live.market_data import FeedHealthSnapshot, LiveMarketDataAdapter
from hft.live.paper_execution import PaperExecutionAdapter, PaperExecutionReport, PaperOrderIntent
from hft.market_data.schemas import MarketEvent
from hft.risk.limits import HFTLimitConfig
from hft.runtime.session_runner import HFTSessionRunner, LiveSessionConfig
from hft.strategies.market_making import InventoryAwareMarketMakingStrategy
from tests._workspace_tmp import reset_tmp_dir


class FakeLiveMarketDataAdapter(LiveMarketDataAdapter):
    def __init__(self, batches: list[list[MarketEvent]]):
        self.batches = list(batches)

    def check_feed(self, symbols: list[str]) -> FeedHealthSnapshot:
        return FeedHealthSnapshot(True, "fake", "unit", 200, len(symbols), "ok", {})

    def poll(self, symbols: list[str]) -> list[MarketEvent]:
        return self.batches.pop(0) if self.batches else []


class FakePaperExecutionAdapter(PaperExecutionAdapter):
    def __init__(self, *, fill_after_submit: bool = True):
        self.submitted: list[PaperOrderIntent] = []
        self.live_reports: dict[str, PaperExecutionReport] = {}
        self.flatten_requests: list[str] = []
        self.fill_after_submit = fill_after_submit

    def check_connection(self) -> dict[str, object]:
        return {"ok": True}

    def submit_order(self, intent: PaperOrderIntent) -> PaperExecutionReport:
        self.submitted.append(intent)
        report = PaperExecutionReport(
            order_id=intent.order_id,
            broker_order_id=f"broker-{intent.order_id}",
            symbol=intent.symbol,
            strategy_name=intent.strategy_name,
            side=intent.side,
            price=intent.price,
            quantity=intent.quantity,
            filled_quantity=0.0,
            average_fill_price=0.0,
            decision_timestamp=intent.decision_timestamp,
            send_timestamp=intent.decision_timestamp + 1,
            exchange_receive_timestamp=intent.decision_timestamp + 2,
            ack_timestamp=intent.decision_timestamp + 3,
            updated_at_ns=intent.decision_timestamp + 3,
            fill_timestamp=None,
            state=OrderLifecycleState.LIVE,
            accepted=True,
            reason="new",
            metadata=dict(intent.metadata),
        )
        self.live_reports[report.broker_order_id] = report
        return report

    def cancel_order(self, broker_order_id: str, *, symbol: str | None = None) -> PaperExecutionReport:
        report = self.live_reports[broker_order_id]
        canceled = PaperExecutionReport(
            **{**report.__dict__, "state": OrderLifecycleState.CANCELED, "reason": "canceled", "updated_at_ns": report.updated_at_ns + 1}
        )
        self.live_reports[broker_order_id] = canceled
        return canceled

    def sync_orders(self, symbols: list[str] | None = None) -> list[PaperExecutionReport]:
        reports: list[PaperExecutionReport] = []
        for broker_order_id, report in list(self.live_reports.items()):
            if self.fill_after_submit and report.state == OrderLifecycleState.LIVE:
                filled = PaperExecutionReport(
                    **{
                        **report.__dict__,
                        "state": OrderLifecycleState.FILLED,
                        "filled_quantity": report.quantity,
                        "average_fill_price": report.price,
                        "fill_timestamp": report.updated_at_ns + 2,
                        "updated_at_ns": report.updated_at_ns + 2,
                        "reason": "filled",
                    }
                )
                self.live_reports[broker_order_id] = filled
                reports.append(filled)
            else:
                reports.append(report)
        return reports

    def flatten_positions(self, symbols: list[str] | None = None) -> list[PaperExecutionReport]:
        reports: list[PaperExecutionReport] = []
        for symbol in symbols or []:
            self.flatten_requests.append(symbol)
            reports.append(
                PaperExecutionReport(
                    order_id=f"flatten-{symbol}",
                    broker_order_id=f"flatten-{symbol}",
                    symbol=symbol,
                    strategy_name="flatten",
                    side="sell",
                    price=100.0,
                    quantity=10.0,
                    filled_quantity=10.0,
                    average_fill_price=100.0,
                    decision_timestamp=1,
                    send_timestamp=2,
                    exchange_receive_timestamp=3,
                    ack_timestamp=4,
                    updated_at_ns=5,
                    fill_timestamp=5,
                    state=OrderLifecycleState.FILLED,
                    accepted=True,
                    reason="flattened",
                    metadata={},
                )
            )
        return reports


def regular_events(*, spread: float = 0.02, session: str = "regular") -> list[MarketEvent]:
    return [
        MarketEvent(100, 100, 1, "bid", "fake", "XNAS", "AAPL", "bbo", "buy", 100.0, 100.0, metadata={"session": session}, session=session),
        MarketEvent(100, 100, 2, "ask", "fake", "XNAS", "AAPL", "bbo", "sell", 100.0 + spread, 100.0, metadata={"session": session}, session=session),
        MarketEvent(110, 110, 3, "trade", "fake", "XNAS", "AAPL", "trade", "sell", 100.0, 25.0, trade_id="t1", metadata={"session": session}, session=session),
    ]


class LiveSessionRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = str(reset_tmp_dir("live_session_runner"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_runner_submits_only_risk_checked_orders_and_updates_fill_state(self) -> None:
        runner = HFTSessionRunner(
            config=LiveSessionConfig(symbols=("AAPL",), poll_interval_seconds=0.0, max_cycles=1, base_dir=self.tmpdir),
            strategies={"AAPL": InventoryAwareMarketMakingStrategy()},
            market_data_adapter=FakeLiveMarketDataAdapter([regular_events()]),
            execution_adapter=FakePaperExecutionAdapter(fill_after_submit=True),
            limits=HFTLimitConfig(),
        )

        result = runner.run()
        adapter = runner.execution_adapter

        self.assertTrue(adapter.submitted)
        self.assertTrue(all(intent.risk_checked for intent in adapter.submitted))
        self.assertGreaterEqual(len(result.fills), 1)
        self.assertGreater(result.metrics["fill_rate"], 0.0)
        self.assertTrue((Path(result.output_dir) / "report.html").exists())

    def test_runner_flattens_existing_inventory_when_kill_switch_trips(self) -> None:
        runner = HFTSessionRunner(
            config=LiveSessionConfig(symbols=("AAPL",), poll_interval_seconds=0.0, max_cycles=1, base_dir=self.tmpdir),
            strategies={"AAPL": InventoryAwareMarketMakingStrategy()},
            market_data_adapter=FakeLiveMarketDataAdapter([regular_events(spread=1.0)]),
            execution_adapter=FakePaperExecutionAdapter(fill_after_submit=False),
            limits=HFTLimitConfig(),
        )
        runner.inventories["AAPL"].position = 10.0
        runner.inventories["AAPL"].average_price = 100.0
        runner.inventories["AAPL"].oldest_open_ts_ns = 1
        runner.inventories["AAPL"].last_fill_ts_ns = 1

        runner.run()
        adapter = runner.execution_adapter

        self.assertEqual(adapter.flatten_requests, ["AAPL"])
        self.assertEqual(len(adapter.submitted), 0)

    def test_pre_open_warmup_stands_down_without_submitting_orders(self) -> None:
        runner = HFTSessionRunner(
            config=LiveSessionConfig(symbols=("AAPL",), poll_interval_seconds=0.0, max_cycles=1, base_dir=self.tmpdir),
            strategies={"AAPL": InventoryAwareMarketMakingStrategy()},
            market_data_adapter=FakeLiveMarketDataAdapter([regular_events(session="pre_market")]),
            execution_adapter=FakePaperExecutionAdapter(fill_after_submit=False),
            limits=HFTLimitConfig(),
        )

        runner.run()
        adapter = runner.execution_adapter

        self.assertEqual(adapter.submitted, [])


if __name__ == "__main__":
    unittest.main()
