from __future__ import annotations

import json
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, time as clock_time
from os import getpid
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from hft.backtest.reports import write_json_report
from hft.live.market_data import FeedHealthSnapshot, LiveMarketDataAdapter
from hft.live.paper_execution import PaperExecutionAdapter
from hft.millisecond.engine import MillisecondEngineConfig
from hft.millisecond.runner import MillisecondRuntimeConfig, MillisecondRuntimeResult, MillisecondRuntimeRunner
from hft.risk.limits import HFTLimitConfig
from hft.utils.env import bool_from_value
from hft.utils.logging import JsonlLogger


ET = ZoneInfo("America/New_York")
NowProvider = Callable[[], datetime]
SliceRunner = Callable[[], MillisecondRuntimeResult]


@dataclass(frozen=True)
class MillisecondWatchdogConfig:
    symbols: tuple[str, ...]
    base_dir: str | Path = "data"
    preflight_time_et: str = "09:25"
    start_time_et: str = "09:35"
    stop_time_et: str = "15:55"
    slice_cycles: int = 20
    poll_interval_ms: int = 10
    slice_interval_seconds: float = 30.0
    submit_to_paper: bool = False
    wait_for_window: bool = False
    idle_check_interval_seconds: float = 30.0
    max_slices: int = 0
    max_consecutive_failures: int = 3
    max_consecutive_no_event_slices: int = 10
    lock_ttl_seconds: int = 120
    engine: MillisecondEngineConfig | None = None
    runtime_settings: dict[str, Any] | None = None


@dataclass(frozen=True)
class MillisecondWatchdogResult:
    run_id: str
    output_dir: str
    status: str
    exit_code: int
    metrics: dict[str, Any]


