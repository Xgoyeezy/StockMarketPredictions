from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from backend import stock_direction_model as sdm
from backend.services import equity_snapshot_service as ess


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_EXPORTS_DIR = REPO_ROOT / "runtime-exports" / "strategy-validation"
APP_DB_PATH = sdm.STORAGE_DIR / "app.db"
_ANNUAL_MARGIN_INTEREST_RATE = 0.0
_CURRENT_ROUTE_FAMILY = "current"
_CURRENT_ROUTE_VERSION = "ranked_entry_v1"
_CURRENT_ROUTE_MIN_DIRECTIONAL_FILLS = 10
_CURRENT_ROUTE_MIN_CLOSED_TRADES = 5
_ENDING_EQUITY_CONSISTENCY_TOLERANCE_PCT = 2.0
_GROSS_EXPOSURE_CONSISTENCY_TOLERANCE_PCT = 25.0
_PREDICTION_VALIDATION_MIN_RESOLVED = 20


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "nan"):
        return float(default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(parsed):
        return float(default)
    return float(parsed)


def _coerce_timestamp(value: Any) -> pd.Timestamp | None:
    if value in (None, "", "nan"):
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed


def _safe_json(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _safe_json(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def _round_money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _load_tenant_row(tenant_slug: str) -> dict[str, Any]:
    with closing(sqlite3.connect(APP_DB_PATH)) as conn:
        row = conn.execute(
            "select id, slug, name, metadata from tenants where slug = ? limit 1",
            (tenant_slug,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Tenant '{tenant_slug}' was not found in {APP_DB_PATH}.")
    metadata = {}
    if row[3]:
        metadata = json.loads(row[3])
    return {
        "id": row[0],
        "slug": row[1],
        "name": row[2],
        "metadata": metadata,
    }


def _load_order_events(tenant_id: str) -> pd.DataFrame:
    with closing(sqlite3.connect(APP_DB_PATH)) as conn:
        frame = pd.read_sql_query(
            """
            select
                id,
                tenant_id,
                trade_id,
                ticker,
                event_key,
                status,
                order_type,
                time_in_force,
                route_state,
                book_state,
                detail,
                payload,
                created_at,
                updated_at
            from order_events
            where tenant_id = ?
            order by created_at asc, updated_at asc, id asc
            """,
            conn,
            params=(tenant_id,),
        )
    if frame.empty:
        return frame
    frame["created_at"] = pd.to_datetime(frame["created_at"], errors="coerce", utc=True)
    frame["updated_at"] = pd.to_datetime(frame["updated_at"], errors="coerce", utc=True)
    frame["payload_json"] = frame["payload"].apply(
        lambda raw: json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    )
    return frame


def _load_trade_frames() -> dict[str, pd.DataFrame]:
    return {
        "open_trades": sdm.read_open_trades().copy(),
        "closed_trades": sdm.read_closed_trades().copy(),
        "pending_orders": sdm.read_pending_orders().copy(),
        "forecast_journal": sdm.read_forecast_journal().copy(),
        "trade_journal": sdm.read_trade_journal().copy(),
    }


def _load_tenant_equity_snapshots(tenant_id: str, tenant_slug: str) -> pd.DataFrame:
    snapshots = ess.read_equity_snapshots()
    if snapshots.empty:
        return snapshots
    tenant_id_mask = snapshots.get("tenant_id", pd.Series(dtype=str)).astype(str).str.strip().eq(str(tenant_id or "").strip())
    tenant_slug_mask = snapshots.get("tenant_slug", pd.Series(dtype=str)).astype(str).str.strip().eq(str(tenant_slug or "").strip())
    filtered = snapshots.loc[tenant_id_mask | tenant_slug_mask].copy()
    if filtered.empty:
        return filtered
    filtered["snapshot_at_ts"] = pd.to_datetime(filtered.get("snapshot_at"), errors="coerce", utc=True)
    filtered["cycle_at_ts"] = pd.to_datetime(filtered.get("cycle_at"), errors="coerce", utc=True)
    filtered["effective_ts"] = filtered["cycle_at_ts"].where(filtered["cycle_at_ts"].notna(), filtered["snapshot_at_ts"])
    filtered = filtered.sort_values(["effective_ts", "snapshot_at_ts"], ascending=[True, True], na_position="last")
    return filtered.reset_index(drop=True)


def _trade_settings_snapshot(settings_state: dict[str, Any]) -> dict[str, Any]:
    account_size = _coerce_float(settings_state.get("account_size"), 100000.0)
    max_total_open_notional = _coerce_float(settings_state.get("max_total_open_notional"), account_size)
    max_notional_per_trade = _coerce_float(settings_state.get("max_notional_per_trade"), account_size)
    leverage_cap = max_total_open_notional / account_size if account_size > 0 else 0.0
    per_trade_leverage = max_notional_per_trade / account_size if account_size > 0 else 0.0
    return {
        "position_sizing_rule": (
            f"Risk-percent sizing via current desk automation; account_size={account_size:.2f}, "
            f"risk_percent={_coerce_float(settings_state.get('risk_percent'), 0.25):.2f}%."
        ),
        "max_leverage": {
            "gross_cap_multiple": round(leverage_cap, 4),
            "per_trade_notional_multiple": round(per_trade_leverage, 4),
            "max_total_open_notional": max_total_open_notional,
            "max_notional_per_trade": max_notional_per_trade,
        },
        "entry_rule": (
            "Automation ranks the configured board and only routes a strict VALID TRADE candidate that is "
            "directionally compatible with the current long-only equity posture."
        ),
        "exit_rule": (
            "Automation manages open positions from monitor actions, including STOP HIT, EXIT FULLY NOW, "
            "SELL MORE NOW, and SELL 50% NOW."
        ),
        "stop_logic": "Underlying invalidation-price stop plus monitored exit actions; routed closes can still slip.",
        "timeframe": str(settings_state.get("interval") or "5m"),
        "assets_traded": list(settings_state.get("tickers") or []),
        "order_type": str(settings_state.get("order_type") or "market"),
        "slippage_assumption": (
            "No explicit portfolio-level slippage model is embedded in the current equity curve; "
            "per-trade expected/actual fill fields are recorded when broker data is available."
        ),
        "fees_assumption": (
            "Per-trade fees are modeled explicitly from the trade rows and default to 0 only when the source data "
            "does not provide a fee value."
        ),
        "margin_assumption": (
            "Borrowed amount is tracked explicitly whenever settlement cash goes negative. Margin interest is modeled "
            "explicitly at 0.00% annualized for the current cash-funded v0 caps."
        ),
    }


def _resolve_signal_timestamp(
    row: pd.Series,
    forecast_journal: pd.DataFrame,
) -> str | None:
    if forecast_journal.empty:
        return None
    ticker = str(row.get("ticker") or "").strip().upper()
    interval = str(row.get("interval") or "").strip().lower()
    order_ts = _coerce_timestamp(row.get("submitted_at") or row.get("opened_at"))
    if not ticker or order_ts is None:
        return None
    candidates = forecast_journal.copy()
    if "ticker" in candidates.columns:
        candidates = candidates[candidates["ticker"].astype(str).str.upper() == ticker]
    if "interval" in candidates.columns:
        candidates = candidates[candidates["interval"].astype(str).str.lower() == interval]
    if candidates.empty or "forecast_at" not in candidates.columns:
        return None
    candidates = candidates.copy()
    candidates["forecast_at_ts"] = pd.to_datetime(candidates["forecast_at"], errors="coerce", utc=True)
    candidates = candidates[candidates["forecast_at_ts"].notna()]
    candidates = candidates[candidates["forecast_at_ts"] <= order_ts]
    if candidates.empty:
        return None
    candidates = candidates.sort_values("forecast_at_ts", ascending=False)
    return str(candidates.iloc[0]["forecast_at_ts"].isoformat())


def _trade_event_lookup(order_events: pd.DataFrame) -> dict[str, dict[str, pd.Timestamp | None]]:
    lookup: dict[str, dict[str, pd.Timestamp | None]] = {}
    if order_events.empty:
        return lookup
    for trade_id, group in order_events.groupby(order_events["trade_id"].fillna("")):
        normalized = str(trade_id or "").strip()
        if not normalized:
            continue
        submitted = group.loc[group["event_key"] == "order.submitted", "created_at"]
        filled = group.loc[group["event_key"] == "order.filled", "created_at"]
        closed = group.loc[group["event_key"] == "order.closed", "created_at"]
        rejected = group.loc[group["event_key"] == "order.rejected", "created_at"]
        lookup[normalized] = {
            "submitted_at": submitted.min() if not submitted.empty else None,
            "filled_at": filled.min() if not filled.empty else None,
            "closed_at": closed.min() if not closed.empty else None,
            "rejected_at": rejected.min() if not rejected.empty else None,
        }
    return lookup


def _extract_route_payload_from_container(container: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(container, dict) or not container:
        return {}
    route_family = str(container.get("route_family") or "").strip().lower()
    route_version = str(container.get("route_version") or "").strip()
    route_correlation_id = _normalize_route_correlation_id(container.get("route_correlation_id"))
    if route_family == _CURRENT_ROUTE_FAMILY and not route_version:
        route_version = _CURRENT_ROUTE_VERSION
    automation_entry_reason = str(container.get("automation_entry_reason") or "").strip()
    thesis_direction = str(container.get("thesis_direction") or "").strip().upper()
    option_right = str(container.get("option_right") or "").strip().lower()
    instrument_type = str(container.get("instrument_type") or "").strip().lower()
    directional_exposure = str(container.get("directional_exposure") or "").strip().lower()
    validation_sample_bucket = str(container.get("validation_sample_bucket") or "").strip().lower()
    if not directional_exposure:
        directional_exposure = _directional_exposure_from_row(
            pd.Series(
                {
                    "instrument_type": instrument_type,
                    "option_right": option_right,
                    "side": "BUY",
                    "thesis_direction": thesis_direction,
                }
            )
        )
    if not validation_sample_bucket:
        validation_sample_bucket = _validation_sample_bucket_for_route(route_family, route_version)
    has_explicit_payload = any(
        [
            route_family,
            route_version,
            route_correlation_id,
            automation_entry_reason,
            thesis_direction,
            option_right,
            directional_exposure != "unknown",
            validation_sample_bucket != "legacy",
        ]
    )
    if not has_explicit_payload:
        return {}
    return {
        "route_family": route_family,
        "route_version": route_version,
        "route_correlation_id": route_correlation_id,
        "automation_entry_reason": automation_entry_reason,
        "thesis_direction": thesis_direction,
        "directional_exposure": directional_exposure,
        "validation_sample_bucket": validation_sample_bucket,
        "option_right": option_right,
    }


def _build_trade_route_backfill_lookup(order_events: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if order_events.empty:
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    candidate_keys = (
        "trade",
        "record",
        "filled_record",
        "pending_order",
        "updated_order",
        "synced_order",
        "order",
        "request",
    )
    for _, row in order_events.iterrows():
        trade_id = str(row.get("trade_id") or "").strip()
        if not trade_id:
            continue
        payload_json = row.get("payload_json")
        payload = payload_json if isinstance(payload_json, dict) else {}
        route_payload = _extract_route_payload_from_container(payload)
        for key in candidate_keys:
            route_payload = route_payload or _extract_route_payload_from_container(payload.get(key) or {})
        if not route_payload:
            continue
        current = dict(lookup.get(trade_id) or {})
        for key, value in route_payload.items():
            if value not in (None, "", "unknown", "legacy"):
                current[key] = value
            elif key not in current and value not in (None, ""):
                current[key] = value
        lookup[trade_id] = current
    return lookup


def _backfill_trade_frame_route_metadata(
    frame: pd.DataFrame,
    order_events: pd.DataFrame,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    route_lookup = _build_trade_route_backfill_lookup(order_events)
    if not route_lookup:
        return frame.copy()
    repaired = frame.copy()
    records: list[dict[str, Any]] = []
    for _, row in repaired.iterrows():
        row_dict = row.to_dict()
        trade_id = str(row_dict.get("trade_id") or "").strip()
        route_payload = dict(route_lookup.get(trade_id) or {})
        if route_payload:
            for key, value in route_payload.items():
                existing = row_dict.get(key)
                if existing in (None, "", "nan") or (str(existing).strip().lower() == "legacy" and key == "route_family"):
                    row_dict[key] = value
        records.append(row_dict)
    return pd.DataFrame(records)


def _multiplier_for_trade(row: pd.Series) -> float:
    instrument_type = str(row.get("instrument_type") or "").strip().lower()
    return 1.0 if instrument_type == "equity" else 100.0


def _coverage_start_timestamp(ledger: pd.DataFrame, snapshots: pd.DataFrame) -> pd.Timestamp | None:
    candidates: list[pd.Timestamp] = []
    if not ledger.empty and "timestamp" in ledger.columns:
        series = pd.to_datetime(ledger["timestamp"], errors="coerce", utc=True).dropna()
        if not series.empty:
            candidates.append(series.min())
    if not snapshots.empty and "effective_ts" in snapshots.columns:
        series = pd.to_datetime(snapshots["effective_ts"], errors="coerce", utc=True).dropna()
        if not series.empty:
            candidates.append(series.min())
    return min(candidates) if candidates else None


def _coverage_end_timestamp(ledger: pd.DataFrame, snapshots: pd.DataFrame) -> pd.Timestamp | None:
    candidates: list[pd.Timestamp] = []
    if not ledger.empty and "timestamp" in ledger.columns:
        series = pd.to_datetime(ledger["timestamp"], errors="coerce", utc=True).dropna()
        if not series.empty:
            candidates.append(series.max())
    if not snapshots.empty and "effective_ts" in snapshots.columns:
        series = pd.to_datetime(snapshots["effective_ts"], errors="coerce", utc=True).dropna()
        if not series.empty:
            candidates.append(series.max())
    return max(candidates) if candidates else None


def _ledger_event_window(ledger: pd.DataFrame) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if ledger.empty or "timestamp" not in ledger.columns:
        return None, None
    timestamps = pd.to_datetime(ledger["timestamp"], errors="coerce", utc=True)
    timestamps = timestamps[timestamps.notna()]
    if timestamps.empty:
        return None, None
    return timestamps.min(), timestamps.max()


def _filter_snapshots_to_window(
    snapshots: pd.DataFrame,
    *,
    start_at: pd.Timestamp | None,
    end_at: pd.Timestamp | None,
) -> pd.DataFrame:
    if snapshots.empty or start_at is None or end_at is None:
        return snapshots.iloc[0:0].copy()
    frame = snapshots.copy()
    frame["effective_ts"] = pd.to_datetime(frame.get("effective_ts"), errors="coerce", utc=True)
    frame["effective_ts"] = frame["effective_ts"].where(
        frame["effective_ts"].notna(),
        pd.to_datetime(frame.get("snapshot_at"), errors="coerce", utc=True),
    )
    frame = frame[frame["effective_ts"].notna()].copy()
    if frame.empty:
        return frame
    frame = frame[(frame["effective_ts"] >= start_at) & (frame["effective_ts"] <= end_at)].copy()
    if frame.empty:
        return frame
    frame = frame.sort_values(["effective_ts", "snapshot_at_ts"], ascending=[True, True], na_position="last")
    return frame.reset_index(drop=True)


def _period_for_span(start_at: pd.Timestamp | None, end_at: pd.Timestamp | None) -> str:
    if start_at is None or end_at is None:
        return "1mo"
    span_days = max((end_at - start_at).total_seconds() / 86400.0, 0.0)
    if span_days <= 5:
        return "5d"
    if span_days <= 30:
        return "1mo"
    if span_days <= 90:
        return "3mo"
    if span_days <= 180:
        return "6mo"
    if span_days <= 365:
        return "1y"
    return "2y"


def _benchmark_interval_for_span(start_at: pd.Timestamp | None, end_at: pd.Timestamp | None) -> str:
    if start_at is None or end_at is None:
        return "1d"
    span_days = max((end_at - start_at).total_seconds() / 86400.0, 0.0)
    return "5m" if span_days <= 7 else "1d"


def _extract_price_at_or_before(frame: pd.DataFrame, target: pd.Timestamp | None) -> float | None:
    if frame.empty or target is None:
        return None
    history = frame.copy()
    history.index = pd.to_datetime(history.index, errors="coerce", utc=True)
    history = history[history.index.notna()].sort_index()
    if history.empty or "Close" not in history.columns:
        return None
    eligible = history.loc[history.index <= target]
    if eligible.empty:
        return None
    return _coerce_float(eligible.iloc[-1]["Close"], float("nan"))


def _extract_open_after(frame: pd.DataFrame, target: pd.Timestamp | None) -> float | None:
    if frame.empty or target is None:
        return None
    history = frame.copy()
    history.index = pd.to_datetime(history.index, errors="coerce", utc=True)
    history = history[history.index.notna()].sort_index()
    if history.empty or "Open" not in history.columns:
        return None
    eligible = history.loc[history.index > target]
    if eligible.empty:
        return None
    return _coerce_float(eligible.iloc[0]["Open"], float("nan"))


def _download_history(symbol: str, *, period: str, interval: str) -> pd.DataFrame:
    try:
        return sdm.download_ohlcv(symbol, period=period, interval=interval)
    except Exception:
        return pd.DataFrame()


def _entry_fill_price(row: pd.Series) -> float:
    for candidate in (
        row.get("actual_fill_price"),
        row.get("broker_filled_avg_price"),
        row.get("live_price_at_open"),
    ):
        parsed = _coerce_float(candidate, float("nan"))
        if not math.isnan(parsed) and parsed > 0:
            return parsed
    contract_mid = _coerce_float(row.get("contract_mid_at_open"), float("nan"))
    if not math.isnan(contract_mid) and contract_mid > 0:
        return contract_mid * _multiplier_for_trade(row)
    position_cost = _coerce_float(row.get("position_cost"), 0.0)
    quantity = _coerce_float(row.get("suggested_contracts"), 0.0)
    multiplier = _multiplier_for_trade(row)
    if quantity > 0 and multiplier > 0:
        return position_cost / (quantity * multiplier)
    return 0.0


def _close_fill_price(row: pd.Series) -> float:
    for candidate in (row.get("live_price_at_close"), row.get("broker_filled_avg_price")):
        parsed = _coerce_float(candidate, float("nan"))
        if not math.isnan(parsed) and parsed > 0:
            return parsed
    contract_mid = _coerce_float(row.get("contract_mid_at_close"), float("nan"))
    if not math.isnan(contract_mid) and contract_mid > 0:
        return contract_mid * _multiplier_for_trade(row)
    quantity = _coerce_float(row.get("closed_contracts"), _coerce_float(row.get("suggested_contracts"), 0.0))
    realized_pnl = _coerce_float(row.get("realized_pnl"), 0.0)
    multiplier = _multiplier_for_trade(row)
    entry_fill = _entry_fill_price(row)
    if quantity > 0 and multiplier > 0:
        return entry_fill + realized_pnl / (quantity * multiplier)
    return 0.0


def _entry_reason(row: pd.Series) -> str:
    return (
        f"{str(row.get('trade_decision') or '').strip() or 'UNKNOWN'} | "
        f"{str(row.get('setup_grade') or '').strip() or 'NO_GRADE'} | "
        f"{str(row.get('alignment_label') or '').strip() or 'NO_ALIGNMENT'} | "
        f"{str(row.get('conviction_label') or '').strip() or 'NO_CONVICTION'}"
    )


def _normalize_direction_label(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in {"BULLISH", "BEARISH"} else ""


def _normalize_directional_exposure(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"bullish", "bearish"}:
        return normalized
    return "unknown"


def _normalize_validation_sample_bucket(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "current_route":
        return "current_route"
    return "legacy"


def _validation_sample_bucket_for_route(route_family: Any, route_version: Any) -> str:
    normalized_family = str(route_family or "").strip().lower()
    normalized_version = str(route_version or "").strip()
    if normalized_family == _CURRENT_ROUTE_FAMILY and normalized_version == _CURRENT_ROUTE_VERSION:
        return "current_route"
    return "legacy"


def _directional_exposure_from_row(row: pd.Series) -> str:
    explicit = _normalize_directional_exposure(row.get("directional_exposure"))
    if explicit != "unknown":
        return explicit

    instrument_type = str(row.get("instrument_type") or "").strip().lower()
    option_right = str(row.get("option_right") or "").strip().lower()
    side = str(row.get("side") or "").strip().upper()

    if instrument_type == "equity":
        if side == "BUY":
            return "bullish"
        if side == "SELL":
            return "bearish"
        return "unknown"

    if option_right == "call":
        return "bullish" if side == "BUY" else "bearish"
    if option_right == "put":
        return "bearish" if side == "BUY" else "bullish"

    thesis_direction = _normalize_direction_label(row.get("thesis_direction"))
    if thesis_direction == "BULLISH":
        return "bullish"
    if thesis_direction == "BEARISH":
        return "bearish"
    return "unknown"


def _expected_side_for_verdict(verdict: str) -> str | None:
    if verdict == "BULLISH":
        return "BUY"
    if verdict == "BEARISH":
        return "SELL"
    return None


def _normalize_route_correlation_id(value: Any) -> str:
    return str(value or "").strip()


def _current_route_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool, index=frame.index)
    sample_bucket = frame.get("validation_sample_bucket", pd.Series("", index=frame.index)).apply(_normalize_validation_sample_bucket)
    route_family = frame.get("route_family", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip().str.lower()
    route_version = frame.get("route_version", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()
    route_mask = (route_family == _CURRENT_ROUTE_FAMILY) & (route_version == _CURRENT_ROUTE_VERSION)
    return (sample_bucket == "current_route").fillna(False) | route_mask.fillna(False)


def _filter_current_route_ledger(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return ledger.copy()
    mask = _current_route_mask(ledger)
    return ledger.loc[mask].copy().reset_index(drop=True)


def _build_alignment_stats(fills: pd.DataFrame) -> dict[str, Any]:
    if fills.empty:
        return {
            "fill_count": 0,
            "directional_fill_count": 0,
            "aligned_count": 0,
            "mismatched_count": 0,
            "mismatch_rate": 0.0,
            "mismatches": [],
        }

    mismatches: list[dict[str, Any]] = []
    aligned_count = 0
    directional_fill_count = 0
    for _, row in fills.iterrows():
        verdict = _normalize_direction_label(row.get("signal_verdict"))
        side = str(row.get("side") or "").strip().upper()
        expected_side = _expected_side_for_verdict(verdict)
        if not verdict:
            continue
        actual_direction = _directional_exposure_from_row(row)
        if actual_direction == "unknown":
            continue
        directional_fill_count += 1
        if verdict == actual_direction.upper():
            aligned_count += 1
            continue
        mismatches.append(
            {
                "timestamp": row.get("timestamp"),
                "trade_id": row.get("trade_id"),
                "symbol": row.get("symbol"),
                "signal_verdict": verdict,
                "executed_side": side,
                "expected_side": expected_side,
                "expected_direction": verdict,
                "actual_direction": actual_direction.upper(),
                "directional_exposure": actual_direction,
                "validation_sample_bucket": _normalize_validation_sample_bucket(row.get("validation_sample_bucket")),
                "reason_for_entry": row.get("reason_for_entry"),
            }
        )

    mismatched_count = len(mismatches)
    mismatch_rate = float(mismatched_count / directional_fill_count) if directional_fill_count > 0 else 0.0
    return {
        "fill_count": int(len(fills)),
        "directional_fill_count": int(directional_fill_count),
        "aligned_count": int(aligned_count),
        "mismatched_count": int(mismatched_count),
        "mismatch_rate": round(mismatch_rate, 6),
        "mismatches": _safe_json(mismatches),
    }


def _exit_reason(row: pd.Series) -> str:
    if str(row.get("status") or "").strip().upper() == "PARTIAL":
        return "Partial close"
    if _coerce_float(row.get("realized_pnl"), 0.0) < 0:
        return "Loss exit"
    if _coerce_float(row.get("realized_pnl"), 0.0) > 0:
        return "Profit exit"
    return "Flat exit"


def _build_validation_route_payload(row: pd.Series) -> dict[str, Any]:
    route_family = str(row.get("route_family") or "").strip().lower() or "legacy"
    route_version = str(row.get("route_version") or "").strip()
    route_correlation_id = _normalize_route_correlation_id(row.get("route_correlation_id"))
    thesis_direction = _normalize_direction_label(row.get("thesis_direction") or row.get("verdict"))
    directional_exposure = _normalize_directional_exposure(row.get("directional_exposure"))
    if directional_exposure == "unknown":
        directional_exposure = _directional_exposure_from_row(
            pd.Series(
                {
                    "instrument_type": row.get("instrument_type"),
                    "option_right": row.get("option_right"),
                    "side": "BUY",
                    "thesis_direction": thesis_direction,
                }
            )
        )
    validation_sample_bucket = _normalize_validation_sample_bucket(row.get("validation_sample_bucket"))
    if validation_sample_bucket == "legacy" and route_family == _CURRENT_ROUTE_FAMILY and route_version == _CURRENT_ROUTE_VERSION:
        validation_sample_bucket = "current_route"
    return {
        "route_family": route_family,
        "route_version": route_version,
        "route_correlation_id": route_correlation_id,
        "automation_entry_reason": str(row.get("automation_entry_reason") or "").strip(),
        "thesis_direction": thesis_direction,
        "directional_exposure": directional_exposure,
        "validation_sample_bucket": validation_sample_bucket,
        "option_right": str(row.get("option_right") or "").strip().lower(),
    }


def _build_entry_candidate_frame(*frames: pd.DataFrame) -> pd.DataFrame:
    usable_frames: list[pd.DataFrame] = []
    for frame in frames:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        usable_frames.append(frame.copy())
    if not usable_frames:
        return pd.DataFrame()
    if len(usable_frames) == 1:
        return usable_frames[0].reset_index(drop=True)
    return pd.concat(usable_frames, ignore_index=True)


def build_trade_validation_ledger(
    *,
    tenant_slug: str,
    starting_capital: float,
) -> pd.DataFrame:
    tenant = _load_tenant_row(tenant_slug)
    frames = _load_trade_frames()
    order_events = _load_order_events(tenant["id"])
    forecast_journal = frames["forecast_journal"]
    closed_trades = _backfill_trade_frame_route_metadata(frames["closed_trades"], order_events)
    open_trades = _backfill_trade_frame_route_metadata(frames["open_trades"], order_events)

    events: list[dict[str, Any]] = []
    time_lookup = _trade_event_lookup(order_events)

    entry_candidates = _build_entry_candidate_frame(closed_trades, open_trades)
    if not entry_candidates.empty and "opened_at" in entry_candidates.columns:
        entry_candidates["opened_at_ts"] = pd.to_datetime(entry_candidates["opened_at"], errors="coerce", utc=True)
        entry_candidates = entry_candidates.sort_values(["opened_at_ts", "trade_id"], ascending=[True, True], na_position="last")
    emitted_fills: set[str] = set()
    for _, row in entry_candidates.iterrows():
        trade_id = str(row.get("trade_id") or "").strip()
        if not trade_id or trade_id in emitted_fills:
            continue
        emitted_fills.add(trade_id)
        timestamps = time_lookup.get(trade_id, {})
        signal_timestamp = _resolve_signal_timestamp(row, forecast_journal)
        order_timestamp = timestamps.get("submitted_at") or _coerce_timestamp(row.get("submitted_at"))
        fill_timestamp = timestamps.get("filled_at") or _coerce_timestamp(row.get("opened_at"))
        quantity = _coerce_float(row.get("suggested_contracts"), 0.0)
        entry_fill_price = _entry_fill_price(row)
        slippage = _coerce_float(row.get("fill_slippage_dollars"), 0.0)
        fees = _coerce_float(row.get("fees"), 0.0)
        route_payload = _build_validation_route_payload(row)

        events.append(
            {
                "timestamp": fill_timestamp,
                "event_type": "fill",
                "trade_id": trade_id,
                "symbol": str(row.get("ticker") or "").strip().upper(),
                "side": "BUY",
                "signal_timestamp": signal_timestamp,
                "order_timestamp": order_timestamp.isoformat() if order_timestamp is not None else None,
                "fill_timestamp": fill_timestamp.isoformat() if fill_timestamp is not None else None,
                "fill_price": entry_fill_price,
                "quantity": quantity,
                "fees": fees,
                "slippage": slippage,
                "reason_for_entry": _entry_reason(row),
                "reason_for_exit": None,
                "instrument_type": str(row.get("instrument_type") or "").strip().lower() or "equity",
                "position_cost": _coerce_float(row.get("position_cost"), entry_fill_price * quantity * _multiplier_for_trade(row)),
                "max_risk_dollars": _coerce_float(row.get("max_risk_dollars"), 0.0),
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "verdict": str(row.get("verdict") or "").strip(),
                "interval": str(row.get("interval") or "").strip().lower(),
                "order_type": str(row.get("order_type") or "").strip().lower(),
                "status": str(row.get("status") or "").strip().upper(),
                "source": "entry",
                **route_payload,
            }
        )

    if not closed_trades.empty:
        for _, row in closed_trades.iterrows():
            trade_id = str(row.get("trade_id") or "").strip()
            if not trade_id:
                continue
            timestamps = time_lookup.get(trade_id, {})
            signal_timestamp = _resolve_signal_timestamp(row, forecast_journal)
            order_timestamp = timestamps.get("submitted_at") or _coerce_timestamp(row.get("submitted_at"))
            closed_at = timestamps.get("closed_at") or _coerce_timestamp(row.get("closed_at"))
            if closed_at is None:
                continue
            entry_fill_price = _entry_fill_price(row)
            close_quantity = _coerce_float(row.get("closed_contracts"), _coerce_float(row.get("suggested_contracts"), 0.0))
            close_fill_price = _close_fill_price(row)
            slippage = _coerce_float(row.get("fill_slippage_dollars"), 0.0)
            fees = _coerce_float(row.get("fees"), 0.0)
            route_payload = _build_validation_route_payload(row)
            events.append(
                {
                    "timestamp": closed_at,
                    "event_type": "close",
                    "trade_id": trade_id,
                    "symbol": str(row.get("ticker") or "").strip().upper(),
                    "side": "SELL",
                    "signal_timestamp": signal_timestamp,
                    "order_timestamp": order_timestamp.isoformat() if order_timestamp is not None else None,
                    "fill_timestamp": closed_at.isoformat(),
                    "fill_price": close_fill_price,
                    "quantity": close_quantity,
                    "fees": fees,
                    "slippage": slippage,
                    "reason_for_entry": _entry_reason(row),
                    "reason_for_exit": _exit_reason(row),
                    "instrument_type": str(row.get("instrument_type") or "").strip().lower() or "equity",
                    "position_cost": _coerce_float(row.get("position_cost"), entry_fill_price * _coerce_float(row.get("suggested_contracts"), 0.0) * _multiplier_for_trade(row)),
                    "max_risk_dollars": _coerce_float(row.get("max_risk_dollars"), 0.0),
                    "realized_pnl": _coerce_float(row.get("realized_pnl"), 0.0),
                    "unrealized_pnl": 0.0,
                    "verdict": str(row.get("verdict") or "").strip(),
                    "interval": str(row.get("interval") or "").strip().lower(),
                    "order_type": str(row.get("order_type") or "").strip().lower(),
                    "status": str(row.get("status") or "").strip().upper(),
                    "source": "close",
                    **route_payload,
                }
            )

    if not events:
        return pd.DataFrame(
            columns=[
                "timestamp",
                "event_type",
                "trade_id",
                "symbol",
                "side",
                "signal_timestamp",
                "order_timestamp",
                "fill_timestamp",
                "fill_price",
                "quantity",
                "cash_before",
                "cash_after",
                "position_before",
                "position_after",
                "gross_exposure",
                "net_exposure",
                "realized_pnl",
                "unrealized_pnl",
                "fees",
                "slippage",
                "borrowed_amount",
                "margin_interest",
                "equity_after_fill",
                "reason_for_entry",
                "reason_for_exit",
                "route_family",
                "route_version",
                "route_correlation_id",
                "automation_entry_reason",
                "thesis_direction",
                "directional_exposure",
                "validation_sample_bucket",
            ]
        )

    ledger = pd.DataFrame(events)
    event_order = {"fill": 0, "close": 1}
    ledger["timestamp"] = pd.to_datetime(ledger["timestamp"], errors="coerce", utc=True)
    ledger["_event_order"] = ledger["event_type"].map(event_order).fillna(9)
    ledger = ledger.sort_values(["timestamp", "_event_order", "trade_id"], ascending=[True, True, True]).reset_index(drop=True)

    cash_balance = float(starting_capital)
    cumulative_fees = 0.0
    cumulative_margin_interest = 0.0
    open_positions: dict[str, dict[str, float]] = {}
    rows: list[dict[str, Any]] = []
    previous_timestamp: pd.Timestamp | None = None
    previous_borrowed_amount = 0.0

    for _, row in ledger.iterrows():
        symbol = str(row["symbol"])
        quantity = _coerce_float(row.get("quantity"), 0.0)
        fill_price = _coerce_float(row.get("fill_price"), 0.0)
        multiplier = 1.0 if str(row.get("instrument_type") or "").strip().lower() == "equity" else 100.0
        notional = _coerce_float(row.get("position_cost"), fill_price * quantity * multiplier)
        fees = _coerce_float(row.get("fees"), 0.0)
        slippage = _coerce_float(row.get("slippage"), 0.0)
        cumulative_fees += fees
        timestamp = row["timestamp"] if pd.notna(row["timestamp"]) else None
        margin_interest = 0.0
        if (
            previous_timestamp is not None
            and timestamp is not None
            and previous_borrowed_amount > 0
            and _ANNUAL_MARGIN_INTEREST_RATE > 0
        ):
            elapsed_days = max((timestamp - previous_timestamp).total_seconds(), 0.0) / 86400.0
            margin_interest = previous_borrowed_amount * _ANNUAL_MARGIN_INTEREST_RATE * (elapsed_days / 365.0)
        cumulative_margin_interest += margin_interest

        position_before = _coerce_float((open_positions.get(symbol) or {}).get("quantity"), 0.0)
        cash_before = cash_balance

        if row["event_type"] == "fill":
            cash_balance -= notional
            open_positions[symbol] = {
                "quantity": position_before + quantity,
                "market_value": notional,
            }
            realized_pnl = 0.0
        else:
            market_value = fill_price * quantity * multiplier
            cash_balance += market_value
            remaining_quantity = max(0.0, position_before - quantity)
            if remaining_quantity <= 0:
                open_positions.pop(symbol, None)
            else:
                open_positions[symbol] = {
                    "quantity": remaining_quantity,
                    "market_value": remaining_quantity * fill_price * multiplier,
                }
            realized_pnl = _coerce_float(row.get("realized_pnl"), 0.0)

        position_after = _coerce_float((open_positions.get(symbol) or {}).get("quantity"), 0.0)
        gross_exposure = sum(abs(item.get("market_value", 0.0)) for item in open_positions.values())
        net_exposure = sum(item.get("market_value", 0.0) for item in open_positions.values())
        long_market_value = sum(max(item.get("market_value", 0.0), 0.0) for item in open_positions.values())
        short_market_value = sum(abs(min(item.get("market_value", 0.0), 0.0)) for item in open_positions.values())
        borrowed_amount = max(-cash_balance, 0.0)
        effective_cash = max(cash_balance, 0.0)
        equity_after = effective_cash + long_market_value - short_market_value - borrowed_amount - cumulative_fees - cumulative_margin_interest
        previous_timestamp = timestamp
        previous_borrowed_amount = borrowed_amount

        rows.append(
            {
                "timestamp": timestamp.isoformat() if timestamp is not None else None,
                "event_type": row["event_type"],
                "trade_id": row["trade_id"],
                "symbol": symbol,
                "side": row["side"],
                "signal_timestamp": row["signal_timestamp"],
                "order_timestamp": row["order_timestamp"],
                "fill_timestamp": row["fill_timestamp"],
                "fill_price": _round_money(fill_price),
                "quantity": quantity,
                "cash_before": _round_money(cash_before),
                "cash_after": _round_money(cash_balance),
                "position_before": position_before,
                "position_after": position_after,
                "gross_exposure": _round_money(gross_exposure),
                "net_exposure": _round_money(net_exposure),
                "long_market_value": _round_money(long_market_value),
                "short_market_value": _round_money(short_market_value),
                "borrowed_amount": _round_money(borrowed_amount),
                "realized_pnl": _round_money(realized_pnl),
                "unrealized_pnl": 0.0,
                "fees": _round_money(fees),
                "slippage": _round_money(slippage),
                "margin_interest": _round_money(margin_interest),
                "equity_after_fill": _round_money(equity_after),
                "reason_for_entry": row["reason_for_entry"],
                "reason_for_exit": row["reason_for_exit"],
                "instrument_type": row["instrument_type"],
                "option_right": row.get("option_right"),
                "interval": row["interval"],
                "signal_verdict": row["verdict"],
                "order_type": row["order_type"],
                "status": row["status"],
                "route_family": row.get("route_family"),
                "route_version": row.get("route_version"),
                "route_correlation_id": row.get("route_correlation_id"),
                "automation_entry_reason": row.get("automation_entry_reason"),
                "thesis_direction": row.get("thesis_direction"),
                "directional_exposure": row.get("directional_exposure"),
                "validation_sample_bucket": row.get("validation_sample_bucket"),
                "cumulative_fees": _round_money(cumulative_fees),
                "cumulative_margin_interest": _round_money(cumulative_margin_interest),
                "position_cost": _round_money(notional if row["event_type"] == "fill" else _coerce_float(row.get("position_cost"), 0.0)),
                "max_risk_dollars": _round_money(_coerce_float(row.get("max_risk_dollars"), 0.0)),
                "annual_margin_interest_rate": _ANNUAL_MARGIN_INTEREST_RATE,
            }
        )

    return pd.DataFrame(rows)


def _max_drawdown(equity_series: pd.Series) -> float:
    running_peak = equity_series.cummax()
    drawdown = (equity_series - running_peak) / running_peak.replace(0.0, pd.NA)
    drawdown = drawdown.fillna(0.0)
    return float(abs(drawdown.min()) * 100.0) if not drawdown.empty else 0.0


def compute_ledger_metrics(ledger: pd.DataFrame, *, starting_capital: float) -> dict[str, Any]:
    if ledger.empty:
        return {
            "trade_count": 0,
            "closed_trade_count": 0,
            "ending_equity": starting_capital,
            "return_pct": 0.0,
            "profit_factor": None,
            "profit_factor_status": "no_trades",
            "max_drawdown_pct": 0.0,
            "average_trade_profit": 0.0,
            "average_trade_cost": 0.0,
            "gross_exposure_peak": 0.0,
            "daily_loss_worst": 0.0,
            "weekly_loss_worst": 0.0,
        }

    closes = ledger[ledger["event_type"] == "close"].copy()
    fills = ledger[ledger["event_type"] == "fill"].copy()
    ending_equity = _coerce_float(ledger["equity_after_fill"].iloc[-1], starting_capital)
    return_pct = ((ending_equity - starting_capital) / starting_capital * 100.0) if starting_capital > 0 else 0.0

    realized = pd.to_numeric(closes.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    winners = realized[realized > 0]
    losers = realized[realized < 0]
    gross_profit = float(winners.sum()) if not winners.empty else 0.0
    gross_loss = float(abs(losers.sum())) if not losers.empty else 0.0
    if gross_loss > 0:
        profit_factor: float | None = float(gross_profit / gross_loss)
        profit_factor_status = "measured"
    elif gross_profit > 0:
        profit_factor = None
        profit_factor_status = "no_losses_yet"
    else:
        profit_factor = None
        profit_factor_status = "flat_or_losing"
    avg_trade_profit = float(realized.mean()) if not realized.empty else 0.0

    cost_series = pd.to_numeric(ledger.get("fees", pd.Series(dtype=float)), errors="coerce").fillna(0.0) + pd.to_numeric(
        ledger.get("slippage", pd.Series(dtype=float)),
        errors="coerce",
    ).fillna(0.0).abs() + pd.to_numeric(
        ledger.get("margin_interest", pd.Series(dtype=float)),
        errors="coerce",
    ).fillna(0.0)
    average_trade_cost = float(cost_series.mean()) if not cost_series.empty else 0.0
    gross_exposure_peak = float(pd.to_numeric(ledger["gross_exposure"], errors="coerce").fillna(0.0).max())
    max_drawdown_pct = _max_drawdown(pd.to_numeric(ledger["equity_after_fill"], errors="coerce").fillna(starting_capital))
    total_fees = float(pd.to_numeric(ledger.get("fees", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    total_margin_interest = float(pd.to_numeric(ledger.get("margin_interest", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    peak_borrowed_amount = float(pd.to_numeric(ledger.get("borrowed_amount", pd.Series(dtype=float)), errors="coerce").fillna(0.0).max())

    if "timestamp" in ledger.columns:
        timestamp_series = pd.to_datetime(ledger["timestamp"], errors="coerce", utc=True)
        closes = closes.copy()
        closes["timestamp_ts"] = pd.to_datetime(closes["timestamp"], errors="coerce", utc=True)
        closes["pnl"] = realized.values if len(realized) == len(closes.index) else pd.to_numeric(
            closes.get("realized_pnl", pd.Series(dtype=float)), errors="coerce"
        ).fillna(0.0)
        closes["day"] = closes["timestamp_ts"].dt.tz_convert("America/New_York").dt.date
        closes["week"] = closes["timestamp_ts"].dt.tz_convert("America/New_York").dt.strftime("%G-W%V")
        daily_loss_worst = float(closes.groupby("day")["pnl"].sum().min()) if not closes.empty else 0.0
        weekly_loss_worst = float(closes.groupby("week")["pnl"].sum().min()) if not closes.empty else 0.0
    else:
        daily_loss_worst = 0.0
        weekly_loss_worst = 0.0

    return {
        "trade_count": int(len(ledger)),
        "closed_trade_count": int(len(closes)),
        "ending_equity": round(ending_equity, 4),
        "return_pct": round(return_pct, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "profit_factor_status": profit_factor_status,
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "average_trade_profit": round(avg_trade_profit, 4),
        "average_trade_cost": round(average_trade_cost, 4),
        "gross_exposure_peak": round(gross_exposure_peak, 4),
        "total_fees": round(total_fees, 4),
        "total_margin_interest": round(total_margin_interest, 4),
        "peak_borrowed_amount": round(peak_borrowed_amount, 4),
        "daily_loss_worst": round(daily_loss_worst, 4),
        "weekly_loss_worst": round(weekly_loss_worst, 4),
    }


def build_signal_execution_alignment_report(ledger: pd.DataFrame) -> dict[str, Any]:
    empty_alignment = {
        "fill_count": 0,
        "directional_fill_count": 0,
        "aligned_count": 0,
        "mismatched_count": 0,
        "mismatch_rate": 0.0,
        "mismatches": [],
        "all_history_fill_count": 0,
        "all_history_directional_fill_count": 0,
        "all_history_aligned_count": 0,
        "all_history_mismatched_count": 0,
        "all_history_mismatch_rate": 0.0,
        "all_history_mismatches": [],
        "current_route_fill_count": 0,
        "current_route_directional_fill_count": 0,
        "current_route_aligned_count": 0,
        "current_route_mismatched_count": 0,
        "current_route_mismatch_rate": 0.0,
        "current_route_mismatches": [],
        "legacy_fill_count": 0,
        "legacy_directional_fill_count": 0,
        "legacy_aligned_count": 0,
        "legacy_mismatched_count": 0,
        "legacy_mismatch_rate": 0.0,
        "legacy_mismatches": [],
        "retired_rule_fill_count": 0,
        "retired_rule_directional_fill_count": 0,
        "retired_rule_aligned_count": 0,
        "retired_rule_mismatched_count": 0,
        "retired_rule_mismatch_rate": 0.0,
        "retired_rule_mismatches": [],
    }
    if ledger.empty:
        return empty_alignment

    fills = ledger[ledger["event_type"] == "fill"].copy()
    if fills.empty:
        return empty_alignment

    current_mask = _current_route_mask(fills)
    current_route_fills = fills.loc[current_mask].copy()
    legacy_fills = fills.loc[~current_mask].copy()
    overall = _build_alignment_stats(fills)
    current_route = _build_alignment_stats(current_route_fills)
    legacy = _build_alignment_stats(legacy_fills)
    return {
        **overall,
        "all_history_fill_count": overall["fill_count"],
        "all_history_directional_fill_count": overall["directional_fill_count"],
        "all_history_aligned_count": overall["aligned_count"],
        "all_history_mismatched_count": overall["mismatched_count"],
        "all_history_mismatch_rate": overall["mismatch_rate"],
        "all_history_mismatches": overall["mismatches"],
        "current_route_fill_count": current_route["fill_count"],
        "current_route_directional_fill_count": current_route["directional_fill_count"],
        "current_route_aligned_count": current_route["aligned_count"],
        "current_route_mismatched_count": current_route["mismatched_count"],
        "current_route_mismatch_rate": current_route["mismatch_rate"],
        "current_route_mismatches": current_route["mismatches"],
        "legacy_fill_count": legacy["fill_count"],
        "legacy_directional_fill_count": legacy["directional_fill_count"],
        "legacy_aligned_count": legacy["aligned_count"],
        "legacy_mismatched_count": legacy["mismatched_count"],
        "legacy_mismatch_rate": legacy["mismatch_rate"],
        "legacy_mismatches": legacy["mismatches"],
        "retired_rule_fill_count": legacy["fill_count"],
        "retired_rule_directional_fill_count": legacy["directional_fill_count"],
        "retired_rule_aligned_count": legacy["aligned_count"],
        "retired_rule_mismatched_count": legacy["mismatched_count"],
        "retired_rule_mismatch_rate": legacy["mismatch_rate"],
        "retired_rule_mismatches": legacy["mismatches"],
    }


def build_drawdown_report(ledger: pd.DataFrame) -> dict[str, Any]:
    if ledger.empty:
        return {
            "max_drawdown_pct": 0.0,
            "peak_timestamp": None,
            "trough_timestamp": None,
            "breakdown": {},
        }
    frame = ledger.copy()
    frame["equity"] = pd.to_numeric(frame["equity_after_fill"], errors="coerce").fillna(0.0)
    frame["running_peak"] = frame["equity"].cummax()
    frame["drawdown_pct"] = ((frame["equity"] - frame["running_peak"]) / frame["running_peak"].replace(0.0, pd.NA)).fillna(0.0)
    trough_index = frame["drawdown_pct"].idxmin()
    trough_row = frame.loc[trough_index]
    prior = frame.loc[:trough_index]
    peak_index = prior["equity"].idxmax()
    peak_row = frame.loc[peak_index]

    window = frame.loc[peak_index:trough_index].copy()
    closes = window[window["event_type"] == "close"].copy()
    breakdown: dict[str, Any] = {}
    if not closes.empty:
        closes["timestamp_ts"] = pd.to_datetime(closes["timestamp"], errors="coerce", utc=True)
        closes["hour_et"] = closes["timestamp_ts"].dt.tz_convert("America/New_York").dt.hour
        breakdown = {
            "by_symbol": closes.groupby("symbol")["realized_pnl"].sum().sort_values().round(4).to_dict(),
            "by_interval": closes.groupby("interval")["realized_pnl"].sum().sort_values().round(4).to_dict(),
            "by_verdict": closes.groupby("signal_verdict")["realized_pnl"].sum().sort_values().round(4).to_dict(),
            "by_hour_et": closes.groupby("hour_et")["realized_pnl"].sum().sort_values().round(4).to_dict(),
            "by_order_type": closes.groupby("order_type")["realized_pnl"].sum().sort_values().round(4).to_dict(),
        }

    return {
        "max_drawdown_pct": round(abs(float(trough_row["drawdown_pct"])) * 100.0, 4),
        "peak_timestamp": peak_row["timestamp"],
        "trough_timestamp": trough_row["timestamp"],
        "peak_equity": round(float(peak_row["equity"]), 4),
        "trough_equity": round(float(trough_row["equity"]), 4),
        "breakdown": _safe_json(breakdown),
    }


def build_mark_to_market_report(
    snapshots: pd.DataFrame,
    *,
    starting_capital: float,
) -> dict[str, Any]:
    if snapshots.empty:
        return {
            "snapshot_count": 0,
            "coverage_start": None,
            "coverage_end": None,
            "latest_equity": starting_capital,
            "latest_cash_estimate": starting_capital,
            "latest_realized_pnl": 0.0,
            "latest_unrealized_pnl": 0.0,
            "gross_exposure_peak": 0.0,
            "max_drawdown_pct": 0.0,
            "peak_timestamp": None,
            "trough_timestamp": None,
            "peak_equity": starting_capital,
            "trough_equity": starting_capital,
        }

    frame = snapshots.copy()
    frame["effective_ts"] = pd.to_datetime(frame.get("effective_ts"), errors="coerce", utc=True)
    frame["effective_ts"] = frame["effective_ts"].where(frame["effective_ts"].notna(), pd.to_datetime(frame.get("snapshot_at"), errors="coerce", utc=True))
    frame = frame[frame["effective_ts"].notna()].copy()
    if frame.empty:
        return {
            "snapshot_count": 0,
            "coverage_start": None,
            "coverage_end": None,
            "latest_equity": starting_capital,
            "latest_cash_estimate": starting_capital,
            "latest_realized_pnl": 0.0,
            "latest_unrealized_pnl": 0.0,
            "gross_exposure_peak": 0.0,
            "max_drawdown_pct": 0.0,
            "peak_timestamp": None,
            "trough_timestamp": None,
            "peak_equity": starting_capital,
            "trough_equity": starting_capital,
        }

    def _numeric_column(name: str, default: float) -> pd.Series:
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors="coerce").fillna(default)
        return pd.Series([default] * len(frame.index), index=frame.index, dtype="float64")

    frame["equity_value"] = _numeric_column("equity", starting_capital)
    frame["cash_estimate_value"] = _numeric_column("cash_estimate", starting_capital)
    frame["realized_pnl_value"] = _numeric_column("realized_pnl", 0.0)
    frame["unrealized_pnl_value"] = _numeric_column("unrealized_pnl", 0.0)
    frame["gross_exposure_value"] = _numeric_column("gross_exposure", 0.0)
    frame = frame.sort_values(["effective_ts", "snapshot_at_ts"], ascending=[True, True], na_position="last")
    frame["running_peak"] = frame["equity_value"].cummax()
    frame["drawdown_pct"] = ((frame["equity_value"] - frame["running_peak"]) / frame["running_peak"].replace(0.0, pd.NA)).fillna(0.0)

    trough_index = frame["drawdown_pct"].idxmin()
    trough_row = frame.loc[trough_index]
    prior = frame.loc[:trough_index]
    peak_index = prior["equity_value"].idxmax()
    peak_row = frame.loc[peak_index]
    latest_row = frame.iloc[-1]

    return {
        "snapshot_count": int(len(frame.index)),
        "coverage_start": frame.iloc[0]["effective_ts"].isoformat(),
        "coverage_end": latest_row["effective_ts"].isoformat(),
        "latest_equity": round(float(latest_row["equity_value"]), 4),
        "latest_cash_estimate": round(float(latest_row["cash_estimate_value"]), 4),
        "latest_realized_pnl": round(float(latest_row["realized_pnl_value"]), 4),
        "latest_unrealized_pnl": round(float(latest_row["unrealized_pnl_value"]), 4),
        "gross_exposure_peak": round(float(frame["gross_exposure_value"].max()), 4),
        "max_drawdown_pct": round(abs(float(trough_row["drawdown_pct"])) * 100.0, 4),
        "peak_timestamp": peak_row["effective_ts"].isoformat(),
        "trough_timestamp": trough_row["effective_ts"].isoformat(),
        "peak_equity": round(float(peak_row["equity_value"]), 4),
        "trough_equity": round(float(trough_row["equity_value"]), 4),
    }


def _build_metrics_source_summary(
    ledger: pd.DataFrame,
    snapshots: pd.DataFrame,
    *,
    ledger_metrics: dict[str, Any],
    mark_to_market_report: dict[str, Any],
    starting_capital: float,
) -> dict[str, Any]:
    snapshot_count = int(mark_to_market_report.get("snapshot_count") or 0)
    required_snapshot_columns = {"equity", "cash_estimate", "gross_exposure"}
    snapshot_columns = {str(column) for column in list(snapshots.columns)}
    missing_snapshot_columns = sorted(required_snapshot_columns - snapshot_columns)
    coverage_start = _coerce_timestamp(mark_to_market_report.get("coverage_start"))
    coverage_end = _coerce_timestamp(mark_to_market_report.get("coverage_end"))

    ledger_timestamps = pd.to_datetime(ledger.get("timestamp"), errors="coerce", utc=True) if "timestamp" in ledger.columns else pd.Series(dtype="datetime64[ns, UTC]")
    ledger_timestamps = ledger_timestamps[ledger_timestamps.notna()]
    ledger_start = ledger_timestamps.min() if not ledger_timestamps.empty else None
    ledger_end = ledger_timestamps.max() if not ledger_timestamps.empty else None

    spans_window = bool(
        snapshot_count >= 2
        and not missing_snapshot_columns
        and coverage_start is not None
        and coverage_end is not None
        and (ledger_start is None or coverage_start <= ledger_start)
        and (ledger_end is None or coverage_end >= ledger_end)
    )
    if snapshot_count <= 0:
        coverage_status = "missing"
    elif missing_snapshot_columns:
        coverage_status = "missing_fields"
    elif not spans_window:
        coverage_status = "partial_window"
    else:
        coverage_status = "complete"

    ledger_ending_equity = _coerce_float(ledger_metrics.get("ending_equity"), starting_capital)
    snapshot_ending_equity = _coerce_float(mark_to_market_report.get("latest_equity"), starting_capital)
    ending_equity_diff = abs(snapshot_ending_equity - ledger_ending_equity)
    ending_equity_denominator = max(abs(snapshot_ending_equity), abs(ledger_ending_equity), abs(float(starting_capital)), 1.0)
    ending_equity_diff_pct = (ending_equity_diff / ending_equity_denominator) * 100.0

    ledger_gross_peak = _coerce_float(ledger_metrics.get("gross_exposure_peak"), 0.0)
    snapshot_gross_peak = _coerce_float(mark_to_market_report.get("gross_exposure_peak"), 0.0)
    gross_exposure_diff = abs(snapshot_gross_peak - ledger_gross_peak)
    gross_exposure_denominator = max(abs(snapshot_gross_peak), abs(ledger_gross_peak), 1.0)
    gross_exposure_diff_pct = (gross_exposure_diff / gross_exposure_denominator) * 100.0

    if coverage_status != "complete":
        consistency_status = "unavailable"
    elif (
        ending_equity_diff_pct <= _ENDING_EQUITY_CONSISTENCY_TOLERANCE_PCT
        and gross_exposure_diff_pct <= _GROSS_EXPOSURE_CONSISTENCY_TOLERANCE_PCT
    ):
        consistency_status = "consistent"
    else:
        consistency_status = "inconsistent"

    use_mark_to_market = coverage_status == "complete" and consistency_status == "consistent"
    if consistency_status == "inconsistent":
        integrity_status = "fail"
        basis = "Mark-to-market snapshots disagree materially with the event ledger, so ledger metrics remain authoritative."
    elif coverage_status == "complete":
        integrity_status = "pass"
        basis = "Mark-to-market snapshot coverage spans the analyzed window and is consistent with the event ledger."
    else:
        integrity_status = "partial"
        basis = "Mark-to-market snapshot coverage is incomplete for the analyzed window, so ledger metrics remain authoritative."

    return {
        "status": integrity_status,
        "basis": basis,
        "metrics_source": "mark_to_market" if use_mark_to_market else "event_ledger",
        "mark_to_market_coverage_status": coverage_status,
        "ledger_snapshot_consistency": consistency_status,
        "snapshot_count": snapshot_count,
        "coverage_start": mark_to_market_report.get("coverage_start"),
        "coverage_end": mark_to_market_report.get("coverage_end"),
        "ledger_window_start": ledger_start.isoformat() if ledger_start is not None else None,
        "ledger_window_end": ledger_end.isoformat() if ledger_end is not None else None,
        "missing_snapshot_columns": missing_snapshot_columns,
        "ending_equity_diff": round(ending_equity_diff, 4),
        "ending_equity_diff_pct": round(ending_equity_diff_pct, 4),
        "gross_exposure_diff": round(gross_exposure_diff, 4),
        "gross_exposure_diff_pct": round(gross_exposure_diff_pct, 4),
        "use_mark_to_market": use_mark_to_market,
    }


def _build_current_route_validation_integrity(
    ledger: pd.DataFrame,
    snapshots: pd.DataFrame,
    *,
    starting_capital: float,
    current_route_sample_status: str,
    current_route_ledger: pd.DataFrame | None = None,
) -> dict[str, Any]:
    current_route_ledger = (
        current_route_ledger.copy()
        if isinstance(current_route_ledger, pd.DataFrame)
        else _filter_current_route_ledger(ledger)
    )
    route_window_start, route_window_end = _ledger_event_window(current_route_ledger)
    route_window_snapshots = _filter_snapshots_to_window(
        snapshots,
        start_at=route_window_start,
        end_at=route_window_end,
    )
    route_window_metrics = compute_ledger_metrics(current_route_ledger, starting_capital=starting_capital)
    route_window_mark_to_market = build_mark_to_market_report(
        route_window_snapshots,
        starting_capital=starting_capital,
    )
    integrity = _build_metrics_source_summary(
        current_route_ledger,
        route_window_snapshots,
        ledger_metrics=route_window_metrics,
        mark_to_market_report=route_window_mark_to_market,
        starting_capital=starting_capital,
    )
    route_window_start_iso = route_window_start.isoformat() if route_window_start is not None else None
    route_window_end_iso = route_window_end.isoformat() if route_window_end is not None else None
    if current_route_ledger.empty:
        integrity["status"] = "partial"
        integrity["basis"] = "No current-route fills have been recorded yet, so rollout validation is still collecting sample."
    integrity["current_route_sample_status"] = str(current_route_sample_status or "insufficient").strip().lower() or "insufficient"
    integrity["route_window_start"] = route_window_start_iso
    integrity["route_window_end"] = route_window_end_iso
    integrity["route_window_snapshot_count"] = int(route_window_mark_to_market.get("snapshot_count") or 0)
    integrity["route_window_metrics_source"] = integrity.get("metrics_source")
    integrity["route_window_mark_to_market_coverage_status"] = integrity.get("mark_to_market_coverage_status")
    integrity["route_window_ledger_snapshot_consistency"] = integrity.get("ledger_snapshot_consistency")
    return integrity


def _categorize_holding_minutes(minutes: float | None) -> str:
    if minutes is None or math.isnan(minutes):
        return "unknown"
    if minutes < 1:
        return "<1m"
    if minutes < 5:
        return "1-5m"
    if minutes < 15:
        return "5-15m"
    if minutes < 60:
        return "15-60m"
    return "60m+"


def _categorize_position_size(notional: float | None) -> str:
    value = 0.0 if notional is None or math.isnan(notional) else float(notional)
    if value < 5_000:
        return "<5k"
    if value < 10_000:
        return "5k-10k"
    if value < 25_000:
        return "10k-25k"
    return "25k+"


def _proxy_correlation_bucket(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if normalized in {"SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "SMH"}:
        return "index_or_sector_etf"
    if normalized in {"AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN", "GOOGL", "TSLA"}:
        return "mega_cap_tech"
    return "single_name_other"


def _stop_behavior_bucket(reason_for_exit: Any, realized_pnl: float) -> str:
    reason = str(reason_for_exit or "").strip().lower()
    if "partial" in reason:
        return "partial_close"
    if "stop" in reason:
        return "stop_exit"
    if realized_pnl < 0:
        return "loss_exit"
    if realized_pnl > 0:
        return "profit_exit"
    return "flat_exit"


def _build_group_breakdown(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    if frame.empty or column not in frame.columns:
        return []
    grouped = (
        frame.groupby(column, dropna=False)
        .agg(
            trade_count=("trade_id", "count"),
            realized_pnl=("realized_pnl_value", "sum"),
            average_pnl=("realized_pnl_value", "mean"),
            average_position_cost=("position_cost_value", "mean"),
        )
        .reset_index()
        .sort_values(["realized_pnl", "trade_count"], ascending=[True, False], na_position="last")
    )
    items: list[dict[str, Any]] = []
    for _, row in grouped.iterrows():
        items.append(
            {
                "bucket": str(row[column]) if pd.notna(row[column]) else "unknown",
                "trade_count": int(row["trade_count"]),
                "realized_pnl": round(float(row["realized_pnl"]), 4),
                "average_pnl": round(float(row["average_pnl"]), 4),
                "average_position_cost": round(float(row["average_position_cost"]), 4),
            }
        )
    return items


def build_drawdown_decomposition_report(
    ledger: pd.DataFrame,
    snapshots: pd.DataFrame,
    *,
    starting_capital: float,
) -> dict[str, Any]:
    if ledger.empty:
        return {
            "window_source": "no_ledger",
            "window_start": None,
            "window_end": None,
            "close_count": 0,
            "net_realized_pnl": 0.0,
            "worst_trade": None,
            "by_symbol": [],
            "by_signal_verdict": [],
            "by_entry_reason": [],
            "by_exit_reason": [],
            "by_stop_behavior": [],
            "by_hour_et": [],
            "by_holding_bucket": [],
            "by_position_size_bucket": [],
            "by_proxy_correlation_bucket": [],
            "worst_trades": [],
        }

    closes = ledger[ledger["event_type"] == "close"].copy()
    if closes.empty:
        return {
            "window_source": "no_closed_trades",
            "window_start": None,
            "window_end": None,
            "close_count": 0,
            "net_realized_pnl": 0.0,
            "worst_trade": None,
            "by_symbol": [],
            "by_signal_verdict": [],
            "by_entry_reason": [],
            "by_exit_reason": [],
            "by_stop_behavior": [],
            "by_hour_et": [],
            "by_holding_bucket": [],
            "by_position_size_bucket": [],
            "by_proxy_correlation_bucket": [],
            "worst_trades": [],
        }

    closes["timestamp_ts"] = pd.to_datetime(closes["timestamp"], errors="coerce", utc=True)
    closes["signal_ts"] = pd.to_datetime(closes.get("signal_timestamp"), errors="coerce", utc=True)
    closes["realized_pnl_value"] = pd.to_numeric(closes.get("realized_pnl"), errors="coerce").fillna(0.0)
    closes["position_cost_value"] = pd.to_numeric(closes.get("position_cost"), errors="coerce").fillna(0.0)
    closes["holding_minutes"] = (
        (closes["timestamp_ts"] - closes["signal_ts"]).dt.total_seconds() / 60.0
    ).where(closes["timestamp_ts"].notna() & closes["signal_ts"].notna())
    closes["hour_et"] = closes["timestamp_ts"].dt.tz_convert("America/New_York").dt.hour
    closes["holding_bucket"] = closes["holding_minutes"].apply(_categorize_holding_minutes)
    closes["position_size_bucket"] = closes["position_cost_value"].apply(_categorize_position_size)
    closes["proxy_correlation_bucket"] = closes["symbol"].apply(_proxy_correlation_bucket)
    closes["stop_behavior"] = closes.apply(
        lambda row: _stop_behavior_bucket(row.get("reason_for_exit"), _coerce_float(row.get("realized_pnl_value"), 0.0)),
        axis=1,
    )

    window_source = "all_closed_trades_fallback"
    window_start: pd.Timestamp | None = closes["timestamp_ts"].min()
    window_end: pd.Timestamp | None = closes["timestamp_ts"].max()

    if not snapshots.empty:
        mark_to_market = build_mark_to_market_report(snapshots, starting_capital=starting_capital)
        peak_ts = _coerce_timestamp(mark_to_market.get("peak_timestamp"))
        trough_ts = _coerce_timestamp(mark_to_market.get("trough_timestamp"))
        if peak_ts is not None and trough_ts is not None and trough_ts > peak_ts and _coerce_float(mark_to_market.get("max_drawdown_pct"), 0.0) > 0:
            window = closes.loc[(closes["timestamp_ts"] >= peak_ts) & (closes["timestamp_ts"] <= trough_ts)].copy()
            if not window.empty:
                closes = window
                window_source = "mark_to_market_drawdown_window"
                window_start = peak_ts
                window_end = trough_ts

    worst_trade_row = closes.sort_values("realized_pnl_value", ascending=True).iloc[0]
    worst_trades_frame = closes.sort_values("realized_pnl_value", ascending=True).head(10)
    worst_trades = [
        {
            "trade_id": str(row.get("trade_id") or ""),
            "symbol": str(row.get("symbol") or ""),
            "closed_at": row["timestamp_ts"].isoformat() if pd.notna(row["timestamp_ts"]) else None,
            "realized_pnl": round(float(row["realized_pnl_value"]), 4),
            "signal_verdict": str(row.get("signal_verdict") or ""),
            "entry_reason": str(row.get("reason_for_entry") or ""),
            "exit_reason": str(row.get("reason_for_exit") or ""),
            "holding_minutes": round(float(row["holding_minutes"]), 4) if pd.notna(row["holding_minutes"]) else None,
            "position_cost": round(float(row["position_cost_value"]), 4),
            "proxy_correlation_bucket": str(row.get("proxy_correlation_bucket") or "unknown"),
        }
        for _, row in worst_trades_frame.iterrows()
    ]

    return {
        "window_source": window_source,
        "window_start": window_start.isoformat() if window_start is not None else None,
        "window_end": window_end.isoformat() if window_end is not None else None,
        "close_count": int(len(closes.index)),
        "net_realized_pnl": round(float(closes["realized_pnl_value"].sum()), 4),
        "worst_trade": worst_trades[0] if worst_trades else None,
        "by_symbol": _build_group_breakdown(closes, "symbol"),
        "by_signal_verdict": _build_group_breakdown(closes, "signal_verdict"),
        "by_entry_reason": _build_group_breakdown(closes, "reason_for_entry"),
        "by_exit_reason": _build_group_breakdown(closes, "reason_for_exit"),
        "by_stop_behavior": _build_group_breakdown(closes, "stop_behavior"),
        "by_hour_et": _build_group_breakdown(closes, "hour_et"),
        "by_holding_bucket": _build_group_breakdown(closes, "holding_bucket"),
        "by_position_size_bucket": _build_group_breakdown(closes, "position_size_bucket"),
        "by_proxy_correlation_bucket": _build_group_breakdown(closes, "proxy_correlation_bucket"),
        "worst_trades": _safe_json(worst_trades),
    }


def build_broker_reconciliation_report(
    ledger: pd.DataFrame,
    *,
    open_trades: pd.DataFrame,
    closed_trades: pd.DataFrame,
    pending_orders: pd.DataFrame,
    order_events: pd.DataFrame,
) -> dict[str, Any]:
    tolerance_qty = 0.001
    tolerance_price = 0.01
    open_trades = _backfill_trade_frame_route_metadata(open_trades, order_events)
    closed_trades = _backfill_trade_frame_route_metadata(closed_trades, order_events)
    pending_orders = _backfill_trade_frame_route_metadata(pending_orders, order_events)

    def _container_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        containers = [payload]
        for key in (
            "trade",
            "record",
            "filled_record",
            "pending_order",
            "updated_order",
            "synced_order",
            "order",
            "request",
            "execution",
        ):
            inner = payload.get(key)
            if isinstance(inner, dict):
                containers.append(inner)
        return containers

    def _merge_route_payload(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in candidate.items():
            if value not in (None, "", "unknown", "legacy"):
                merged[key] = value
            elif key == "route_correlation_id" and key not in merged and value not in (None, ""):
                merged[key] = value
            elif key not in merged and value not in (None, ""):
                merged[key] = value
        return merged

    def _collect_broker_order_ids(container: dict[str, Any]) -> set[str]:
        broker_ids: set[str] = set()
        for key in ("broker_order_id", "broker_close_order_id"):
            normalized = str(container.get(key) or "").strip()
            if normalized:
                broker_ids.add(normalized)
        return broker_ids

    def _build_event_metadata(row: pd.Series) -> dict[str, Any]:
        payload_json = row.get("payload_json")
        payload = payload_json if isinstance(payload_json, dict) else {}
        route_payload: dict[str, Any] = {}
        broker_order_ids: set[str] = set()
        for container in _container_list(payload):
            route_payload = _merge_route_payload(route_payload, _extract_route_payload_from_container(container))
            broker_order_ids.update(_collect_broker_order_ids(container))
        route_family = str(route_payload.get("route_family") or "").strip().lower()
        route_version = str(route_payload.get("route_version") or "").strip()
        validation_sample_bucket = _normalize_validation_sample_bucket(route_payload.get("validation_sample_bucket"))
        current_route = validation_sample_bucket == "current_route" or (
            route_family == _CURRENT_ROUTE_FAMILY and route_version == _CURRENT_ROUTE_VERSION
        )
        return {
            "trade_id": str(row.get("trade_id") or "").strip(),
            "event_key": str(row.get("event_key") or "").strip().lower(),
            "status": str(row.get("status") or "").strip().lower() or None,
            "created_at": _coerce_timestamp(row.get("created_at")),
            "detail": str(row.get("detail") or "").strip() or None,
            "route_family": route_family or "legacy",
            "route_version": route_version,
            "route_correlation_id": _normalize_route_correlation_id(route_payload.get("route_correlation_id")),
            "validation_sample_bucket": validation_sample_bucket,
            "current_route": current_route,
            "broker_order_ids": broker_order_ids,
        }

    local_groups: dict[str, dict[str, Any]] = {}
    correlation_index: dict[str, set[str]] = {}
    broker_order_index: dict[str, set[str]] = {}

    def _ingest_local(frame: pd.DataFrame, source: str) -> None:
        if frame.empty:
            return
        for _, row in frame.iterrows():
            trade_id = str(row.get("trade_id") or "").strip()
            if not trade_id:
                continue
            route_family = str(row.get("route_family") or "").strip().lower() or "legacy"
            route_version = str(row.get("route_version") or "").strip()
            validation_sample_bucket = _normalize_validation_sample_bucket(row.get("validation_sample_bucket"))
            current_route = validation_sample_bucket == "current_route" or (
                route_family == _CURRENT_ROUTE_FAMILY and route_version == _CURRENT_ROUTE_VERSION
            )
            item = local_groups.setdefault(
                trade_id,
                {
                    "trade_id": trade_id,
                    "symbol": str(row.get("ticker") or "").strip().upper() or None,
                    "sources": [],
                    "broker_name": None,
                    "broker_order_ids": set(),
                    "local_entry_qty": 0.0,
                    "local_closed_qty": 0.0,
                    "local_remaining_qty": 0.0,
                    "local_fill_price": None,
                    "local_fill_statuses": set(),
                    "route_family": route_family,
                    "route_version": route_version,
                    "route_correlation_id": _normalize_route_correlation_id(row.get("route_correlation_id")),
                    "validation_sample_bucket": validation_sample_bucket,
                    "current_route": current_route,
                },
            )
            item["sources"].append(source)
            if not item["symbol"]:
                item["symbol"] = str(row.get("ticker") or "").strip().upper() or None
            broker_name = str(row.get("broker_name") or "").strip().lower()
            if broker_name and not item["broker_name"]:
                item["broker_name"] = broker_name
            for broker_key in ("broker_order_id", "broker_close_order_id"):
                broker_order_id = str(row.get(broker_key) or "").strip()
                if broker_order_id:
                    item["broker_order_ids"].add(broker_order_id)
                    broker_order_index.setdefault(broker_order_id, set()).add(trade_id)
            route_correlation_id = _normalize_route_correlation_id(row.get("route_correlation_id"))
            if route_correlation_id:
                item["route_correlation_id"] = route_correlation_id
                correlation_index.setdefault(route_correlation_id, set()).add(trade_id)
            item["route_family"] = route_family or str(item.get("route_family") or "legacy")
            item["route_version"] = route_version or str(item.get("route_version") or "")
            item["validation_sample_bucket"] = validation_sample_bucket or str(item.get("validation_sample_bucket") or "legacy")
            item["current_route"] = bool(item.get("current_route")) or current_route
            item["local_entry_qty"] = max(
                float(item["local_entry_qty"]),
                _coerce_float(
                    row.get("broker_filled_qty"),
                    _coerce_float(row.get("filled_contracts"), _coerce_float(row.get("suggested_contracts"), 0.0)),
                ),
            )
            item["local_closed_qty"] += _coerce_float(row.get("closed_contracts"), 0.0)
            item["local_remaining_qty"] = max(
                float(item["local_remaining_qty"]),
                _coerce_float(
                    row.get("remaining_contracts_after_close"),
                    _coerce_float(row.get("suggested_contracts"), _coerce_float(row.get("remaining_contracts"), 0.0)),
                )
                if source == "open"
                else _coerce_float(row.get("remaining_contracts_after_close"), 0.0),
            )
            fill_price = _coerce_float(
                row.get("actual_fill_price"),
                _coerce_float(row.get("broker_filled_avg_price"), float("nan")),
            )
            if item["local_fill_price"] is None and not math.isnan(fill_price) and fill_price > 0:
                item["local_fill_price"] = fill_price
            status = str(row.get("broker_status") or row.get("order_status") or row.get("status") or "").strip().lower()
            if status:
                item["local_fill_statuses"].add(status)

    _ingest_local(open_trades, "open")
    _ingest_local(closed_trades, "closed")
    _ingest_local(pending_orders, "pending")

    ledger_fills = ledger[ledger["event_type"] == "fill"].copy() if not ledger.empty else pd.DataFrame()
    ledger_closes = ledger[ledger["event_type"] == "close"].copy() if not ledger.empty else pd.DataFrame()

    event_groups: dict[str, dict[str, Any]] = {}
    unmatched_event_groups: dict[str, dict[str, Any]] = {}
    last_submitted_current_route_at: pd.Timestamp | None = None
    last_current_route_fill_at: pd.Timestamp | None = None
    last_current_route_close_at: pd.Timestamp | None = None

    def _resolve_event_trade_id(metadata: dict[str, Any]) -> tuple[str | None, str | None]:
        route_correlation_id = str(metadata.get("route_correlation_id") or "").strip()
        if route_correlation_id:
            trade_ids = correlation_index.get(route_correlation_id) or set()
            if len(trade_ids) == 1:
                return next(iter(trade_ids)), "route_correlation_id"
        for broker_order_id in sorted(metadata.get("broker_order_ids") or []):
            trade_ids = broker_order_index.get(broker_order_id) or set()
            if len(trade_ids) == 1:
                return next(iter(trade_ids)), "broker_order_id"
        if not bool(metadata.get("current_route")):
            trade_id = str(metadata.get("trade_id") or "").strip()
            if trade_id and trade_id in local_groups:
                return trade_id, "legacy_trade_id"
        return None, None

    if not order_events.empty:
        for _, row in order_events.iterrows():
            metadata = _build_event_metadata(row)
            created_at = metadata.get("created_at")
            event_key = str(metadata.get("event_key") or "").strip().lower()
            if bool(metadata.get("current_route")):
                if event_key == "order.submitted" and created_at is not None:
                    last_submitted_current_route_at = created_at if last_submitted_current_route_at is None else max(last_submitted_current_route_at, created_at)
                elif event_key == "order.filled" and created_at is not None:
                    last_current_route_fill_at = created_at if last_current_route_fill_at is None else max(last_current_route_fill_at, created_at)
                elif event_key == "order.closed" and created_at is not None:
                    last_current_route_close_at = created_at if last_current_route_close_at is None else max(last_current_route_close_at, created_at)

            matched_trade_id, matched_via = _resolve_event_trade_id(metadata)
            if matched_trade_id:
                group = event_groups.setdefault(
                    matched_trade_id,
                    {
                        "event_counts": Counter(),
                        "event_keys": set(),
                        "latest_status": None,
                        "matched_via": set(),
                    },
                )
                group["event_counts"][event_key] += 1
                group["event_keys"].add(event_key)
                if metadata.get("status"):
                    group["latest_status"] = metadata.get("status")
                if matched_via:
                    group["matched_via"].add(matched_via)
                continue

            orphan_key = (
                str(metadata.get("route_correlation_id") or "").strip()
                or "|".join(sorted(metadata.get("broker_order_ids") or []))
                or str(metadata.get("trade_id") or "").strip()
                or f"orphan-{len(unmatched_event_groups) + 1}"
            )
            group = unmatched_event_groups.setdefault(
                orphan_key,
                {
                    "trade_id": str(metadata.get("trade_id") or "").strip() or None,
                    "route_family": str(metadata.get("route_family") or "legacy"),
                    "route_version": str(metadata.get("route_version") or ""),
                    "route_correlation_id": str(metadata.get("route_correlation_id") or "").strip() or None,
                    "validation_sample_bucket": str(metadata.get("validation_sample_bucket") or "legacy"),
                    "current_route": bool(metadata.get("current_route")),
                    "event_counts": Counter(),
                    "event_keys": set(),
                    "latest_status": None,
                    "events": [],
                    "broker_order_ids": set(metadata.get("broker_order_ids") or set()),
                },
            )
            group["event_counts"][event_key] += 1
            group["event_keys"].add(event_key)
            group["broker_order_ids"].update(metadata.get("broker_order_ids") or set())
            if metadata.get("status"):
                group["latest_status"] = metadata.get("status")
            group["events"].append(
                {
                    "event_key": event_key,
                    "status": metadata.get("status"),
                    "created_at": created_at.isoformat() if created_at is not None else None,
                    "detail": metadata.get("detail"),
                }
            )

    reconciliation_items: list[dict[str, Any]] = []
    issue_counter: Counter[str] = Counter()
    current_route_issue_counter: Counter[str] = Counter()
    reconciled_current_route_fill_trade_ids: set[str] = set()
    reconciled_current_route_close_trade_ids: set[str] = set()

    ledger_trade_ids = {
        str(value).strip()
        for value in ledger.get("trade_id", pd.Series(dtype=str)).astype(str).tolist()
        if str(value).strip()
    }
    all_trade_ids = sorted(set(local_groups.keys()) | ledger_trade_ids)
    for trade_id in all_trade_ids:
        local_item = local_groups.get(trade_id, {})
        fill_rows = (
            ledger_fills[ledger_fills.get("trade_id", pd.Series(dtype=str)).astype(str).str.strip() == trade_id]
            if not ledger_fills.empty
            else pd.DataFrame()
        )
        close_rows = (
            ledger_closes[ledger_closes.get("trade_id", pd.Series(dtype=str)).astype(str).str.strip() == trade_id]
            if not ledger_closes.empty
            else pd.DataFrame()
        )
        ledger_fill_qty = (
            float(pd.to_numeric(fill_rows.get("quantity", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
            if not fill_rows.empty
            else 0.0
        )
        ledger_close_qty = (
            float(pd.to_numeric(close_rows.get("quantity", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
            if not close_rows.empty
            else 0.0
        )
        ledger_fill_price = _coerce_float(fill_rows.iloc[0].get("fill_price"), float("nan")) if not fill_rows.empty else float("nan")
        local_fill_price = _coerce_float(local_item.get("local_fill_price"), float("nan"))
        local_entry_qty = _coerce_float(local_item.get("local_entry_qty"), 0.0)
        local_closed_qty = _coerce_float(local_item.get("local_closed_qty"), 0.0)
        event_info = event_groups.get(
            trade_id,
            {"event_counts": Counter(), "event_keys": set(), "latest_status": None, "matched_via": set()},
        )

        issues: list[str] = []
        if local_item and event_info["event_counts"].get("order.submitted", 0) <= 0:
            issues.append("missing_order_submitted_event")
        if local_item and event_info["event_counts"].get("order.filled", 0) <= 0 and local_entry_qty > 0:
            issues.append("missing_order_filled_event")
        if "closed" in list(local_item.get("sources") or []) and event_info["event_counts"].get("order.closed", 0) <= 0:
            issues.append("missing_order_closed_event")
        if local_item and abs(local_entry_qty - ledger_fill_qty) > tolerance_qty:
            issues.append("fill_quantity_mismatch")
        if local_item and local_closed_qty > 0 and abs(local_closed_qty - ledger_close_qty) > tolerance_qty:
            issues.append("close_quantity_mismatch")
        if local_item and not math.isnan(local_fill_price) and not math.isnan(ledger_fill_price) and abs(local_fill_price - ledger_fill_price) > tolerance_price:
            issues.append("fill_price_mismatch")
        if local_item and fill_rows.empty:
            issues.append("missing_ledger_fill")
        if "closed" in list(local_item.get("sources") or []) and close_rows.empty:
            issues.append("missing_ledger_close")

        fill_reconciled = bool(local_item) and local_entry_qty > 0 and not any(
            issue in issues for issue in ("missing_order_filled_event", "fill_quantity_mismatch", "missing_ledger_fill")
        )
        close_reconciled = bool(local_item) and local_closed_qty > 0 and not any(
            issue in issues for issue in ("missing_order_closed_event", "close_quantity_mismatch", "missing_ledger_close")
        )
        if bool(local_item.get("current_route")) and fill_reconciled:
            reconciled_current_route_fill_trade_ids.add(trade_id)
        if bool(local_item.get("current_route")) and close_reconciled:
            reconciled_current_route_close_trade_ids.add(trade_id)

        for issue in issues:
            issue_counter[issue] += 1
            if bool(local_item.get("current_route")):
                current_route_issue_counter[issue] += 1

        reconciliation_items.append(
            {
                "trade_id": trade_id,
                "symbol": local_item.get("symbol") or (str(fill_rows.iloc[0].get("symbol") or "").strip().upper() if not fill_rows.empty else None),
                "sources": sorted(set(local_item.get("sources") or [])),
                "broker_name": local_item.get("broker_name"),
                "broker_order_ids": sorted(local_item.get("broker_order_ids") or []),
                "route_family": local_item.get("route_family") or "legacy",
                "route_version": local_item.get("route_version"),
                "route_correlation_id": local_item.get("route_correlation_id"),
                "validation_sample_bucket": local_item.get("validation_sample_bucket") or "legacy",
                "match_modes": sorted(event_info.get("matched_via") or []),
                "local_entry_qty": round(local_entry_qty, 6),
                "ledger_fill_qty": round(ledger_fill_qty, 6),
                "local_closed_qty": round(local_closed_qty, 6),
                "ledger_close_qty": round(ledger_close_qty, 6),
                "local_remaining_qty": round(_coerce_float(local_item.get("local_remaining_qty"), 0.0), 6),
                "local_fill_price": round(local_fill_price, 4) if not math.isnan(local_fill_price) else None,
                "ledger_fill_price": round(ledger_fill_price, 4) if not math.isnan(ledger_fill_price) else None,
                "event_counts": dict(event_info["event_counts"]),
                "event_keys": sorted(event_info["event_keys"]),
                "latest_event_status": event_info["latest_status"],
                "fill_reconciled": fill_reconciled,
                "close_reconciled": close_reconciled,
                "issues": issues,
            }
        )

    current_route_orphan_event_count = 0
    legacy_orphan_event_count = 0
    for orphan_group in unmatched_event_groups.values():
        rejected_only = (
            orphan_group["event_counts"].get("order.rejected", 0) > 0
            and orphan_group["event_counts"].get("order.filled", 0) <= 0
            and orphan_group["event_counts"].get("order.closed", 0) <= 0
        )
        if rejected_only:
            continue
        orphan_issue = "current_route_orphan_order_events" if orphan_group.get("current_route") else "legacy_orphan_order_events"
        orphan_event_count = len(list(orphan_group.get("events") or [])) or int(sum(orphan_group["event_counts"].values()))
        if orphan_group.get("current_route"):
            current_route_orphan_event_count += orphan_event_count
        else:
            legacy_orphan_event_count += orphan_event_count
        issue_counter["orphan_order_events"] += orphan_event_count
        issue_counter[orphan_issue] += orphan_event_count
        reconciliation_items.append(
            {
                "trade_id": orphan_group.get("trade_id"),
                "symbol": None,
                "sources": [],
                "broker_name": None,
                "broker_order_ids": sorted(orphan_group.get("broker_order_ids") or []),
                "route_family": orphan_group.get("route_family") or "legacy",
                "route_version": orphan_group.get("route_version"),
                "route_correlation_id": orphan_group.get("route_correlation_id"),
                "validation_sample_bucket": orphan_group.get("validation_sample_bucket") or "legacy",
                "match_modes": [],
                "local_entry_qty": 0.0,
                "ledger_fill_qty": 0.0,
                "local_closed_qty": 0.0,
                "ledger_close_qty": 0.0,
                "local_remaining_qty": 0.0,
                "local_fill_price": None,
                "ledger_fill_price": None,
                "event_counts": dict(orphan_group["event_counts"]),
                "event_keys": sorted(orphan_group["event_keys"]),
                "latest_event_status": orphan_group["latest_status"],
                "fill_reconciled": False,
                "close_reconciled": False,
                "issues": [orphan_issue],
            }
        )

    current_route_issue_count = int(sum(current_route_issue_counter.values()))
    if current_route_orphan_event_count > 0:
        current_route_reconciliation_status = "orphaned"
    elif current_route_issue_count > 0:
        current_route_reconciliation_status = "issues_present"
    elif reconciled_current_route_fill_trade_ids or reconciled_current_route_close_trade_ids or last_submitted_current_route_at is not None:
        current_route_reconciliation_status = "clean"
    else:
        current_route_reconciliation_status = "waiting"

    return {
        "trade_count": len(reconciliation_items),
        "matched_trade_count": int(sum(1 for item in reconciliation_items if not item["issues"])),
        "issue_counts": dict(issue_counter),
        "current_route_issue_counts": dict(current_route_issue_counter),
        "current_route_issue_count": current_route_issue_count,
        "current_route_reconciliation_status": current_route_reconciliation_status,
        "current_route_orphan_order_event_count": int(current_route_orphan_event_count),
        "legacy_orphan_order_event_count": int(legacy_orphan_event_count),
        "reconciled_current_route_fill_trade_ids": sorted(reconciled_current_route_fill_trade_ids),
        "reconciled_current_route_close_trade_ids": sorted(reconciled_current_route_close_trade_ids),
        "last_submitted_current_route_order_at": last_submitted_current_route_at.isoformat() if last_submitted_current_route_at is not None else None,
        "last_current_route_fill_at": last_current_route_fill_at.isoformat() if last_current_route_fill_at is not None else None,
        "last_current_route_close_at": last_current_route_close_at.isoformat() if last_current_route_close_at is not None else None,
        "items": _safe_json(reconciliation_items),
    }


def _filter_reconciled_current_route_ledger(
    ledger: pd.DataFrame,
    broker_reconciliation: dict[str, Any] | None,
) -> pd.DataFrame:
    current_route_ledger = _filter_current_route_ledger(ledger)
    if current_route_ledger.empty:
        return current_route_ledger
    if broker_reconciliation is None:
        return current_route_ledger.copy().reset_index(drop=True)
    fill_trade_ids = {
        str(value).strip()
        for value in list((broker_reconciliation or {}).get("reconciled_current_route_fill_trade_ids") or [])
        if str(value).strip()
    }
    close_trade_ids = {
        str(value).strip()
        for value in list((broker_reconciliation or {}).get("reconciled_current_route_close_trade_ids") or [])
        if str(value).strip()
    }
    if not fill_trade_ids and not close_trade_ids:
        return current_route_ledger.iloc[0:0].copy()
    trade_ids = current_route_ledger.get("trade_id", pd.Series("", index=current_route_ledger.index)).astype(str).str.strip()
    event_types = current_route_ledger.get("event_type", pd.Series("", index=current_route_ledger.index)).astype(str).str.strip().str.lower()
    keep_mask = (event_types.eq("fill") & trade_ids.isin(fill_trade_ids)) | (event_types.eq("close") & trade_ids.isin(close_trade_ids))
    return current_route_ledger.loc[keep_mask].copy().reset_index(drop=True)


def build_next_bar_replay_report(ledger: pd.DataFrame) -> dict[str, Any]:
    fills = ledger[ledger["event_type"] == "fill"].copy() if not ledger.empty else pd.DataFrame()
    if fills.empty:
        return {
            "trade_count": 0,
            "replayed_count": 0,
            "average_entry_penalty_dollars": 0.0,
            "average_entry_penalty_bps": 0.0,
            "worse_fill_rate": 0.0,
            "items": [],
        }

    fills["signal_ts"] = pd.to_datetime(fills.get("signal_timestamp"), errors="coerce", utc=True)
    fills["actual_fill_price_value"] = pd.to_numeric(fills.get("fill_price"), errors="coerce")
    quantity_series = (
        fills["quantity"]
        if "quantity" in fills.columns
        else pd.Series(0.0, index=fills.index, dtype="float64")
    )
    fills["quantity_value"] = pd.to_numeric(quantity_series, errors="coerce").fillna(0.0)
    items: list[dict[str, Any]] = []
    cache: dict[tuple[str, str, str], pd.DataFrame] = {}

    for _, row in fills.iterrows():
        symbol = str(row.get("symbol") or "").strip().upper()
        interval = str(row.get("interval") or "5m").strip().lower() or "5m"
        signal_ts = row.get("signal_ts")
        actual_fill_price = _coerce_float(row.get("actual_fill_price_value"), float("nan"))
        quantity = _coerce_float(row.get("quantity_value"), 0.0)
        if not symbol or pd.isna(signal_ts) or math.isnan(actual_fill_price) or actual_fill_price <= 0 or quantity <= 0:
            continue

        period = _period_for_span(signal_ts - pd.Timedelta(days=2), signal_ts + pd.Timedelta(days=2))
        cache_key = (symbol, interval, period)
        if cache_key not in cache:
            cache[cache_key] = _download_history(symbol, period=period, interval=interval)
        history = cache[cache_key]
        next_open = _extract_open_after(history, signal_ts)
        if next_open is None or math.isnan(next_open) or next_open <= 0:
            continue

        multiplier = 1.0 if str(row.get("instrument_type") or "").strip().lower() == "equity" else 100.0
        penalty_dollars = (next_open - actual_fill_price) * quantity * multiplier
        penalty_bps = ((next_open - actual_fill_price) / actual_fill_price) * 10000.0 if actual_fill_price > 0 else 0.0
        items.append(
            {
                "trade_id": row.get("trade_id"),
                "symbol": symbol,
                "interval": interval,
                "signal_timestamp": signal_ts.isoformat(),
                "actual_fill_price": round(actual_fill_price, 4),
                "replay_next_open": round(next_open, 4),
                "entry_penalty_dollars": round(float(penalty_dollars), 4),
                "entry_penalty_bps": round(float(penalty_bps), 4),
                "worse_fill": bool(penalty_dollars > 0),
            }
        )

    if not items:
        return {
            "trade_count": int(len(fills.index)),
            "replayed_count": 0,
            "average_entry_penalty_dollars": 0.0,
            "average_entry_penalty_bps": 0.0,
            "worse_fill_rate": 0.0,
            "items": [],
        }

    item_frame = pd.DataFrame(items)
    return {
        "trade_count": int(len(fills.index)),
        "replayed_count": int(len(item_frame.index)),
        "average_entry_penalty_dollars": round(float(pd.to_numeric(item_frame["entry_penalty_dollars"], errors="coerce").fillna(0.0).mean()), 4),
        "average_entry_penalty_bps": round(float(pd.to_numeric(item_frame["entry_penalty_bps"], errors="coerce").fillna(0.0).mean()), 4),
        "worse_fill_rate": round(float(pd.to_numeric(item_frame["worse_fill"], errors="coerce").fillna(0.0).mean()), 6),
        "items": _safe_json(items),
    }


def _attach_replay_penalties(ledger: pd.DataFrame, replay_report: dict[str, Any]) -> pd.DataFrame:
    if ledger.empty:
        return ledger.copy()
    replay_lookup = {
        str(item.get("trade_id") or "").strip(): _coerce_float(item.get("entry_penalty_dollars"), 0.0)
        for item in list(replay_report.get("items") or [])
        if str(item.get("trade_id") or "").strip()
    }
    frame = ledger.copy()
    frame["replay_entry_penalty_dollars"] = frame.get("trade_id", pd.Series(dtype=str)).astype(str).map(replay_lookup).fillna(0.0)
    return frame


def build_current_route_execution_realism_report(
    ledger: pd.DataFrame,
    *,
    starting_capital: float,
    current_settings: dict[str, Any],
    broker_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_route_ledger = _filter_reconciled_current_route_ledger(
        ledger,
        broker_reconciliation,
    )
    next_bar_replay = build_next_bar_replay_report(current_route_ledger)
    current_route_ledger = _attach_replay_penalties(current_route_ledger, next_bar_replay)
    stress_matrix = build_stress_matrix_results(
        current_route_ledger,
        starting_capital=starting_capital,
        current_settings=current_settings,
    )
    metrics = compute_ledger_metrics(current_route_ledger, starting_capital=starting_capital)
    alignment = build_signal_execution_alignment_report(current_route_ledger)
    directional_fill_count = int(alignment.get("directional_fill_count") or 0)
    closed_trade_count = int(metrics.get("closed_trade_count") or 0)
    sample_status = (
        "sufficient"
        if directional_fill_count >= _CURRENT_ROUTE_MIN_DIRECTIONAL_FILLS and closed_trade_count >= _CURRENT_ROUTE_MIN_CLOSED_TRADES
        else "insufficient"
    )
    return {
        "trade_count": int(metrics.get("trade_count") or 0),
        "closed_trade_count": closed_trade_count,
        "signal_execution_alignment": alignment,
        "next_bar_replay": next_bar_replay,
        "stress_matrix": stress_matrix,
        "sample_status": sample_status,
        "current_route_fill_count": directional_fill_count,
        "current_route_reconciliation_status": str(
            (broker_reconciliation or {}).get("current_route_reconciliation_status") or "waiting"
        ).strip().lower() or "waiting",
        "current_route_orphan_order_event_count": int(
            (broker_reconciliation or {}).get("current_route_orphan_order_event_count") or 0
        ),
        "legacy_orphan_order_event_count": int(
            (broker_reconciliation or {}).get("legacy_orphan_order_event_count") or 0
        ),
        "directional_fill_threshold": _CURRENT_ROUTE_MIN_DIRECTIONAL_FILLS,
        "closed_trade_threshold": _CURRENT_ROUTE_MIN_CLOSED_TRADES,
    }


def build_current_route_benchmark_report(
    ledger: pd.DataFrame,
    snapshots: pd.DataFrame,
    *,
    starting_capital: float,
) -> dict[str, Any]:
    current_route_ledger = _filter_current_route_ledger(ledger)
    if "equity_after_fill" in current_route_ledger.columns:
        metrics = compute_ledger_metrics(current_route_ledger, starting_capital=starting_capital)
    else:
        closes = current_route_ledger[current_route_ledger.get("event_type", pd.Series(dtype=str)).astype(str) == "close"].copy()
        trade_metrics = _build_window_trade_metrics(closes, starting_equity=starting_capital)
        metrics = {
            "trade_count": int(trade_metrics.get("trade_count") or 0),
            "closed_trade_count": int(trade_metrics.get("trade_count") or 0),
            "ending_equity": float(trade_metrics.get("ending_equity") or starting_capital),
            "return_pct": float(trade_metrics.get("return_pct") or 0.0),
        }
    benchmark_report = build_benchmark_report(
        current_route_ledger,
        snapshots,
        starting_capital=starting_capital,
        strategy_ending_equity=float(metrics.get("ending_equity") or starting_capital),
    )
    return {
        "trade_count": int(metrics.get("trade_count") or 0),
        "closed_trade_count": int(metrics.get("closed_trade_count") or 0),
        **benchmark_report,
    }


def build_benchmark_report(
    ledger: pd.DataFrame,
    snapshots: pd.DataFrame,
    *,
    starting_capital: float,
    strategy_ending_equity: float,
) -> dict[str, Any]:
    if ledger.empty:
        return {
            "coverage_start": None,
            "coverage_end": None,
            "benchmark_interval": None,
            "benchmarks": [],
        }

    coverage_start = _coverage_start_timestamp(ledger, snapshots)
    coverage_end = _coverage_end_timestamp(ledger, snapshots)
    if coverage_start is None or coverage_end is None:
        return {
            "coverage_start": None,
            "coverage_end": None,
            "benchmark_interval": None,
            "benchmarks": [],
        }

    benchmark_interval = _benchmark_interval_for_span(coverage_start, coverage_end)
    period = _period_for_span(coverage_start, coverage_end)
    symbols = sorted({str(value).strip().upper() for value in ledger.get("symbol", pd.Series(dtype=str)).astype(str).tolist() if str(value).strip()})
    benchmark_symbols = ["SPY", "QQQ"]
    results: list[dict[str, Any]] = []

    for symbol in benchmark_symbols:
        history = _download_history(symbol, period=period, interval=benchmark_interval)
        start_price = _extract_price_at_or_before(history, coverage_start)
        end_price = _extract_price_at_or_before(history, coverage_end)
        if start_price is None or end_price is None or start_price <= 0:
            continue
        return_pct = ((end_price - start_price) / start_price) * 100.0
        results.append(
            {
                "key": symbol.lower(),
                "label": symbol,
                "start_price": round(float(start_price), 4),
                "end_price": round(float(end_price), 4),
                "return_pct": round(float(return_pct), 4),
                "ending_equity": round(float(starting_capital * (1.0 + return_pct / 100.0)), 4),
            }
        )

    basket_returns: list[float] = []
    basket_components: list[dict[str, Any]] = []
    for symbol in symbols:
        history = _download_history(symbol, period=period, interval=benchmark_interval)
        start_price = _extract_price_at_or_before(history, coverage_start)
        end_price = _extract_price_at_or_before(history, coverage_end)
        if start_price is None or end_price is None or start_price <= 0:
            continue
        return_pct = ((end_price - start_price) / start_price) * 100.0
        basket_returns.append(float(return_pct))
        basket_components.append(
            {
                "symbol": symbol,
                "start_price": round(float(start_price), 4),
                "end_price": round(float(end_price), 4),
                "return_pct": round(float(return_pct), 4),
            }
        )
    if basket_returns:
        equal_weight_return_pct = float(pd.Series(basket_returns, dtype="float64").mean())
        results.append(
            {
                "key": "equal_weight_traded_basket",
                "label": "Equal-weight traded basket",
                "component_count": len(basket_components),
                "return_pct": round(equal_weight_return_pct, 4),
                "ending_equity": round(float(starting_capital * (1.0 + equal_weight_return_pct / 100.0)), 4),
                "components": _safe_json(basket_components),
            }
        )

    strategy_return_pct = ((strategy_ending_equity - starting_capital) / starting_capital * 100.0) if starting_capital > 0 else 0.0
    best_benchmark = max(results, key=lambda item: _coerce_float(item.get("return_pct"), float("-inf"))) if results else None
    return {
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
        "benchmark_interval": benchmark_interval,
        "strategy_return_pct": round(float(strategy_return_pct), 4),
        "strategy_ending_equity": round(float(strategy_ending_equity), 4),
        "benchmarks": _safe_json(results),
        "best_benchmark": _safe_json(best_benchmark) if best_benchmark is not None else None,
    }


def _month_floor_utc(value: pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return pd.Timestamp(year=timestamp.year, month=timestamp.month, day=1, tz="UTC")


def _months_spanned_inclusive(start_at: pd.Timestamp, end_at: pd.Timestamp) -> int:
    start_month = _month_floor_utc(start_at)
    end_month = _month_floor_utc(end_at)
    return ((end_month.year - start_month.year) * 12) + (end_month.month - start_month.month) + 1


def _build_window_trade_metrics(closes: pd.DataFrame, *, starting_equity: float) -> dict[str, Any]:
    if closes.empty:
        return {
            "trade_count": 0,
            "starting_equity": round(float(starting_equity), 4),
            "ending_equity": round(float(starting_equity), 4),
            "return_pct": 0.0,
            "profit_factor": None,
            "profit_factor_status": "no_trades",
            "max_drawdown_pct": 0.0,
            "average_trade_profit": 0.0,
            "win_rate": None,
        }

    realized = pd.to_numeric(closes.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    gross_profit = float(realized[realized > 0].sum()) if not realized.empty else 0.0
    gross_loss = float(abs(realized[realized < 0].sum())) if not realized.empty else 0.0
    if gross_loss > 0:
        profit_factor: float | None = gross_profit / gross_loss
        profit_factor_status = "measured"
    elif gross_profit > 0:
        profit_factor = None
        profit_factor_status = "no_losses_yet"
    else:
        profit_factor = None
        profit_factor_status = "flat_or_losing"
    equity_curve = realized.cumsum() + float(starting_equity)
    ending_equity = float(equity_curve.iloc[-1]) if not equity_curve.empty else float(starting_equity)
    return_pct = ((ending_equity - float(starting_equity)) / float(starting_equity) * 100.0) if starting_equity > 0 else 0.0
    return {
        "trade_count": int(len(realized.index)),
        "starting_equity": round(float(starting_equity), 4),
        "ending_equity": round(float(ending_equity), 4),
        "return_pct": round(float(return_pct), 4),
        "profit_factor": round(float(profit_factor), 4) if profit_factor is not None else None,
        "profit_factor_status": profit_factor_status,
        "max_drawdown_pct": round(float(_max_drawdown(pd.concat([pd.Series([float(starting_equity)]), equity_curve], ignore_index=True))), 4),
        "average_trade_profit": round(float(realized.mean()), 4),
        "win_rate": round(float((realized > 0).mean()), 6),
    }


def build_walk_forward_report(
    ledger: pd.DataFrame,
    *,
    starting_capital: float,
    train_months: int = 6,
    test_months: int = 1,
) -> dict[str, Any]:
    closes = ledger[ledger["event_type"] == "close"].copy() if not ledger.empty else pd.DataFrame()
    required_coverage_months = train_months + test_months
    empty_response = {
        "status": "insufficient_coverage",
        "train_months": train_months,
        "test_months": test_months,
        "required_coverage_months": required_coverage_months,
        "coverage_start": None,
        "coverage_end": None,
        "coverage_months_available": 0,
        "window_count": 0,
        "stitched_test_metrics": _build_window_trade_metrics(pd.DataFrame(), starting_equity=starting_capital),
        "windows": [],
    }
    if closes.empty or "timestamp" not in closes.columns:
        return empty_response

    closes["timestamp_ts"] = pd.to_datetime(closes["timestamp"], errors="coerce", utc=True)
    closes["realized_pnl"] = pd.to_numeric(closes.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    closes = closes[closes["timestamp_ts"].notna()].sort_values("timestamp_ts").reset_index(drop=True)
    if closes.empty:
        return empty_response

    coverage_start = pd.Timestamp(closes["timestamp_ts"].min())
    coverage_end = pd.Timestamp(closes["timestamp_ts"].max())
    coverage_months_available = _months_spanned_inclusive(coverage_start, coverage_end)
    if coverage_months_available < required_coverage_months:
        return {
            **empty_response,
            "coverage_start": coverage_start.isoformat(),
            "coverage_end": coverage_end.isoformat(),
            "coverage_months_available": coverage_months_available,
        }

    coverage_start_month = _month_floor_utc(coverage_start)
    coverage_end_month = _month_floor_utc(coverage_end)
    first_test_start = coverage_start_month + pd.DateOffset(months=train_months)
    windows: list[dict[str, Any]] = []
    stitched_test_frames: list[pd.DataFrame] = []
    stitched_test_start_equity: float | None = None
    current_test_start = first_test_start

    while current_test_start <= coverage_end_month:
        train_start = current_test_start - pd.DateOffset(months=train_months)
        test_end = current_test_start + pd.DateOffset(months=test_months)
        training_frame = closes[(closes["timestamp_ts"] >= train_start) & (closes["timestamp_ts"] < current_test_start)].copy()
        test_frame = closes[(closes["timestamp_ts"] >= current_test_start) & (closes["timestamp_ts"] < test_end)].copy()
        training_start_equity = float(starting_capital + closes.loc[closes["timestamp_ts"] < train_start, "realized_pnl"].sum())
        test_start_equity = float(starting_capital + closes.loc[closes["timestamp_ts"] < current_test_start, "realized_pnl"].sum())
        if stitched_test_start_equity is None:
            stitched_test_start_equity = test_start_equity
        if not test_frame.empty:
            stitched_test_frames.append(test_frame)
        windows.append(
            {
                "train_start": pd.Timestamp(train_start).isoformat(),
                "train_end": (pd.Timestamp(current_test_start) - pd.Timedelta(microseconds=1)).isoformat(),
                "test_start": pd.Timestamp(current_test_start).isoformat(),
                "test_end": (pd.Timestamp(test_end) - pd.Timedelta(microseconds=1)).isoformat(),
                "training_metrics": _build_window_trade_metrics(training_frame, starting_equity=training_start_equity),
                "test_metrics": _build_window_trade_metrics(test_frame, starting_equity=test_start_equity),
            }
        )
        current_test_start = test_end

    stitched_test_frame = pd.concat(stitched_test_frames, ignore_index=True) if stitched_test_frames else pd.DataFrame()
    stitched_metrics = _build_window_trade_metrics(
        stitched_test_frame,
        starting_equity=stitched_test_start_equity if stitched_test_start_equity is not None else starting_capital,
    )
    return {
        "status": "ok" if windows else "no_windows",
        "train_months": train_months,
        "test_months": test_months,
        "required_coverage_months": required_coverage_months,
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
        "coverage_months_available": coverage_months_available,
        "window_count": len(windows),
        "stitched_test_metrics": stitched_metrics,
        "windows": _safe_json(windows),
    }


def build_kill_switch_report(
    *,
    baseline: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    current_settings = dict(baseline.get("current_settings") or {})
    starting_capital = _coerce_float(baseline.get("starting_capital"), 0.0)
    max_leverage = dict(current_settings.get("max_leverage") or {})

    recommended_thresholds = {
        "max_gross_exposure_multiple": 1.0,
        "risk_per_trade_pct_range": {"min": 0.25, "max": 0.5},
        "max_dollar_risk_per_trade_range": {"min": round(float(starting_capital * 0.0025), 2), "max": round(float(starting_capital * 0.005), 2)},
        "hard_max_loss_per_trade_dollars": round(float(starting_capital * 0.01), 2),
        "max_single_position_range": {"min": round(float(starting_capital * 0.05), 2), "max": round(float(starting_capital * 0.10), 2)},
        "max_open_positions": 5,
        "max_correlated_bucket_range": {"min": round(float(starting_capital * 0.20), 2), "max": round(float(starting_capital * 0.30), 2)},
        "daily_stop_loss_dollars": round(float(starting_capital * 0.02), 2),
        "weekly_stop_loss_dollars": round(float(starting_capital * 0.05), 2),
        "cut_size_equity": round(float(starting_capital * 0.95), 2),
        "stop_bot_equity": round(float(starting_capital * 0.90), 2),
        "full_audit_equity": round(float(starting_capital * 0.85), 2),
    }

    configured_thresholds = {
        "risk_percent": _coerce_float(current_settings.get("risk_percent"), 0.0),
        "gross_cap_multiple": _coerce_float(max_leverage.get("gross_cap_multiple"), 0.0),
        "max_total_open_notional": _coerce_float(max_leverage.get("max_total_open_notional"), 0.0),
        "max_notional_per_trade": _coerce_float(max_leverage.get("max_notional_per_trade"), 0.0),
        "max_open_positions": int(current_settings.get("max_open_positions") or 0),
        "daily_stop_dollars": _coerce_float(current_settings.get("daily_stop_dollars"), 0.0) if current_settings.get("daily_stop_dollars") is not None else None,
        "weekly_stop_dollars": _coerce_float(current_settings.get("weekly_stop_dollars"), 0.0) if current_settings.get("weekly_stop_dollars") is not None else None,
        "cut_size_equity": _coerce_float(current_settings.get("cut_size_equity"), 0.0) if current_settings.get("cut_size_equity") is not None else None,
        "stop_bot_equity": _coerce_float(current_settings.get("stop_bot_equity"), 0.0) if current_settings.get("stop_bot_equity") is not None else None,
        "full_audit_equity": _coerce_float(current_settings.get("full_audit_equity"), 0.0) if current_settings.get("full_audit_equity") is not None else None,
    }

    checks = {
        "risk_per_trade_within_target_range": recommended_thresholds["risk_per_trade_pct_range"]["min"] <= configured_thresholds["risk_percent"] <= recommended_thresholds["risk_per_trade_pct_range"]["max"],
        "gross_exposure_within_initial_cap": configured_thresholds["gross_cap_multiple"] <= recommended_thresholds["max_gross_exposure_multiple"],
        "max_open_positions_within_target": configured_thresholds["max_open_positions"] <= recommended_thresholds["max_open_positions"],
        "daily_stop_configured": configured_thresholds["daily_stop_dollars"] is not None,
        "weekly_stop_configured": configured_thresholds["weekly_stop_dollars"] is not None,
        "cut_size_threshold_configured": configured_thresholds["cut_size_equity"] is not None,
        "stop_bot_threshold_configured": configured_thresholds["stop_bot_equity"] is not None,
        "full_audit_threshold_configured": configured_thresholds["full_audit_equity"] is not None,
    }

    observed = {
        "ending_equity": _coerce_float(metrics.get("ending_equity"), starting_capital),
        "gross_exposure_peak": _coerce_float(metrics.get("gross_exposure_peak"), 0.0),
        "worst_daily_pnl_dollars": _coerce_float(metrics.get("daily_loss_worst"), 0.0),
        "worst_weekly_pnl_dollars": _coerce_float(metrics.get("weekly_loss_worst"), 0.0),
    }

    required_live_switches_present = all(
        checks[key]
        for key in (
            "daily_stop_configured",
            "weekly_stop_configured",
            "cut_size_threshold_configured",
            "stop_bot_threshold_configured",
            "full_audit_threshold_configured",
        )
    )
    base_caps_present = (
        checks["risk_per_trade_within_target_range"]
        and checks["gross_exposure_within_initial_cap"]
        and checks["max_open_positions_within_target"]
    )
    if base_caps_present and required_live_switches_present:
        status = "pass"
        summary = "Kill-switch thresholds are explicitly configured and aligned with the restricted rollout profile."
        detail = "The current settings now include daily, weekly, and equity-cut guardrails in addition to the base position-sizing caps."
    elif base_caps_present:
        status = "partial"
        summary = "Base caps exist, but the full live-style kill-switch stack is still incomplete."
        detail = "Per-trade sizing, gross exposure, and open-position caps are present, but daily, weekly, or equity-cut checkpoints are still missing from the exported settings."
    else:
        status = "fail"
        summary = "The current settings do not yet meet the restricted guardrail profile."
        detail = "Either the base position-sizing caps are outside the initial target range or the exported settings are missing the necessary rollout guardrails."

    return {
        "status": status,
        "summary": summary,
        "detail": detail,
        "recommended_thresholds": recommended_thresholds,
        "configured_thresholds": _safe_json(configured_thresholds),
        "checks": checks,
        "observed": observed,
    }


def run_trade_order_monte_carlo(
    ledger: pd.DataFrame,
    *,
    starting_capital: float,
    runs: int = 1000,
) -> dict[str, Any]:
    closes = ledger[ledger["event_type"] == "close"].copy()
    realized = pd.to_numeric(closes.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    if realized.empty:
        return {
            "runs": runs,
            "trade_count": 0,
            "median_ending_equity": starting_capital,
            "worst_ending_equity": starting_capital,
            "worst_drawdown_pct": 0.0,
            "probability_10pct_drawdown": 0.0,
            "probability_20pct_drawdown": 0.0,
            "probability_50pct_drawdown": 0.0,
            "probability_of_ruin": 0.0,
        }

    rng = pd.Series(range(runs))
    endings: list[float] = []
    max_drawdowns: list[float] = []
    pnl_values = realized.tolist()
    for seed in rng.tolist():
        shuffled = pd.Series(pnl_values).sample(frac=1.0, replace=False, random_state=int(seed)).reset_index(drop=True)
        equity = shuffled.cumsum() + float(starting_capital)
        endings.append(float(equity.iloc[-1]))
        max_drawdowns.append(_max_drawdown(equity))

    endings_series = pd.Series(endings, dtype="float64")
    drawdown_series = pd.Series(max_drawdowns, dtype="float64")
    return {
        "runs": runs,
        "trade_count": int(len(realized)),
        "median_ending_equity": round(float(endings_series.median()), 4),
        "worst_ending_equity": round(float(endings_series.min()), 4),
        "worst_drawdown_pct": round(float(drawdown_series.max()), 4),
        "probability_10pct_drawdown": round(float((drawdown_series >= 10.0).mean()), 6),
        "probability_20pct_drawdown": round(float((drawdown_series >= 20.0).mean()), 6),
        "probability_50pct_drawdown": round(float((drawdown_series >= 50.0).mean()), 6),
        "probability_of_ruin": round(float((endings_series <= 0.0).mean()), 6),
    }


def _variant_entry(
    *,
    key: str,
    label: str,
    overrides: dict[str, Any],
    notes: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "overrides": overrides,
        "notes": notes,
    }


def build_test_matrix(current_settings: dict[str, Any]) -> list[dict[str, Any]]:
    current_leverage = _coerce_float(current_settings.get("max_leverage", {}).get("gross_cap_multiple"), 0.0)
    current_risk = _coerce_float(current_settings.get("risk_percent"), 0.25)
    correlation_bucket_cap_pct = 25.0
    return [
        _variant_entry(key="A", label="Current settings", overrides={}, notes="Baseline v0 settings."),
        _variant_entry(key="B", label="1x max gross exposure", overrides={"gross_exposure_cap": 1.0}, notes=f"Current cap {current_leverage:.2f}x."),
        _variant_entry(key="C", label="2x max gross exposure", overrides={"gross_exposure_cap": 2.0}, notes=f"Current cap {current_leverage:.2f}x."),
        _variant_entry(key="D", label="3x max gross exposure", overrides={"gross_exposure_cap": 3.0}, notes=f"Current cap {current_leverage:.2f}x."),
        _variant_entry(key="E", label="Current settings with 2x slippage", overrides={"slippage_multiplier": 2.0}, notes="Doubles recorded slippage or modeled fallback."),
        _variant_entry(key="F", label="Current settings with 3x slippage", overrides={"slippage_multiplier": 3.0}, notes="Triples recorded slippage or modeled fallback."),
        _variant_entry(key="G", label="Next-bar execution only", overrides={"use_next_bar_replay": True, "execution_penalty_bps": 10.0}, notes="Uses replayed next-bar open when available and falls back to a fixed penalty when history is missing."),
        _variant_entry(key="H", label="No averaging down", overrides={"allow_average_down": False}, notes="Skip adds that worsen basis on same symbol."),
        _variant_entry(key="I", label="No pyramiding", overrides={"allow_pyramiding": False}, notes="Skip multiple concurrent adds on same symbol."),
        _variant_entry(key="J", label="0.50% max risk per trade", overrides={"risk_percent_cap": 0.50}, notes=f"Current per-trade risk {current_risk:.2f}%."),
        _variant_entry(key="K", label="0.25% max risk per trade", overrides={"risk_percent_cap": 0.25}, notes=f"Current per-trade risk {current_risk:.2f}%."),
        _variant_entry(
            key="L",
            label="25% proxy correlation bucket cap",
            overrides={"correlation_bucket_cap_pct_equity": correlation_bucket_cap_pct},
            notes=(
                "Caps overlapping exposure inside the same proxy correlation bucket at 25% of realized equity "
                "using recorded entry and exit timestamps."
            ),
        ),
        _variant_entry(
            key="M",
            label="Ranked-entry stack (1.5x gross cap)",
            overrides={"gross_exposure_cap": 1.5, "candidate_rank_limit": 2},
            notes="Tests the wider gross cap together with a top-two ranked-entry throttle.",
        ),
        _variant_entry(
            key="N",
            label="35% proxy correlation bucket cap",
            overrides={"correlation_bucket_cap_pct_equity": 35.0},
            notes="Tests the wider production bucket cap for overlapping proxy exposure.",
        ),
        _variant_entry(
            key="O",
            label="Winner-only pyramiding",
            overrides={"allow_pyramiding": True, "winner_only_pyramiding": True},
            notes="Allows only one additional add on a symbol and only when the active leg is already profitable.",
        ),
        _variant_entry(
            key="P",
            label="2.5x edge-to-cost floor",
            overrides={"min_edge_to_cost_ratio": 2.5},
            notes="Drops trades that do not clear the tighter expected-edge-to-cost hurdle.",
        ),
    ]


def _simulate_variant(
    closes: pd.DataFrame,
    *,
    starting_capital: float,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    if closes.empty:
        return compute_ledger_metrics(pd.DataFrame(), starting_capital=starting_capital)

    frame = closes.copy()
    frame["symbol"] = frame.get("symbol", pd.Series(dtype=str)).astype(str)
    frame["position_cost"] = pd.to_numeric(frame.get("position_cost", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    frame["realized_pnl"] = pd.to_numeric(frame.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    frame["max_risk_dollars"] = pd.to_numeric(frame.get("max_risk_dollars", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    frame["fill_slippage_dollars"] = pd.to_numeric(frame.get("slippage", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    frame["entry_ts"] = frame.apply(
        lambda row: (
            _coerce_timestamp(row.get("signal_timestamp"))
            or _coerce_timestamp(row.get("order_timestamp"))
            or _coerce_timestamp(row.get("fill_timestamp"))
            or _coerce_timestamp(row.get("timestamp"))
        ),
        axis=1,
    )
    frame["exit_ts"] = frame.apply(
        lambda row: (
            _coerce_timestamp(row.get("timestamp"))
            or _coerce_timestamp(row.get("fill_timestamp"))
            or _coerce_timestamp(row.get("order_timestamp"))
            or _coerce_timestamp(row.get("signal_timestamp"))
        ),
        axis=1,
    )
    frame["proxy_correlation_bucket"] = frame["symbol"].apply(_proxy_correlation_bucket)
    frame = frame.sort_values(["entry_ts", "exit_ts"], ascending=[True, True], na_position="last").reset_index(drop=True)

    accepted_positions: list[dict[str, Any]] = []
    active_positions: list[dict[str, Any]] = []
    equity_for_limits = float(starting_capital)
    for _, row in frame.iterrows():
        candidate_rank_limit = int(_coerce_float(overrides.get("candidate_rank_limit"), 0.0) or 0.0)
        if candidate_rank_limit > 0:
            candidate_rank = _coerce_float(
                row.get("portfolio_rank", row.get("ranking_rank", row.get("board_rank"))),
                0.0,
            )
            if candidate_rank > 0 and candidate_rank > candidate_rank_limit:
                continue
        min_edge_to_cost_ratio = _coerce_float(overrides.get("min_edge_to_cost_ratio"), 0.0)
        if min_edge_to_cost_ratio > 0:
            realized_edge_ratio = _coerce_float(row.get("edge_to_cost_ratio"), 0.0)
            if realized_edge_ratio > 0 and realized_edge_ratio < min_edge_to_cost_ratio:
                continue

        entry_ts = row.get("entry_ts")
        exit_ts = row.get("exit_ts")
        if pd.isna(entry_ts):
            entry_ts = exit_ts
        if pd.isna(exit_ts):
            exit_ts = entry_ts

        remaining_active_positions: list[dict[str, Any]] = []
        for active_position in active_positions:
            active_exit_ts = active_position.get("exit_ts")
            if pd.notna(entry_ts) and pd.notna(active_exit_ts) and active_exit_ts <= entry_ts:
                equity_for_limits += _coerce_float(active_position.get("pnl_after_cost"), 0.0)
                continue
            remaining_active_positions.append(active_position)
        active_positions = remaining_active_positions

        scale = 1.0
        gross_cap = overrides.get("gross_exposure_cap")
        if gross_cap:
            allowed_notional = float(gross_cap) * max(equity_for_limits, 1.0)
            if row["position_cost"] > allowed_notional and row["position_cost"] > 0:
                scale = min(scale, allowed_notional / row["position_cost"])
        risk_cap = overrides.get("risk_percent_cap")
        if risk_cap and row["max_risk_dollars"] > 0:
            allowed_risk = float(risk_cap) / 100.0 * max(equity_for_limits, 1.0)
            if row["max_risk_dollars"] > allowed_risk:
                scale = min(scale, allowed_risk / row["max_risk_dollars"])

        symbol = str(row.get("symbol") or "")
        if overrides.get("allow_pyramiding") is False and any(active_position.get("symbol") == symbol for active_position in active_positions):
            scale = 0.0
        elif overrides.get("winner_only_pyramiding") and any(active_position.get("symbol") == symbol for active_position in active_positions):
            if any(
                _coerce_float(active_position.get("pnl_after_cost"), 0.0) <= 0
                for active_position in active_positions
                if active_position.get("symbol") == symbol
            ):
                scale = 0.0
        correlation_bucket_cap_pct = _coerce_float(overrides.get("correlation_bucket_cap_pct_equity"), 0.0)
        if correlation_bucket_cap_pct > 0 and row["position_cost"] > 0:
            bucket = str(row.get("proxy_correlation_bucket") or _proxy_correlation_bucket(symbol))
            active_bucket_notional = sum(
                _coerce_float(active_position.get("notional"), 0.0)
                for active_position in active_positions
                if str(active_position.get("bucket") or "") == bucket
            )
            allowed_bucket_notional = correlation_bucket_cap_pct / 100.0 * max(equity_for_limits, 1.0)
            remaining_bucket_notional = max(allowed_bucket_notional - active_bucket_notional, 0.0)
            if remaining_bucket_notional <= 0:
                scale = 0.0
            else:
                scale = min(scale, remaining_bucket_notional / row["position_cost"])
        if scale <= 0:
            continue

        slippage_multiplier = _coerce_float(overrides.get("slippage_multiplier"), 1.0)
        base_slippage = abs(_coerce_float(row.get("slippage"), 0.0))
        if base_slippage == 0.0 and row["position_cost"] > 0:
            base_slippage = row["position_cost"] * 0.0005
        scaled_position_cost = row["position_cost"] * scale
        execution_penalty_bps = _coerce_float(overrides.get("execution_penalty_bps"), 0.0)
        penalty = (scaled_position_cost * execution_penalty_bps / 10000.0) if execution_penalty_bps else 0.0
        replay_adjustment = 0.0
        if overrides.get("use_next_bar_replay"):
            replay_adjustment = _coerce_float(row.get("replay_entry_penalty_dollars"), 0.0) * scale

        scaled_realized = row["realized_pnl"] * scale
        scaled_cost = base_slippage * scale * slippage_multiplier + penalty
        pnl_after_cost = scaled_realized - scaled_cost - replay_adjustment
        accepted_positions.append(
            {
                "timestamp_ts": exit_ts,
                "timestamp": exit_ts.isoformat() if pd.notna(exit_ts) else row.get("timestamp"),
                "symbol": symbol,
                "realized_pnl": pnl_after_cost,
                "slippage": scaled_cost,
                "gross_exposure": scaled_position_cost,
                "pnl_after_cost": pnl_after_cost,
            }
        )
        active_positions.append(
            {
                "symbol": symbol,
                "bucket": str(row.get("proxy_correlation_bucket") or _proxy_correlation_bucket(symbol)),
                "notional": scaled_position_cost,
                "exit_ts": exit_ts,
                "pnl_after_cost": pnl_after_cost,
            }
        )

    if not accepted_positions:
        return compute_ledger_metrics(pd.DataFrame(), starting_capital=starting_capital)

    accepted_positions.sort(
        key=lambda item: (
            item.get("timestamp_ts") if pd.notna(item.get("timestamp_ts")) else pd.Timestamp.max.tz_localize("UTC"),
            str(item.get("symbol") or ""),
        )
    )
    equity = float(starting_capital)
    simulated_events: list[dict[str, Any]] = []
    for accepted_position in accepted_positions:
        equity += _coerce_float(accepted_position.get("pnl_after_cost"), 0.0)
        simulated_events.append(
            {
                "timestamp": accepted_position.get("timestamp"),
                "event_type": "close",
                "symbol": accepted_position.get("symbol"),
                "realized_pnl": accepted_position.get("realized_pnl"),
                "fees": 0.0,
                "slippage": accepted_position.get("slippage"),
                "equity_after_fill": equity,
                "gross_exposure": accepted_position.get("gross_exposure"),
            }
        )

    return compute_ledger_metrics(pd.DataFrame(simulated_events), starting_capital=starting_capital)


def evaluate_ranked_entry_rollout_acceptance(
    stress_matrix: list[dict[str, Any]],
    *,
    starting_capital: float,
    baseline_key: str = "A",
    candidate_key: str = "M",
) -> dict[str, Any]:
    matrix_by_key = {
        str(item.get("key") or "").strip().upper(): item
        for item in list(stress_matrix or [])
        if str(item.get("key") or "").strip()
    }
    baseline = matrix_by_key.get(str(baseline_key or "").strip().upper())
    candidate = matrix_by_key.get(str(candidate_key or "").strip().upper())
    if baseline is None or candidate is None:
        return {
            "accepted": False,
            "status": "missing",
            "basis": "The required baseline or candidate stress-matrix row is missing.",
            "baseline_key": baseline_key,
            "candidate_key": candidate_key,
        }

    baseline_metrics = dict(baseline.get("metrics") or {})
    candidate_metrics = dict(candidate.get("metrics") or {})
    baseline_ending_equity = _coerce_float(baseline_metrics.get("ending_equity"), float(starting_capital))
    candidate_ending_equity = _coerce_float(candidate_metrics.get("ending_equity"), float(starting_capital))
    baseline_expectancy = _coerce_float(baseline_metrics.get("average_trade_profit"), 0.0)
    candidate_expectancy = _coerce_float(candidate_metrics.get("average_trade_profit"), 0.0)
    baseline_drawdown = _coerce_float(baseline_metrics.get("max_drawdown_pct"), 0.0)
    candidate_drawdown = _coerce_float(candidate_metrics.get("max_drawdown_pct"), 0.0)
    candidate_gross_peak = _coerce_float(candidate_metrics.get("gross_exposure_peak"), 0.0)
    gross_cap_dollars = float(starting_capital) * 1.5

    improves_return_or_expectancy = (
        candidate_ending_equity > baseline_ending_equity
        or candidate_expectancy > baseline_expectancy
    )
    drawdown_limit = baseline_drawdown * 1.15 if baseline_drawdown > 0 else 0.0
    drawdown_ok = candidate_drawdown <= drawdown_limit
    gross_ok = candidate_gross_peak <= gross_cap_dollars
    accepted = bool(improves_return_or_expectancy and drawdown_ok and gross_ok)
    status = "accepted" if accepted else "rejected"
    basis_parts = []
    if not improves_return_or_expectancy:
        basis_parts.append("Candidate does not improve baseline ending equity or average trade profit.")
    if not drawdown_ok:
        basis_parts.append(
            f"Candidate drawdown {candidate_drawdown:.2f}% exceeds the allowed {drawdown_limit:.2f}% ceiling."
        )
    if not gross_ok:
        basis_parts.append(
            f"Candidate gross exposure peak ${candidate_gross_peak:,.2f} exceeds the 1.5x cap of ${gross_cap_dollars:,.2f}."
        )
    if not basis_parts:
        basis_parts.append("Candidate improves the baseline without breaching drawdown or gross-exposure limits.")
    return {
        "accepted": accepted,
        "status": status,
        "basis": " ".join(basis_parts),
        "baseline_key": str(baseline.get("key") or baseline_key),
        "candidate_key": str(candidate.get("key") or candidate_key),
        "baseline": {
            "ending_equity": round(baseline_ending_equity, 4),
            "average_trade_profit": round(baseline_expectancy, 4),
            "max_drawdown_pct": round(baseline_drawdown, 4),
        },
        "candidate": {
            "ending_equity": round(candidate_ending_equity, 4),
            "average_trade_profit": round(candidate_expectancy, 4),
            "max_drawdown_pct": round(candidate_drawdown, 4),
            "gross_exposure_peak": round(candidate_gross_peak, 4),
        },
        "drawdown_limit_pct": round(drawdown_limit, 4),
        "gross_cap_dollars": round(gross_cap_dollars, 4),
    }


def _prediction_source_family(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "alpaca" in text:
        return "alpaca"
    if "polygon" in text:
        return "polygon"
    if "yfinance" in text:
        return "yfinance"
    if "fallback" in text:
        return "fallback"
    return text or "unknown"


def _prediction_runtime_status() -> dict[str, Any]:
    adapter = str(getattr(sdm.settings, "market_data_adapter", "") or "").strip().lower()
    missing_requirements: list[str] = []
    if adapter != "hybrid":
        missing_requirements.append("MARKET_DATA_ADAPTER=hybrid")
    if not str(getattr(sdm.settings, "alpaca_api_key_id", "") or "").strip():
        missing_requirements.append("ALPACA_API_KEY")
    if not str(getattr(sdm.settings, "alpaca_api_secret_key", "") or "").strip():
        missing_requirements.append("ALPACA_SECRET_KEY")
    if not str(getattr(sdm.settings, "polygon_api_key", "") or "").strip():
        missing_requirements.append("POLYGON_API_KEY")
    return {
        "adapter": adapter or "unknown",
        "ready": not missing_requirements,
        "missing_requirements": missing_requirements,
    }


def _prediction_validation_configuration(row: pd.Series) -> str:
    explicit_configuration = str(row.get("prediction_configuration") or "").strip().lower()
    if explicit_configuration in {"proxy_baseline", "hybrid_stock_only", "full_hybrid"}:
        return explicit_configuration
    market_state_source = _prediction_source_family(row.get("market_state_source"))
    relative_strength_source = _prediction_source_family(row.get("relative_strength_source"))
    options_flow_source = _prediction_source_family(row.get("options_flow_source"))
    event_revision_source = _prediction_source_family(row.get("event_revision_source"))
    degraded_prediction = str(row.get("degraded_prediction") or "").strip().lower() in {"1", "true", "yes"}
    stock_side_paid = market_state_source == "alpaca" and relative_strength_source == "alpaca"
    polygon_side_paid = options_flow_source == "polygon" and event_revision_source == "polygon"
    if stock_side_paid and polygon_side_paid and not degraded_prediction:
        return "full_hybrid"
    if stock_side_paid:
        return "hybrid_stock_only"
    return "proxy_baseline"


def _empty_prediction_validation_metrics() -> dict[str, Any]:
    return {
        "resolved_count": 0,
        "hit_rate": None,
        "directional_expectancy": None,
        "net_pnl": None,
        "brier_score": None,
        "log_loss": None,
        "max_drawdown_pct": None,
        "sample_status": "insufficient",
    }


def _prediction_validation_metrics(rows: pd.DataFrame, *, starting_capital: float) -> dict[str, Any]:
    if rows.empty:
        return _empty_prediction_validation_metrics()
    metrics_rows = rows.copy()
    metrics_rows["probability_up"] = pd.to_numeric(metrics_rows.get("probability_up"), errors="coerce")
    metrics_rows["actual_target_up"] = pd.to_numeric(metrics_rows.get("actual_target_up"), errors="coerce")
    metrics_rows["actual_return"] = pd.to_numeric(metrics_rows.get("actual_return"), errors="coerce")
    metrics_rows = metrics_rows.dropna(subset=["probability_up", "actual_target_up", "actual_return"])
    if metrics_rows.empty:
        return _empty_prediction_validation_metrics()

    direction = metrics_rows["probability_up"].apply(lambda value: 1.0 if float(value) >= 0.5 else -1.0)
    directional_return = metrics_rows["actual_return"] * direction
    predicted_up = metrics_rows["probability_up"] >= 0.5
    actual_up = metrics_rows["actual_target_up"] >= 0.5
    hit_rate = float((predicted_up == actual_up).mean())
    brier_score = float(((metrics_rows["probability_up"] - metrics_rows["actual_target_up"]) ** 2).mean())
    clipped_probabilities = metrics_rows["probability_up"].clip(lower=1e-6, upper=1 - 1e-6)
    log_loss = float(
        -(
            (metrics_rows["actual_target_up"] * clipped_probabilities.apply(math.log))
            + ((1.0 - metrics_rows["actual_target_up"]) * (1.0 - clipped_probabilities).apply(math.log))
        ).mean()
    )
    equity_curve = float(starting_capital) * (1.0 + directional_return.fillna(0.0)).cumprod()
    running_peak = equity_curve.cummax()
    max_drawdown_pct = float(
        (((equity_curve / running_peak.replace(0.0, float("nan"))) - 1.0).fillna(0.0).min()) * -100.0
    )
    resolved_count = int(len(metrics_rows))
    return {
        "resolved_count": resolved_count,
        "hit_rate": round(hit_rate, 4),
        "directional_expectancy": round(float(directional_return.mean()), 6),
        "net_pnl": round(float(directional_return.sum() * float(starting_capital)), 4),
        "brier_score": round(brier_score, 6),
        "log_loss": round(log_loss, 6),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "sample_status": "sufficient" if resolved_count >= _PREDICTION_VALIDATION_MIN_RESOLVED else "insufficient",
    }


def _prediction_calibration_bucket_report(rows: pd.DataFrame, column: str, label_key: str) -> list[dict[str, Any]]:
    if rows.empty or column not in rows.columns:
        return []
    report_rows = rows.copy()
    report_rows["probability_up"] = pd.to_numeric(report_rows.get("probability_up"), errors="coerce")
    report_rows["actual_target_up"] = pd.to_numeric(report_rows.get("actual_target_up"), errors="coerce")
    report_rows = report_rows.dropna(subset=["probability_up", "actual_target_up"])
    if report_rows.empty:
        return []
    report_rows[column] = report_rows[column].astype(str).str.strip()
    report_rows = report_rows[report_rows[column].ne("")]
    buckets: list[dict[str, Any]] = []
    for bucket_name, group in report_rows.groupby(column, sort=False):
        hit_rate = float(group["actual_target_up"].mean())
        average_probability_up = float(group["probability_up"].mean())
        brier_score = float(((group["probability_up"] - group["actual_target_up"]) ** 2).mean())
        buckets.append(
            {
                label_key: str(bucket_name),
                "resolved_count": int(len(group)),
                "empirical_hit_rate": round(hit_rate, 4),
                "average_probability_up": round(average_probability_up, 4),
                "edge": round(hit_rate - average_probability_up, 4),
                "brier_score": round(brier_score, 6),
            }
        )
    buckets.sort(key=lambda item: (-int(item.get("resolved_count") or 0), str(item.get(label_key) or "")))
    return buckets


def _prediction_driver_ablation_report(rows: pd.DataFrame) -> list[dict[str, Any]]:
    if rows.empty:
        return []
    driver_columns = {
        "market_state": "market_state_probability_shift",
        "relative_strength": "relative_strength_probability_shift",
        "options_flow": "options_flow_probability_shift",
        "event_revision": "event_revision_probability_shift",
    }
    ablation_rows: list[dict[str, Any]] = []
    for driver_name, column in driver_columns.items():
        if column not in rows.columns:
            continue
        active = rows.copy()
        active[column] = pd.to_numeric(active.get(column), errors="coerce")
        active["actual_target_up"] = pd.to_numeric(active.get("actual_target_up"), errors="coerce")
        active["actual_return"] = pd.to_numeric(active.get("actual_return"), errors="coerce")
        active = active.dropna(subset=[column, "actual_target_up", "actual_return"])
        active = active[active[column].abs() >= 0.0025]
        if active.empty:
            ablation_rows.append(
                {
                    "driver": driver_name,
                    "active_count": 0,
                    "mean_abs_shift": 0.0,
                    "mean_signed_shift": 0.0,
                    "helpful_rate": None,
                    "directional_expectancy": None,
                }
            )
            continue
        helpful = (
            ((active[column] >= 0) & (active["actual_target_up"] >= 0.5))
            | ((active[column] < 0) & (active["actual_target_up"] < 0.5))
        )
        direction = active[column].apply(lambda value: 1.0 if float(value) >= 0 else -1.0)
        ablation_rows.append(
            {
                "driver": driver_name,
                "active_count": int(len(active)),
                "mean_abs_shift": round(float(active[column].abs().mean()), 6),
                "mean_signed_shift": round(float(active[column].mean()), 6),
                "helpful_rate": round(float(helpful.mean()), 4),
                "directional_expectancy": round(float((active["actual_return"] * direction).mean()), 6),
            }
        )
    ablation_rows.sort(key=lambda item: (-int(item.get("active_count") or 0), str(item.get("driver") or "")))
    return ablation_rows


def _default_prediction_promotion_tier() -> str:
    return "hybrid_stock_only"


def _prediction_tier_label(configuration: str | None) -> str:
    normalized = str(configuration or "").strip() or _default_prediction_promotion_tier()
    return normalized


def build_intraday_prediction_validation_report(
    forecast_journal: pd.DataFrame,
    *,
    starting_capital: float,
) -> dict[str, Any]:
    runtime_status = _prediction_runtime_status()
    active_prediction_stack_version = str(getattr(sdm, "INTRADAY_PREDICTION_STACK_VERSION", "intraday_hybrid_v1"))
    default_promotion_tier = _default_prediction_promotion_tier()
    empty_metrics = {
        "proxy_baseline": _empty_prediction_validation_metrics(),
        "hybrid_stock_only": _empty_prediction_validation_metrics(),
        "full_hybrid": _empty_prediction_validation_metrics(),
    }
    if forecast_journal.empty:
        return {
            "status": "partial",
            "accepted": False,
            "label": f"{default_promotion_tier} validation collecting sample",
            "basis": "No resolved forecast-journal sample is available yet for intraday prediction validation.",
            "baseline_key": "proxy_baseline",
            "candidate_key": default_promotion_tier,
            "preferred_candidate_configuration": default_promotion_tier,
            "active_candidate_configuration": default_promotion_tier,
            "prediction_promotion_tier": default_promotion_tier,
            "minimum_resolved_rows": _PREDICTION_VALIDATION_MIN_RESOLVED,
            "active_prediction_stack_version": active_prediction_stack_version,
            "runtime_activation": runtime_status,
            "legacy_resolved_rows_excluded": 0,
            "paired_group_counts": {"hybrid_stock_only": 0, "full_hybrid": 0},
            "configurations": empty_metrics,
            "calibration_buckets": {},
            "driver_ablation": [],
        }

    rows = forecast_journal.copy()
    rows["probability_up"] = pd.to_numeric(rows.get("probability_up"), errors="coerce")
    rows["actual_target_up"] = pd.to_numeric(rows.get("actual_target_up"), errors="coerce")
    rows["actual_return"] = pd.to_numeric(rows.get("actual_return"), errors="coerce")
    rows["degraded_prediction"] = rows.get("degraded_prediction", False)
    if "prediction_stack_version" not in rows.columns:
        rows["prediction_stack_version"] = ""
    if "prediction_configuration" not in rows.columns:
        rows["prediction_configuration"] = ""
    if "forecast_group_id" not in rows.columns:
        rows["forecast_group_id"] = ""
    rows["prediction_stack_version"] = rows["prediction_stack_version"].fillna("").astype(str)
    rows["prediction_configuration"] = rows["prediction_configuration"].fillna("").astype(str)
    rows["forecast_group_id"] = rows["forecast_group_id"].fillna("").astype(str)
    rows["prediction_configuration"] = rows.apply(_prediction_validation_configuration, axis=1)
    resolved_all = rows.dropna(subset=["probability_up", "actual_target_up", "actual_return"]).copy()
    if resolved_all.empty:
        return {
            "status": "partial",
            "accepted": False,
            "label": f"{default_promotion_tier} validation collecting sample",
            "basis": "Forecast-journal rows exist, but none are resolved yet for intraday prediction validation.",
            "baseline_key": "proxy_baseline",
            "candidate_key": default_promotion_tier,
            "preferred_candidate_configuration": default_promotion_tier,
            "active_candidate_configuration": default_promotion_tier,
            "prediction_promotion_tier": default_promotion_tier,
            "minimum_resolved_rows": _PREDICTION_VALIDATION_MIN_RESOLVED,
            "active_prediction_stack_version": active_prediction_stack_version,
            "runtime_activation": runtime_status,
            "legacy_resolved_rows_excluded": 0,
            "paired_group_counts": {"hybrid_stock_only": 0, "full_hybrid": 0},
            "configurations": empty_metrics,
            "calibration_buckets": {},
            "driver_ablation": [],
        }

    active_version_rows = rows.loc[
        rows["prediction_stack_version"].astype(str).eq(active_prediction_stack_version)
        & rows["prediction_configuration"].astype(str).isin({"proxy_baseline", "hybrid_stock_only", "full_hybrid"})
    ].copy()
    full_hybrid_config_seen = bool(
        active_version_rows["prediction_configuration"].astype(str).eq("full_hybrid").any()
    )
    stock_only_config_seen = bool(
        active_version_rows["prediction_configuration"].astype(str).eq("hybrid_stock_only").any()
    )
    preferred_candidate_configuration = (
        "full_hybrid"
        if full_hybrid_config_seen
        else "hybrid_stock_only"
        if stock_only_config_seen
        else default_promotion_tier
    )
    active_rows = resolved_all.loc[
        resolved_all["prediction_stack_version"].astype(str).eq(active_prediction_stack_version)
        & resolved_all["prediction_configuration"].astype(str).isin({"proxy_baseline", "hybrid_stock_only", "full_hybrid"})
    ].copy()
    legacy_resolved_rows_excluded = int(len(resolved_all) - len(active_rows))
    if active_rows.empty:
        active_candidate_configuration = (
            "hybrid_stock_only"
            if stock_only_config_seen
            else preferred_candidate_configuration
        )
        missing_requirements = list(runtime_status.get("missing_requirements") or [])
        basis = (
            "Hybrid runtime activation is incomplete: " + ", ".join(missing_requirements) + "."
            if missing_requirements
            else (
                "Post-upgrade forecast-journal rows exist, but none are resolved yet for the active prediction-stack version."
                if not active_version_rows.empty
                else "No post-upgrade forecast-journal rows have been recorded for the active prediction-stack version yet."
            )
        )
        return {
            "status": "partial",
            "accepted": False,
            "label": f"{active_candidate_configuration} validation collecting sample",
            "basis": basis,
            "baseline_key": "proxy_baseline",
            "candidate_key": active_candidate_configuration,
            "preferred_candidate_configuration": preferred_candidate_configuration,
            "active_candidate_configuration": active_candidate_configuration,
            "prediction_promotion_tier": active_candidate_configuration,
            "minimum_resolved_rows": _PREDICTION_VALIDATION_MIN_RESOLVED,
            "active_prediction_stack_version": active_prediction_stack_version,
            "runtime_activation": runtime_status,
            "legacy_resolved_rows_excluded": legacy_resolved_rows_excluded,
            "active_resolved_rows": 0,
            "active_group_count": 0,
            "paired_group_counts": {"hybrid_stock_only": 0, "full_hybrid": 0},
            "configurations": empty_metrics,
            "calibration_buckets": {},
            "driver_ablation": [],
        }

    synthetic_group_mask = active_rows["forecast_group_id"].astype(str).str.strip().eq("")
    if synthetic_group_mask.any():
        active_rows.loc[synthetic_group_mask, "forecast_group_id"] = (
            active_rows.loc[synthetic_group_mask, "ticker"].astype(str)
            + "|"
            + active_rows.loc[synthetic_group_mask, "interval"].astype(str)
            + "|"
            + active_rows.loc[synthetic_group_mask, "forecast_at"].astype(str)
        )

    config_sets = active_rows.groupby("forecast_group_id")["prediction_configuration"].agg(
        lambda values: {str(value or "").strip().lower() for value in values}
    )
    full_hybrid_group_ids = config_sets[
        config_sets.apply(lambda value: "proxy_baseline" in value and "full_hybrid" in value)
    ].index
    stock_only_group_ids = config_sets[
        config_sets.apply(lambda value: "proxy_baseline" in value and "hybrid_stock_only" in value)
    ].index
    all_proxy_group_ids = full_hybrid_group_ids.union(stock_only_group_ids)
    all_proxy_rows = active_rows.loc[
        active_rows["forecast_group_id"].isin(all_proxy_group_ids)
        & active_rows["prediction_configuration"].astype(str).eq("proxy_baseline")
    ].copy()
    stock_only_proxy_rows = active_rows.loc[
        active_rows["forecast_group_id"].isin(stock_only_group_ids)
        & active_rows["prediction_configuration"].astype(str).eq("proxy_baseline")
    ].copy()
    full_hybrid_proxy_rows = active_rows.loc[
        active_rows["forecast_group_id"].isin(full_hybrid_group_ids)
        & active_rows["prediction_configuration"].astype(str).eq("proxy_baseline")
    ].copy()
    stock_only_rows = active_rows.loc[
        active_rows["forecast_group_id"].isin(stock_only_group_ids)
        & active_rows["prediction_configuration"].astype(str).eq("hybrid_stock_only")
    ].copy()
    full_hybrid_rows = active_rows.loc[
        active_rows["forecast_group_id"].isin(full_hybrid_group_ids)
        & active_rows["prediction_configuration"].astype(str).eq("full_hybrid")
    ].copy()

    configuration_reports = {
        "proxy_baseline": _prediction_validation_metrics(all_proxy_rows, starting_capital=starting_capital),
        "hybrid_stock_only": _prediction_validation_metrics(stock_only_rows, starting_capital=starting_capital),
        "full_hybrid": _prediction_validation_metrics(full_hybrid_rows, starting_capital=starting_capital),
    }
    comparison_baselines = {
        "hybrid_stock_only": _prediction_validation_metrics(stock_only_proxy_rows, starting_capital=starting_capital),
        "full_hybrid": _prediction_validation_metrics(full_hybrid_proxy_rows, starting_capital=starting_capital),
    }
    candidate_priority = ("full_hybrid", "hybrid_stock_only")
    active_candidate_configuration = (
        "hybrid_stock_only"
        if stock_only_config_seen or int(len(stock_only_group_ids)) > 0
        else preferred_candidate_configuration
    )
    for candidate_name in candidate_priority:
        candidate_metrics = configuration_reports[candidate_name]
        baseline_metrics = comparison_baselines[candidate_name]
        if (
            int(candidate_metrics.get("resolved_count") or 0) >= _PREDICTION_VALIDATION_MIN_RESOLVED
            and int(baseline_metrics.get("resolved_count") or 0) >= _PREDICTION_VALIDATION_MIN_RESOLVED
        ):
            active_candidate_configuration = candidate_name
            break
    baseline = comparison_baselines.get(active_candidate_configuration, _empty_prediction_validation_metrics())
    candidate = configuration_reports.get(active_candidate_configuration, _empty_prediction_validation_metrics())
    baseline_resolved = int(baseline.get("resolved_count") or 0)
    candidate_resolved = int(candidate.get("resolved_count") or 0)
    active_candidate_label = _prediction_tier_label(active_candidate_configuration)
    if not bool(runtime_status.get("ready")):
        status = "partial"
        accepted = False
        basis = "Hybrid runtime activation is incomplete: " + ", ".join(runtime_status.get("missing_requirements") or [])
    elif baseline_resolved < _PREDICTION_VALIDATION_MIN_RESOLVED or candidate_resolved < _PREDICTION_VALIDATION_MIN_RESOLVED:
        status = "partial"
        accepted = False
        insufficiency_basis = (
            f"The active forecast-journal window does not yet have enough paired proxy-baseline and {active_candidate_label} rows to score the promotion tier."
        )
        if active_candidate_configuration == "hybrid_stock_only":
            full_hybrid_note = (
                "full_hybrid unavailable; running no-pay tier. "
                if int(len(full_hybrid_group_ids)) <= 0 and not full_hybrid_config_seen
                else "full_hybrid unavailable for promotion; running no-pay tier. "
            )
            basis = full_hybrid_note + insufficiency_basis
        else:
            basis = insufficiency_basis
    else:
        baseline_expectancy = _coerce_float(baseline.get("directional_expectancy"), 0.0)
        candidate_expectancy = _coerce_float(candidate.get("directional_expectancy"), 0.0)
        baseline_net_pnl = _coerce_float(baseline.get("net_pnl"), 0.0)
        candidate_net_pnl = _coerce_float(candidate.get("net_pnl"), 0.0)
        baseline_brier = _coerce_float(baseline.get("brier_score"), float("inf"))
        candidate_brier = _coerce_float(candidate.get("brier_score"), float("inf"))
        baseline_log_loss = _coerce_float(baseline.get("log_loss"), float("inf"))
        candidate_log_loss = _coerce_float(candidate.get("log_loss"), float("inf"))
        baseline_drawdown = _coerce_float(baseline.get("max_drawdown_pct"), 0.0)
        candidate_drawdown = _coerce_float(candidate.get("max_drawdown_pct"), 0.0)
        improves_return_or_expectancy = candidate_net_pnl > baseline_net_pnl or candidate_expectancy > baseline_expectancy
        calibration_ok = candidate_brier <= (baseline_brier + 0.0025) and candidate_log_loss <= (baseline_log_loss + 0.01)
        drawdown_limit = (baseline_drawdown * 1.15) if baseline_drawdown > 0 else 15.0
        drawdown_ok = candidate_drawdown <= drawdown_limit
        accepted = bool(improves_return_or_expectancy and calibration_ok and drawdown_ok)
        status = "pass" if accepted else "fail"
        reasons = []
        if not improves_return_or_expectancy:
            reasons.append(f"{active_candidate_label} does not improve proxy-baseline expectancy or net directional PnL.")
        if not calibration_ok:
            reasons.append(f"{active_candidate_label} worsens calibration beyond the allowed hold threshold.")
        if not drawdown_ok:
            reasons.append(
                f"{active_candidate_label} drawdown {candidate_drawdown:.2f}% exceeds the allowed {drawdown_limit:.2f}% ceiling."
            )
        if not reasons:
            reasons.append(
                f"{active_candidate_label} improves intraday edge without degrading calibration or drawdown beyond the allowed limit."
            )
        basis = " ".join(reasons)

    active_baseline_rows = (
        stock_only_proxy_rows if active_candidate_configuration == "hybrid_stock_only" else full_hybrid_proxy_rows
    )
    active_candidate_rows = (
        stock_only_rows if active_candidate_configuration == "hybrid_stock_only" else full_hybrid_rows
    )
    calibration_rows = pd.concat(
        [
            active_baseline_rows,
            active_candidate_rows,
        ],
        ignore_index=True,
    ) if not active_baseline_rows.empty or not active_candidate_rows.empty else active_rows.iloc[0:0].copy()
    calibration_buckets = {
        "market_regime": _prediction_calibration_bucket_report(calibration_rows, "market_regime", "market_regime"),
        "session_label": _prediction_calibration_bucket_report(calibration_rows, "session_label", "session_label"),
        "event_window_label": _prediction_calibration_bucket_report(calibration_rows, "event_window_label", "event_window_label"),
        "volatility_regime": _prediction_calibration_bucket_report(calibration_rows, "volatility_regime", "volatility_regime"),
    }
    driver_ablation = _prediction_driver_ablation_report(active_candidate_rows.copy())
    return {
        "status": status,
        "accepted": accepted,
        "label": (
            f"{active_candidate_label} accepted"
            if status == "pass"
            else f"{active_candidate_label} validation collecting sample"
            if status == "partial"
            else f"{active_candidate_label} validation failed"
        ),
        "basis": basis,
        "baseline_key": "proxy_baseline",
        "candidate_key": active_candidate_configuration,
        "preferred_candidate_configuration": preferred_candidate_configuration,
        "active_candidate_configuration": active_candidate_configuration,
        "prediction_promotion_tier": active_candidate_configuration,
        "minimum_resolved_rows": _PREDICTION_VALIDATION_MIN_RESOLVED,
        "active_prediction_stack_version": active_prediction_stack_version,
        "runtime_activation": runtime_status,
        "legacy_resolved_rows_excluded": legacy_resolved_rows_excluded,
        "active_resolved_rows": int(len(active_rows)),
        "active_group_count": int(active_rows["forecast_group_id"].nunique()),
        "paired_group_counts": {
            "hybrid_stock_only": int(len(stock_only_group_ids)),
            "full_hybrid": int(len(full_hybrid_group_ids)),
        },
        "configurations": configuration_reports,
        "baseline": baseline,
        "candidate": candidate,
        "calibration_buckets": calibration_buckets,
        "driver_ablation": driver_ablation,
        "state_family_counts": {
            "proxy_baseline": int((active_rows["prediction_configuration"] == "proxy_baseline").sum()),
            "hybrid_stock_only": int((active_rows["prediction_configuration"] == "hybrid_stock_only").sum()),
            "full_hybrid": int((active_rows["prediction_configuration"] == "full_hybrid").sum()),
        },
    }


def build_stress_matrix_results(
    ledger: pd.DataFrame,
    *,
    starting_capital: float,
    current_settings: dict[str, Any],
) -> list[dict[str, Any]]:
    closes = ledger[ledger["event_type"] == "close"].copy()
    variants = build_test_matrix(current_settings)
    results: list[dict[str, Any]] = []
    for variant in variants:
        metrics = _simulate_variant(
            closes,
            starting_capital=starting_capital,
            overrides=dict(variant["overrides"]),
        )
        results.append({**variant, "metrics": metrics})
    return results


def build_v0_baseline(
    *,
    tenant_slug: str,
    starting_capital: float,
    known_peak_equity: float,
    known_drawdown_peak: float,
    known_drawdown_trough: float,
    known_max_drawdown_pct: float,
) -> dict[str, Any]:
    tenant = _load_tenant_row(tenant_slug)
    trade_automation = dict((tenant["metadata"] or {}).get("trade_automation") or {})
    settings_state = dict(trade_automation.get("settings") or {})
    runtime_state = dict(trade_automation.get("runtime") or {})
    settings_snapshot = _trade_settings_snapshot(settings_state)
    settings_snapshot["risk_percent"] = _coerce_float(settings_state.get("risk_percent"), 0.25)
    settings_snapshot["max_open_positions"] = int(settings_state.get("max_open_positions") or 0)
    settings_snapshot["regular_hours_only"] = bool(settings_state.get("regular_hours_only"))
    settings_snapshot["fractional_shares_only"] = bool(settings_state.get("fractional_shares_only"))
    settings_snapshot["execution_intent"] = str(settings_state.get("execution_intent") or "broker_paper")
    settings_snapshot["use_fast_model"] = bool(settings_state.get("use_fast_model"))

    baseline = {
        "version": "v0",
        "frozen_at": pd.Timestamp.utcnow().isoformat(),
        "tenant_slug": tenant_slug,
        "tenant_id": tenant["id"],
        "starting_capital": starting_capital,
        "known_peak_equity": known_peak_equity,
        "known_drawdown_peak": known_drawdown_peak,
        "known_drawdown_trough": known_drawdown_trough,
        "known_max_drawdown_pct": known_max_drawdown_pct,
        "current_settings": settings_snapshot,
        "runtime_state": _safe_json(runtime_state),
        "notes": [
            "Peak equity and known drawdown values are frozen from operator-provided reference figures, not inferred from the current local ledger.",
            "Current local equity history in the repo is still primarily closed-trade realized PnL plus current open-trade monitoring, so v0 keeps both the known reference and the locally reconstructed ledger side by side.",
        ],
        "pass_fail_targets": {
            "max_drawdown_pct": {"target": 20.0, "stretch": 15.0},
            "profit_factor_min": 1.3,
            "min_trades": 200,
            "average_trade_profit_vs_cost_multiple": 2.0,
            "max_single_trade_loss_pct_equity": 1.0,
            "max_symbol_drawdown_pct": 5.0,
            "gross_exposure_usual_cap": 2.0,
            "daily_loss_floor_pct": -2.0,
            "weekly_loss_floor_pct": -5.0,
        },
    }
    return baseline


@dataclass
class ValidationExportResult:
    output_dir: Path
    baseline_path: Path
    ledger_path: Path
    equity_snapshots_path: Path
    broker_reconciliation_path: Path
    intraday_prediction_validation_path: Path
    benchmark_report_path: Path
    next_bar_replay_path: Path
    walk_forward_path: Path
    stress_matrix_path: Path
    drawdown_report_path: Path
    drawdown_decomposition_path: Path
    mark_to_market_report_path: Path
    monte_carlo_path: Path
    kill_switch_report_path: Path
    summary_path: Path
    tracker_path: Path
    tracker_markdown_path: Path
    metrics: dict[str, Any]


def _tracker_item(
    key: str,
    title: str,
    status: str,
    summary: str,
    detail: str,
    *,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "key": key,
        "title": title,
        "status": status,
        "summary": summary,
        "detail": detail,
    }
    if evidence:
        payload["evidence"] = evidence
    return payload


def _format_tracker_markdown(tracker: dict[str, Any]) -> str:
    lines = [
        "# Strategy Validation Tracker",
        "",
        f"- Generated: {tracker.get('generated_at')}",
        f"- Tenant: {tracker.get('tenant_slug')}",
        f"- Baseline: {tracker.get('version')}",
        f"- Overall status: {tracker.get('overall_status')}",
        f"- Strategy settings locked: {tracker.get('settings_locked')}",
        "",
        "## Checklist",
    ]
    for item in list(tracker.get("checklist") or []):
        lines.extend(
            [
                "",
                f"### {item.get('title')} [{str(item.get('status') or '').upper()}]",
                f"- Summary: {item.get('summary')}",
                f"- Detail: {item.get('detail')}",
            ]
        )
        evidence = item.get("evidence") or {}
        if evidence:
            lines.append(f"- Evidence: {json.dumps(_safe_json(evidence), sort_keys=True)}")
    lines.extend(["", "## Version Track"])
    for item in list(tracker.get("version_track") or []):
        lines.extend(
            [
                "",
                f"- {item.get('version')}: {item.get('title')} [{str(item.get('status') or '').upper()}]",
                f"  - {item.get('detail')}",
            ]
        )
    lines.extend(["", "## Next Actions"])
    for item in list(tracker.get("next_actions") or []):
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def build_validation_tracker(
    *,
    baseline: dict[str, Any],
    ledger: pd.DataFrame,
    summary: dict[str, Any],
    stress_matrix: list[dict[str, Any]],
    benchmark_report: dict[str, Any],
    current_route_benchmark_report: dict[str, Any] | None = None,
    next_bar_replay: dict[str, Any],
    walk_forward_report: dict[str, Any],
    drawdown_decomposition: dict[str, Any],
    broker_reconciliation: dict[str, Any],
    monte_carlo: dict[str, Any],
    current_route_execution_realism: dict[str, Any] | None = None,
    kill_switch_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    targets = baseline.get("pass_fail_targets") or {}
    metrics = summary.get("metrics") or {}
    ledger_limits = summary.get("ledger_limits") or {}
    alignment = summary.get("signal_execution_alignment") or {}
    current_route_benchmark_report = current_route_benchmark_report or {}
    current_route_execution_realism = current_route_execution_realism or {}
    kill_switch_report = kill_switch_report or {}
    validation_integrity = summary.get("validation_integrity") or {}
    current_route_validation_integrity = summary.get("current_route_validation_integrity") or validation_integrity
    current_alignment = current_route_execution_realism.get("signal_execution_alignment") or {}
    current_settings = baseline.get("current_settings") or {}
    accounting_columns = {
        "fees",
        "borrowed_amount",
        "margin_interest",
        "cumulative_fees",
        "cumulative_margin_interest",
        "long_market_value",
        "short_market_value",
    }
    accounting_explicit = accounting_columns.issubset(set(ledger.columns))

    required_ledger_fields = [
        "timestamp",
        "symbol",
        "side",
        "signal_timestamp",
        "order_timestamp",
        "fill_timestamp",
        "fill_price",
        "quantity",
        "cash_before",
        "cash_after",
        "position_before",
        "position_after",
        "gross_exposure",
        "net_exposure",
        "realized_pnl",
        "unrealized_pnl",
        "fees",
        "slippage",
        "equity_after_fill",
        "reason_for_entry",
        "reason_for_exit",
        "route_family",
        "route_version",
        "automation_entry_reason",
        "thesis_direction",
        "directional_exposure",
        "validation_sample_bucket",
    ]
    ledger_columns = {str(column) for column in list(ledger.columns)}
    missing_ledger_fields = [field for field in required_ledger_fields if field not in ledger_columns]

    stress_by_key = {
        str(item.get("key") or "").strip().upper(): item
        for item in stress_matrix
        if str(item.get("key") or "").strip()
    }
    slippage_2x_return = _coerce_float((((stress_by_key.get("E") or {}).get("metrics") or {}).get("return_pct")), 0.0)
    slippage_3x_return = _coerce_float((((stress_by_key.get("F") or {}).get("metrics") or {}).get("return_pct")), 0.0)
    next_bar_return = _coerce_float((((stress_by_key.get("G") or {}).get("metrics") or {}).get("return_pct")), 0.0)
    current_route_stress_by_key = {
        str(item.get("key") or "").strip().upper(): item
        for item in list(current_route_execution_realism.get("stress_matrix") or [])
        if str(item.get("key") or "").strip()
    }
    current_route_slippage_2x_return = _coerce_float((((current_route_stress_by_key.get("E") or {}).get("metrics") or {}).get("return_pct")), 0.0)
    current_route_slippage_3x_return = _coerce_float((((current_route_stress_by_key.get("F") or {}).get("metrics") or {}).get("return_pct")), 0.0)
    current_route_next_bar_return = _coerce_float((((current_route_stress_by_key.get("G") or {}).get("metrics") or {}).get("return_pct")), 0.0)
    leverage_keys_present = all(key in stress_by_key for key in ("B", "C", "D"))
    risk_keys_present = all(key in stress_by_key for key in ("J", "K"))
    correlation_cap_present = "L" in stress_by_key
    current_route_ranked_entry_keys_present = all(key in current_route_stress_by_key for key in ("M", "N", "O", "P"))
    ranked_entry_rollout = evaluate_ranked_entry_rollout_acceptance(
        list(current_route_execution_realism.get("stress_matrix") or []),
        starting_capital=float(baseline.get("starting_capital") or 0.0),
        baseline_key="A",
        candidate_key="M",
    )
    required_stress_scenarios = 16

    trade_count = int(metrics.get("trade_count") or 0)
    closed_trade_count = int(metrics.get("closed_trade_count") or 0)
    profit_factor = metrics.get("profit_factor")
    average_trade_profit = _coerce_float(metrics.get("average_trade_profit"), 0.0)
    average_trade_cost = _coerce_float(metrics.get("average_trade_cost"), 0.0)
    cost_multiple = average_trade_profit / average_trade_cost if average_trade_cost > 0 else None
    best_benchmark_return = _coerce_float((benchmark_report.get("best_benchmark") or {}).get("return_pct"), 0.0)
    strategy_return = _coerce_float(benchmark_report.get("strategy_return_pct"), 0.0)
    current_route_best_benchmark_return = _coerce_float((current_route_benchmark_report.get("best_benchmark") or {}).get("return_pct"), 0.0)
    current_route_strategy_return = _coerce_float(current_route_benchmark_report.get("strategy_return_pct"), 0.0)
    unresolved_reconciliation = int(broker_reconciliation.get("current_route_issue_count") or 0)
    current_route_reconciliation_status = str(
        broker_reconciliation.get("current_route_reconciliation_status") or "waiting"
    ).strip().lower() or "waiting"
    current_route_orphan_order_event_count = int(
        broker_reconciliation.get("current_route_orphan_order_event_count") or 0
    )
    legacy_orphan_order_event_count = int(
        broker_reconciliation.get("legacy_orphan_order_event_count") or 0
    )
    drawdown_source = str(drawdown_decomposition.get("window_source") or "")
    snapshot_count = int(ledger_limits.get("mark_to_market_snapshot_count") or 0)
    monte_trade_count = int(monte_carlo.get("trade_count") or 0)
    current_route_fill_count = int(current_alignment.get("fill_count") or current_route_execution_realism.get("trade_count") or 0)
    current_route_directional_fill_count = int(current_alignment.get("directional_fill_count") or 0)
    current_route_closed_trade_count = int(current_route_execution_realism.get("closed_trade_count") or 0)
    current_route_mismatched_count = int(current_alignment.get("mismatched_count") or 0)
    current_route_sample_status = str(current_route_execution_realism.get("sample_status") or "insufficient").strip().lower() or "insufficient"
    legacy_fill_count = int(alignment.get("legacy_fill_count") or alignment.get("retired_rule_fill_count") or 0)
    legacy_directional_fill_count = int(alignment.get("legacy_directional_fill_count") or alignment.get("retired_rule_directional_fill_count") or 0)
    legacy_mismatched_count = int(alignment.get("legacy_mismatched_count") or alignment.get("retired_rule_mismatched_count") or 0)
    metrics_source = str(current_route_validation_integrity.get("metrics_source") or "event_ledger").strip().lower() or "event_ledger"
    mark_to_market_coverage_status = str(current_route_validation_integrity.get("mark_to_market_coverage_status") or "missing").strip().lower() or "missing"
    ledger_snapshot_consistency = str(current_route_validation_integrity.get("ledger_snapshot_consistency") or "unavailable").strip().lower() or "unavailable"
    route_window_start = current_route_validation_integrity.get("route_window_start")
    route_window_end = current_route_validation_integrity.get("route_window_end")
    route_window_snapshot_count = int(current_route_validation_integrity.get("route_window_snapshot_count") or 0)
    current_route_benchmark_trade_count = int(current_route_benchmark_report.get("trade_count") or 0)
    current_route_benchmark_closed_trade_count = int(current_route_benchmark_report.get("closed_trade_count") or 0)
    walk_forward_window_count = int(walk_forward_report.get("window_count") or 0)
    walk_forward_status_key = str(walk_forward_report.get("status") or "").strip().lower()
    walk_forward_test_metrics = dict(walk_forward_report.get("stitched_test_metrics") or {})
    walk_forward_trade_count = int(walk_forward_test_metrics.get("trade_count") or 0)
    walk_forward_profit_factor = walk_forward_test_metrics.get("profit_factor")
    walk_forward_drawdown_pct = _coerce_float(walk_forward_test_metrics.get("max_drawdown_pct"), 0.0)
    walk_forward_required_months = int(walk_forward_report.get("required_coverage_months") or 0)
    walk_forward_months_available = int(walk_forward_report.get("coverage_months_available") or 0)
    if walk_forward_status_key != "ok":
        walk_forward_status = "partial"
        walk_forward_summary = "Walk-forward coverage is not sufficient yet."
        walk_forward_detail = "The walk-forward harness is exported, but the current history does not yet provide enough monthly coverage for a 6m/1m rolling test."
    elif walk_forward_trade_count < int(targets.get("min_trades") or 0):
        walk_forward_status = "partial"
        walk_forward_summary = "Walk-forward export exists, but the stitched test sample is still too small."
        walk_forward_detail = "Rolling windows are available, but the stitched test months do not yet have enough closed trades to validate robustness."
    elif (
        walk_forward_profit_factor is not None
        and walk_forward_profit_factor >= _coerce_float(targets.get("profit_factor_min"), 0.0)
        and walk_forward_drawdown_pct <= _coerce_float((targets.get("max_drawdown_pct") or {}).get("target"), 100.0)
    ):
        walk_forward_status = "pass"
        walk_forward_summary = "Walk-forward results currently clear the restricted validation thresholds."
        walk_forward_detail = "The stitched test-window sample has enough trade depth and currently meets the profit-factor and drawdown targets."
    else:
        walk_forward_status = "fail"
        walk_forward_summary = "Walk-forward results do not yet clear the restricted validation thresholds."
        walk_forward_detail = "The rolling test windows exist, but the stitched test sample does not yet support a go decision."

    if current_route_mismatched_count or current_route_slippage_2x_return < 0 or current_route_slippage_3x_return < 0 or current_route_next_bar_return < 0:
        execution_realism_status = "fail"
        execution_realism_summary = "Current-route realistic execution variants do not currently hold up cleanly."
        execution_realism_detail = (
            "The current post-fix route still shows directional mismatches or turns negative under next-bar / "
            "higher-slippage variants."
        )
    elif current_route_sample_status != "sufficient":
        execution_realism_status = "partial"
        execution_realism_summary = "Current-route execution realism is not signed off yet."
        execution_realism_detail = (
            "Retired pre-fix fills remain in the audit trail, but the current post-fix route still lacks enough "
            "directional fill and close coverage to validate execution behavior."
        )
    else:
        execution_realism_status = "pass"
        execution_realism_summary = "Current-route execution realism currently clears the restricted checks."
        execution_realism_detail = (
            "Directional fills are aligned on the current route and the restricted next-bar / higher-slippage variants "
            "remain non-negative."
        )

    if not accounting_explicit:
        accounting_status = "fail"
        accounting_summary = "The exported ledger is still missing explicit accounting fields."
        accounting_detail = "Current exports still do not model borrowed amount, explicit margin interest, and explicit fee treatment end-to-end."
    elif ledger_snapshot_consistency == "inconsistent":
        accounting_status = "fail"
        accounting_summary = "Ledger and mark-to-market accounting disagree materially."
        accounting_detail = "Snapshot-derived ending equity or gross exposure deviates beyond the allowed tolerance, so the export cannot be trusted for promotion decisions yet."
    elif mark_to_market_coverage_status == "complete":
        accounting_status = "pass"
        accounting_summary = "Accounting treatment is explicit and snapshot coverage is coherent."
        accounting_detail = "The ledger carries explicit accounting fields and the mark-to-market series spans the analyzed window without conflicting with the event ledger."
    else:
        accounting_status = "partial"
        accounting_summary = "Accounting treatment is explicit, but snapshot coverage is still incomplete."
        accounting_detail = "The route-window ledger models fees, borrowed amount, and margin interest explicitly, but current-route snapshot coverage is not yet complete enough to override ledger metrics."

    if current_route_benchmark_closed_trade_count <= 0:
        benchmarking_status = "partial"
        benchmarking_summary = "Current-route benchmarking is not signed off yet."
        benchmarking_detail = (
            "The full-history benchmark remains in the audit export, but the current post-fix route does not yet have "
            "enough closed trades to compare against simple benchmarks."
        )
    elif current_route_strategy_return < current_route_best_benchmark_return:
        benchmarking_status = "fail"
        benchmarking_summary = "Current-route strategy currently trails the best simple benchmark."
        benchmarking_detail = (
            "The current post-fix route underperforms at least one simple benchmark over the covered current-route window."
        )
    else:
        benchmarking_status = "pass"
        benchmarking_summary = "Current-route strategy currently beats the tracked simple benchmarks."
        benchmarking_detail = (
            "The current post-fix route outperforms the tracked simple benchmarks over the covered current-route window."
        )

    min_trades_target = int(targets.get("min_trades") or 0)
    profit_factor_min = _coerce_float(targets.get("profit_factor_min"), 0.0)
    cost_multiple_target = _coerce_float(targets.get("average_trade_profit_vs_cost_multiple"), 0.0)
    max_drawdown_target_pct = _coerce_float((targets.get("max_drawdown_pct") or {}).get("target"), 100.0)
    enough_trade_depth_for_metrics = closed_trade_count >= min_trades_target if min_trades_target > 0 else True
    metrics_have_full_evidence = (
        enough_trade_depth_for_metrics
        and execution_realism_status == "pass"
        and benchmarking_status != "partial"
        and accounting_status == "pass"
    )
    hard_metric_failure = (
        execution_realism_status == "fail"
        or benchmarking_status == "fail"
        or (profit_factor is not None and profit_factor < profit_factor_min)
        or (cost_multiple is not None and average_trade_cost > 0 and cost_multiple < cost_multiple_target)
        or _coerce_float(metrics.get("max_drawdown_pct"), 0.0) > max_drawdown_target_pct
    )
    if not metrics_have_full_evidence:
        pass_fail_metrics_status = "partial"
        pass_fail_metrics_summary = "Restricted pass/fail metrics are not signed off yet."
        pass_fail_metrics_detail = (
            "The export is tracking the right thresholds, but the current sample is still too small or too incomplete "
            "to make a go/no-go call."
        )
    elif hard_metric_failure:
        pass_fail_metrics_status = "fail"
        pass_fail_metrics_summary = "The strategy does not yet clear the validation thresholds."
        pass_fail_metrics_detail = (
            "With enough evidence in hand, the restricted thresholds still are not met across trade depth, "
            "benchmark-relative performance, or execution stability."
        )
    else:
        pass_fail_metrics_status = "pass"
        pass_fail_metrics_summary = "The restricted pass/fail metrics currently clear the validation thresholds."
        pass_fail_metrics_detail = (
            "The current restricted sample meets the configured trade-depth, drawdown, cost, and benchmark-relative checks."
        )

    if unresolved_reconciliation > 0 or current_route_orphan_order_event_count > 0:
        paper_validation_status = "fail"
        paper_validation_summary = "Broker-vs-ledger reconciliation is not clean yet."
        paper_validation_detail = "Lifecycle or broker reconciliation mismatches still need to be resolved before the post-fix route can be signed off."
    elif current_route_sample_status != "sufficient":
        paper_validation_status = "partial"
        paper_validation_summary = "Current-route paper validation is still collecting sample."
        paper_validation_detail = "Lifecycle mismatches are currently resolved, but the current-route sample is still too small to sign off on execution behavior."
    elif accounting_status != "pass" or execution_realism_status != "pass":
        paper_validation_status = "partial"
        paper_validation_summary = "Current-route paper validation is close, but integrity checks are still open."
        paper_validation_detail = "Current-route collection has enough trades, but accounting coverage or execution realism still needs to clear before promotion."
    else:
        paper_validation_status = "pass"
        paper_validation_summary = "Current-route paper validation is signed off."
        paper_validation_detail = "Current-route paper fills, closes, and accounting integrity are strong enough to support promotion review."

    if current_route_sample_status != "sufficient":
        ranked_entry_rollout_status = "partial"
        ranked_entry_rollout_summary = "Current-route ranked-entry validation is still collecting sample."
        ranked_entry_rollout_detail = (
            "Current-route paper evidence is still below the fill and close thresholds, so the widened profile is not yet scored for promotion."
        )
    elif bool(ranked_entry_rollout.get("accepted")) and accounting_status == "pass" and execution_realism_status == "pass":
        ranked_entry_rollout_status = "pass"
        ranked_entry_rollout_summary = "The widened ranked-entry profile clears the promotion gate."
        ranked_entry_rollout_detail = str(
            ranked_entry_rollout.get("basis")
            or "The widened ranked-entry profile clears the promotion gate."
        )
    elif current_route_ranked_entry_keys_present:
        ranked_entry_rollout_status = "partial"
        ranked_entry_rollout_summary = "The widened ranked-entry profile remains validation-only."
        ranked_entry_rollout_detail = (
            "Ranked-entry promotion remains blocked until accounting and execution realism both clear validation."
            if bool(ranked_entry_rollout.get("accepted")) and (accounting_status != "pass" or execution_realism_status != "pass")
            else str(
                ranked_entry_rollout.get("basis")
                or "The ranked-entry rollout decision artifact is not available yet."
            )
        )
    else:
        ranked_entry_rollout_status = "fail"
        ranked_entry_rollout_summary = "The ranked-entry promotion scenarios are not fully exported yet."
        ranked_entry_rollout_detail = "The ranked-entry rollout decision artifact is not available yet."

    checklist: list[dict[str, Any]] = [
        _tracker_item(
            "freeze_v0",
            "Freeze current version",
            "pass",
            "Current strategy is frozen as v0.",
            "Current automation settings are exported and should remain unchanged while validation is in progress.",
            evidence={
                "baseline_version": baseline.get("version"),
                "timeframe": current_settings.get("timeframe"),
                "assets_traded": current_settings.get("assets_traded"),
                "execution_intent": current_settings.get("execution_intent"),
            },
        ),
        _tracker_item(
            "trade_ledger",
            "Trade ledger coverage",
            "pass" if not missing_ledger_fields else "fail",
            "Trade and equity event ledger is exported.",
            "The validation ledger includes the audit fields needed to reconstruct fills, closes, and exposure." if not missing_ledger_fields else "The ledger export is missing required audit fields.",
            evidence={
                "trade_count": trade_count,
                "closed_trade_count": closed_trade_count,
                "missing_fields": missing_ledger_fields,
            },
        ),
        _tracker_item(
            "accounting",
            "Accounting model",
            accounting_status,
            accounting_summary,
            accounting_detail,
            evidence={
                "snapshot_count": snapshot_count,
                "mark_to_market_complete": bool(ledger_limits.get("mark_to_market_complete")),
                "fees_assumption": current_settings.get("fees_assumption"),
                "margin_assumption": current_settings.get("margin_assumption"),
                "accounting_columns_present": accounting_explicit,
                "audit_metrics_source": validation_integrity.get("metrics_source"),
                "audit_mark_to_market_coverage_status": validation_integrity.get("mark_to_market_coverage_status"),
                "audit_ledger_snapshot_consistency": validation_integrity.get("ledger_snapshot_consistency"),
                "metrics_source": metrics_source,
                "mark_to_market_coverage_status": mark_to_market_coverage_status,
                "ledger_snapshot_consistency": ledger_snapshot_consistency,
                "route_window_start": route_window_start,
                "route_window_end": route_window_end,
                "route_window_snapshot_count": route_window_snapshot_count,
                "peak_borrowed_amount": metrics.get("peak_borrowed_amount"),
                "total_fees": metrics.get("total_fees"),
                "total_margin_interest": metrics.get("total_margin_interest"),
            },
        ),
        _tracker_item(
            "execution_realism",
            "Execution realism",
            execution_realism_status,
            execution_realism_summary,
            execution_realism_detail,
            evidence={
                "all_history_mismatch_rate": alignment.get("mismatch_rate"),
                "all_history_slippage_2x_return_pct": slippage_2x_return,
                "all_history_slippage_3x_return_pct": slippage_3x_return,
                "all_history_next_bar_return_pct": next_bar_return,
                "legacy_fill_count": legacy_fill_count,
                "legacy_directional_fill_count": legacy_directional_fill_count,
                "legacy_mismatched_count": legacy_mismatched_count,
                "retired_rule_directional_fill_count": legacy_directional_fill_count,
                "retired_rule_mismatched_count": legacy_mismatched_count,
                "current_route_fill_count": current_route_fill_count,
                "current_route_directional_fill_count": current_route_directional_fill_count,
                "current_route_closed_trade_count": current_route_closed_trade_count,
                "current_route_sample_status": current_route_sample_status,
                "directional_fill_threshold": _CURRENT_ROUTE_MIN_DIRECTIONAL_FILLS,
                "closed_trade_threshold": _CURRENT_ROUTE_MIN_CLOSED_TRADES,
                "current_route_mismatch_rate": current_alignment.get("mismatch_rate"),
                "current_route_slippage_2x_return_pct": current_route_slippage_2x_return,
                "current_route_slippage_3x_return_pct": current_route_slippage_3x_return,
                "current_route_next_bar_return_pct": current_route_next_bar_return,
            },
        ),
        _tracker_item(
            "stress_matrix",
            "Stress matrix",
            "partial" if len(stress_matrix) >= required_stress_scenarios else "fail",
            "The requested stress matrix is exported.",
            "The scenario coverage is present, but the sample is too small for the matrix to validate robustness.",
            evidence={
                "scenario_count": len(stress_matrix),
                "required_scenarios": required_stress_scenarios,
                "closed_trade_count": closed_trade_count,
            },
        ),
        _tracker_item(
            "pass_fail_metrics",
            "Pass or fail metrics",
            pass_fail_metrics_status,
            pass_fail_metrics_summary,
            pass_fail_metrics_detail,
            evidence={
                "closed_trade_count": closed_trade_count,
                "min_trades_target": min_trades_target,
                "profit_factor": profit_factor,
                "profit_factor_min": profit_factor_min,
                "average_trade_cost_multiple": cost_multiple,
                "cost_multiple_target": cost_multiple_target,
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "max_drawdown_target_pct": max_drawdown_target_pct,
                "execution_realism_status": execution_realism_status,
                "benchmarking_status": benchmarking_status,
            },
        ),
        _tracker_item(
            "benchmarking",
            "Benchmarking",
            benchmarking_status,
            benchmarking_summary,
            benchmarking_detail,
            evidence={
                "all_history_strategy_return_pct": strategy_return,
                "all_history_best_benchmark_return_pct": best_benchmark_return,
                "all_history_best_benchmark": (benchmark_report.get("best_benchmark") or {}).get("label"),
                "current_route_trade_count": current_route_benchmark_trade_count,
                "current_route_closed_trade_count": current_route_benchmark_closed_trade_count,
                "current_route_strategy_return_pct": current_route_strategy_return,
                "current_route_best_benchmark_return_pct": current_route_best_benchmark_return,
                "current_route_best_benchmark": (current_route_benchmark_report.get("best_benchmark") or {}).get("label"),
            },
        ),
        _tracker_item(
            "drawdown_diagnostic",
            "Drawdown diagnostic",
            "partial",
            "Drawdown decomposition is exported, but it is not yet tied to a statistically meaningful stress window.",
            "Current attribution still relies on a small fallback window instead of the major drawdown event that motivated the review.",
            evidence={
                "window_source": drawdown_source,
                "net_realized_pnl": drawdown_decomposition.get("net_realized_pnl"),
                "worst_trade_symbol": (drawdown_decomposition.get("worst_trade") or {}).get("symbol"),
            },
        ),
        _tracker_item(
            "kill_switches",
            "Kill switches and guardrails",
            str(kill_switch_report.get("status") or "partial"),
            str(kill_switch_report.get("summary") or "Kill-switch validation is not complete yet."),
            str(kill_switch_report.get("detail") or "The exported settings still need explicit guardrail validation."),
            evidence={
                "configured_thresholds": kill_switch_report.get("configured_thresholds"),
                "checks": kill_switch_report.get("checks"),
                "observed": kill_switch_report.get("observed"),
                "recommended_thresholds": kill_switch_report.get("recommended_thresholds"),
            },
        ),
        _tracker_item(
            "walk_forward",
            "Walk-forward testing",
            walk_forward_status,
            walk_forward_summary,
            walk_forward_detail,
            evidence={
                "status": walk_forward_report.get("status"),
                "window_count": walk_forward_window_count,
                "coverage_months_available": walk_forward_months_available,
                "required_coverage_months": walk_forward_required_months,
                "stitched_test_trade_count": walk_forward_trade_count,
                "stitched_test_profit_factor": walk_forward_profit_factor,
                "stitched_test_max_drawdown_pct": walk_forward_drawdown_pct,
            },
        ),
        _tracker_item(
            "monte_carlo",
            "Monte Carlo trade-order test",
            "partial" if monte_trade_count > 0 else "pending",
            "Monte Carlo export exists, but the current trade sample is too small to be informative.",
            "Trade-order reshuffling is only useful after the ledger has enough closed trades to estimate tail risk.",
            evidence={
                "trade_count": monte_trade_count,
                "runs": monte_carlo.get("runs"),
                "worst_drawdown_pct": monte_carlo.get("worst_drawdown_pct"),
            },
        ),
        _tracker_item(
            "paper_validation",
            "Paper validation and broker reconciliation",
            paper_validation_status,
            paper_validation_summary,
            paper_validation_detail,
            evidence={
                "reconciliation_issue_count": unresolved_reconciliation,
                "current_route_reconciliation_status": current_route_reconciliation_status,
                "current_route_orphan_order_event_count": current_route_orphan_order_event_count,
                "legacy_orphan_order_event_count": legacy_orphan_order_event_count,
                "matched_trade_count": broker_reconciliation.get("matched_trade_count"),
                "trade_count": broker_reconciliation.get("trade_count"),
                "current_route_fill_count": current_route_fill_count,
                "current_route_closed_trade_count": current_route_closed_trade_count,
                "current_route_sample_status": current_route_sample_status,
            },
        ),
        _tracker_item(
            "ranked_entry_rollout",
            "Ranked-entry rollout gate",
            ranked_entry_rollout_status,
            ranked_entry_rollout_summary,
            ranked_entry_rollout_detail,
            evidence={
                "baseline_key": ranked_entry_rollout.get("baseline_key"),
                "candidate_key": ranked_entry_rollout.get("candidate_key"),
                "accepted": bool(ranked_entry_rollout.get("accepted")),
                "scenario_count": len(list(current_route_execution_realism.get("stress_matrix") or [])),
                "required_ranked_entry_scenarios": ["M", "N", "O", "P"],
                "baseline_metrics": ranked_entry_rollout.get("baseline"),
                "candidate_metrics": ranked_entry_rollout.get("candidate"),
                "drawdown_limit_pct": ranked_entry_rollout.get("drawdown_limit_pct"),
                "gross_cap_dollars": ranked_entry_rollout.get("gross_cap_dollars"),
                "audit_metrics_source": validation_integrity.get("metrics_source"),
                "audit_mark_to_market_coverage_status": validation_integrity.get("mark_to_market_coverage_status"),
                "audit_ledger_snapshot_consistency": validation_integrity.get("ledger_snapshot_consistency"),
                "metrics_source": metrics_source,
                "mark_to_market_coverage_status": mark_to_market_coverage_status,
                "ledger_snapshot_consistency": ledger_snapshot_consistency,
                "route_window_start": route_window_start,
                "route_window_end": route_window_end,
                "route_window_snapshot_count": route_window_snapshot_count,
                "current_route_sample_status": current_route_sample_status,
                "validation_integrity_blocked": accounting_status != "pass" or execution_realism_status != "pass",
            },
        ),
        _tracker_item(
            "tiny_live",
            "Tiny live deployment",
            "pending",
            "Live deployment remains intentionally blocked.",
            "The strategy should stay in paper validation until the restricted version passes and the sample is large enough.",
        ),
    ]

    version_track = [
        {
            "version": "v0",
            "title": "Current bot frozen",
            "status": "pass",
            "detail": "Current settings are frozen and exported as the baseline.",
        },
        {
            "version": "v1",
            "title": "Realistic costs",
            "status": "pass" if "E" in stress_by_key and "F" in stress_by_key else "pending",
            "detail": "2x and 3x slippage scenarios are exported in the stress matrix.",
        },
        {
            "version": "v2",
            "title": "Next-bar execution",
            "status": "pass" if next_bar_replay.get("trade_count") else "pending",
            "detail": "Next-bar replay report is exported for the current trade sample.",
        },
        {
            "version": "v3",
            "title": "Leverage cap scenarios",
            "status": "pass" if leverage_keys_present else "pending",
            "detail": "1x, 2x, and 3x gross exposure scenarios are included in the matrix.",
        },
        {
            "version": "v4",
            "title": "Risk-per-trade cap scenarios",
            "status": "pass" if risk_keys_present else "pending",
            "detail": "0.50% and 0.25% per-trade risk scenarios are included in the matrix.",
        },
        {
            "version": "v5",
            "title": "Correlation cap",
            "status": "pass" if correlation_cap_present else "pending",
            "detail": (
                "A dedicated 25% proxy correlation-bucket cap scenario is included in the matrix."
                if correlation_cap_present
                else "No dedicated correlation-cap scenario is exported yet."
            ),
        },
        {
            "version": "v6",
            "title": "Improved exits",
            "status": "pending",
            "detail": "Exit changes are not being tested yet because the current version remains frozen.",
        },
        {
            "version": "v7",
            "title": "Ranked entries",
            "status": "pass" if current_route_ranked_entry_keys_present else "pending",
            "detail": (
                "M/N/O/P ranked-entry scenarios are exported and can be used as the promotion artifact."
                if current_route_ranked_entry_keys_present
                else "The ranked-entry promotion scenarios are not fully exported yet."
            ),
        },
    ]

    status_counts = Counter(item.get("status") for item in checklist)
    overall_status = "blocked" if status_counts.get("fail") else "in_progress" if status_counts.get("partial") or status_counts.get("pending") else "pass"
    next_actions = [
        "Keep the current settings frozen as v0 until the tracker clears the execution-realism and benchmark failures.",
        "Accumulate at least 200 closed paper trades under the corrected routing rules before judging edge.",
        "Accumulate at least seven months of covered history so the 6m/1m walk-forward harness has meaningful windows.",
        "Review the M/N/O/P ranked-entry export and keep broker-live locked unless the promotion candidate is accepted.",
    ]

    return {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "tenant_slug": baseline.get("tenant_slug"),
        "version": baseline.get("version"),
        "settings_locked": True,
        "overall_status": overall_status,
        "status_counts": dict(status_counts),
        "current_settings": baseline.get("current_settings"),
        "checklist": checklist,
        "version_track": version_track,
        "next_actions": next_actions,
    }


def export_strategy_validation(
    *,
    tenant_slug: str,
    starting_capital: float,
    known_peak_equity: float,
    known_drawdown_peak: float,
    known_drawdown_trough: float,
    known_max_drawdown_pct: float,
    output_dir: Path | None = None,
) -> ValidationExportResult:
    destination = output_dir or (RUNTIME_EXPORTS_DIR / "latest")
    destination.mkdir(parents=True, exist_ok=True)

    baseline = build_v0_baseline(
        tenant_slug=tenant_slug,
        starting_capital=starting_capital,
        known_peak_equity=known_peak_equity,
        known_drawdown_peak=known_drawdown_peak,
        known_drawdown_trough=known_drawdown_trough,
        known_max_drawdown_pct=known_max_drawdown_pct,
    )
    ledger = build_trade_validation_ledger(
        tenant_slug=tenant_slug,
        starting_capital=starting_capital,
    )
    tenant = _load_tenant_row(tenant_slug)
    trade_frames = _load_trade_frames()
    order_events = _load_order_events(tenant["id"])
    equity_snapshots = _load_tenant_equity_snapshots(tenant["id"], tenant_slug)
    broker_reconciliation = build_broker_reconciliation_report(
        ledger,
        open_trades=trade_frames["open_trades"],
        closed_trades=trade_frames["closed_trades"],
        pending_orders=trade_frames["pending_orders"],
        order_events=order_events,
    )
    next_bar_replay = build_next_bar_replay_report(ledger)
    ledger = _attach_replay_penalties(ledger, next_bar_replay)
    reconciled_current_route_ledger = _filter_reconciled_current_route_ledger(
        ledger,
        broker_reconciliation,
    )
    current_route_execution_realism = build_current_route_execution_realism_report(
        ledger,
        starting_capital=starting_capital,
        current_settings=baseline["current_settings"],
        broker_reconciliation=broker_reconciliation,
    )
    intraday_prediction_validation = build_intraday_prediction_validation_report(
        trade_frames["forecast_journal"],
        starting_capital=starting_capital,
    )
    current_route_validation_integrity = _build_current_route_validation_integrity(
        ledger,
        equity_snapshots,
        starting_capital=starting_capital,
        current_route_sample_status=str(current_route_execution_realism.get("sample_status") or "insufficient"),
        current_route_ledger=reconciled_current_route_ledger,
    )
    walk_forward_report = build_walk_forward_report(
        ledger,
        starting_capital=starting_capital,
    )
    metrics = compute_ledger_metrics(ledger, starting_capital=starting_capital)
    signal_execution_alignment = build_signal_execution_alignment_report(ledger)
    ledger_drawdown_report = build_drawdown_report(ledger)
    mark_to_market_report = build_mark_to_market_report(equity_snapshots, starting_capital=starting_capital)
    validation_integrity = _build_metrics_source_summary(
        ledger,
        equity_snapshots,
        ledger_metrics=metrics,
        mark_to_market_report=mark_to_market_report,
        starting_capital=starting_capital,
    )
    validation_integrity["current_route_sample_status"] = str(current_route_execution_realism.get("sample_status") or "insufficient")
    drawdown_report = mark_to_market_report if bool(validation_integrity.get("use_mark_to_market")) else ledger_drawdown_report
    drawdown_decomposition = build_drawdown_decomposition_report(
        ledger,
        equity_snapshots,
        starting_capital=starting_capital,
    )
    if bool(validation_integrity.get("use_mark_to_market")):
        metrics["ending_equity"] = round(float(mark_to_market_report["latest_equity"]), 4)
        metrics["return_pct"] = round(((float(mark_to_market_report["latest_equity"]) - starting_capital) / starting_capital * 100.0), 4) if starting_capital > 0 else 0.0
        metrics["gross_exposure_peak"] = round(float(mark_to_market_report["gross_exposure_peak"]), 4)
        metrics["max_drawdown_pct"] = round(float(mark_to_market_report["max_drawdown_pct"]), 4)
    metrics["max_drawdown_source"] = "mark_to_market_snapshots" if bool(validation_integrity.get("use_mark_to_market")) else "event_ledger"
    benchmark_report = build_benchmark_report(
        ledger,
        equity_snapshots,
        starting_capital=starting_capital,
        strategy_ending_equity=float(metrics.get("ending_equity") or starting_capital),
    )
    current_route_window_start = _coerce_timestamp(current_route_validation_integrity.get("route_window_start"))
    current_route_window_end = _coerce_timestamp(current_route_validation_integrity.get("route_window_end"))
    current_route_snapshots = _filter_snapshots_to_window(
        equity_snapshots,
        start_at=current_route_window_start,
        end_at=current_route_window_end,
    )
    current_route_benchmark_report = build_current_route_benchmark_report(
        ledger,
        current_route_snapshots,
        starting_capital=starting_capital,
    )
    kill_switch_report = build_kill_switch_report(
        baseline=baseline,
        metrics=metrics,
    )
    monte_carlo = run_trade_order_monte_carlo(ledger, starting_capital=starting_capital)
    stress_matrix = build_stress_matrix_results(
        ledger,
        starting_capital=starting_capital,
        current_settings=baseline["current_settings"],
    )

    baseline_path = destination / "v0_baseline.json"
    ledger_path = destination / "trade_validation_ledger.csv"
    equity_snapshots_path = destination / "equity_snapshots.csv"
    broker_reconciliation_path = destination / "broker_reconciliation.json"
    intraday_prediction_validation_path = destination / "intraday_prediction_validation.json"
    benchmark_report_path = destination / "benchmark_report.json"
    next_bar_replay_path = destination / "next_bar_replay.json"
    walk_forward_path = destination / "walk_forward.json"
    stress_matrix_path = destination / "stress_matrix.json"
    drawdown_report_path = destination / "drawdown_report.json"
    drawdown_decomposition_path = destination / "drawdown_decomposition.json"
    mark_to_market_report_path = destination / "mark_to_market_report.json"
    monte_carlo_path = destination / "monte_carlo.json"
    kill_switch_report_path = destination / "kill_switch_report.json"
    summary_path = destination / "summary.json"
    tracker_path = destination / "validation_tracker.json"
    tracker_markdown_path = destination / "validation_tracker.md"

    baseline_path.write_text(json.dumps(_safe_json(baseline), indent=2), encoding="utf-8")
    ledger.to_csv(ledger_path, index=False)
    equity_snapshots.to_csv(equity_snapshots_path, index=False)
    broker_reconciliation_path.write_text(json.dumps(_safe_json(broker_reconciliation), indent=2), encoding="utf-8")
    intraday_prediction_validation_path.write_text(
        json.dumps(_safe_json(intraday_prediction_validation), indent=2),
        encoding="utf-8",
    )
    benchmark_report_path.write_text(json.dumps(_safe_json(benchmark_report), indent=2), encoding="utf-8")
    next_bar_replay_path.write_text(json.dumps(_safe_json(next_bar_replay), indent=2), encoding="utf-8")
    walk_forward_path.write_text(json.dumps(_safe_json(walk_forward_report), indent=2), encoding="utf-8")
    stress_matrix_path.write_text(json.dumps(_safe_json(stress_matrix), indent=2), encoding="utf-8")
    drawdown_report_path.write_text(json.dumps(_safe_json(drawdown_report), indent=2), encoding="utf-8")
    drawdown_decomposition_path.write_text(json.dumps(_safe_json(drawdown_decomposition), indent=2), encoding="utf-8")
    mark_to_market_report_path.write_text(json.dumps(_safe_json(mark_to_market_report), indent=2), encoding="utf-8")
    monte_carlo_path.write_text(json.dumps(_safe_json(monte_carlo), indent=2), encoding="utf-8")
    kill_switch_report_path.write_text(json.dumps(_safe_json(kill_switch_report), indent=2), encoding="utf-8")
    snapshot_count = int(mark_to_market_report.get("snapshot_count") or 0)
    coverage_status = str(validation_integrity.get("mark_to_market_coverage_status") or "missing").strip().lower() or "missing"
    if snapshot_count <= 0:
        ledger_limit_description = "The reconstructed ledger is based on fill/close events from CSV and broker lifecycle rows. No continuous intratrade equity snapshots have been recorded yet."
    elif coverage_status == "partial_window":
        ledger_limit_description = "Mark-to-market snapshot capture has started, but the recorded window does not yet span the full analyzed ledger."
    elif coverage_status == "missing_fields":
        ledger_limit_description = "Mark-to-market snapshots exist, but required equity, cash, or gross-exposure fields are missing from the export."
    else:
        ledger_limit_description = "Continuous intratrade mark-to-market equity snapshots are available for the current automation history."
    summary = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "tenant_slug": tenant_slug,
        "starting_capital": starting_capital,
        "known_reference_drawdown": {
            "peak_equity": known_peak_equity,
            "drawdown_peak": known_drawdown_peak,
            "drawdown_trough": known_drawdown_trough,
            "max_drawdown_pct": known_max_drawdown_pct,
        },
        "ledger_limits": {
            "mark_to_market_complete": coverage_status == "complete",
            "mark_to_market_snapshot_count": snapshot_count,
            "coverage_start": mark_to_market_report.get("coverage_start"),
            "coverage_end": mark_to_market_report.get("coverage_end"),
            "description": ledger_limit_description,
        },
        "metrics": metrics,
        "validation_integrity": validation_integrity,
        "current_route_validation_integrity": current_route_validation_integrity,
        "signal_execution_alignment": signal_execution_alignment,
        "broker_reconciliation": broker_reconciliation,
        "intraday_prediction_validation": intraday_prediction_validation,
        "drawdown_report": drawdown_report,
        "drawdown_decomposition": drawdown_decomposition,
        "ledger_drawdown_report": ledger_drawdown_report,
        "mark_to_market_report": mark_to_market_report,
        "benchmark_report": benchmark_report,
        "current_route_benchmark_report": current_route_benchmark_report,
        "next_bar_replay": next_bar_replay,
        "current_route_execution_realism": current_route_execution_realism,
        "walk_forward_report": walk_forward_report,
        "monte_carlo": monte_carlo,
        "kill_switch_report": kill_switch_report,
        "stress_matrix_count": len(stress_matrix),
    }
    tracker = build_validation_tracker(
        baseline=baseline,
        ledger=ledger,
        summary=summary,
        stress_matrix=stress_matrix,
        benchmark_report=benchmark_report,
        current_route_benchmark_report=current_route_benchmark_report,
        next_bar_replay=next_bar_replay,
        walk_forward_report=walk_forward_report,
        drawdown_decomposition=drawdown_decomposition,
        broker_reconciliation=broker_reconciliation,
        monte_carlo=monte_carlo,
        current_route_execution_realism=current_route_execution_realism,
        kill_switch_report=kill_switch_report,
    )
    summary_path.write_text(json.dumps(_safe_json(summary), indent=2), encoding="utf-8")
    tracker_path.write_text(json.dumps(_safe_json(tracker), indent=2), encoding="utf-8")
    tracker_markdown_path.write_text(_format_tracker_markdown(tracker), encoding="utf-8")

    return ValidationExportResult(
        output_dir=destination,
        baseline_path=baseline_path,
        ledger_path=ledger_path,
        equity_snapshots_path=equity_snapshots_path,
        broker_reconciliation_path=broker_reconciliation_path,
        intraday_prediction_validation_path=intraday_prediction_validation_path,
        benchmark_report_path=benchmark_report_path,
        next_bar_replay_path=next_bar_replay_path,
        walk_forward_path=walk_forward_path,
        stress_matrix_path=stress_matrix_path,
        drawdown_report_path=drawdown_report_path,
        drawdown_decomposition_path=drawdown_decomposition_path,
        mark_to_market_report_path=mark_to_market_report_path,
        monte_carlo_path=monte_carlo_path,
        kill_switch_report_path=kill_switch_report_path,
        summary_path=summary_path,
        tracker_path=tracker_path,
        tracker_markdown_path=tracker_markdown_path,
        metrics=metrics,
    )
