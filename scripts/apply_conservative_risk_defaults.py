from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "backend" / "storage" / "app.db"

RISK_OVERLAY: dict[str, Any] = {
    "max_gross_leverage": 1.0,
    "max_single_position_pct": 10.0,
    "max_correlated_bucket_pct": 30.0,
    "max_daily_loss_pct": 2.0,
    "max_weekly_loss_pct": 5.0,
    "drawdown_size_cut_pct": 5.0,
    "drawdown_stop_pct": 10.0,
    "drawdown_audit_pct": 15.0,
    "risk_cut_multiplier": 0.5,
    "allow_pyramiding": False,
    "allow_averaging_down": False,
    "min_edge_to_cost_ratio": 2.0,
    "market_slippage_bps": 20.0,
    "limit_slippage_bps": 10.0,
    "max_spread_bps": 25.0,
    "min_average_dollar_volume": 1_000_000.0,
    "max_order_adv_pct": 1.0,
    "max_intraday_volume_pct": 5.0,
    "no_new_entries_first_minutes": 5,
    "no_new_entries_before_close_minutes": 10,
    "require_liquidity_fields": False,
    "require_edge_fields": False,
}


def _coerce_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if parsed > 0 else float(default)


def _merge_settings(current: dict[str, Any]) -> dict[str, Any]:
    settings = dict(current or {})
    account_size = _coerce_float(settings.get("account_size"), 100000.0)
    settings["account_size"] = account_size
    settings["risk_percent"] = min(_coerce_float(settings.get("risk_percent"), 0.25), 0.25)
    settings["execution_intent"] = str(settings.get("execution_intent") or "broker_paper")
    settings["instrument_type"] = "equity"
    settings["regular_hours_only"] = True
    settings["long_only"] = True
    settings["equities_only"] = True
    settings["fractional_shares_only"] = True
    settings["max_open_positions"] = min(int(settings.get("max_open_positions") or 5), 5)
    settings["max_notional_per_trade"] = min(_coerce_float(settings.get("max_notional_per_trade"), account_size * 0.10), account_size * 0.25)
    settings["max_total_open_notional"] = min(_coerce_float(settings.get("max_total_open_notional"), account_size * 0.75), account_size)
    settings["max_daily_loss_r"] = min(_coerce_float(settings.get("max_daily_loss_r"), 2.0), 2.0)
    settings["max_consecutive_losses"] = min(int(settings.get("max_consecutive_losses") or 3), 3)
    settings["max_daily_entries"] = min(int(settings.get("max_daily_entries") or 3), 3)
    settings["max_daily_entries_per_symbol"] = min(int(settings.get("max_daily_entries_per_symbol") or 1), 1)
    settings.update(RISK_OVERLAY)
    return settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the conservative risk overlay to the current tenant automation settings.")
    parser.add_argument("--database", default=str(DEFAULT_DB_PATH), help="SQLite app.db path.")
    parser.add_argument("--tenant-slug", default="alpha-desk", help="Tenant slug to update.")
    parser.add_argument("--dry-run", action="store_true", help="Print the merged settings without writing the database.")
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
        merged_settings = _merge_settings(current_settings)
        trade_automation["settings"] = merged_settings
        metadata["trade_automation"] = trade_automation

        result = {
            "tenant_slug": row[1],
            "dry_run": bool(args.dry_run),
            "settings": merged_settings,
        }
        print(json.dumps(result, indent=2, sort_keys=True))

        if not args.dry_run:
            conn.execute("update tenants set metadata = ? where id = ?", (json.dumps(metadata), row[0]))
            conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
