from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from backend import stock_direction_model as sdm
from backend.services.storage_utils import write_dataframe_csv


EQUITY_SNAPSHOTS_PATH = sdm.STORAGE_DIR / "equity_snapshots.csv"
EQUITY_SNAPSHOT_COLUMNS = [
    "snapshot_at",
    "cycle_at",
    "cycle_id",
    "source",
    "tenant_id",
    "tenant_slug",
    "profile_key",
    "account_size",
    "cash_estimate",
    "long_market_value",
    "short_market_value",
    "borrowed_amount",
    "fees_accrued",
    "margin_interest_accrued",
    "open_cost",
    "pending_notional",
    "unrealized_pnl",
    "realized_pnl",
    "equity",
    "gross_exposure",
    "net_exposure",
    "active_trade_count",
    "pending_order_count",
    "cycle_count",
    "success_count",
    "error_count",
    "rejection_count",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "nan"):
        return float(default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return float(parsed) if pd.notna(parsed) else float(default)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _normalize_timestamp(value: Any) -> str | None:
    if value in (None, "", "nan"):
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.isoformat()


def _filter_automation_rows(frame: pd.DataFrame, *, tenant_id: str, profile_key: str | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame.iloc[0:0].copy()
    marker = frame.get("automation_origin", pd.Series(dtype=str)).astype(str).str.strip().str.lower()
    scope = frame.get("automation_tenant_id", pd.Series(dtype=str)).astype(str).str.strip()
    profile = frame.get("automation_profile_key", pd.Series(dtype=str)).astype(str).str.strip().str.lower()
    matches = marker.eq("trade_automation") & scope.eq(str(tenant_id or "").strip())
    normalized_profile_key = str(profile_key or "").strip().lower()
    if normalized_profile_key:
        matches = matches & profile.eq(normalized_profile_key)
    if not matches.any():
        return frame.iloc[0:0].copy()
    return frame.loc[matches].copy()


def _estimate_notional(row: dict[str, Any]) -> float:
    for key in ("total_position_cost", "projected_position_cost", "notional", "position_notional"):
        value = _coerce_float(row.get(key), 0.0)
        if value > 0:
            return value

    units = 0.0
    for key in ("filled_quantity", "qty", "quantity", "suggested_contracts"):
        value = _coerce_float(row.get(key), 0.0)
        if value > 0:
            units = value
            break

    price = 0.0
    for key in ("limit_price", "entry_price", "live_price", "current_underlying_price", "close"):
        value = _coerce_float(row.get(key), 0.0)
        if value > 0:
            price = value
            break
    return float(units * price) if units > 0 and price > 0 else 0.0


def _empty_snapshot_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=EQUITY_SNAPSHOT_COLUMNS)


def read_equity_snapshots(file_path: Path = EQUITY_SNAPSHOTS_PATH) -> pd.DataFrame:
    if not file_path.exists():
        return _empty_snapshot_frame()
    try:
        frame = pd.read_csv(file_path)
    except (OSError, pd.errors.EmptyDataError):
        return _empty_snapshot_frame()
    if frame.empty:
        return _empty_snapshot_frame()
    for column in EQUITY_SNAPSHOT_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame[EQUITY_SNAPSHOT_COLUMNS].copy()


def _filter_tenant_snapshots(
    frame: pd.DataFrame,
    *,
    tenant_id: str,
    tenant_slug: str | None = None,
    profile_key: str | None = None,
) -> pd.DataFrame:
    if frame.empty:
        return frame.iloc[0:0].copy()
    normalized_tenant_id = str(tenant_id or "").strip()
    normalized_tenant_slug = str(tenant_slug or "").strip()
    tenant_id_mask = frame.get("tenant_id", pd.Series(dtype=str)).astype(str).str.strip().eq(normalized_tenant_id)
    if normalized_tenant_slug:
        tenant_slug_mask = frame.get("tenant_slug", pd.Series(dtype=str)).astype(str).str.strip().eq(normalized_tenant_slug)
        matches = tenant_id_mask | tenant_slug_mask
    else:
        matches = tenant_id_mask
    normalized_profile_key = str(profile_key or "").strip().lower()
    if normalized_profile_key:
        profile_mask = frame.get("profile_key", pd.Series(dtype=str)).astype(str).str.strip().str.lower().eq(normalized_profile_key)
        matches = matches & profile_mask
    if not matches.any():
        return frame.iloc[0:0].copy()
    return frame.loc[matches].copy()


def get_latest_trade_automation_equity_snapshot(
    *,
    tenant_id: str,
    tenant_slug: str | None = None,
    profile_key: str | None = None,
    file_path: Path = EQUITY_SNAPSHOTS_PATH,
) -> dict[str, Any] | None:
    snapshots = _filter_tenant_snapshots(
        read_equity_snapshots(file_path),
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        profile_key=profile_key,
    )
    if snapshots.empty:
        return None
    snapshots["snapshot_at_ts"] = pd.to_datetime(snapshots.get("snapshot_at"), errors="coerce", utc=True)
    snapshots["cycle_at_ts"] = pd.to_datetime(snapshots.get("cycle_at"), errors="coerce", utc=True)
    snapshots = snapshots.sort_values(
        ["cycle_at_ts", "snapshot_at_ts"],
        ascending=[False, False],
        na_position="last",
    )
    return snapshots.iloc[0][EQUITY_SNAPSHOT_COLUMNS].to_dict()


def build_trade_automation_equity_snapshot(
    *,
    tenant: Any,
    state: dict[str, Any],
    profile_key: str | None = None,
    cycle_at: Any | None = None,
    cycle_id: Any | None = None,
    monitored_open: pd.DataFrame | None = None,
    pending_orders: pd.DataFrame | None = None,
    closed_trades: pd.DataFrame | None = None,
    now: datetime | None = None,
    source: str = "trade_automation_cycle",
    effective_funds: float | None = None,
) -> dict[str, Any] | None:
    runtime_state = dict(state.get("runtime") or {})
    settings_state = dict(state.get("settings") or {})
    normalized_cycle_at = _normalize_timestamp(cycle_at if cycle_at is not None else runtime_state.get("last_cycle_at"))
    if normalized_cycle_at is None:
        return None
    normalized_cycle_id = str(
        cycle_id
        if cycle_id is not None
        else (runtime_state.get("last_action") or {}).get("cycle_id")
        or ""
    ).strip() or None

    now_value = now or _utc_now()
    monitored_open = monitored_open if monitored_open is not None else sdm.monitor_open_trades()
    pending_orders = pending_orders if pending_orders is not None else sdm.read_pending_orders()
    closed_trades = closed_trades if closed_trades is not None else sdm.read_closed_trades()

    owned_open = _filter_automation_rows(monitored_open, tenant_id=tenant.id, profile_key=profile_key)
    owned_pending = _filter_automation_rows(pending_orders, tenant_id=tenant.id, profile_key=profile_key)
    owned_closed = _filter_automation_rows(closed_trades, tenant_id=tenant.id, profile_key=profile_key)

    account_size = _coerce_float(effective_funds, _coerce_float(settings_state.get("account_size"), 0.0))
    open_cost = float(pd.to_numeric(owned_open.get("position_cost"), errors="coerce").fillna(0.0).sum()) if not owned_open.empty else 0.0
    unrealized_pnl = float(pd.to_numeric(owned_open.get("unrealized_pnl"), errors="coerce").fillna(0.0).sum()) if not owned_open.empty else 0.0
    realized_pnl = float(pd.to_numeric(owned_closed.get("realized_pnl"), errors="coerce").fillna(0.0).sum()) if not owned_closed.empty else 0.0
    pending_notional = 0.0
    if not owned_pending.empty:
        for row in owned_pending.to_dict(orient="records"):
            pending_notional += _estimate_notional(row)

    long_market_value = open_cost + unrealized_pnl
    short_market_value = 0.0
    borrowed_amount = 0.0
    fees_accrued = 0.0
    margin_interest_accrued = 0.0
    cash_estimate = account_size + realized_pnl - open_cost
    equity = cash_estimate + long_market_value - short_market_value - borrowed_amount - fees_accrued - margin_interest_accrued
    gross_exposure = abs(long_market_value) + abs(short_market_value)
    net_exposure = long_market_value - short_market_value

    return {
        "snapshot_at": now_value.astimezone(timezone.utc).isoformat(),
        "cycle_at": normalized_cycle_at,
        "cycle_id": normalized_cycle_id,
        "source": str(source or "trade_automation_cycle").strip().lower(),
        "tenant_id": str(tenant.id or "").strip(),
        "tenant_slug": str(getattr(tenant, "slug", "") or "").strip(),
        "profile_key": str(profile_key or "").strip().lower() or None,
        "account_size": round(account_size, 4),
        "cash_estimate": round(cash_estimate, 4),
        "long_market_value": round(long_market_value, 4),
        "short_market_value": round(short_market_value, 4),
        "borrowed_amount": round(borrowed_amount, 4),
        "fees_accrued": round(fees_accrued, 4),
        "margin_interest_accrued": round(margin_interest_accrued, 4),
        "open_cost": round(open_cost, 4),
        "pending_notional": round(pending_notional, 4),
        "unrealized_pnl": round(unrealized_pnl, 4),
        "realized_pnl": round(realized_pnl, 4),
        "equity": round(equity, 4),
        "gross_exposure": round(gross_exposure, 4),
        "net_exposure": round(net_exposure, 4),
        "active_trade_count": int(len(owned_open)),
        "pending_order_count": int(len(owned_pending)),
        "cycle_count": _coerce_int(runtime_state.get("cycle_count"), 0),
        "success_count": _coerce_int(runtime_state.get("success_count"), 0),
        "error_count": _coerce_int(runtime_state.get("error_count"), 0),
        "rejection_count": _coerce_int(runtime_state.get("rejection_count"), 0),
    }


def record_trade_automation_equity_snapshot(
    *,
    tenant: Any,
    state: dict[str, Any],
    profile_key: str | None = None,
    cycle_at: Any | None = None,
    cycle_id: Any | None = None,
    monitored_open: pd.DataFrame | None = None,
    pending_orders: pd.DataFrame | None = None,
    closed_trades: pd.DataFrame | None = None,
    now: datetime | None = None,
    source: str = "trade_automation_cycle",
    file_path: Path = EQUITY_SNAPSHOTS_PATH,
    effective_funds: float | None = None,
) -> dict[str, Any] | None:
    snapshot = build_trade_automation_equity_snapshot(
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        cycle_at=cycle_at,
        cycle_id=cycle_id,
        monitored_open=monitored_open,
        pending_orders=pending_orders,
        closed_trades=closed_trades,
        now=now,
        source=source,
        effective_funds=effective_funds,
    )
    if snapshot is None:
        return None

    existing = read_equity_snapshots(file_path)
    next_frame = existing.copy() if not existing.empty else _empty_snapshot_frame()
    if not next_frame.empty:
        existing_profile_key = (
            next_frame.get("profile_key", pd.Series("", index=next_frame.index))
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
            .replace({"nan": "", "none": "", "<na>": ""})
        )
        snapshot_profile_key = str(snapshot.get("profile_key") or "").strip().lower()
        match_mask = (
            next_frame["tenant_id"].astype(str).str.strip().eq(str(snapshot["tenant_id"]))
            & next_frame["cycle_at"].astype(str).str.strip().eq(str(snapshot["cycle_at"]))
            & existing_profile_key.eq(snapshot_profile_key)
        )
        if match_mask.any():
            next_frame = next_frame.loc[~match_mask].copy()
    snapshot_frame = pd.DataFrame([snapshot], columns=EQUITY_SNAPSHOT_COLUMNS)
    if next_frame.empty:
        next_frame = snapshot_frame.copy()
    else:
        next_frame = pd.concat([next_frame, snapshot_frame], ignore_index=True)
    if not next_frame.empty:
        next_frame["snapshot_at_ts"] = pd.to_datetime(next_frame["snapshot_at"], errors="coerce", utc=True)
        next_frame["cycle_at_ts"] = pd.to_datetime(next_frame["cycle_at"], errors="coerce", utc=True)
        next_frame = next_frame.sort_values(
            ["cycle_at_ts", "snapshot_at_ts", "tenant_slug"],
            ascending=[True, True, True],
            na_position="last",
        )
        next_frame = next_frame[EQUITY_SNAPSHOT_COLUMNS].reset_index(drop=True)
    write_dataframe_csv(file_path, next_frame)
    return snapshot
