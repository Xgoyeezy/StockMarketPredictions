from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hft.backtest.reports import write_json_report
from hft.execution.order_state import OrderLifecycleState
from hft.live.market_data import LiveMarketDataAdapter
from hft.live.paper_execution import PaperExecutionAdapter, PaperExecutionReport
from hft.millisecond.engine import MillisecondDecision, MillisecondEngineConfig, MillisecondSignalEngine
from hft.risk.checks import HFTRiskEngine
from hft.risk.limits import HFTLimitConfig
from hft.utils.ids import NamedIdPool
from hft.utils.logging import JsonlLogger


@dataclass(frozen=True)
class MillisecondRuntimeConfig:
    symbols: tuple[str, ...]
    max_cycles: int = 1
    poll_interval_ms: int = 10
    submit_to_paper: bool = False
    base_dir: str | Path = "data"
    engine: MillisecondEngineConfig = field(default_factory=MillisecondEngineConfig)
    poll_retry_attempts: int = 2
    poll_retry_backoff_ms: int = 100
    max_consecutive_poll_errors: int = 3
    require_execution_connection: bool = True


@dataclass(frozen=True)
class MillisecondRuntimeResult:
    run_id: str
    output_dir: str
    decisions: list[MillisecondDecision]
    reports: list[PaperExecutionReport]
    metrics: dict[str, Any]
    submit_to_paper: bool


