from __future__ import annotations
import argparse, json, os, signal, sys, time
from pathlib import Path
from institutional_trading.accounts.reconciliation import ReconciliationService
from institutional_trading.audit.logger import HashChainedAuditLogger
from institutional_trading.audit.replay import read_replay_events
from institutional_trading.config.loader import (
    ConfigError,
    DEFAULT_CONFIG_PATH,
    audit_paths_from_config,
    build_risk_limits,
    load_config,
    runtime_dir_from_config,
    validate_paper_safe_config,
    watchdog_interval_from_config,
)
from institutional_trading.execution.paper import PaperBrokerAdapter
from institutional_trading.risk.engine import RiskEngine
from institutional_trading.service.runner import TradingService

def build_service(config_path: str | Path | None = None, runtime_dir: str | Path | None = None) -> TradingService:
    config = load_config(config_path)
    validate_paper_safe_config(config)
    resolved_runtime_dir = runtime_dir_from_config(config, runtime_dir)
    audit_log, sqlite_index = audit_paths_from_config(config, resolved_runtime_dir, runtime_overridden=runtime_dir is not None)
    risk_engine = RiskEngine(build_risk_limits(config))
    if bool(config.get("risk", {}).get("global_kill_switch_default", False)):
        risk_engine.kill_switch.trip_global("config_default")
    return TradingService(PaperBrokerAdapter(), risk_engine, HashChainedAuditLogger(audit_log, sqlite_index), resolved_runtime_dir)

def run_service(service: TradingService, *, config_path: str, heartbeat_interval: float, max_heartbeats: int | None = None) -> int:
    stop = False
    def _stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _stop)
    service.start(pid=os.getpid(), config_path=config_path)
    heartbeats = 0
    try:
        while not stop:
            service.heartbeat(pid=os.getpid(), config_path=config_path)
            heartbeats += 1
            if max_heartbeats is not None and heartbeats >= max_heartbeats:
                break
            time.sleep(heartbeat_interval)
    finally:
        service.stop(pid=os.getpid(), config_path=config_path)
    return 0

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="institutional-trading"); p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH)); p.add_argument("--runtime-dir", default=None); sub = p.add_subparsers(dest="command", required=True)
    for n in ("start","stop","status","health","backtest","reconcile"): sub.add_parser(n)
    run = sub.add_parser("run"); run.add_argument("--heartbeat-interval", type=float, default=None); run.add_argument("--max-heartbeats", type=int, default=None)
    k = sub.add_parser("kill"); k.add_argument("--reason", required=True); r = sub.add_parser("replay"); r.add_argument("--audit-log", required=True); args = p.parse_args(argv)
    try:
        svc = build_service(args.config, args.runtime_dir)
    except ConfigError as exc:
        print(json.dumps({"status": "error", "reason": "config_error", "detail": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    config_path = str(Path(args.config))
    if args.command == "run":
        interval = args.heartbeat_interval if args.heartbeat_interval is not None else watchdog_interval_from_config(load_config(args.config))
        return run_service(svc, config_path=config_path, heartbeat_interval=interval, max_heartbeats=args.max_heartbeats)
    if args.command == "start": print(json.dumps(svc.start(config_path=config_path).to_record(), indent=2, sort_keys=True))
    elif args.command == "stop": print(json.dumps(svc.stop(config_path=config_path).to_record(), indent=2, sort_keys=True))
    elif args.command == "status": print(json.dumps(svc.status(), indent=2, sort_keys=True))
    elif args.command == "health": print(json.dumps(svc.health_status(), indent=2, sort_keys=True))
    elif args.command == "kill": print(json.dumps(svc.kill(args.reason).to_record(), indent=2, sort_keys=True))
    elif args.command == "replay":
        events = read_replay_events(args.audit_log); print(json.dumps({"events": len(events), "first_sequence": events[0].sequence if events else None}, indent=2, sort_keys=True))
    elif args.command == "reconcile":
        rep = ReconciliationService().reconcile(fills=[], broker_positions={}); print(json.dumps({"clean": rep.clean, "breaks": len(rep.breaks)}, indent=2, sort_keys=True))
    elif args.command == "backtest": print(json.dumps({"status": "ready", "detail": "Use BacktestEngine from Python for configured scenario runs."}, indent=2, sort_keys=True))
    return 0
if __name__ == "__main__": raise SystemExit(main())