class MillisecondWatchdog:
    def __init__(
        self,
        *,
        config: MillisecondWatchdogConfig,
        market_data_adapter: LiveMarketDataAdapter,
        execution_adapter: PaperExecutionAdapter,
        limits: HFTLimitConfig,
        now_provider: NowProvider | None = None,
        slice_runner: SliceRunner | None = None,
    ):
        self.config = config
        self.market_data_adapter = market_data_adapter
        self.execution_adapter = execution_adapter
        self.limits = limits
        self.now_provider = now_provider or (lambda: datetime.now(tz=ET))
        self.slice_runner = slice_runner
        self.base_dir = Path(config.base_dir)

    def run(self) -> MillisecondWatchdogResult:
        run_id = f"millisecond_watchdog-{time.time_ns()}"
        run_dir = self.base_dir / "millisecond_watchdog" / f"run_id={run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        event_log = JsonlLogger(run_dir / "watchdog_events.jsonl")
        events: list[dict[str, Any]] = []
        child_runs: list[dict[str, Any]] = []
        lock_acquired = False

        lock_status = self._acquire_process_lock(run_id, run_dir, events, event_log)
        if lock_status is not None:
            return self._finalize(run_dir, run_id, lock_status, 1, events, child_runs)
        lock_acquired = True

        try:
            gate = self._wait_until_trading_window(events, event_log)
            if gate is not None:
                return self._finalize(run_dir, run_id, gate, 0, events, child_runs)

            preflight_status = self._preflight(events, event_log)
            if preflight_status is not None:
                return self._finalize(run_dir, run_id, preflight_status, 1, events, child_runs)

            consecutive_failures = 0
            consecutive_no_events = 0
            slices_started = 0
            final_status = "completed"
            exit_code = 0

            while self._in_trading_window(self._now_et()):
                self._write_heartbeat(run_id, run_dir, status="running", events=events, child_runs=child_runs)
                if self.config.max_slices and slices_started >= self.config.max_slices:
                    final_status = "max_slices_reached"
                    break
                slices_started += 1
                result = self._run_slice()
                child_record = {
                    "slice_number": slices_started,
                    "run_id": result.run_id,
                    "output_dir": result.output_dir,
                    "submit_to_paper": result.submit_to_paper,
                    "metrics": result.metrics,
                }
                child_runs.append(child_record)
                self._emit(events, event_log, "slice_completed", "millisecond slice completed", **child_record)
                self._write_heartbeat(run_id, run_dir, status="slice_completed", events=events, child_runs=child_runs)

                if not result.metrics.get("runtime_ok"):
                    consecutive_failures += 1
                    consecutive_no_events = 0
                    self._emit(
                        events,
                        event_log,
                        "slice_runtime_blocked",
                        str(result.metrics.get("runtime_status") or "runtime_not_ok"),
                        consecutive_failures=consecutive_failures,
                        child_run_id=result.run_id,
                    )
                    if consecutive_failures >= max(int(self.config.max_consecutive_failures), 1):
                        final_status = "runtime_failure_circuit_open"
                        exit_code = 1
                        break
                else:
                    consecutive_failures = 0
                    if int(result.metrics.get("decision_count") or 0) == 0:
                        consecutive_no_events += 1
                        self._emit(
                            events,
                            event_log,
                            "slice_no_events",
                            "slice completed with no market events",
                            consecutive_no_event_slices=consecutive_no_events,
                            child_run_id=result.run_id,
                        )
                        if consecutive_no_events >= max(int(self.config.max_consecutive_no_event_slices), 1):
                            final_status = "no_event_circuit_open"
                            exit_code = 1
                            break
                    else:
                        consecutive_no_events = 0

                if self.config.slice_interval_seconds > 0 and self._in_trading_window(self._now_et()):
                    time.sleep(float(self.config.slice_interval_seconds))

            if final_status == "completed" and not self._in_trading_window(self._now_et()):
                final_status = "market_window_closed"
            return self._finalize(run_dir, run_id, final_status, exit_code, events, child_runs)
        finally:
            if lock_acquired:
                self._release_process_lock(run_id)

    def _wait_until_trading_window(self, events: list[dict[str, Any]], event_log: JsonlLogger) -> str | None:
        while True:
            now = self._now_et()
            if not _is_market_day(now):
                self._emit(events, event_log, "market_closed", "not a regular market day", now_et=now.isoformat())
                return "market_closed"
            if now.time() >= self._time(self.config.stop_time_et):
                self._emit(events, event_log, "market_closed", "watchdog start is after stop time", now_et=now.isoformat())
                return "market_closed"
            if now.time() < self._time(self.config.preflight_time_et):
                self._emit(events, event_log, "waiting_for_preflight", "before preflight window", now_et=now.isoformat())
                if not self.config.wait_for_window:
                    return "waiting_for_preflight"
                time.sleep(max(float(self.config.idle_check_interval_seconds), 0.0))
                continue
            if now.time() < self._time(self.config.start_time_et):
                preflight_status = self._preflight(events, event_log)
                if preflight_status is not None:
                    return preflight_status
                self._emit(events, event_log, "waiting_for_start", "preflight passed; before trading start", now_et=now.isoformat())
                if not self.config.wait_for_window:
                    return "preflight_ok_waiting_for_start"
                time.sleep(max(float(self.config.idle_check_interval_seconds), 0.0))
                continue
            return None

    def _preflight(self, events: list[dict[str, Any]], event_log: JsonlLogger) -> str | None:
        try:
            feed = self.market_data_adapter.check_feed(list(self.config.symbols))
        except Exception as exc:
            self._emit(events, event_log, "feed_check_failed", str(exc) or "feed_check_failed")
            return "feed_check_failed"
        self._emit(events, event_log, "feed_checked", feed.message, feed=_feed_record(feed))
        if not feed.ok:
            return "feed_unavailable"
        try:
            execution = self.execution_adapter.check_connection()
        except Exception as exc:
            self._emit(events, event_log, "execution_check_failed", str(exc) or "execution_check_failed")
            return "execution_check_failed"
        execution_ok = bool(execution.get("ok")) if isinstance(execution, dict) else False
        execution_message = str(execution.get("message") or ("ok" if execution_ok else "paper execution unavailable")) if isinstance(execution, dict) else "paper execution unavailable"
        self._emit(events, event_log, "execution_checked", execution_message, execution={"ok": execution_ok, "message": execution_message})
        if not execution_ok:
            return "execution_unavailable"
        return None

    def _run_slice(self) -> MillisecondRuntimeResult:
        if self.slice_runner is not None:
            return self.slice_runner()
        runtime_settings = dict(self.config.runtime_settings or {})
        runner = MillisecondRuntimeRunner(
            config=MillisecondRuntimeConfig(
                symbols=self.config.symbols,
                max_cycles=int(self.config.slice_cycles),
                poll_interval_ms=int(self.config.poll_interval_ms),
                submit_to_paper=bool(self.config.submit_to_paper),
                base_dir=self.config.base_dir,
                engine=self.config.engine or MillisecondEngineConfig(),
                poll_retry_attempts=int(runtime_settings.get("poll_retry_attempts", 2)),
                poll_retry_backoff_ms=int(runtime_settings.get("poll_retry_backoff_ms", 100)),
                max_consecutive_poll_errors=int(runtime_settings.get("max_consecutive_poll_errors", 3)),
                require_execution_connection=bool_from_value(runtime_settings.get("require_execution_connection"), True),
            ),
            market_data_adapter=self.market_data_adapter,
            execution_adapter=self.execution_adapter,
            limits=self.limits,
        )
        return runner.run()

    def _finalize(
        self,
        run_dir: Path,
        run_id: str,
        status: str,
        exit_code: int,
        events: list[dict[str, Any]],
        child_runs: list[dict[str, Any]],
    ) -> MillisecondWatchdogResult:
        runtime_failure_count = sum(1 for event in events if event.get("event_type") == "slice_runtime_blocked")
        no_event_slice_count = sum(1 for event in events if event.get("event_type") == "slice_no_events")
        symbol_set_hash = hashlib.sha256(",".join(sorted(symbol.upper() for symbol in self.config.symbols)).encode("utf-8")).hexdigest()[:16]
        latency_samples: list[float] = []
        for item in child_runs:
            child_metrics = dict(item.get("metrics") or {})
            for key in ("latency_ms", "avg_latency_ms", "poll_latency_ms", "submit_latency_ms"):
                value = child_metrics.get(key)
                try:
                    normalized = float(value)
                except (TypeError, ValueError):
                    continue
                if normalized >= 0:
                    latency_samples.append(normalized)
        metrics = {
            "watchdog_ok": exit_code == 0,
            "status": status,
            "event_count": len(events),
            "child_run_count": len(child_runs),
            "active_slice_count": len(child_runs),
            "runtime_failure_count": runtime_failure_count,
            "no_event_slice_count": no_event_slice_count,
            "submit_to_paper": bool(self.config.submit_to_paper),
            "mode_badge": "submit-paper" if self.config.submit_to_paper else "dry-run",
            "dry_run": not bool(self.config.submit_to_paper),
            "paper_submit_mode": bool(self.config.submit_to_paper),
            "symbols": list(self.config.symbols),
            "symbol_set_hash": symbol_set_hash,
            "last_child_run_id": child_runs[-1]["run_id"] if child_runs else None,
            "last_child_run_output_dir": child_runs[-1]["output_dir"] if child_runs else None,
            "decision_count": sum(int((item.get("metrics") or {}).get("decision_count") or 0) for item in child_runs),
            "submit_count": sum(int((item.get("metrics") or {}).get("submit_count") or 0) for item in child_runs),
            "rejection_count": sum(int((item.get("metrics") or {}).get("rejection_count") or 0) for item in child_runs),
            "blocker_reason": status if exit_code else None,
            "near_close_no_new_order_proof": {
                "stop_time_et": self.config.stop_time_et,
                "new_orders_stop_before_close": True,
                "detail": "The watchdog trading loop only runs before the configured stop time.",
            },
            "latency_percentiles": _latency_percentiles(latency_samples),
        }
        summary = {
            "run_id": run_id,
            "status": status,
            "exit_code": exit_code,
            "metrics": metrics,
            "events": events,
            "child_runs": child_runs,
            "updated_at_ns": time.time_ns(),
            "output_dir": str(run_dir),
        }
        write_json_report(run_dir / "watchdog_summary.json", summary)
        self._write_heartbeat(run_id, run_dir, status=status, events=events, child_runs=child_runs)
        self._write_latest_index(summary)
        return MillisecondWatchdogResult(run_id=run_id, output_dir=str(run_dir), status=status, exit_code=exit_code, metrics=metrics)

    def _emit(self, events: list[dict[str, Any]], event_log: JsonlLogger, event_type: str, message: str, **metadata: Any) -> None:
        record = {
            "event_type": event_type,
            "message": message,
            "created_at_ns": time.time_ns(),
            "created_at_et": self._now_et().isoformat(),
            "metadata": metadata,
        }
        events.append(record)
        event_log.emit(record)

    def _now_et(self) -> datetime:
        now = self.now_provider()
        if now.tzinfo is None:
            return now.replace(tzinfo=ET)
        return now.astimezone(ET)

    @staticmethod
    def _time(value: str) -> clock_time:
        hour, minute = value.split(":", 1)
        return clock_time(hour=int(hour), minute=int(minute))

    def _in_trading_window(self, now: datetime) -> bool:
        return _is_market_day(now) and self._time(self.config.start_time_et) <= now.time() < self._time(self.config.stop_time_et)

    def _watchdog_root(self) -> Path:
        root = self.base_dir / "millisecond_watchdog"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _symbol_lock_key(self) -> str:
        normalized = "-".join(sorted(symbol.upper() for symbol in self.config.symbols if symbol))
        return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in normalized or "default")

    def _lock_path(self) -> Path:
        return self._watchdog_root() / f"watchdog_{self._symbol_lock_key()}.lock.json"

    def _latest_path(self) -> Path:
        return self._watchdog_root() / "latest.json"

    def _heartbeat_path(self, run_dir: Path) -> Path:
        return run_dir / "watchdog_heartbeat.json"

    def _lock_payload(self, run_id: str, run_dir: Path, *, status: str = "running") -> dict[str, Any]:
        return {
            "pid": getpid(),
            "run_id": run_id,
            "symbols": list(self.config.symbols),
            "symbol_key": self._symbol_lock_key(),
            "status": status,
            "created_at_ns": time.time_ns(),
            "heartbeat_at_ns": time.time_ns(),
            "output_dir": str(run_dir),
        }

    def _is_lock_stale(self, payload: dict[str, Any]) -> bool:
        heartbeat = int(payload.get("heartbeat_at_ns") or payload.get("created_at_ns") or 0)
        if heartbeat <= 0:
            return True
        age_seconds = (time.time_ns() - heartbeat) / 1_000_000_000
        return age_seconds > max(int(self.config.lock_ttl_seconds), 1)

    def _acquire_process_lock(
        self,
        run_id: str,
        run_dir: Path,
        events: list[dict[str, Any]],
        event_log: JsonlLogger,
    ) -> str | None:
        lock_path = self._lock_path()
        payload = self._lock_payload(run_id, run_dir)
        if lock_path.exists():
            try:
                existing = json.loads(lock_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}
            if existing and not self._is_lock_stale(existing):
                self._emit(
                    events,
                    event_log,
                    "watchdog_lock_active",
                    "another millisecond watchdog is already active for this symbol set",
                    lock_path=str(lock_path),
                    existing=existing,
                )
                return "watchdog_already_running"
            self._emit(events, event_log, "watchdog_stale_lock_reclaimed", "stale watchdog lock reclaimed", lock_path=str(lock_path), existing=existing)
            try:
                lock_path.unlink()
            except OSError:
                return "watchdog_lock_reclaim_failed"
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
        except FileExistsError:
            self._emit(events, event_log, "watchdog_lock_race", "watchdog lock appeared during startup", lock_path=str(lock_path))
            return "watchdog_already_running"
        self._emit(events, event_log, "watchdog_lock_acquired", "watchdog process lock acquired", lock_path=str(lock_path))
        self._write_heartbeat(run_id, run_dir, status="lock_acquired", events=events, child_runs=[])
        return None

    def _release_process_lock(self, run_id: str) -> None:
        lock_path = self._lock_path()
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
        except json.JSONDecodeError:
            payload = {}
        if str(payload.get("run_id") or "") == run_id:
            try:
                lock_path.unlink()
            except OSError:
                pass

    def _write_heartbeat(
        self,
        run_id: str,
        run_dir: Path,
        *,
        status: str,
        events: list[dict[str, Any]],
        child_runs: list[dict[str, Any]],
    ) -> None:
        heartbeat = {
            "pid": getpid(),
            "run_id": run_id,
            "status": status,
            "symbols": list(self.config.symbols),
            "heartbeat_at_ns": time.time_ns(),
            "heartbeat_at_et": self._now_et().isoformat(),
            "event_count": len(events),
            "child_run_count": len(child_runs),
            "active_slice_count": len(child_runs),
            "runtime_failure_count": sum(1 for event in events if event.get("event_type") == "slice_runtime_blocked"),
            "no_event_slice_count": sum(1 for event in events if event.get("event_type") == "slice_no_events"),
            "last_child_run_id": child_runs[-1]["run_id"] if child_runs else None,
            "output_dir": str(run_dir),
        }
        write_json_report(self._heartbeat_path(run_dir), heartbeat)
        lock_path = self._lock_path()
        if lock_path.exists():
            try:
                lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                lock_payload = {}
            if str(lock_payload.get("run_id") or "") == run_id:
                lock_payload.update({"heartbeat_at_ns": heartbeat["heartbeat_at_ns"], "status": status, "output_dir": str(run_dir)})
                write_json_report(lock_path, lock_payload)

    def _write_latest_index(self, summary: dict[str, Any]) -> None:
        latest = {
            "run_id": summary.get("run_id"),
            "status": summary.get("status"),
            "exit_code": summary.get("exit_code"),
            "output_dir": summary.get("output_dir"),
            "updated_at_ns": summary.get("updated_at_ns") or time.time_ns(),
            "metrics": summary.get("metrics") or {},
            "latest_child_run_id": (summary.get("metrics") or {}).get("last_child_run_id"),
            "latest_child_run_output_dir": (summary.get("metrics") or {}).get("last_child_run_output_dir"),
            "blocker_reason": (summary.get("metrics") or {}).get("blocker_reason"),
        }
        write_json_report(self._latest_path(), latest)