class MillisecondRuntimeRunner:
    def __init__(
        self,
        *,
        config: MillisecondRuntimeConfig,
        market_data_adapter: LiveMarketDataAdapter,
        execution_adapter: PaperExecutionAdapter,
        limits: HFTLimitConfig,
    ):
        self.config = config
        self.market_data_adapter = market_data_adapter
        self.execution_adapter = execution_adapter
        self.limits = limits
        self.engine = MillisecondSignalEngine(config=config.engine)
        self.ids = NamedIdPool()
        self.risk_engines = {symbol.upper(): HFTRiskEngine(limits=limits) for symbol in config.symbols}
        self.base_dir = Path(config.base_dir)

    def run(self) -> MillisecondRuntimeResult:
        run_id = f"millisecond_run-{time.time_ns()}"
        run_dir = self.base_dir / "millisecond" / f"run_id={run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        decision_log = JsonlLogger(run_dir / "decisions.jsonl")
        report_log = JsonlLogger(run_dir / "paper_reports.jsonl")
        runtime_log = JsonlLogger(run_dir / "runtime_events.jsonl")

        decisions: list[MillisecondDecision] = []
        reports: list[PaperExecutionReport] = []
        runtime_events: list[dict[str, Any]] = []
        poll_error_count = 0
        recovered_poll_error_count = 0
        consecutive_poll_errors = 0
        execution_ok = True
        execution_message = "not_checked"

        try:
            feed = self.market_data_adapter.check_feed(list(self.config.symbols))
        except Exception as exc:
            feed_message = str(exc) or "market_data_feed_check_failed"
            runtime_event = _runtime_event("feed_check_failed", feed_message)
            runtime_events.append(runtime_event)
            runtime_log.emit(runtime_event)
            metrics = self._metrics(
                decisions,
                reports,
                feed_ok=False,
                feed_message=feed_message,
                execution_ok=False if self.config.submit_to_paper else True,
                execution_message="not_checked",
                runtime_status="feed_check_failed",
                runtime_event_count=len(runtime_events),
            )
            self._write_summary(run_dir, run_id, decisions, reports, metrics, runtime_events)
            return MillisecondRuntimeResult(run_id, str(run_dir), decisions, reports, metrics, self.config.submit_to_paper)

        if not feed.ok:
            runtime_event = _runtime_event("feed_unavailable", feed.message)
            runtime_events.append(runtime_event)
            runtime_log.emit(runtime_event)
            metrics = self._metrics(
                decisions,
                reports,
                feed_ok=False,
                feed_message=feed.message,
                execution_ok=False if self.config.submit_to_paper else True,
                execution_message="not_checked",
                runtime_status="feed_unavailable",
                runtime_event_count=len(runtime_events),
            )
            self._write_summary(run_dir, run_id, decisions, reports, metrics, runtime_events)
            return MillisecondRuntimeResult(run_id, str(run_dir), decisions, reports, metrics, self.config.submit_to_paper)

        if self.config.submit_to_paper and self.config.require_execution_connection:
            execution_ok, execution_message = self._check_execution_connection()
            if not execution_ok:
                runtime_event = _runtime_event("execution_unavailable", execution_message)
                runtime_events.append(runtime_event)
                runtime_log.emit(runtime_event)
                metrics = self._metrics(
                    decisions,
                    reports,
                    feed_ok=True,
                    feed_message=feed.message,
                    execution_ok=False,
                    execution_message=execution_message,
                    runtime_status="execution_unavailable",
                    runtime_event_count=len(runtime_events),
                )
                self._write_summary(run_dir, run_id, decisions, reports, metrics, runtime_events)
                return MillisecondRuntimeResult(run_id, str(run_dir), decisions, reports, metrics, self.config.submit_to_paper)

        for cycle in range(max(int(self.config.max_cycles), 0)):
            events, poll_error, recovered_errors = self._poll_with_retries()
            if poll_error:
                poll_error_count += int(poll_error["attempts"])
                consecutive_poll_errors += 1
                runtime_event = _runtime_event(
                    "market_data_poll_failed",
                    str(poll_error["message"]),
                    cycle=cycle,
                    attempts=int(poll_error["attempts"]),
                )
                runtime_events.append(runtime_event)
                runtime_log.emit(runtime_event)
                if consecutive_poll_errors >= max(int(self.config.max_consecutive_poll_errors), 1):
                    metrics = self._metrics(
                        decisions,
                        reports,
                        feed_ok=False,
                        feed_message=str(poll_error["message"]) or "market_data_poll_failed",
                        poll_error_count=poll_error_count,
                        recovered_poll_error_count=recovered_poll_error_count,
                        execution_ok=execution_ok,
                        execution_message=execution_message,
                        runtime_status="poll_error_circuit_open",
                        runtime_event_count=len(runtime_events),
                    )
                    self._write_summary(run_dir, run_id, decisions, reports, metrics, runtime_events)
                    return MillisecondRuntimeResult(
                        run_id,
                        str(run_dir),
                        decisions,
                        reports,
                        metrics,
                        self.config.submit_to_paper,
                    )
                if self.config.poll_interval_ms > 0 and cycle < self.config.max_cycles - 1:
                    time.sleep(self.config.poll_interval_ms / 1000.0)
                continue
            if recovered_errors:
                poll_error_count += recovered_errors
                recovered_poll_error_count += recovered_errors
                runtime_event = _runtime_event(
                    "market_data_poll_recovered",
                    "market data poll recovered after retry",
                    cycle=cycle,
                    recovered_errors=recovered_errors,
                )
                runtime_events.append(runtime_event)
                runtime_log.emit(runtime_event)
            if consecutive_poll_errors:
                recovered_poll_error_count += consecutive_poll_errors
            consecutive_poll_errors = 0
            for event in sorted(events):
                try:
                    decision = self.engine.process_event(event)
                except Exception as exc:
                    runtime_event = _runtime_event(
                        "decision_exception",
                        str(exc) or "decision_engine_failed",
                        cycle=cycle,
                        symbol=getattr(event, "symbol", ""),
                    )
                    runtime_events.append(runtime_event)
                    runtime_log.emit(runtime_event)
                    continue
                decisions.append(decision)
                decision_log.emit({**decision.to_record(), "cycle": cycle})
                if decision.action != "submit":
                    continue
                report = self._handle_submit_decision(decision)
                reports.append(report)
                report_log.emit({**report.__dict__, "state": report.state.value})
            if self.config.poll_interval_ms > 0 and cycle < self.config.max_cycles - 1:
                time.sleep(self.config.poll_interval_ms / 1000.0)

        metrics = self._metrics(
            decisions,
            reports,
            feed_ok=True,
            feed_message=feed.message,
            poll_error_count=poll_error_count,
            recovered_poll_error_count=recovered_poll_error_count,
            execution_ok=execution_ok,
            execution_message=execution_message,
            runtime_status="completed",
            runtime_event_count=len(runtime_events),
        )
        self._write_summary(run_dir, run_id, decisions, reports, metrics, runtime_events)
        return MillisecondRuntimeResult(run_id, str(run_dir), decisions, reports, metrics, self.config.submit_to_paper)

    def _poll_with_retries(self) -> tuple[list[Any], dict[str, Any] | None, int]:
        last_error = ""
        max_attempts = max(int(self.config.poll_retry_attempts), 0) + 1
        for attempt in range(max_attempts):
            try:
                return self.market_data_adapter.poll(list(self.config.symbols)), None, attempt
            except Exception as exc:
                last_error = str(exc) or exc.__class__.__name__
                if attempt < max_attempts - 1 and self.config.poll_retry_backoff_ms > 0:
                    sleep_ms = max(int(self.config.poll_retry_backoff_ms), 0) * (attempt + 1)
                    time.sleep(sleep_ms / 1000.0)
        return [], {"message": last_error or "market_data_poll_failed", "attempts": max_attempts}, 0

    def _check_execution_connection(self) -> tuple[bool, str]:
        try:
            status = self.execution_adapter.check_connection()
        except Exception as exc:
            return False, str(exc) or "paper_execution_check_failed"
        ok = bool(status.get("ok")) if isinstance(status, dict) else False
        message = str(status.get("message") or ("ok" if ok else "paper execution unavailable")) if isinstance(status, dict) else "paper execution unavailable"
        return ok, message

    def _handle_submit_decision(self, decision: MillisecondDecision) -> PaperExecutionReport:
        risk_engine = self.risk_engines[decision.symbol.upper()]
        intent = decision.to_paper_intent(
            order_id=self.ids.next("ms_paper_order"),
            strategy_name=self.config.engine.strategy_name,
        )
        risk_engine.update_runtime_state(
            latest_spread=float(decision.metadata.get("spread") or 0.0),
            latest_volatility=0.0,
            inventory=0.0,
            notional_exposure=0.0,
        )
        risk = risk_engine.validate_order(intent)
        risk_engine.record_message(intent.decision_timestamp)
        if not risk.allowed:
            intent.risk_checked = False
            intent.risk_reason = risk.reason
            return _rejected_report(intent, reason=risk.reason)
        intent.risk_checked = True
        intent.risk_reason = risk.reason
        if not self.config.submit_to_paper:
            return _simulated_report(intent, reason="dry_run_not_submitted")
        try:
            return self.execution_adapter.submit_order(intent)
        except Exception as exc:
            return _rejected_report(intent, reason=f"submit_exception:{str(exc) or exc.__class__.__name__}")

    @staticmethod
    def _metrics(
        decisions: list[MillisecondDecision],
        reports: list[PaperExecutionReport],
        *,
        feed_ok: bool,
        feed_message: str,
        poll_error_count: int = 0,
        recovered_poll_error_count: int = 0,
        execution_ok: bool = True,
        execution_message: str = "not_checked",
        runtime_status: str = "completed",
        runtime_event_count: int = 0,
    ) -> dict[str, Any]:
        latencies = [decision.decision_latency_ns for decision in decisions]
        sorted_latencies = sorted(latencies)
        submitted = [decision for decision in decisions if decision.action == "submit"]
        blocked = [decision for decision in decisions if decision.blocked]
        accepted_reports = [report for report in reports if report.accepted]
        submit_errors = [report for report in reports if str(report.reason).startswith("submit_exception:")]
        runtime_ok = bool(feed_ok and execution_ok and runtime_status == "completed" and not submit_errors)
        return {
            "runtime_ok": runtime_ok,
            "runtime_status": runtime_status,
            "feed_ok": feed_ok,
            "feed_message": feed_message,
            "execution_ok": execution_ok,
            "execution_message": execution_message,
            "poll_error_count": poll_error_count,
            "recovered_poll_error_count": recovered_poll_error_count,
            "runtime_event_count": runtime_event_count,
            "decision_count": len(decisions),
            "submit_decision_count": len(submitted),
            "blocked_decision_count": len(blocked),
            "paper_report_count": len(reports),
            "accepted_report_count": len(accepted_reports),
            "submit_error_count": len(submit_errors),
            "p50_decision_latency_ns": _percentile(sorted_latencies, 0.50),
            "p95_decision_latency_ns": _percentile(sorted_latencies, 0.95),
            "p99_decision_latency_ns": _percentile(sorted_latencies, 0.99),
            "max_decision_latency_ns": max(latencies, default=0),
        }

    @staticmethod
    def _write_summary(
        run_dir: Path,
        run_id: str,
        decisions: list[MillisecondDecision],
        reports: list[PaperExecutionReport],
        metrics: dict[str, Any],
        runtime_events: list[dict[str, Any]],
    ) -> None:
        write_json_report(
            run_dir / "session_summary.json",
            {
                "run_id": run_id,
                "metrics": metrics,
                "runtime_events": runtime_events,
                "decisions": [decision.to_record() for decision in decisions],
                "paper_reports": [_report_record(report) for report in reports],
            },
        )


