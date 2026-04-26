from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.ten_x_validation_service import export_ten_x_stand_test


def main() -> int:
    parser = argparse.ArgumentParser(description="Test whether the current strategy result can stand as a 10x account-value result.")
    parser.add_argument("--tenant-slug", default="alpha-desk")
    parser.add_argument("--starting-capital", type=float, default=100000.0)
    parser.add_argument("--current-equity", type=float, default=1900000.0)
    parser.add_argument("--target-multiple", type=float, default=10.0)
    parser.add_argument("--known-peak-equity", type=float, default=1900000.0)
    parser.add_argument("--known-drawdown-peak", type=float, default=1800000.0)
    parser.add_argument("--known-drawdown-trough", type=float, default=1300000.0)
    parser.add_argument("--known-max-drawdown-pct", type=float, default=27.8)
    parser.add_argument("--ledger-csv", default="", help="Optional exported trade_validation_ledger.csv path.")
    parser.add_argument("--output-dir", default="", help="Defaults to runtime-exports/ten-x-stand-test/latest.")
    args = parser.parse_args()
    result = export_ten_x_stand_test(
        tenant_slug=args.tenant_slug,
        starting_capital=args.starting_capital,
        current_equity=args.current_equity,
        target_multiple=args.target_multiple,
        known_peak_equity=args.known_peak_equity,
        known_drawdown_peak=args.known_drawdown_peak,
        known_drawdown_trough=args.known_drawdown_trough,
        known_max_drawdown_pct=args.known_max_drawdown_pct,
        ledger_csv=Path(args.ledger_csv).resolve() if args.ledger_csv else None,
        output_dir=Path(args.output_dir).resolve() if args.output_dir else None,
    )
    print(json.dumps({
        "output_dir": str(result.output_dir),
        "report": str(result.report_path),
        "summary": str(result.summary_path),
        "scenario_matrix": str(result.scenario_matrix_path),
        "ledger": str(result.ledger_path) if result.ledger_path else None,
        "verdict": result.summary["verdict"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