def _is_market_day(now: datetime) -> bool:
    return now.weekday() < 5


def _age_seconds_from_ns(value: Any) -> float | None:
    try:
        timestamp_ns = int(value or 0)
    except (TypeError, ValueError):
        return None
    if timestamp_ns <= 0:
        return None
    return max((time.time_ns() - timestamp_ns) / 1_000_000_000, 0.0)


def _latency_percentiles(samples: list[float]) -> dict[str, float | int | None]:
    if not samples:
        return {"sample_count": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None}
    ordered = sorted(float(value) for value in samples if value >= 0)
    if not ordered:
        return {"sample_count": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None}

    def percentile(pct: float) -> float:
        index = min(max(int(round((len(ordered) - 1) * pct)), 0), len(ordered) - 1)
        return round(ordered[index], 3)

    return {
        "sample_count": len(ordered),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
    }


def _annotate_watchdog_payload(payload: dict[str, Any], *, lock_ttl_seconds: int = 120) -> dict[str, Any]:
    annotated = dict(payload or {})
    updated_age = _age_seconds_from_ns(annotated.get("updated_at_ns"))
    heartbeat_age = _age_seconds_from_ns(annotated.get("heartbeat_at_ns"))
    created_age = _age_seconds_from_ns(annotated.get("created_at_ns"))
    if updated_age is not None:
        annotated["updated_age_seconds"] = round(updated_age, 3)
    if heartbeat_age is not None:
        annotated["heartbeat_age_seconds"] = round(heartbeat_age, 3)
    if created_age is not None:
        annotated["age_seconds"] = round(created_age, 3)
    stale_basis = heartbeat_age if heartbeat_age is not None else created_age
    annotated["stale"] = bool(stale_basis is not None and stale_basis > max(int(lock_ttl_seconds), 1))
    return annotated


