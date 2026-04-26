from __future__ import annotations

import argparse
import json
from pathlib import Path

from hft.market_data.schemas import MarketEvent
from hft.optimization.calibration import (
    build_queue_observations,
    calibrate_latency_from_events,
    calibrate_queue_model_from_events,
    evaluate_fill_model,
)
from hft.optimization.reports import write_optimization_report
from hft.optimization.search import optimize_market_making
from hft.optimization.types import OptimizationRunConfig
from hft.order_book.replay import ReplayEventStream
from hft.risk.limits import HFTLimitConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone HFT optimization tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    calibrate = subparsers.add_parser("calibrate", help="Calibrate latency and queue models from replay data")
    calibrate.add_argument("--input", required=True, help="Replay JSONL or Parquet file")
    calibrate.add_argument("--output", required=True, help="Directory for calibration artifact")

    search = subparsers.add_parser("search", help="Run market-making optimization")
    search.add_argument("--session-dir", required=True, help="Directory containing session replay files")
    search.add_argument("--output", required=True, help="Optimization base directory")

    validate = subparsers.add_parser("validate", help="Validate a single optimization run directory")
    validate.add_argument("--run-dir", required=True, help="Optimization run directory")

    champion = subparsers.add_parser("champion-report", help="Print champion report summary")
    champion.add_argument("--run-dir", required=True, help="Optimization run directory")

    args = parser.parse_args(argv)
    if args.command == "calibrate":
        return _run_calibration(Path(args.input), Path(args.output))
    if args.command == "search":
        return _run_search(Path(args.session_dir), Path(args.output))
    if args.command == "validate":
        return _run_validate(Path(args.run_dir))
    if args.command == "champion-report":
        return _run_champion_report(Path(args.run_dir))
    return 1


def _run_calibration(input_path: Path, output_dir: Path) -> int:
    events = list(ReplayEventStream.from_path(input_path))
    latency = calibrate_latency_from_events(events)
    queue = calibrate_queue_model_from_events(events)
    fill_report = evaluate_fill_model(build_queue_observations(events), artifact=queue)
    payload = {
        "latency_artifact": latency.__dict__,
        "queue_artifact": queue.__dict__,
        "fill_report": fill_report.__dict__,
    }
    write_optimization_report(output_dir / "calibration.json", payload)
    return 0


def _run_search(session_dir: Path, output_dir: Path) -> int:
    session_events: dict[str, list[MarketEvent]] = {}
    for path in sorted(session_dir.glob("*.jsonl")) + sorted(session_dir.glob("*.parquet")):
        session_events[path.stem] = list(ReplayEventStream.from_path(path))
    result = optimize_market_making(
        session_events=session_events,
        base_dir=output_dir,
        config=OptimizationRunConfig(),
        risk_limits=HFTLimitConfig(),
        fee_model={},
    )
    print(json.dumps({"run_id": result.run_id, "output_dir": result.output_dir}, indent=2))
    return 0


def _run_validate(run_dir: Path) -> int:
    summary_path = run_dir / "optimization_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(summary_path)
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    required = {"baseline_vs_champion", "feature_importance", "regime_decomposition", "parameter_sensitivity"}
    missing = sorted(required.difference(payload))
    if missing:
        raise RuntimeError(f"Optimization summary missing sections: {', '.join(missing)}")
    print("validation-ok")
    return 0


def _run_champion_report(run_dir: Path) -> int:
    summary_path = run_dir / "optimization_summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    report = payload.get("baseline_vs_champion", {}).get("selection_report", {})
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
