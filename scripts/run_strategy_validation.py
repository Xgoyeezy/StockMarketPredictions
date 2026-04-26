from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.strategy_validation_service import export_strategy_validation


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze the current strategy as v0 and export validation artifacts.")
    parser.add_argument("--tenant-slug", default="alpha-desk", help="Tenant slug that owns the current automation state.")
    parser.add_argument("--starting-capital", type=float, default=100000.0, help="Starting capital for the reconstructed equity ledger.")
    parser.add_argument("--known-peak-equity", type=float, default=1_900_000.0, help="Known peak equity reference for v0.")
    parser.add_argument("--known-drawdown-peak", type=float, default=1_800_000.0, help="Known drawdown peak reference.")
    parser.add_argument("--known-drawdown-trough", type=float, default=1_300_000.0, help="Known drawdown trough reference.")
    parser.add_argument("--known-max-drawdown-pct", type=float, default=27.8, help="Known max drawdown reference.")
    parser.add_argument(
        "--output-dir",
        default="",
        help="Optional explicit output directory. Defaults to runtime-exports/strategy-validation/latest.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve() if args.output_dir else None
    result = export_strategy_validation(
        tenant_slug=args.tenant_slug,
        starting_capital=args.starting_capital,
        known_peak_equity=args.known_peak_equity,
        known_drawdown_peak=args.known_drawdown_peak,
        known_drawdown_trough=args.known_drawdown_trough,
        known_max_drawdown_pct=args.known_max_drawdown_pct,
        output_dir=output_dir,
    )
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "baseline": str(result.baseline_path),
                "ledger": str(result.ledger_path),
                "equity_snapshots": str(result.equity_snapshots_path),
                "broker_reconciliation": str(result.broker_reconciliation_path),
                "benchmark_report": str(result.benchmark_report_path),
                "next_bar_replay": str(result.next_bar_replay_path),
                "walk_forward": str(result.walk_forward_path),
                "stress_matrix": str(result.stress_matrix_path),
                "drawdown_report": str(result.drawdown_report_path),
                "drawdown_decomposition": str(result.drawdown_decomposition_path),
                "mark_to_market_report": str(result.mark_to_market_report_path),
                "monte_carlo": str(result.monte_carlo_path),
                "kill_switch_report": str(result.kill_switch_report_path),
                "summary": str(result.summary_path),
                "tracker": str(result.tracker_path),
                "tracker_markdown": str(result.tracker_markdown_path),
                "metrics": result.metrics,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