def read_watchdog_status(base_dir: str | Path = "data") -> dict[str, Any]:
    root = Path(base_dir) / "millisecond_watchdog"
    latest_path = root / "latest.json"
    lock_paths = sorted(root.glob("watchdog_*.lock.json")) if root.exists() else []
    locks: list[dict[str, Any]] = []
    for lock_path in lock_paths:
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"status": "decode_error", "path": str(lock_path)}
        payload["path"] = str(lock_path)
        locks.append(_annotate_watchdog_payload(payload))
    if latest_path.exists():
        try:
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            latest = {"status": "decode_error", "path": str(latest_path)}
    else:
        latest = {"status": "not_started", "path": str(latest_path)}
    latest = _annotate_watchdog_payload(latest)
    heartbeat = {}
    output_dir = str(latest.get("output_dir") or "").strip()
    if output_dir:
        latest["summary_path"] = str(Path(output_dir) / "watchdog_summary.json")
        latest["events_path"] = str(Path(output_dir) / "watchdog_events.jsonl")
        if latest.get("latest_child_run_id"):
            latest["latest_child_run_link"] = latest.get("latest_child_run_output_dir") or f"data/millisecond/run_id={latest.get('latest_child_run_id')}"
        if latest.get("blocker_reason"):
            latest["latest_blocker_link"] = latest["summary_path"]
        heartbeat_path = Path(output_dir) / "watchdog_heartbeat.json"
        if heartbeat_path.exists():
            try:
                heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                heartbeat = {"status": "decode_error", "path": str(heartbeat_path)}
            heartbeat["path"] = str(heartbeat_path)
            heartbeat = _annotate_watchdog_payload(heartbeat)
    return {
        "root": str(root),
        "latest": latest,
        "heartbeat": heartbeat,
        "active_locks": locks,
        "active_lock_count": len(locks),
        "stale_lock_count": sum(1 for item in locks if item.get("stale")),
    }


