from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.services import notes_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

MARKET_TIMEZONE = ZoneInfo("America/New_York")
TRANSACTION_COST_NOTE_OWNER = "automation-ai"
TRANSACTION_COST_HISTORY_LIMIT = 12
TRANSACTION_COST_NOTE_LIMIT = 250
TRANSACTION_COST_PERSONAL_PAPER_PROFILE = "personal_paper"
TRANSACTION_COST_POST_CLOSE_BUFFER_MINUTES = 15

TRANSACTION_COST_SETTINGS_DEFAULTS: dict[str, Any] = {
    "transaction_cost_calibration_enabled": True,
    "transaction_cost_calibration_auto_review_enabled": True,
    "transaction_cost_calibration_apply_to_live": False,
    "transaction_cost_calibration_min_samples": 20,
    "transaction_cost_calibration_stale_after_sessions": 5,
    "transaction_cost_calibration_max_candidate_penalty": 20.0,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on"}:
            return True
        if cleaned in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if pd.isna(parsed):
        return float(default)
    return float(parsed)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _clamp_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    return max(float(minimum), min(float(maximum), _coerce_float(value, default)))


def _clamp_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    return max(int(minimum), min(int(maximum), _coerce_int(value, default)))


def _normalize_profile_key(profile_key: str | None) -> str:
    return str(profile_key or TRANSACTION_COST_PERSONAL_PAPER_PROFILE).strip().lower()


def _profile_tag(profile_key: str) -> str:
    cleaned = _normalize_profile_key(profile_key).replace(":", "-") or TRANSACTION_COST_PERSONAL_PAPER_PROFILE
    return f"profile-{cleaned}"


def _session_day_for(now: datetime | None = None) -> str:
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(MARKET_TIMEZONE).date().isoformat()


def _session_bounds_utc(session_day: str) -> tuple[datetime, datetime]:
    day = date.fromisoformat(session_day)
    start_local = datetime.combine(day, time.min, tzinfo=MARKET_TIMEZONE)
    end_local = datetime.combine(day, time.max, tzinfo=MARKET_TIMEZONE)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _recent_trading_days(now: datetime, count: int) -> list[str]:
    cursor = now.astimezone(MARKET_TIMEZONE).date()
    days: list[str] = []
    while len(days) < max(1, int(count)):
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor = cursor.fromordinal(cursor.toordinal() - 1)
    return days


def transaction_cost_review_session_day_for(
    value: datetime | None = None,
    *,
    forced: bool = False,
) -> tuple[str, bool]:
    now = value or _utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(MARKET_TIMEZONE)
    session_day = local.date().isoformat()
    review_time = datetime.combine(
        local.date(),
        time(16, TRANSACTION_COST_POST_CLOSE_BUFFER_MINUTES),
        tzinfo=MARKET_TIMEZONE,
    )
    return session_day, bool(forced or local >= review_time)


def _review_time_for_session_day(session_day: str) -> datetime:
    local_review = datetime.combine(
        date.fromisoformat(session_day),
        time(16, TRANSACTION_COST_POST_CLOSE_BUFFER_MINUTES),
        tzinfo=MARKET_TIMEZONE,
    )
    return local_review.astimezone(timezone.utc)


def _next_trading_day_after(session_day: str) -> str:
    cursor = date.fromisoformat(session_day)
    while True:
        cursor = cursor.fromordinal(cursor.toordinal() + 1)
        if cursor.weekday() < 5:
            return cursor.isoformat()


def next_eligible_transaction_cost_review_at(value: datetime | None = None) -> datetime:
    now = value or _utc_now()
    session_day, review_open = transaction_cost_review_session_day_for(now)
    if review_open:
        return now
    return _review_time_for_session_day(session_day)


def next_eligible_transaction_cost_review_after_session(session_day: str) -> datetime:
    return _review_time_for_session_day(_next_trading_day_after(session_day))


def normalize_transaction_cost_calibration_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    return {
        "transaction_cost_calibration_enabled": _coerce_bool(
            state.get("transaction_cost_calibration_enabled"),
            bool(TRANSACTION_COST_SETTINGS_DEFAULTS["transaction_cost_calibration_enabled"]),
        ),
        "transaction_cost_calibration_auto_review_enabled": _coerce_bool(
            state.get("transaction_cost_calibration_auto_review_enabled"),
            bool(TRANSACTION_COST_SETTINGS_DEFAULTS["transaction_cost_calibration_auto_review_enabled"]),
        ),
        "transaction_cost_calibration_apply_to_live": _coerce_bool(
            state.get("transaction_cost_calibration_apply_to_live"),
            bool(TRANSACTION_COST_SETTINGS_DEFAULTS["transaction_cost_calibration_apply_to_live"]),
        ),
        "transaction_cost_calibration_min_samples": _clamp_int(
            state.get("transaction_cost_calibration_min_samples"),
            int(TRANSACTION_COST_SETTINGS_DEFAULTS["transaction_cost_calibration_min_samples"]),
            minimum=1,
            maximum=500,
        ),
        "transaction_cost_calibration_stale_after_sessions": _clamp_int(
            state.get("transaction_cost_calibration_stale_after_sessions"),
            int(TRANSACTION_COST_SETTINGS_DEFAULTS["transaction_cost_calibration_stale_after_sessions"]),
            minimum=1,
            maximum=60,
        ),
        "transaction_cost_calibration_max_candidate_penalty": _clamp_float(
            state.get("transaction_cost_calibration_max_candidate_penalty"),
            float(TRANSACTION_COST_SETTINGS_DEFAULTS["transaction_cost_calibration_max_candidate_penalty"]),
            minimum=0.0,
            maximum=100.0,
        ),
    }


def normalize_transaction_cost_calibration_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    history = [
        serialize_value(item)
        for item in list(runtime.get("transaction_cost_calibration_history") or [])[:TRANSACTION_COST_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "transaction_cost_calibration_last_report": serialize_value(
            runtime.get("transaction_cost_calibration_last_report") or {}
        ),
        "transaction_cost_calibration_last_note_id": str(
            runtime.get("transaction_cost_calibration_last_note_id") or ""
        ).strip()
        or None,
        "transaction_cost_calibration_note_session_day": str(
            runtime.get("transaction_cost_calibration_note_session_day") or ""
        ).strip()
        or None,
        "transaction_cost_calibration_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("transaction_cost_calibration_last_run_at"))
        ),
        "transaction_cost_calibration_last_scheduled_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("transaction_cost_calibration_last_scheduled_run_at"))
        ),
        "transaction_cost_calibration_last_scheduled_session_day": str(
            runtime.get("transaction_cost_calibration_last_scheduled_session_day") or ""
        ).strip()
        or None,
        "transaction_cost_calibration_next_eligible_run_at": (
            _serialize_datetime(_parse_datetime(runtime.get("transaction_cost_calibration_next_eligible_run_at")))
            or _serialize_datetime(next_eligible_transaction_cost_review_at())
        ),
        "transaction_cost_calibration_last_skipped_reason": str(
            runtime.get("transaction_cost_calibration_last_skipped_reason") or ""
        ).strip()
        or None,
        "transaction_cost_calibration_last_error": str(
            runtime.get("transaction_cost_calibration_last_error") or ""
        ).strip()
        or None,
        "transaction_cost_calibration_history": history,
    }


