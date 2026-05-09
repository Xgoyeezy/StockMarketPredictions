from __future__ import annotations

import json
import shutil
import time
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hft.live.market_data import FeedHealthSnapshot, LiveMarketDataAdapter
from hft.live.paper_execution import PaperExecutionAdapter, PaperExecutionReport, PaperOrderIntent
from hft.millisecond import MillisecondRuntimeResult, MillisecondWatchdog, MillisecondWatchdogConfig, cleanup_watchdog_locks, read_watchdog_status
from hft.risk.limits import HFTLimitConfig
from tests._workspace_tmp import reset_tmp_dir


ET = ZoneInfo("America/New_York")


class FakeMarketDataAdapter(LiveMarketDataAdapter):
    def __init__(self, *, feed_ok: bool = True):
        self.feed_ok = feed_ok
        self.poll_count = 0

    def check_feed(self, symbols: list[str]) -> FeedHealthSnapshot:
        return FeedHealthSnapshot(
            ok=self.feed_ok,
            provider="fake",
            feed="unit",
            status_code=200 if self.feed_ok else 503,
            symbol_count=len(symbols) if self.feed_ok else 0,
            message="ok" if self.feed_ok else "feed unavailable",
            metadata={},
        )

    def poll(self, symbols: list[str]):
        self.poll_count += 1
        return []


class FakePaperExecutionAdapter(PaperExecutionAdapter):
    def __init__(self, *, ok: bool = True):
        self.ok = ok

    def check_connection(self) -> dict[str, object]:
        return {"ok": self.ok, "message": "ok" if self.ok else "paper unavailable"}

    def submit_order(self, intent: PaperOrderIntent) -> PaperExecutionReport:
        raise AssertionError("watchdog tests inject child runtime results")

    def cancel_order(self, broker_order_id: str, *, symbol: str | None = None) -> PaperExecutionReport:
        raise AssertionError("watchdog tests do not cancel")

    def sync_orders(self, symbols: list[str] | None = None) -> list[PaperExecutionReport]:
        return []

    def flatten_positions(self, symbols: list[str] | None = None) -> list[PaperExecutionReport]:
        return []


class SliceQueue:
    def __init__(self, results: list[MillisecondRuntimeResult]):
        self.results = list(results)

    def __call__(self) -> MillisecondRuntimeResult:
        if not self.results:
            raise AssertionError("slice runner called more times than expected")
        return self.results.pop(0)


def at(hour: int, minute: int) -> datetime:
    return datetime(2026, 4, 30, hour, minute, tzinfo=ET)


def child_result(name: str, *, runtime_ok: bool = True, decision_count: int = 1, runtime_status: str = "completed", latency_ms: float | None = None) -> MillisecondRuntimeResult:
    metrics = {
        "runtime_ok": runtime_ok,
        "runtime_status": runtime_status,
        "decision_count": decision_count,
        "feed_ok": runtime_ok,
        "execution_ok": True,
    }
    if latency_ms is not None:
        metrics["latency_ms"] = latency_ms
    return MillisecondRuntimeResult(
        run_id=name,
        output_dir=f"data/millisecond/run_id={name}",
        decisions=[],
        reports=[],
        submit_to_paper=False,
        metrics=metrics,
    )


class MillisecondWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = reset_tmp_dir("millisecond_watchdog")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _watchdog(
        self,
        *,
        now: datetime,
        slice_results: list[MillisecondRuntimeResult] | None = None,
        feed_ok: bool = True,
        execution_ok: bool = True,
        **config_overrides,
    ) -> MillisecondWatchdog:
        config = MillisecondWatchdogConfig(
            symbols=("AAPL",),
            base_dir=self.tmpdir,
            slice_interval_seconds=0,
            wait_for_window=False,
            **config_overrides,
        )
        return MillisecondWatchdog(
            config=config,
            market_data_adapter=FakeMarketDataAdapter(feed_ok=feed_ok),
            execution_adapter=FakePaperExecutionAdapter(ok=execution_ok),
            limits=HFTLimitConfig(),
            now_provider=lambda: now,
            slice_runner=SliceQueue(slice_results or []),
        )

    def test_watchdog_does_not_trade_before_start_time(self) -> None:
        result = self._watchdog(now=at(9, 30), slice_results=[child_result("should-not-run")]).run()

        self.assertEqual(result.status, "preflight_ok_waiting_for_start")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.metrics["child_run_count"], 0)

    def test_watchdog_stops_new_slices_after_stop_time(self) -> None:
        result = self._watchdog(now=at(15, 56), slice_results=[child_result("should-not-run")]).run()

        self.assertEqual(result.status, "market_closed")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.metrics["child_run_count"], 0)

    def test_feed_preflight_failure_blocks_startup(self) -> None:
        result = self._watchdog(now=at(10, 0), feed_ok=False).run()

        self.assertEqual(result.status, "feed_unavailable")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.metrics["child_run_count"], 0)

    def test_execution_preflight_failure_blocks_startup(self) -> None:
        result = self._watchdog(now=at(10, 0), execution_ok=False).run()

        self.assertEqual(result.status, "execution_unavailable")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.metrics["child_run_count"], 0)

    def test_consecutive_runtime_failures_open_circuit(self) -> None:
        result = self._watchdog(
            now=at(10, 0),
            max_consecutive_failures=2,
            slice_results=[
                child_result("fail-1", runtime_ok=False, runtime_status="poll_error_circuit_open"),
                child_result("fail-2", runtime_ok=False, runtime_status="poll_error_circuit_open"),
            ],
        ).run()

        self.assertEqual(result.status, "runtime_failure_circuit_open")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.metrics["child_run_count"], 2)

    def test_consecutive_no_event_slices_stop_cleanly(self) -> None:
        result = self._watchdog(
            now=at(10, 0),
            max_consecutive_no_event_slices=2,
            slice_results=[
                child_result("empty-1", decision_count=0),
                child_result("empty-2", decision_count=0),
            ],
        ).run()

        self.assertEqual(result.status, "no_event_circuit_open")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.metrics["child_run_count"], 2)

    def test_successful_slices_record_child_runs_and_summary(self) -> None:
        result = self._watchdog(
            now=at(10, 0),
            max_slices=2,
            slice_results=[
                child_result("ok-1", decision_count=1, latency_ms=4.0),
                child_result("ok-2", decision_count=2, latency_ms=8.0),
            ],
        ).run()

        summary = json.loads((Path(result.output_dir) / "watchdog_summary.json").read_text(encoding="utf-8"))

        self.assertEqual(result.status, "max_slices_reached")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(summary["metrics"]["child_run_count"], 2)
        self.assertEqual([item["run_id"] for item in summary["child_runs"]], ["ok-1", "ok-2"])
        self.assertEqual(summary["metrics"]["mode_badge"], "dry-run")
        self.assertEqual(summary["metrics"]["latency_percentiles"]["sample_count"], 2)
        self.assertIn("symbol_set_hash", summary["metrics"])
        self.assertTrue(summary["metrics"]["near_close_no_new_order_proof"]["new_orders_stop_before_close"])

    def test_active_process_lock_blocks_second_watchdog(self) -> None:
        lock_dir = self.tmpdir / "millisecond_watchdog"
        lock_dir.mkdir(parents=True, exist_ok=True)
        (lock_dir / "watchdog_AAPL.lock.json").write_text(
            json.dumps(
                {
                    "pid": 999999,
                    "run_id": "existing-run",
                    "symbols": ["AAPL"],
                    "heartbeat_at_ns": time.time_ns(),
                    "status": "running",
                }
            ),
            encoding="utf-8",
        )

        result = self._watchdog(now=at(10, 0), slice_results=[child_result("should-not-run")]).run()

        self.assertEqual(result.status, "watchdog_already_running")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.metrics["child_run_count"], 0)

    def test_stale_process_lock_is_reclaimed(self) -> None:
        lock_dir = self.tmpdir / "millisecond_watchdog"
        lock_dir.mkdir(parents=True, exist_ok=True)
        (lock_dir / "watchdog_AAPL.lock.json").write_text(
            json.dumps(
                {
                    "pid": 999999,
                    "run_id": "stale-run",
                    "symbols": ["AAPL"],
                    "heartbeat_at_ns": time.time_ns() - 10_000_000_000,
                    "status": "running",
                }
            ),
            encoding="utf-8",
        )

        result = self._watchdog(now=at(10, 0), lock_ttl_seconds=1, max_slices=1, slice_results=[child_result("ok-1")]).run()

        self.assertEqual(result.status, "max_slices_reached")
        self.assertEqual(result.exit_code, 0)
        self.assertFalse((lock_dir / "watchdog_AAPL.lock.json").exists())

    def test_watchdog_writes_heartbeat_and_latest_status_index(self) -> None:
        result = self._watchdog(now=at(10, 0), max_slices=1, slice_results=[child_result("ok-1", decision_count=3)]).run()

        heartbeat = json.loads((Path(result.output_dir) / "watchdog_heartbeat.json").read_text(encoding="utf-8"))
        status = read_watchdog_status(self.tmpdir)

        self.assertEqual(heartbeat["run_id"], result.run_id)
        self.assertEqual(status["latest"]["run_id"], result.run_id)
        self.assertEqual(status["latest"]["metrics"]["decision_count"], 3)
        self.assertEqual(status["latest"]["metrics"]["active_slice_count"], 1)
        self.assertIn("updated_age_seconds", status["latest"])
        self.assertEqual(status["heartbeat"]["run_id"], result.run_id)
        self.assertEqual(status["active_lock_count"], 0)

    def test_watchdog_status_marks_stale_locks_and_cleanup_removes_them(self) -> None:
        lock_dir = self.tmpdir / "millisecond_watchdog"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "watchdog_AAPL.lock.json"
        lock_path.write_text(
            json.dumps(
                {
                    "pid": 999999,
                    "run_id": "stale-run",
                    "symbols": ["AAPL"],
                    "heartbeat_at_ns": time.time_ns() - 200_000_000_000,
                    "created_at_ns": time.time_ns() - 200_000_000_000,
                    "status": "running",
                }
            ),
            encoding="utf-8",
        )

        status = read_watchdog_status(self.tmpdir)
        cleanup = cleanup_watchdog_locks(self.tmpdir, max_age_seconds=1)

        self.assertEqual(status["active_lock_count"], 1)
        self.assertEqual(status["stale_lock_count"], 1)
        self.assertEqual(cleanup["removed_count"], 1)
        self.assertFalse(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