def cleanup_watchdog_locks(
    base_dir: str | Path = "data",
    *,
    max_age_seconds: int = 120,
    force: bool = False,
) -> dict[str, Any]:
    root = Path(base_dir) / "millisecond_watchdog"
    lock_paths = sorted(root.glob("watchdog_*.lock.json")) if root.exists() else []
    removed: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    for lock_path in lock_paths:
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"status": "decode_error", "path": str(lock_path)}
        payload["path"] = str(lock_path)
        annotated = _annotate_watchdog_payload(payload, lock_ttl_seconds=max_age_seconds)
        should_remove = bool(force or annotated.get("stale") or annotated.get("status") == "decode_error")
        if should_remove:
            try:
                lock_path.unlink()
                annotated["removed"] = True
                removed.append(annotated)
            except OSError as exc:
                annotated["removed"] = False
                annotated["error"] = str(exc)
                retained.append(annotated)
        else:
            annotated["removed"] = False
            retained.append(annotated)
    return {
        "root": str(root),
        "removed_count": len(removed),
        "retained_count": len(retained),
        "removed": removed,
        "retained": retained,
        "force": bool(force),
        "max_age_seconds": int(max_age_seconds),
    }


def _feed_record(feed: FeedHealthSnapshot) -> dict[str, Any]:
    return {
        "ok": feed.ok,
        "provider": feed.provider,
        "feed": feed.feed,
        "status_code": feed.status_code,
        "symbol_count": feed.symbol_count,
        "message": feed.message,
        "metadata": feed.metadata,
    }
