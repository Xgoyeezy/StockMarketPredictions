from __future__ import annotations

import json
import shutil
import time
import unittest
from pathlib import Path

from hft.execution.order_state import OrderLifecycleState
from hft.live.market_data import FeedHealthSnapshot, LiveMarketDataAdapter
from hft.live.paper_execution import PaperExecutionAdapter, PaperExecutionReport, PaperOrderIntent
from hft.market_data.schemas import MarketEvent
from hft.millisecond import MillisecondEngineConfig, MillisecondRuntimeConfig, MillisecondRuntimeRunner, MillisecondSignalEngine
from hft.millisecond.cli import _load_engine_config, _load_runtime_settings, _resolve_path
from hft.risk.limits import HFTLimitConfig
from tests._workspace_tmp import reset_tmp_dir


def bbo_events(*, timestamp_ns: int | None = None, session: str = "regular") -> list[MarketEvent]:
    ts = int(timestamp_ns or time.time_ns())
    return [
        MarketEvent(
            ts,
            ts,
            1,
            "bid",
            "unit",
            "XNAS",
            "AAPL",
            "bbo",
            "buy",
            100.00,
            900.0,
            metadata={"session": session},
            session=session,
        ),
        MarketEvent(
            ts + 1,
            ts + 1,
            2,
            "ask",
            "unit",
            "XNAS",
            "AAPL",
            "bbo",
            "sell",
            100.04,
            100.0,
            metadata={"session": session},
            session=session,
        ),
    ]


class FakeMarketDataAdapter(LiveMarketDataAdapter):
    def __init__(self, batches: list[list[MarketEvent]]):
        self.batches = list(batches)

    def check_feed(self, symbols: list[str]) -> FeedHealthSnapshot:
        return FeedHealthSnapshot(True, "fake", "unit", 200, len(symbols), "ok", {})

    def poll(self, symbols: list[str]) -> list[MarketEvent]:
        return self.batches.pop(0) if self.batches else []


class PollFailureMarketDataAdapter(FakeMarketDataAdapter):
    def poll(self, symbols: list[str]) -> list[MarketEvent]:
        raise RuntimeError("too many requests.")


class FlakyMarketDataAdapter(FakeMarketDataAdapter):
    def __init__(self):
        super().__init__([bbo_events()])
        self.failures_remaining = 1

    def poll(self, symbols: list[str]) -> list[MarketEvent]:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("temporary rate limit")
        return super().poll(symbols)