def build_transaction_cost_calibration_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    settings = normalize_transaction_cost_calibration_settings(state.get("settings"))
    runtime = normalize_transaction_cost_calibration_runtime(state.get("runtime"))
    report = dict(runtime.get("transaction_cost_calibration_last_report") or {})
    if not report:
        return {
            "status": "not_run" if settings["transaction_cost_calibration_enabled"] else "disabled",
            "label": "Not run" if settings["transaction_cost_calibration_enabled"] else "Disabled",
            "enabled": settings["transaction_cost_calibration_enabled"],
            "auto_review_enabled": settings["transaction_cost_calibration_auto_review_enabled"],
            "apply_to_live": settings["transaction_cost_calibration_apply_to_live"],
            "min_samples": settings["transaction_cost_calibration_min_samples"],
            "stale_after_sessions": settings["transaction_cost_calibration_stale_after_sessions"],
            "max_candidate_penalty": settings["transaction_cost_calibration_max_candidate_penalty"],
            "sample_count": 0,
            "related_note_id": runtime.get("transaction_cost_calibration_last_note_id"),
            "next_eligible_run_at": runtime.get("transaction_cost_calibration_next_eligible_run_at"),
            "history": runtime.get("transaction_cost_calibration_history") or [],
        }
    report.setdefault("enabled", settings["transaction_cost_calibration_enabled"])
    report.setdefault("auto_review_enabled", settings["transaction_cost_calibration_auto_review_enabled"])
    report.setdefault("apply_to_live", settings["transaction_cost_calibration_apply_to_live"])
    report.setdefault("min_samples", settings["transaction_cost_calibration_min_samples"])
    report.setdefault("stale_after_sessions", settings["transaction_cost_calibration_stale_after_sessions"])
    report.setdefault("max_candidate_penalty", settings["transaction_cost_calibration_max_candidate_penalty"])
    report["related_note_id"] = report.get("related_note_id") or runtime.get(
        "transaction_cost_calibration_last_note_id"
    )
    report["next_eligible_run_at"] = runtime.get("transaction_cost_calibration_next_eligible_run_at")
    report["last_scheduled_run_at"] = runtime.get("transaction_cost_calibration_last_scheduled_run_at")
    report["last_scheduled_session_day"] = runtime.get("transaction_cost_calibration_last_scheduled_session_day")
    report["last_skipped_reason"] = runtime.get("transaction_cost_calibration_last_skipped_reason")
    report["history"] = runtime.get("transaction_cost_calibration_history") or []
    return serialize_value(report)


