from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from hft.live.market_data import AlpacaEquityMarketDataAdapter
from hft.live.paper_execution import AlpacaPaperExecutionAdapter
from hft.millisecond import (
    MillisecondEngineConfig,
    MillisecondRuntimeConfig,
    MillisecondRuntimeRunner,
    MillisecondWatchdog,
    MillisecondWatchdogConfig,
    cleanup_watchdog_locks,
    read_watchdog_status,
)
from hft.risk.limits import GlobalRiskLimits, HFTLimitConfig, SymbolRiskLimits
from hft.utils.env import bool_from_value

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_path(path: str | Path) -> Path:
    target = Path(path)
    if target.is_absolute():
        return target
    if target.exists():
        return target.resolve()
    return (PROJECT_ROOT / target).resolve()


def _read_yaml(path: str | Path) -> dict[str, Any]:
    target = _resolve_path(path)
    if not target.exists():
        return {}
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}


def _load_symbols(path: str | Path, requested: list[str] | None = None) -> tuple[str, ...]:
    payload = _read_yaml(path)
    enabled = []
    wanted = {symbol.upper() for symbol in (requested or [])}
    for item in payload.get("symbols", []):
        symbol = str(item.get("symbol") or "").upper()
        if not symbol or not item.get("enabled", True):
            continue
        if wanted and symbol not in wanted:
            continue
        enabled.append(symbol)
    return tuple(enabled)


def _load_limits(path: str | Path) -> HFTLimitConfig:
    payload = _read_yaml(path)
    global_payload = dict(payload.get("global") or {})
    symbol_payload = dict(payload.get("symbols") or {})
    global_limits = GlobalRiskLimits(**global_payload)
    default_symbol = SymbolRiskLimits(**dict(symbol_payload.get("default") or {}))
    symbols = {
        str(symbol).upper(): SymbolRiskLimits(**dict(values or {}))
        for symbol, values in symbol_payload.items()
        if str(symbol).lower() != "default"
    }
    return HFTLimitConfig(global_limits=global_limits, symbol_limits={"DEFAULT": default_symbol, **symbols})


def _load_engine_config(path: str | Path) -> MillisecondEngineConfig:
    payload = _read_yaml(path)
    engine_payload = dict(payload.get("engine") or payload)
    allowed_sessions = tuple(
        str(item).strip().lower()
        for item in engine_payload.get("allowed_sessions", ["regular"])
        if str(item).strip()
    )
    return MillisecondEngineConfig(
        enabled=bool(engine_payload.get("enabled", True)),
        strategy_name=str(engine_payload.get("strategy_name") or "millisecond_micro_scalper"),
        order_quantity=float(engine_payload.get("order_quantity", 1.0)),
        min_edge_bps=float(engine_payload.get("min_edge_bps", 1.5)),
        max_spread_bps=float(engine_payload.get("max_spread_bps", 8.0)),
        max_quote_age_ns=int(engine_payload.get("max_quote_age_ns", 500_000_000)),
        max_decision_latency_ns=int(engine_payload.get("max_decision_latency_ns", 2_000_000)),
        min_order_interval_ms=int(engine_payload.get("min_order_interval_ms", 250)),
        max_orders_per_second=int(engine_payload.get("max_orders_per_second", 2)),
        allowed_sessions=allowed_sessions or ("regular",),
        price_offset_bps=float(engine_payload.get("price_offset_bps", 0.5)),
    )


def _load_runtime_settings(path: str | Path) -> dict[str, Any]:
    payload = _read_yaml(path)
    return dict(payload.get("runtime") or {})


def run_millisecond_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the millisecond-decision HFT research engine.")
    parser.add_argument("--base-dir", default="data")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--symbols", default="AAPL")
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--poll-interval-ms", type=int, default=10)
    parser.add_argument("--submit-paper", action="store_true")
    parser.add_argument("--symbols-config", default="configs/symbols.yaml")
    parser.add_argument("--risk-config", default="configs/risk_limits.yaml")
    parser.add_argument("--millisecond-config", default="configs/millisecond.yaml")
    args = parser.parse_args(argv)

    symbol_filter = [symbol.strip().upper() for symbol in str(args.symbols or "").split(",") if symbol.strip()]
    symbols = _load_symbols(_resolve_path(args.symbols_config), requested=symbol_filter)
    if not symbols:
        symbols = tuple(symbol_filter)
    millisecond_config_path = _resolve_path(args.millisecond_config)
    runtime_settings = _load_runtime_settings(millisecond_config_path)
    runner = MillisecondRuntimeRunner(
        config=MillisecondRuntimeConfig(
            symbols=symbols,
            max_cycles=int(args.cycles),
            poll_interval_ms=int(args.poll_interval_ms),
            submit_to_paper=bool(args.submit_paper),
            base_dir=Path(args.base_dir),
            engine=_load_engine_config(millisecond_config_path),
            poll_retry_attempts=int(runtime_settings.get("poll_retry_attempts", 2)),
            poll_retry_backoff_ms=int(runtime_settings.get("poll_retry_backoff_ms", 100)),
            max_consecutive_poll_errors=int(runtime_settings.get("max_consecutive_poll_errors", 3)),
            require_execution_connection=bool_from_value(runtime_settings.get("require_execution_connection"), True),
        ),
        market_data_adapter=AlpacaEquityMarketDataAdapter.from_env(args.env_file),
        execution_adapter=AlpacaPaperExecutionAdapter.from_env(args.env_file),
        limits=_load_limits(_resolve_path(args.risk_config)),
    )
    result = runner.run()
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "output_dir": result.output_dir,
                "submit_to_paper": result.submit_to_paper,
                "metrics": result.metrics,
            },
            indent=2,
        )
    )
    return 0 if result.metrics.get("runtime_ok") else 1