class FakePaperExecutionAdapter(PaperExecutionAdapter):
    def __init__(self):
        self.submitted: list[PaperOrderIntent] = []

    def check_connection(self) -> dict[str, object]:
        return {"ok": True}

    def submit_order(self, intent: PaperOrderIntent) -> PaperExecutionReport:
        self.submitted.append(intent)
        return PaperExecutionReport(
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

    def cancel_order(self, broker_order_id: str, *, symbol: str | None = None) -> PaperExecutionReport:
        raise AssertionError("millisecond runner should not cancel in these tests")

    def sync_orders(self, symbols: list[str] | None = None) -> list[PaperExecutionReport]:
        return []

    def flatten_positions(self, symbols: list[str] | None = None) -> list[PaperExecutionReport]:
        return []


class FailingPaperExecutionAdapter(FakePaperExecutionAdapter):
    def submit_order(self, intent: PaperOrderIntent) -> PaperExecutionReport:
        self.submitted.append(intent)
        raise RuntimeError("broker submit unavailable")


class UnhealthyPaperExecutionAdapter(FakePaperExecutionAdapter):
    def check_connection(self) -> dict[str, object]:
        return {"ok": False, "message": "paper account unavailable"}

    def submit_order(self, intent: PaperOrderIntent) -> PaperExecutionReport:
        raise AssertionError("submit must not be called when execution health fails")


class MillisecondSignalEngineTests(unittest.TestCase):
    def test_engine_generates_submit_when_edge_and_spread_pass(self) -> None:
        engine = MillisecondSignalEngine(config=MillisecondEngineConfig(min_edge_bps=1.0, max_spread_bps=8.0))

        first, second = bbo_events()
        waiting = engine.process_event(first)
        decision = engine.process_event(second)

        self.assertEqual(waiting.reason, "waiting_top_of_book")
        self.assertEqual(decision.action, "submit")
        self.assertEqual(decision.side, "buy")
        self.assertGreaterEqual(decision.edge_bps, 1.0)
        self.assertLessEqual(decision.decision_latency_ns, engine.config.max_decision_latency_ns)

    def test_throttle_blocks_immediate_second_submit(self) -> None:
        engine = MillisecondSignalEngine(config=MillisecondEngineConfig(min_edge_bps=1.0, min_order_interval_ms=1000))
        first, second = bbo_events()
        engine.process_event(first)
        submit = engine.process_event(second)
        blocked = engine.process_event(second)

        self.assertEqual(submit.action, "submit")
        self.assertEqual(blocked.action, "blocked")
        self.assertEqual(blocked.reason, "symbol_order_interval")

    def test_stale_quote_blocks_submission(self) -> None:
        engine = MillisecondSignalEngine(config=MillisecondEngineConfig(min_edge_bps=1.0, max_quote_age_ns=1))
        stale_ts = time.time_ns() - 1_000_000
        first, second = bbo_events(timestamp_ns=stale_ts)
        engine.process_event(first)
        decision = engine.process_event(second)

        self.assertEqual(decision.action, "stand_down")
        self.assertEqual(decision.reason, "stale_quote")


class MillisecondRuntimeRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = reset_tmp_dir("millisecond_runtime")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_runtime_dry_run_writes_summary_without_submitting_to_broker(self) -> None:
        execution = FakePaperExecutionAdapter()
        runner = MillisecondRuntimeRunner(
            config=MillisecondRuntimeConfig(
                symbols=("AAPL",),
                max_cycles=1,
                poll_interval_ms=0,
                submit_to_paper=False,
                base_dir=self.tmpdir,
                engine=MillisecondEngineConfig(min_edge_bps=1.0),
            ),
            market_data_adapter=FakeMarketDataAdapter([bbo_events()]),
            execution_adapter=execution,
            limits=HFTLimitConfig(),
        )

        result = runner.run()
        summary = json.loads((Path(result.output_dir) / "session_summary.json").read_text(encoding="utf-8"))

        self.assertEqual(execution.submitted, [])
        self.assertGreaterEqual(result.metrics["submit_decision_count"], 1)
        self.assertEqual(result.metrics["paper_report_count"], 1)
        self.assertTrue(summary["metrics"]["feed_ok"])

    def test_runtime_submit_paper_sends_only_risk_checked_order(self) -> None:
        execution = FakePaperExecutionAdapter()
        runner = MillisecondRuntimeRunner(
            config=MillisecondRuntimeConfig(
                symbols=("AAPL",),
                max_cycles=1,
                poll_interval_ms=0,
                submit_to_paper=True,
                base_dir=self.tmpdir,
                engine=MillisecondEngineConfig(min_edge_bps=1.0),
            ),
            market_data_adapter=FakeMarketDataAdapter([bbo_events()]),
            execution_adapter=execution,
            limits=HFTLimitConfig(),
        )

        result = runner.run()

        self.assertEqual(len(execution.submitted), 1)
        self.assertTrue(execution.submitted[0].risk_checked)
        self.assertEqual(execution.submitted[0].risk_reason, "allowed")
        self.assertEqual(result.metrics["accepted_report_count"], 1)

    def test_runtime_records_poll_failure_without_submitting(self) -> None:
        execution = FakePaperExecutionAdapter()
        runner = MillisecondRuntimeRunner(
            config=MillisecondRuntimeConfig(
                symbols=("AAPL",),
                max_cycles=1,
                poll_interval_ms=0,
                submit_to_paper=True,
                base_dir=self.tmpdir,
                engine=MillisecondEngineConfig(min_edge_bps=1.0),
                poll_retry_attempts=0,
                max_consecutive_poll_errors=1,
            ),
            market_data_adapter=PollFailureMarketDataAdapter([]),
            execution_adapter=execution,
            limits=HFTLimitConfig(),
        )

        result = runner.run()
        summary = json.loads((Path(result.output_dir) / "session_summary.json").read_text(encoding="utf-8"))

        self.assertEqual(execution.submitted, [])
        self.assertFalse(result.metrics["feed_ok"])
        self.assertEqual(result.metrics["poll_error_count"], 1)
        self.assertEqual(result.metrics["runtime_status"], "poll_error_circuit_open")
        self.assertIn("too many requests", result.metrics["feed_message"])
        self.assertEqual(summary["metrics"]["paper_report_count"], 0)

    def test_runtime_recovers_from_transient_poll_failure(self) -> None:
        execution = FakePaperExecutionAdapter()
        runner = MillisecondRuntimeRunner(
            config=MillisecondRuntimeConfig(
                symbols=("AAPL",),
                max_cycles=1,
                poll_interval_ms=0,
                submit_to_paper=False,
                base_dir=self.tmpdir,
                engine=MillisecondEngineConfig(min_edge_bps=1.0),
                poll_retry_attempts=1,
                poll_retry_backoff_ms=0,
            ),
            market_data_adapter=FlakyMarketDataAdapter(),
            execution_adapter=execution,
            limits=HFTLimitConfig(),
        )

        result = runner.run()

        self.assertTrue(result.metrics["runtime_ok"])
        self.assertEqual(result.metrics["poll_error_count"], 1)
        self.assertEqual(result.metrics["recovered_poll_error_count"], 1)
        self.assertGreaterEqual(result.metrics["submit_decision_count"], 1)

    def test_runtime_rejects_submit_exception_without_crashing(self) -> None:
        execution = FailingPaperExecutionAdapter()
        runner = MillisecondRuntimeRunner(
            config=MillisecondRuntimeConfig(
                symbols=("AAPL",),
                max_cycles=1,
                poll_interval_ms=0,
                submit_to_paper=True,
                base_dir=self.tmpdir,
                engine=MillisecondEngineConfig(min_edge_bps=1.0),
            ),
            market_data_adapter=FakeMarketDataAdapter([bbo_events()]),
            execution_adapter=execution,
            limits=HFTLimitConfig(),
        )

        result = runner.run()

        self.assertEqual(len(execution.submitted), 1)
        self.assertFalse(result.metrics["runtime_ok"])
        self.assertEqual(result.metrics["submit_error_count"], 1)
        self.assertEqual(result.reports[0].state, OrderLifecycleState.REJECTED)
        self.assertIn("submit_exception", result.reports[0].reason)

    def test_runtime_blocks_submit_when_execution_health_fails(self) -> None:
        runner = MillisecondRuntimeRunner(
            config=MillisecondRuntimeConfig(
                symbols=("AAPL",),
                max_cycles=1,
                poll_interval_ms=0,
                submit_to_paper=True,
                base_dir=self.tmpdir,
                engine=MillisecondEngineConfig(min_edge_bps=1.0),
            ),
            market_data_adapter=FakeMarketDataAdapter([bbo_events()]),
            execution_adapter=UnhealthyPaperExecutionAdapter(),
            limits=HFTLimitConfig(),
        )

        result = runner.run()

        self.assertFalse(result.metrics["runtime_ok"])
        self.assertFalse(result.metrics["execution_ok"])
        self.assertEqual(result.metrics["runtime_status"], "execution_unavailable")
        self.assertEqual(result.metrics["decision_count"], 0)


class MillisecondCliTests(unittest.TestCase):
    def test_config_path_resolves_from_project_root(self) -> None:
        resolved = _resolve_path("configs/millisecond.yaml")
        config = _load_engine_config(resolved)

        self.assertTrue(resolved.exists())
        self.assertEqual(config.strategy_name, "millisecond_micro_scalper")
        self.assertEqual(config.allowed_sessions, ("regular",))
        runtime_settings = _load_runtime_settings(resolved)
        self.assertEqual(runtime_settings["max_consecutive_poll_errors"], 3)


if __name__ == "__main__":
    unittest.main()