def _owned_rows(frame: pd.DataFrame | None, *, tenant_id: str | None, profile_key: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    if "automation_origin" in result.columns:
        marker = result["automation_origin"].astype(str).str.strip().str.lower()
        result = result[marker.eq("trade_automation")]
    if tenant_id and "automation_tenant_id" in result.columns:
        scope = result["automation_tenant_id"].astype(str).str.strip()
        result = result[scope.eq(str(tenant_id).strip())]
    if profile_key and "automation_profile_key" in result.columns:
        profile = result["automation_profile_key"].astype(str).str.strip().str.lower()
        result = result[profile.eq(profile_key)]
    return result.copy()


def _windowed_closed_rows(frame: pd.DataFrame, *, session_days: list[str]) -> pd.DataFrame:
    if frame.empty or not session_days:
        return frame.copy()
    timestamps = pd.to_datetime(frame.get("closed_at", pd.Series(dtype=str)), errors="coerce", utc=True)
    if not timestamps.notna().any():
        return frame.copy()
    start, _ = _session_bounds_utc(session_days[-1])
    _, end = _session_bounds_utc(session_days[0])
    return frame[timestamps.ge(start) & timestamps.le(end)].copy()


def _row_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _row_float(row: dict[str, Any], *keys: str, default: float | None = 0.0) -> float | None:
    value = _row_value(row, *keys)
    if value is None:
        return default
    return _coerce_float(value, default or 0.0)


def _spread_bps(row: dict[str, Any]) -> float | None:
    direct = _row_float(
        row,
        "realized_spread_bps",
        "fill_spread_bps",
        "spread_bps",
        "accuracy_spread_bps",
        "daily_objective_spread_bps",
        "bid_ask_spread_bps",
        default=None,
    )
    if direct is not None:
        return abs(float(direct))
    spread_pct = _row_float(row, "spread_pct", "contract_spread_pct", default=None)
    if spread_pct is not None:
        return abs(float(spread_pct) * 10000.0)
    return None


def _estimated_cost_bps(row: dict[str, Any]) -> float | None:
    direct = _row_float(
        row,
        "estimated_cost_bps",
        "accuracy_estimated_cost_bps",
        "expected_cost_bps",
        "transaction_cost_adjusted_estimated_cost_bps",
        default=None,
    )
    if direct is not None:
        return max(0.0, float(direct))
    edge = _row_float(row, "accuracy_expected_edge_bps", "expected_edge_bps", "edge_bps", default=None)
    ratio = _row_float(row, "accuracy_edge_to_cost_ratio", "edge_to_cost_ratio", default=None)
    if edge is not None and ratio is not None and ratio > 0:
        return abs(float(edge)) / max(float(ratio), 0.0001)
    slippage = _row_float(row, "estimated_slippage_bps", "accuracy_estimated_slippage_bps", default=None)
    spread = _spread_bps(row)
    if slippage is not None or spread is not None:
        return max(0.0, float(slippage or 0.0) + float(spread or 0.0) / 2.0)
    return None


def _realized_slippage_bps(row: dict[str, Any]) -> float | None:
    value = _row_float(
        row,
        "realized_slippage_bps",
        "slippage_bps",
        "fill_slippage_bps",
        "entry_slippage_bps",
        "average_slippage_bps",
        default=None,
    )
    return abs(float(value)) if value is not None else None


def _realized_cost_bps(row: dict[str, Any]) -> float | None:
    direct = _row_float(row, "realized_cost_bps", "net_execution_cost_bps", default=None)
    if direct is not None:
        return max(0.0, float(direct))
    slippage = _realized_slippage_bps(row)
    spread = _spread_bps(row)
    if slippage is not None or spread is not None:
        return max(0.0, float(slippage or 0.0) + float(spread or 0.0) / 2.0)
    return None


def _expected_edge_bps(row: dict[str, Any]) -> float | None:
    value = _row_float(
        row,
        "transaction_cost_adjusted_expected_edge_bps",
        "accuracy_calibrated_expected_edge_bps",
        "accuracy_expected_edge_bps",
        "expected_edge_bps",
        "edge_bps",
        "forecast_edge_bps",
        default=None,
    )
    return abs(float(value)) if value is not None else None


def _session_bucket(row: dict[str, Any], fallback: str) -> str:
    bucket = str(_row_value(row, "session_bucket", "accuracy_session_bucket") or "").strip().lower()
    if bucket:
        return bucket
    closed_at = _parse_datetime(_row_value(row, "closed_at", "opened_at", "submitted_at"))
    if closed_at is None:
        return fallback
    local = closed_at.astimezone(MARKET_TIMEZONE)
    if local.hour < 9 or (local.hour == 9 and local.minute < 30):
        return "pre_market"
    if local.hour >= 16:
        return "after_hours"
    return "regular"


def _liquidity_bucket(value: Any) -> str:
    liquidity = _coerce_float(value, 0.0)
    if liquidity >= 50_000_000:
        return "high_liquidity"
    if liquidity >= 5_000_000:
        return "medium_liquidity"
    if liquidity > 0:
        return "thin_liquidity"
    return "unknown_liquidity"


def _pattern_key(row: dict[str, Any], *, fallback_session: str) -> str:
    pattern = str(_row_value(row, "accuracy_pattern_key", "setup_bucket", "pattern_key") or "").strip().lower()
    if pattern:
        return pattern
    bucket = str(_row_value(row, "proxy_correlation_bucket") or "unknown").strip().lower() or "unknown"
    instrument = str(_row_value(row, "automation_instrument_type", "instrument_type") or "equity").strip().lower()
    verdict = str(_row_value(row, "verdict", "forecast_direction", "thesis_direction") or "neutral").strip().lower()
    return f"{bucket}|{instrument}|{_session_bucket(row, fallback_session)}|{verdict}"


def _trade_cost_samples(frame: pd.DataFrame, *, fallback_session_day: str) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    samples: list[dict[str, Any]] = []
    for raw in frame.to_dict(orient="records"):
        row = dict(raw)
        ticker = str(_row_value(row, "ticker", "symbol") or "").strip().upper()
        if not ticker:
            continue
        position_cost = max(
            _coerce_float(_row_value(row, "position_cost", "notional", "entry_notional"), 0.0),
            0.0,
        )
        realized_pnl = _coerce_float(_row_value(row, "realized_pnl", "pnl"), 0.0)
        estimated_cost = _estimated_cost_bps(row)
        realized_cost = _realized_cost_bps(row)
        slippage = _realized_slippage_bps(row)
        spread = _spread_bps(row)
        expected_edge = _expected_edge_bps(row)
        session_bucket = _session_bucket(row, fallback_session_day)
        liquidity_value = _row_float(
            row,
            "average_dollar_volume",
            "avg_dollar_volume",
            "daily_dollar_volume",
            "dollar_volume",
            default=None,
        )
        error = realized_cost - estimated_cost if realized_cost is not None and estimated_cost is not None else None
        edge_after_cost_bps = realized_pnl / position_cost * 10000.0 if position_cost > 0 else None
        samples.append(
            {
                "trade_id": str(_row_value(row, "trade_id", "id") or "").strip() or None,
                "ticker": ticker,
                "pattern_key": _pattern_key(row, fallback_session=session_bucket),
                "session_bucket": session_bucket,
                "liquidity_bucket": _liquidity_bucket(liquidity_value),
                "estimated_cost_bps": round(estimated_cost, 4) if estimated_cost is not None else None,
                "realized_cost_bps": round(realized_cost, 4) if realized_cost is not None else None,
                "cost_error_bps": round(error, 4) if error is not None else None,
                "estimated_spread_bps": round(spread, 4) if spread is not None else None,
                "realized_spread_bps": round(spread, 4) if spread is not None else None,
                "slippage_bps": round(slippage, 4) if slippage is not None else None,
                "expected_edge_bps": round(expected_edge, 4) if expected_edge is not None else None,
                "edge_after_cost_bps": round(edge_after_cost_bps, 4) if edge_after_cost_bps is not None else None,
                "realized_pnl": round(realized_pnl, 4),
                "position_cost": round(position_cost, 4),
                "cost_negative": bool(expected_edge and expected_edge > 0 and realized_pnl < 0),
            }
        )
    return samples


def _order_event_summary(db: Session | None, *, tenant: Tenant, session_days: list[str]) -> dict[str, Any]:
    if db is None or not session_days:
        return {
            "event_count": 0,
            "filled_count": 0,
            "partial_count": 0,
            "error_count": 0,
            "fill_quality": "unknown",
        }
    start_at, _ = _session_bounds_utc(session_days[-1])
    _, end_at = _session_bounds_utc(session_days[0])
    try:
        rows = db.scalars(
            select(OrderEventRecord)
            .where(OrderEventRecord.tenant_id == tenant.id)
            .where(OrderEventRecord.created_at >= start_at, OrderEventRecord.created_at <= end_at)
        ).all()
    except Exception:
        rows = []
    filled = 0
    partial = 0
    errors = 0
    for row in rows:
        status = str(getattr(row, "status", "") or "").lower()
        event_key = str(getattr(row, "event_key", "") or "").lower()
        payload = getattr(row, "payload_json", None) or {}
        broker_status = str(payload.get("broker_status") or payload.get("status") or "").lower()
        token = f"{event_key}|{status}|{broker_status}"
        if "partial" in token:
            partial += 1
        if "fill" in token or "filled" in token or "closed" in token:
            filled += 1
        if any(marker in token for marker in ("error", "failed", "rejected", "blocked")):
            errors += 1
    if errors:
        quality = "weak"
    elif partial:
        quality = "partial"
    elif filled:
        quality = "clean"
    else:
        quality = "unknown"
    return {
        "event_count": len(rows),
        "filled_count": filled,
        "partial_count": partial,
        "error_count": errors,
        "fill_quality": quality,
    }


def _average(values: list[float]) -> float | None:
    cleaned = [float(value) for value in values if value is not None and pd.notna(value)]
    if not cleaned:
        return None
    return sum(cleaned) / len(cleaned)


def _coverage(samples: list[dict[str, Any]], key: str) -> dict[str, Any]:
    count = sum(1 for sample in samples if sample.get(key) is not None)
    total = len(samples)
    return {"count": count, "pct": round(count / total * 100.0, 2) if total else 0.0}


def _bucket_breakdown(samples: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        grouped.setdefault(str(sample.get(key) or "unknown"), []).append(sample)
    rows: list[dict[str, Any]] = []
    for bucket, items in grouped.items():
        errors = [float(item["cost_error_bps"]) for item in items if item.get("cost_error_bps") is not None]
        slippage = [float(item["slippage_bps"]) for item in items if item.get("slippage_bps") is not None]
        pnl_values = [float(item.get("realized_pnl") or 0.0) for item in items]
        rows.append(
            {
                "bucket": bucket,
                "sample_count": len(items),
                "average_cost_error_bps": round(_average(errors) or 0.0, 4) if errors else None,
                "average_slippage_bps": round(_average(slippage) or 0.0, 4) if slippage else None,
                "total_pnl": round(sum(pnl_values), 4),
                "cost_negative_count": sum(1 for item in items if item.get("cost_negative")),
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            _coerce_float(item.get("average_cost_error_bps"), 0.0),
            -_coerce_float(item.get("total_pnl"), 0.0),
            item["sample_count"],
        ),
        reverse=True,
    )


def _build_bucket_lookup(
    weak_symbols: list[dict[str, Any]],
    weak_setups: list[dict[str, Any]],
    weak_liquidity: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "symbols": {str(item.get("bucket") or "").upper(): item for item in weak_symbols if item.get("bucket")},
        "setups": {str(item.get("bucket") or "").lower(): item for item in weak_setups if item.get("bucket")},
        "liquidity": {str(item.get("bucket") or "").lower(): item for item in weak_liquidity if item.get("bucket")},
    }


def _candidate_history_coverage(runtime: dict[str, Any]) -> dict[str, Any]:
    history = [item for item in list(runtime.get("accuracy_candidate_history") or []) if isinstance(item, dict)]
    if not history:
        return {
            "candidate_count": 0,
            "edge_coverage_pct": 0.0,
            "spread_coverage_pct": 0.0,
            "cost_coverage_pct": 0.0,
        }
    edge = sum(1 for item in history if _row_value(item, "expected_edge_bps", "accuracy_expected_edge_bps"))
    spread = sum(1 for item in history if _row_value(item, "spread_bps", "accuracy_spread_bps"))
    cost = sum(1 for item in history if _row_value(item, "estimated_cost_bps", "expected_cost_bps"))
    total = len(history)
    return {
        "candidate_count": total,
        "edge_coverage_pct": round(edge / total * 100.0, 2),
        "spread_coverage_pct": round(spread / total * 100.0, 2),
        "cost_coverage_pct": round(cost / total * 100.0, 2),
    }


def build_transaction_cost_calibration_report(
    *,
    db: Session | None = None,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    owned_closed: pd.DataFrame | None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_profile_key = _normalize_profile_key(profile_key)
    settings_state = dict(state.get("settings") or {})
    settings = normalize_transaction_cost_calibration_settings(settings_state)
    runtime = dict(state.get("runtime") or {})
    session_day = _session_day_for(now)
    window_days = _recent_trading_days(now, int(settings["transaction_cost_calibration_stale_after_sessions"]))
    closed_frame = _windowed_closed_rows(owned_closed if owned_closed is not None else pd.DataFrame(), session_days=window_days)
    samples = _trade_cost_samples(closed_frame, fallback_session_day=session_day)
    cost_samples = [sample for sample in samples if sample.get("cost_error_bps") is not None]
    sample_count = len(cost_samples)
    min_samples = int(settings["transaction_cost_calibration_min_samples"])
    cost_errors = [float(item["cost_error_bps"]) for item in cost_samples]
    realized_costs = [float(item["realized_cost_bps"]) for item in cost_samples if item.get("realized_cost_bps") is not None]
    slippage_values = [float(item["slippage_bps"]) for item in samples if item.get("slippage_bps") is not None]
    spread_values = [float(item["realized_spread_bps"]) for item in samples if item.get("realized_spread_bps") is not None]
    average_cost_error = _average(cost_errors)
    average_realized_cost = _average(realized_costs)
    average_slippage = _average(slippage_values)
    worst_slippage = max(slippage_values) if slippage_values else None
    average_spread = _average(spread_values)
    spread_coverage = _coverage(samples, "realized_spread_bps")
    slippage_coverage = _coverage(samples, "slippage_bps")
    cost_coverage = _coverage(samples, "cost_error_bps")
    candidate_coverage = _candidate_history_coverage(runtime)
    symbol_rows = _bucket_breakdown(cost_samples, "ticker")
    setup_rows = _bucket_breakdown(cost_samples, "pattern_key")
    liquidity_rows = _bucket_breakdown(cost_samples, "liquidity_bucket")
    weak_symbol_rows = [
        item
        for item in symbol_rows
        if _coerce_float(item.get("average_cost_error_bps"), 0.0) > 10.0
        or _coerce_float(item.get("total_pnl"), 0.0) < 0.0
        or _coerce_int(item.get("cost_negative_count"), 0) > 0
    ][:8]
    weak_setup_rows = [
        item
        for item in setup_rows
        if _coerce_float(item.get("average_cost_error_bps"), 0.0) > 10.0
        or _coerce_float(item.get("total_pnl"), 0.0) < 0.0
        or _coerce_int(item.get("cost_negative_count"), 0) > 0
    ][:8]
    weak_liquidity_rows = [
        item
        for item in liquidity_rows
        if _coerce_float(item.get("average_cost_error_bps"), 0.0) > 10.0
        or _coerce_int(item.get("cost_negative_count"), 0) > 0
    ][:8]
    order_events = _order_event_summary(db, tenant=tenant, session_days=window_days)
    cost_negative_count = sum(1 for sample in samples if sample.get("cost_negative"))

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not settings["transaction_cost_calibration_enabled"]:
        status = "disabled"
        label = "Transaction cost calibration disabled"
    elif normalized_profile_key != TRANSACTION_COST_PERSONAL_PAPER_PROFILE and not settings[
        "transaction_cost_calibration_apply_to_live"
    ]:
        status = "not_applicable"
        label = "Paper scope only"
    elif sample_count < min_samples:
        status = "collecting"
        label = "Collecting cost samples"
        warnings.append(
            {
                "key": "insufficient_cost_samples",
                "detail": f"{sample_count} cost-calibrated fill(s) available; {min_samples} required.",
            }
        )
    elif (average_cost_error or 0.0) > 25.0 or cost_negative_count >= 2 or order_events["error_count"] > 0:
        status = "blocked"
        label = "Cost drift blocking"
    elif (average_cost_error or 0.0) > 10.0 or weak_symbol_rows or weak_setup_rows or (worst_slippage or 0.0) > 25.0:
        status = "warning"
        label = "Cost drift warning"
    else:
        status = "calibrated"
        label = "Cost model calibrated"

    if status == "blocked":
        blockers.append(
            {
                "key": "cost_drift_blocker",
                "detail": "Realized paper execution cost is materially worse than estimated cost.",
            }
        )
    if order_events["error_count"]:
        blockers.append(
            {
                "key": "fill_quality_error",
                "detail": f"{order_events['error_count']} order event error(s) were observed in the calibration window.",
            }
        )
    if weak_symbol_rows:
        warnings.append(
            {
                "key": "weak_symbols",
                "detail": f"{len(weak_symbol_rows)} symbol bucket(s) have poor realized cost or negative PnL.",
            }
        )
    if weak_setup_rows:
        warnings.append(
            {
                "key": "weak_setups",
                "detail": f"{len(weak_setup_rows)} setup bucket(s) have poor cost survival.",
            }
        )
    if candidate_coverage["candidate_count"] and candidate_coverage["cost_coverage_pct"] < 100:
        warnings.append(
            {
                "key": "candidate_cost_coverage_incomplete",
                "detail": (
                    f"Candidate cost coverage is {candidate_coverage['cost_coverage_pct']:.1f}%; "
                    "new paper candidates should carry estimated cost fields."
                ),
            }
        )

    recommendations: list[dict[str, Any]] = []
    skipped_changes: list[dict[str, Any]] = [
        {
            "field": "baseline_settings",
            "reason": "Transaction cost calibration is advisory/ranking-only; no baseline settings were changed.",
        }
    ]
    if status in {"blocked", "warning"}:
        recommendations.extend(
            [
                {
                    "field": "candidate_ranking",
                    "direction": "penalize_weak_cost_buckets",
                    "reason": "Weak realized cost buckets should receive bounded candidate-score penalties.",
                },
                {
                    "field": "min_edge_to_cost_ratio",
                    "direction": "tighten_or_hold",
                    "reason": "Estimated edge must survive observed paper costs before capacity increases.",
                },
                {
                    "field": "max_spread_bps",
                    "direction": "tighten_or_hold",
                    "reason": "Spread and slippage drift are reducing net edge quality.",
                },
            ]
        )
    elif status == "calibrated":
        recommendations.append(
            {
                "field": "candidate_ranking",
                "direction": "use_calibrated_net_cost",
                "reason": "Paper fills support cost-calibrated ranking for the next session.",
            }
        )
    else:
        recommendations.append(
            {
                "field": "sample_collection",
                "direction": "continue",
                "reason": "Collect more paper fills before applying strong cost penalties.",
            }
        )

    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": normalized_profile_key,
            "session_day": session_day,
            "evaluated_at": _serialize_datetime(now),
            "run_source": str(run_source or "manual").strip().lower() or "manual",
            "session_window": window_days,
            "window_sessions": len(window_days),
            "sample_count": sample_count,
            "min_samples": min_samples,
            "estimated_vs_realized_cost_error_bps": round(average_cost_error, 4)
            if average_cost_error is not None
            else None,
            "average_realized_cost_bps": round(average_realized_cost, 4) if average_realized_cost is not None else None,
            "slippage_error_bps": round(average_slippage, 4) if average_slippage is not None else None,
            "worst_slippage_bps": round(worst_slippage, 4) if worst_slippage is not None else None,
            "spread_error_bps": round(average_spread, 4) if average_spread is not None else None,
            "spread_coverage": spread_coverage,
            "slippage_coverage": slippage_coverage,
            "cost_coverage": cost_coverage,
            "candidate_coverage": candidate_coverage,
            "fill_quality": order_events["fill_quality"],
            "order_event_summary": order_events,
            "liquidity_bucket_reliability": liquidity_rows[:8],
            "weak_symbols": weak_symbol_rows,
            "weak_setups": weak_setup_rows,
            "weak_liquidity_buckets": weak_liquidity_rows,
            "bucket_lookup": _build_bucket_lookup(weak_symbol_rows, weak_setup_rows, weak_liquidity_rows),
            "cost_negative_count": cost_negative_count,
            "recommendations": recommendations[:6],
            "skipped_changes": skipped_changes,
            "blockers": blockers,
            "warnings": warnings,
            "enabled": bool(settings["transaction_cost_calibration_enabled"]),
            "auto_review_enabled": bool(settings["transaction_cost_calibration_auto_review_enabled"]),
            "apply_to_live": bool(settings["transaction_cost_calibration_apply_to_live"]),
        }
    )


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=TRANSACTION_COST_NOTE_OWNER,
            limit=TRANSACTION_COST_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required = {
        "automation-ai",
        "transaction-cost-calibration",
        "paper-evidence",
        "accuracy-calibration",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags") or []}
        if required.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _format_rows(items: list[dict[str, Any]], *, empty: str) -> list[str]:
    if not items:
        return [f"- {empty}"]
    lines: list[str] = []
    for item in items[:8]:
        detail = item.get("detail") or item.get("reason")
        summary = ", ".join(
            f"{key}={value}"
            for key, value in item.items()
            if key not in {"detail", "reason", "bucket_lookup"} and value not in (None, "", [], {})
        )
        if detail:
            summary = f"{summary}; {detail}" if summary else str(detail)
        lines.append(f"- {summary or item}")
    return lines


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    lines = [
        f"Transaction cost calibration for {getattr(tenant, 'name', None) or getattr(tenant, 'slug', 'tenant')}",
        f"Profile: {profile_key}",
        f"Session: {report.get('session_day')}",
        f"Status: {report.get('status')} - {report.get('label')}",
        f"Samples: {report.get('sample_count')}/{report.get('min_samples')}",
        f"Cost error: {report.get('estimated_vs_realized_cost_error_bps')} bps",
        f"Slippage: {report.get('slippage_error_bps')} bps",
        f"Spread: {report.get('spread_error_bps')} bps",
        f"Fill quality: {report.get('fill_quality')}",
        "",
        "Weak symbols",
    ]
    lines.extend(_format_rows(list(report.get("weak_symbols") or []), empty="No weak symbol buckets."))
    lines.extend(["", "Weak setup buckets"])
    lines.extend(_format_rows(list(report.get("weak_setups") or []), empty="No weak setup buckets."))
    lines.extend(["", "Liquidity buckets"])
    lines.extend(_format_rows(list(report.get("liquidity_bucket_reliability") or []), empty="No liquidity evidence."))
    lines.extend(["", "Recommendations"])
    lines.extend(_format_rows(list(report.get("recommendations") or []), empty="No recommendations."))
    lines.extend(["", "Skipped changes"])
    lines.extend(_format_rows(list(report.get("skipped_changes") or []), empty="No skipped changes."))
    if report.get("blockers"):
        lines.extend(["", "Blockers"])
        lines.extend(_format_rows(list(report.get("blockers") or []), empty="No blockers."))
    if report.get("warnings"):
        lines.extend(["", "Warnings"])
        lines.extend(_format_rows(list(report.get("warnings") or []), empty="No warnings."))
    return "\n".join(lines).strip()


def _sync_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "transaction-cost-calibration",
        "paper-evidence",
        "accuracy-calibration",
        _profile_tag(profile_key),
        f"session-{session_day}",
    ]
    title = f"Paper transaction cost calibration - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = (
        str(report.get("related_note_id") or report.get("note_id") or "").strip()
        or _find_existing_note_id(profile_key, session_day)
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": TRANSACTION_COST_NOTE_OWNER,
        "note_type": "risk_review",
        "priority": "high" if report.get("blockers") else "medium",
    }
    if note_id:
        try:
            updated = notes_service.update_note(note_id, payload)
            return str(updated.get("id") or note_id)
        except Exception:
            note_id = None
    try:
        created = notes_service.create_note(**payload)
        return str(created.get("id") or "").strip() or None
    except Exception:
        return None


