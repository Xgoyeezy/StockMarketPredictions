from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "backend" / "storage" / "app.db"


def _coerce_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if parsed > 0 else float(default)


def build_10x_paper_profile(current: dict[str, Any], *, arm_paper_orders: bool) -> dict[str, Any]:
    settings = dict(current or {})
    account_size = _coerce_float(settings.get("account_size"), 100000.0)
    settings["account_size"] = account_size
    settings["validation_mode"] = "10x_stand_test_paper_only"
    settings["target_equity_multiple"] = 10.0
    settings["target_equity"] = account_size * 10.0
    settings["execution_intent"] = "broker_paper"
    settings["instrument_type"] = "equity"
    settings["regular_hours_only"] = True
    settings["long_only"] = True
    settings["equities_only"] = True
    settings["paper_only"] = True
    settings["armed"] = bool(arm_paper_orders)
    settings["max_gross_leverage"] = 10.0
    settings["max_total_open_notional"] = account_size * 10.0
    settings["max_notional_per_trade"] = min(_coerce_float(settings.get("max_notional_per_trade"), account_size), account_size)
    settings["max_open_positions"] = max(int(settings.get("max_open_positions") or 1), 10)
    settings["risk_percent"] = _coerce_float(settings.get("risk_percent"), 0.25)
    settings["max_daily_loss_pct"] = _coerce_float(settings.get("max_daily_loss_pct"), 5.0)
    settings["max_weekly_loss_pct"] = _coerce_float(settings.get("max_weekly_loss_pct"), 10.0)
    settings["drawdown_size_cut_pct"] = _coerce_float(settings.get("drawdown_size_cut_pct"), 10.0)
    settings["drawdown_stop_pct"] = _coerce_float(settings.get("drawdown_stop_pct"), 30.0)
    settings["drawdown_audit_pct"] = _coerce_float(settings.get("drawdown_audit_pct"), 35.0)
    settings["allow_pyramiding"] = bool(settings.get("allow_pyramiding", False))
    settings["allow_averaging_down"] = False
    settings["min_edge_to_cost_ratio"] = _coerce_float(settings.get("min_edge_to_cost_ratio"), 2.0)
    settings["market_slippage_bps"] = _coerce_float(settings.get("market_slippage_bps"), 20.0)
    settings["limit_slippage_bps"] = _coerce_float(settings.get("limit_slippage_bps"), 10.0)
    settings["max_spread_bps"] = _coerce_float(settings.get("max_spread_bps"), 25.0)
    return settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply an aggressive paper-only profile for testing whether the 10x result stands.")
    parser.add_argument("--database", default=str(DEFAULT_DB_PATH), help="SQLite app.db path.")
    parser.add_argument("--tenant-slug", default="alpha-desk")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--arm-paper-orders", action="store_true", help="Set armed=true for broker-paper orders. Without this flag, the profile is saved unarmed.")
    args = parser.parse_args()
    db_path = Path(args.database).resolve()
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("select id, slug, metadata from tenants where slug = ? limit 1", (args.tenant_slug,)).fetchone()
        if row is None:
            raise SystemExit(f"Tenant not found: {args.tenant_slug}")
        metadata = json.loads(row[2] or "{}")
        trade_automation = dict(metadata.get("trade_automation") or {})
        current_settings = dict(trade_automation.get("settings") or {})
        merged = build_10x_paper_profile(current_settings, arm_paper_orders=args.arm_paper_orders)
        trade_automation["settings"] = merged
        metadata["trade_automation"] = trade_automation
        result = {"tenant_slug": row[1], "dry_run": bool(args.dry_run), "armed": bool(merged.get("armed")), "paper_only": bool(merged.get("paper_only")), "settings": merged}
        print(json.dumps(result, indent=2, sort_keys=True))
        if not args.dry_run:
            conn.execute("update tenants set metadata = ? where id = ?", (json.dumps(metadata), row[0]))
            conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