def _percentile(sorted_values: list[int], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(max(int(round((len(sorted_values) - 1) * percentile)), 0), len(sorted_values) - 1)
    return float(sorted_values[index])


def _report_record(report: PaperExecutionReport) -> dict[str, Any]:
    payload = dict(report.__dict__)
    payload["state"] = report.state.value
    return payload


def _runtime_event(event_type: str, message: str, **metadata: Any) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "message": str(message),
        "created_at_ns": time.time_ns(),
        "metadata": metadata,
    }


def _simulated_report(intent, *, reason: str) -> PaperExecutionReport:
    now_ns = time.time_ns()
    return PaperExecutionReport(
        order_id=intent.order_id,
        broker_order_id=f"dry-{intent.order_id}",
        symbol=intent.symbol,
        strategy_name=intent.strategy_name,
        side=intent.side,
        price=float(intent.price),
        quantity=float(intent.quantity),
        filled_quantity=0.0,
        average_fill_price=0.0,
        decision_timestamp=int(intent.decision_timestamp),
        send_timestamp=now_ns,
        exchange_receive_timestamp=now_ns,
        ack_timestamp=now_ns,
        updated_at_ns=now_ns,
        fill_timestamp=None,
        state=OrderLifecycleState.SENT,
        accepted=True,
        reason=reason,
        metadata={**dict(intent.metadata), "paper_submitted": False},
    )


def _rejected_report(intent, *, reason: str) -> PaperExecutionReport:
    now_ns = time.time_ns()
    return PaperExecutionReport(
        order_id=intent.order_id,
        broker_order_id="",
        symbol=intent.symbol,
        strategy_name=intent.strategy_name,
        side=intent.side,
        price=float(intent.price),
        quantity=float(intent.quantity),
        filled_quantity=0.0,
        average_fill_price=0.0,
        decision_timestamp=int(intent.decision_timestamp),
        send_timestamp=now_ns,
        exchange_receive_timestamp=now_ns,
        ack_timestamp=now_ns,
        updated_at_ns=now_ns,
        fill_timestamp=None,
        state=OrderLifecycleState.REJECTED,
        accepted=False,
        reason=reason,
        metadata={**dict(intent.metadata), "paper_submitted": False},
    )