def _persist_report(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    report: dict[str, Any],
    actor: Any = None,
    write_note: bool = False,
) -> dict[str, Any]:
    normalized_profile_key = _normalize_profile_key(profile_key)
    if write_note:
        note_id = _sync_note(tenant=tenant, profile_key=normalized_profile_key, report=report)
        if note_id:
            report["note_id"] = note_id
            report["related_note_id"] = note_id
    runtime = state.setdefault("runtime", {})
    summary_keys = {
        "status",
        "label",
        "profile_key",
        "session_day",
        "evaluated_at",
        "run_source",
        "session_window",
        "window_sessions",
        "sample_count",
        "min_samples",
        "estimated_vs_realized_cost_error_bps",
        "average_realized_cost_bps",
        "slippage_error_bps",
        "worst_slippage_bps",
        "spread_error_bps",
        "spread_coverage",
        "slippage_coverage",
        "cost_coverage",
        "candidate_coverage",
        "fill_quality",
        "order_event_summary",
        "liquidity_bucket_reliability",
        "weak_symbols",
        "weak_setups",
        "weak_liquidity_buckets",
        "bucket_lookup",
        "cost_negative_count",
        "recommendations",
        "skipped_changes",
        "blockers",
        "warnings",
        "note_id",
        "related_note_id",
        "enabled",
        "auto_review_enabled",
        "apply_to_live",
    }
    runtime["transaction_cost_calibration_last_report"] = serialize_value(
        {key: report.get(key) for key in summary_keys if key in report}
    )
    runtime["transaction_cost_calibration_last_run_at"] = report.get("evaluated_at")
    runtime["transaction_cost_calibration_last_note_id"] = report.get("related_note_id") or report.get("note_id")
    runtime["transaction_cost_calibration_note_session_day"] = report.get("session_day")
    runtime["transaction_cost_calibration_last_error"] = None
    history = list(runtime.get("transaction_cost_calibration_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "sample_count": report.get("sample_count"),
            "cost_error_bps": report.get("estimated_vs_realized_cost_error_bps"),
            "slippage_bps": report.get("slippage_error_bps"),
            "note_id": report.get("related_note_id") or report.get("note_id"),
            "run_source": report.get("run_source"),
        },
    )
    runtime["transaction_cost_calibration_history"] = serialize_value(history[:TRANSACTION_COST_HISTORY_LIMIT])
    if db is not None and write_note:
        record_audit_event(
            db,
            event_type="trade_automation.transaction_cost_calibrated",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": normalized_profile_key,
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "sample_count": report.get("sample_count"),
                "cost_error_bps": report.get("estimated_vs_realized_cost_error_bps"),
                "note_id": report.get("related_note_id") or report.get("note_id"),
                "run_source": report.get("run_source"),
            },
        )
    return serialize_value(report)