def run_watchdog_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Supervise market-hours millisecond HFT paper slices.")
    parser.add_argument("--base-dir", default="data")
    parser.add_argument("--status", action="store_true", help="Print the latest watchdog artifact index and active locks.")
    parser.add_argument("--cleanup-locks", action="store_true", help="Remove stale watchdog lock files and print the cleanup report.")
    parser.add_argument("--force-cleanup-locks", action="store_true", help="Remove watchdog lock files even when they do not look stale.")
    parser.add_argument("--lock-ttl-seconds", type=int, default=120)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--symbols", default="AAPL")
    parser.add_argument("--preflight-time-et", default="09:25")
    parser.add_argument("--start-time-et", default="09:35")
    parser.add_argument("--stop-time-et", default="15:55")
    parser.add_argument("--slice-cycles", type=int, default=20)
    parser.add_argument("--poll-interval-ms", type=int, default=10)
    parser.add_argument("--slice-interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-slices", type=int, default=0)
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    parser.add_argument("--max-consecutive-no-event-slices", type=int, default=10)
    parser.add_argument("--wait-for-window", action="store_true")
    parser.add_argument("--dry-run", dest="submit_paper", action="store_false")
    parser.add_argument("--submit-paper", dest="submit_paper", action="store_true")
    parser.set_defaults(submit_paper=False)
    parser.add_argument("--symbols-config", default="configs/symbols.yaml")
    parser.add_argument("--risk-config", default="configs/risk_limits.yaml")
    parser.add_argument("--millisecond-config", default="configs/millisecond.yaml")
    args = parser.parse_args(argv)

    if bool(args.status):
        print(json.dumps(read_watchdog_status(Path(args.base_dir)), indent=2))
        return 0
    if bool(args.cleanup_locks):
        print(
            json.dumps(
                cleanup_watchdog_locks(
                    Path(args.base_dir),
                    max_age_seconds=int(args.lock_ttl_seconds),
                    force=bool(args.force_cleanup_locks),
                ),
                indent=2,
            )
        )
        return 0

    symbol_filter = [symbol.strip().upper() for symbol in str(args.symbols or "").split(",") if symbol.strip()]
    symbols = _load_symbols(_resolve_path(args.symbols_config), requested=symbol_filter)
    if not symbols:
        symbols = tuple(symbol_filter)
    millisecond_config_path = _resolve_path(args.millisecond_config)
    watchdog = MillisecondWatchdog(
        config=MillisecondWatchdogConfig(
            symbols=symbols,
            base_dir=Path(args.base_dir),
            preflight_time_et=str(args.preflight_time_et),
            start_time_et=str(args.start_time_et),
            stop_time_et=str(args.stop_time_et),
            slice_cycles=int(args.slice_cycles),
            poll_interval_ms=int(args.poll_interval_ms),
            slice_interval_seconds=float(args.slice_interval_seconds),
            submit_to_paper=bool(args.submit_paper),
            wait_for_window=bool(args.wait_for_window),
            max_slices=int(args.max_slices),
            max_consecutive_failures=int(args.max_consecutive_failures),
            max_consecutive_no_event_slices=int(args.max_consecutive_no_event_slices),
            engine=_load_engine_config(millisecond_config_path),
            runtime_settings=_load_runtime_settings(millisecond_config_path),
        ),
        market_data_adapter=AlpacaEquityMarketDataAdapter.from_env(args.env_file),
        execution_adapter=AlpacaPaperExecutionAdapter.from_env(args.env_file),
        limits=_load_limits(_resolve_path(args.risk_config)),
    )
    result = watchdog.run()
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "output_dir": result.output_dir,
                "status": result.status,
                "exit_code": result.exit_code,
                "metrics": result.metrics,
            },
            indent=2,
        )
    )
    return int(result.exit_code)


if __name__ == "__main__":
    raise SystemExit(run_millisecond_main())