def evaluate_transaction_cost_calibration_cycle(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    owned_closed: pd.DataFrame | None = None,
    now: datetime | None = None,
    actor: Any = None,
) -> dict[str, Any]:
    report = build_transaction_cost_calibration_report(
        db=db,
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        owned_closed=owned_closed if owned_closed is not None else pd.DataFrame(),
        now=now,
        run_source="cycle",
    )
    return _persist_report(
        db,
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        report=report,
        actor=actor,
        write_note=False,
    )


def run_transaction_cost_calibration_review(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_profile_key = _normalize_profile_key(profile_key)
    tenant_id = str(getattr(tenant, "id", "") or "").strip()
    closed_frame = _owned_rows(sdm.read_closed_trades(), tenant_id=tenant_id, profile_key=normalized_profile_key)
    _ = linked_account
    report = build_transaction_cost_calibration_report(
        db=db,
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        owned_closed=closed_frame,
        now=now,
        run_source=run_source,
    )
    return _persist_report(
        db,
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        report=report,
        actor=actor,
        write_note=True,
    )


def _candidate_numeric(candidate: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = candidate.get(key)
        if value is None or str(value).strip() == "":
            continue
        parsed = _coerce_float(value, 0.0)
        if pd.notna(parsed):
            return float(parsed)
    return None


def _candidate_liquidity_bucket(candidate: dict[str, Any]) -> str:
    liquidity = _candidate_numeric(
        candidate,
        "average_dollar_volume",
        "avg_dollar_volume",
        "average_daily_dollar_volume",
        "dollar_volume",
    )
    if liquidity is None:
        volume = _candidate_numeric(candidate, "average_volume", "avg_volume", "volume")
        price = _candidate_numeric(candidate, "live_price", "close", "current_underlying_price")
        if volume is not None and price is not None:
            liquidity = volume * price
    return _liquidity_bucket(liquidity)


def score_transaction_cost_candidate(
    candidate: dict[str, Any],
    *,
    state: dict[str, Any],
    current_equity: float | None = None,
) -> dict[str, Any]:
    settings = normalize_transaction_cost_calibration_settings((state or {}).get("settings"))
    profile_key = _normalize_profile_key((state or {}).get("profile_key") or TRANSACTION_COST_PERSONAL_PAPER_PROFILE)
    if not settings["transaction_cost_calibration_enabled"]:
        return {}
    if profile_key != TRANSACTION_COST_PERSONAL_PAPER_PROFILE and not settings["transaction_cost_calibration_apply_to_live"]:
        return {}
    runtime = dict((state or {}).get("runtime") or {})
    report = dict(runtime.get("transaction_cost_calibration_last_report") or {})
    sample_count = _coerce_int(report.get("sample_count"), 0)
    min_samples = int(settings["transaction_cost_calibration_min_samples"])
    base_score = _candidate_numeric(candidate, "accuracy_calibrated_score", "portfolio_score", "ranking_score", "setup_score") or 0.0
    estimated_cost = _candidate_numeric(candidate, "estimated_cost_bps", "expected_cost_bps") or 0.0
    edge = _candidate_numeric(candidate, "accuracy_calibrated_expected_edge_bps", "expected_edge_bps", "edge_bps") or 0.0
    if sample_count < min_samples:
        return {
            "transaction_cost_calibrated_score": round(base_score, 2),
            "transaction_cost_candidate_penalty": 0.0,
            "transaction_cost_adjusted_estimated_cost_bps": round(max(estimated_cost, 0.0), 4),
            "transaction_cost_adjusted_expected_edge_bps": round(max(edge, 0.0), 4),
            "transaction_cost_rank_reason": "Collecting transaction-cost samples; no strong cost penalty applied.",
        }
    lookup = dict(report.get("bucket_lookup") or {})
    symbols = dict(lookup.get("symbols") or {})
    setups = dict(lookup.get("setups") or {})
    liquidity = dict(lookup.get("liquidity") or {})
    ticker = str(candidate.get("ticker") or candidate.get("symbol") or "").strip().upper()
    setup = str(candidate.get("setup_bucket") or candidate.get("accuracy_pattern_key") or "").strip().lower()
    liquidity_bucket = _candidate_liquidity_bucket(candidate)
    max_penalty = float(settings["transaction_cost_calibration_max_candidate_penalty"])
    penalty = 0.0
    reasons: list[str] = []
    for key, bucket_map, reason in (
        (ticker, symbols, "weak symbol"),
        (setup, setups, "weak setup"),
        (liquidity_bucket, liquidity, "weak liquidity"),
    ):
        bucket = bucket_map.get(key) if key else None
        if not isinstance(bucket, dict):
            continue
        cost_error = max(_coerce_float(bucket.get("average_cost_error_bps"), 0.0), 0.0)
        cost_negative = _coerce_int(bucket.get("cost_negative_count"), 0)
        pnl_drag = 4.0 if _coerce_float(bucket.get("total_pnl"), 0.0) < 0 else 0.0
        bucket_penalty = min(max_penalty, cost_error * 0.7 + cost_negative * 3.0 + pnl_drag)
        if bucket_penalty > 0:
            penalty = max(penalty, bucket_penalty)
            reasons.append(reason)
    avg_cost_error = max(_coerce_float(report.get("estimated_vs_realized_cost_error_bps"), 0.0), 0.0)
    if avg_cost_error > 0:
        penalty = max(penalty, min(max_penalty, avg_cost_error * 0.35))
        if avg_cost_error > 10:
            reasons.append("cost drift")
    penalty = min(max_penalty, max(0.0, penalty))
    adjusted_cost = max(estimated_cost + penalty, 0.0)
    adjusted_edge = max(edge - penalty, 0.0)
    ratio = adjusted_edge / adjusted_cost if adjusted_cost > 0 else None
    _ = current_equity
    return {
        "transaction_cost_calibrated_score": round(max(0.0, base_score - penalty), 2),
        "transaction_cost_candidate_penalty": round(penalty, 2),
        "transaction_cost_adjusted_estimated_cost_bps": round(adjusted_cost, 4),
        "transaction_cost_adjusted_expected_edge_bps": round(adjusted_edge, 4),
        "transaction_cost_adjusted_edge_to_cost_ratio": round(ratio, 4) if ratio is not None else None,
        "transaction_cost_rank_reason": (
            "Transaction-cost penalty applied for " + ", ".join(sorted(set(reasons)))
            if reasons
            else "Transaction-cost calibration found no weak bucket penalty."
        ),
    }


def apply_transaction_cost_candidate_overlay(
    candidates: list[dict[str, Any]],
    *,
    state: dict[str, Any],
    current_equity: float | None = None,
) -> list[dict[str, Any]]:
    if not candidates:
        return candidates
    annotated: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        item.update(score_transaction_cost_candidate(item, state=state, current_equity=current_equity))
        annotated.append(item)
    return annotated
