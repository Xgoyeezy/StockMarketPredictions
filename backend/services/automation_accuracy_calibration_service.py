from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, Tenant
from backend.services import notes_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

MARKET_TIMEZONE = ZoneInfo("America/New_York")
ACCURACY_NOTE_OWNER = "automation-ai"
ACCURACY_HISTORY_LIMIT = 12
ACCURACY_CANDIDATE_HISTORY_LIMIT = 250
ACCURACY_NOTE_LIMIT = 250
ACCURACY_PERSONAL_PAPER_PROFILE = "personal_paper"

ACCURACY_CALIBRATION_SETTINGS_DEFAULTS: dict[str, Any] = {
    "accuracy_calibration_enabled": True,
    "accuracy_calibration_apply_to_live": False,
    "accuracy_calibration_min_samples": 20,
    "accuracy_calibration_stale_after_sessions": 5,
    "accuracy_calibration_max_candidate_penalty": 25.0,
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


def _session_day_for(now: datetime | None = None) -> str:
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(MARKET_TIMEZONE).date().isoformat()


def _session_bounds_utc(session_day: str) -> tuple[datetime, datetime]:
    day = datetime.strptime(session_day, "%Y-%m-%d").date()
    start_local = datetime.combine(day, time.min, tzinfo=MARKET_TIMEZONE)
    end_local = datetime.combine(day, time.max, tzinfo=MARKET_TIMEZONE)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _profile_tag(profile_key: str) -> str:
    cleaned = str(profile_key or "").strip().lower().replace(":", "-") or ACCURACY_PERSONAL_PAPER_PROFILE
    return f"profile-{cleaned}"


def _normalize_profile_key(profile_key: str | None) -> str:
    return str(profile_key or ACCURACY_PERSONAL_PAPER_PROFILE).strip().lower()


def normalize_accuracy_calibration_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    return {
        "accuracy_calibration_enabled": _coerce_bool(
            state.get("accuracy_calibration_enabled"),
            bool(ACCURACY_CALIBRATION_SETTINGS_DEFAULTS["accuracy_calibration_enabled"]),
        ),
        "accuracy_calibration_apply_to_live": _coerce_bool(
            state.get("accuracy_calibration_apply_to_live"),
            bool(ACCURACY_CALIBRATION_SETTINGS_DEFAULTS["accuracy_calibration_apply_to_live"]),
        ),
        "accuracy_calibration_min_samples": _clamp_int(
            state.get("accuracy_calibration_min_samples"),
            int(ACCURACY_CALIBRATION_SETTINGS_DEFAULTS["accuracy_calibration_min_samples"]),
            minimum=1,
            maximum=500,
        ),
        "accuracy_calibration_stale_after_sessions": _clamp_int(
            state.get("accuracy_calibration_stale_after_sessions"),
            int(ACCURACY_CALIBRATION_SETTINGS_DEFAULTS["accuracy_calibration_stale_after_sessions"]),
            minimum=1,
            maximum=60,
        ),
        "accuracy_calibration_max_candidate_penalty": _clamp_float(
            state.get("accuracy_calibration_max_candidate_penalty"),
            float(ACCURACY_CALIBRATION_SETTINGS_DEFAULTS["accuracy_calibration_max_candidate_penalty"]),
            minimum=0.0,
            maximum=100.0,
        ),
    }


def normalize_accuracy_calibration_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    history = [
        serialize_value(item)
        for item in list(runtime.get("accuracy_calibration_history") or [])[:ACCURACY_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    candidate_history = [
        serialize_value(item)
        for item in list(runtime.get("accuracy_candidate_history") or [])[:ACCURACY_CANDIDATE_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "accuracy_calibration_last_report": serialize_value(runtime.get("accuracy_calibration_last_report") or {}),
        "accuracy_calibration_last_note_id": str(runtime.get("accuracy_calibration_last_note_id") or "").strip() or None,
        "accuracy_calibration_note_session_day": str(runtime.get("accuracy_calibration_note_session_day") or "").strip()
        or None,
        "accuracy_calibration_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("accuracy_calibration_last_run_at"))
        ),
        "accuracy_calibration_last_error": str(runtime.get("accuracy_calibration_last_error") or "").strip() or None,
        "accuracy_calibration_history": history,
        "accuracy_candidate_history": candidate_history,
    }


def build_accuracy_calibration_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    settings = normalize_accuracy_calibration_settings(state.get("settings"))
    runtime = normalize_accuracy_calibration_runtime(state.get("runtime"))
    report = dict(runtime.get("accuracy_calibration_last_report") or {})
    if not report:
        return {
            "status": "not_run" if settings["accuracy_calibration_enabled"] else "disabled",
            "label": "Not run" if settings["accuracy_calibration_enabled"] else "Disabled",
            "enabled": settings["accuracy_calibration_enabled"],
            "apply_to_live": settings["accuracy_calibration_apply_to_live"],
            "min_samples": settings["accuracy_calibration_min_samples"],
            "stale_after_sessions": settings["accuracy_calibration_stale_after_sessions"],
            "max_candidate_penalty": settings["accuracy_calibration_max_candidate_penalty"],
            "sample_count": 0,
            "decision_pnl_accuracy": None,
            "calibrated_expectancy": None,
            "related_note_id": runtime.get("accuracy_calibration_last_note_id"),
            "history": runtime.get("accuracy_calibration_history") or [],
        }
    report.setdefault("enabled", settings["accuracy_calibration_enabled"])
    report.setdefault("apply_to_live", settings["accuracy_calibration_apply_to_live"])
    report.setdefault("min_samples", settings["accuracy_calibration_min_samples"])
    report.setdefault("stale_after_sessions", settings["accuracy_calibration_stale_after_sessions"])
    report.setdefault("max_candidate_penalty", settings["accuracy_calibration_max_candidate_penalty"])
    report["related_note_id"] = report.get("related_note_id") or runtime.get("accuracy_calibration_last_note_id")
    report["history"] = runtime.get("accuracy_calibration_history") or []
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


def _closed_rows_for_session(frame: pd.DataFrame, *, session_day: str | None) -> pd.DataFrame:
    if frame.empty or not session_day:
        return frame.copy()
    timestamps = pd.to_datetime(frame.get("closed_at", pd.Series(dtype=str)), errors="coerce", utc=True)
    if not timestamps.notna().any():
        return frame.copy()
    start, end = _session_bounds_utc(session_day)
    return frame[timestamps.ge(start) & timestamps.le(end)].copy()


def _normalize_confidence(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    parsed = _coerce_float(value, 0.0)
    if parsed > 1.0:
        parsed = parsed / 100.0
    return max(0.0, min(1.0, parsed))


def _row_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _row_float(row: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    value = _row_value(row, *keys)
    return _coerce_float(value, default)


def _candidate_direction(row: dict[str, Any]) -> str:
    raw = str(_row_value(row, "thesis_direction", "verdict", "forecast_direction", "direction") or "").strip().upper()
    if raw in {"BULLISH", "LONG", "BUY", "UP"}:
        return "BULLISH"
    if raw in {"BEARISH", "SHORT", "SELL", "DOWN"}:
        return "BEARISH"
    probability = _normalize_confidence(_row_value(row, "probability_up", "forecast_probability_up"))
    if probability is not None:
        return "BULLISH" if probability >= 0.5 else "BEARISH"
    return raw or "UNKNOWN"


def _candidate_confidence(row: dict[str, Any]) -> float | None:
    direct = _normalize_confidence(
        _row_value(
            row,
            "accuracy_forecast_confidence",
            "forecast_confidence",
            "confidence_score",
            "confidence",
        )
    )
    if direct is not None:
        return direct
    probability = _normalize_confidence(_row_value(row, "probability_up", "forecast_probability_up"))
    if probability is None:
        return None
    direction = _candidate_direction(row)
    return probability if direction == "BULLISH" else 1.0 - probability


def _candidate_pattern(row: dict[str, Any]) -> str:
    bucket = str(_row_value(row, "accuracy_pattern_key", "proxy_correlation_bucket", "correlation_bucket") or "unknown").strip().lower()
    instrument = str(_row_value(row, "automation_instrument_type", "instrument_type") or "equity").strip().lower()
    session = str(_row_value(row, "session_label", "market_session", "time_of_day_bucket") or "session").strip().lower()
    direction = _candidate_direction(row).lower()
    return "|".join(part.replace(" ", "_") for part in (bucket, instrument, session, direction) if part)


def _candidate_summary(row: dict[str, Any], *, selected: bool, now: datetime | None = None, cycle_id: str | None = None) -> dict[str, Any]:
    confidence = _candidate_confidence(row)
    edge_bps = _row_float(row, "accuracy_expected_edge_bps", "expected_edge_bps", "edge_bps", "forecast_edge_bps")
    expected_pnl = _row_float(
        row,
        "accuracy_expected_pnl",
        "daily_objective_expected_pnl",
        "expected_pnl",
        default=max(_row_float(row, "projected_position_cost", "position_cost") * max(edge_bps, 0.0) / 10000.0, 0.0),
    )
    return serialize_value(
        {
            "at": _serialize_datetime(now or _utc_now()),
            "cycle_id": cycle_id,
            "ticker": str(_row_value(row, "ticker", "symbol") or "").strip().upper() or None,
            "instrument_type": str(_row_value(row, "automation_instrument_type", "instrument_type") or "equity").strip().lower(),
            "selected": bool(selected),
            "auto_entry_eligible": _coerce_bool(row.get("auto_entry_eligible"), False),
            "direction": _candidate_direction(row),
            "confidence": confidence,
            "pattern_key": _candidate_pattern(row),
            "portfolio_score": _row_float(row, "portfolio_score", "ranking_score", "setup_score"),
            "execution_score": _row_float(row, "execution_score", "ranking_score", "setup_score"),
            "edge_to_cost_ratio": _row_float(row, "edge_to_cost_ratio"),
            "expected_edge_bps": edge_bps,
            "expected_pnl": expected_pnl,
            "spread_bps": _row_float(row, "daily_objective_spread_bps", "spread_bps", "bid_ask_spread_bps"),
            "liquidity": _row_float(row, "average_dollar_volume", "avg_dollar_volume", "dollar_volume"),
            "rank": row.get("portfolio_rank") or row.get("board_rank"),
            "reject_reason": str(row.get("reject_reason") or "").strip() or None,
        }
    )


def record_candidate_snapshot(
    state: dict[str, Any],
    *,
    candidates: list[dict[str, Any]],
    now: datetime | None = None,
    cycle_id: str | None = None,
    limit: int = 25,
) -> None:
    if not candidates:
        return
    settings = normalize_accuracy_calibration_settings((state or {}).get("settings"))
    if not settings["accuracy_calibration_enabled"]:
        return
    runtime = state.setdefault("runtime", {})
    history = list(runtime.get("accuracy_candidate_history") or [])
    for candidate in list(candidates)[: max(1, int(limit))]:
        history.insert(0, _candidate_summary(candidate, selected=False, now=now, cycle_id=cycle_id))
    runtime["accuracy_candidate_history"] = serialize_value(history[:ACCURACY_CANDIDATE_HISTORY_LIMIT])


def record_selected_candidate(
    state: dict[str, Any],
    *,
    candidate: dict[str, Any],
    now: datetime | None = None,
    cycle_id: str | None = None,
) -> None:
    settings = normalize_accuracy_calibration_settings((state or {}).get("settings"))
    if not settings["accuracy_calibration_enabled"]:
        return
    runtime = state.setdefault("runtime", {})
    history = list(runtime.get("accuracy_candidate_history") or [])
    history.insert(0, _candidate_summary(candidate, selected=True, now=now, cycle_id=cycle_id))
    runtime["accuracy_candidate_history"] = serialize_value(history[:ACCURACY_CANDIDATE_HISTORY_LIMIT])


def build_accuracy_marker_fields(candidate: dict[str, Any]) -> dict[str, Any]:
    summary = _candidate_summary(candidate, selected=True)
    return {
        "accuracy_pattern_key": summary.get("pattern_key"),
        "accuracy_forecast_direction": summary.get("direction"),
        "accuracy_forecast_confidence": summary.get("confidence"),
        "accuracy_expected_edge_bps": summary.get("expected_edge_bps"),
        "accuracy_expected_pnl": summary.get("expected_pnl"),
        "accuracy_edge_to_cost_ratio": summary.get("edge_to_cost_ratio"),
        "accuracy_spread_bps": summary.get("spread_bps"),
        "accuracy_selected_at": summary.get("at"),
    }


def _row_slippage_bps(row: dict[str, Any]) -> float:
    return abs(
        _row_float(
            row,
            "slippage_bps",
            "average_slippage_bps",
            "entry_slippage_bps",
            "accuracy_slippage_bps",
        )
    )


def _selected_outcomes(closed_frame: pd.DataFrame) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for row in closed_frame.to_dict(orient="records"):
        pnl = _row_float(row, "realized_pnl")
        position_cost = max(_row_float(row, "position_cost", "projected_position_cost", "max_risk_dollars"), 1.0)
        confidence = _candidate_confidence(row)
        outcome = 1.0 if pnl > 0 else 0.0
        confidence_error = abs(confidence - outcome) if confidence is not None else None
        expected_edge_bps = _row_float(row, "accuracy_expected_edge_bps", "expected_edge_bps", "edge_bps")
        realized_bps = pnl / position_cost * 10000.0
        edge_error_bps = abs(expected_edge_bps - realized_bps) if expected_edge_bps else None
        outcomes.append(
            {
                "ticker": str(_row_value(row, "ticker", "symbol") or "").strip().upper() or None,
                "pattern_key": _candidate_pattern(row),
                "direction": _candidate_direction(row),
                "confidence": confidence,
                "confidence_error": confidence_error,
                "expected_edge_bps": expected_edge_bps,
                "realized_bps": realized_bps,
                "edge_error_bps": edge_error_bps,
                "realized_pnl": pnl,
                "position_cost": position_cost,
                "slippage_bps": _row_slippage_bps(row),
                "max_adverse_excursion": _row_float(row, "max_adverse_excursion", "max_adverse_excursion_pct"),
                "max_favorable_excursion": _row_float(row, "max_favorable_excursion", "max_favorable_excursion_pct"),
                "helped_daily_objective": pnl > 0,
            }
        )
    return outcomes


def _pattern_breakdown(outcomes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for outcome in outcomes:
        grouped.setdefault(str(outcome.get("pattern_key") or "unknown"), []).append(outcome)
    stats: list[dict[str, Any]] = []
    lookup: dict[str, dict[str, Any]] = {}
    for key, rows in grouped.items():
        pnl_values = [_coerce_float(item.get("realized_pnl"), 0.0) for item in rows]
        slippage_values = [_coerce_float(item.get("slippage_bps"), 0.0) for item in rows if item.get("slippage_bps") is not None]
        trades = len(rows)
        wins = sum(1 for value in pnl_values if value > 0)
        expectancy = sum(pnl_values) / trades if trades else 0.0
        item = {
            "pattern_key": key,
            "sample_count": trades,
            "hit_rate": round(wins / trades, 4) if trades else 0.0,
            "expectancy": round(expectancy, 4),
            "total_pnl": round(sum(pnl_values), 4),
            "average_slippage_bps": round(sum(slippage_values) / len(slippage_values), 4) if slippage_values else None,
        }
        lookup[key] = item
        stats.append(item)
    best = sorted(stats, key=lambda item: (item["expectancy"], item["hit_rate"], item["sample_count"]), reverse=True)[:6]
    weak = sorted(stats, key=lambda item: (item["expectancy"], item["hit_rate"], -item["sample_count"]))[:6]
    return best, weak, lookup


def _rejected_candidate_summary(runtime: dict[str, Any], *, session_day: str | None) -> dict[str, Any]:
    history = [item for item in list(runtime.get("accuracy_candidate_history") or []) if isinstance(item, dict)]
    if session_day:
        history = [item for item in history if str(item.get("at") or "")[:10] == session_day]
    selected = [item for item in history if _coerce_bool(item.get("selected"), False)]
    rejected = [item for item in history if not _coerce_bool(item.get("selected"), False)]
    rejected_expected = [_coerce_float(item.get("expected_pnl"), 0.0) for item in rejected]
    selected_expected = [_coerce_float(item.get("expected_pnl"), 0.0) for item in selected]
    selected_avg = sum(selected_expected) / len(selected_expected) if selected_expected else 0.0
    rejected_avg = sum(rejected_expected) / len(rejected_expected) if rejected_expected else 0.0
    missed = [
        item
        for item in rejected
        if _coerce_float(item.get("expected_pnl"), 0.0) > max(selected_avg, 0.0)
        and _coerce_bool(item.get("auto_entry_eligible"), False)
    ]
    return {
        "selected_candidate_count": len(selected),
        "rejected_candidate_count": len(rejected),
        "selected_expected_pnl_avg": round(selected_avg, 4),
        "rejected_expected_pnl_avg": round(rejected_avg, 4),
        "selected_vs_rejected_delta": round(selected_avg - rejected_avg, 4),
        "missed_opportunity_count": len(missed),
        "missed_opportunities": serialize_value(missed[:6]),
    }


def build_accuracy_calibration_report(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    owned_closed: pd.DataFrame | None,
    now: datetime | None = None,
    run_source: str = "manual",
    session_day: str | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_profile_key = _normalize_profile_key(profile_key)
    settings_state = dict(state.get("settings") or {})
    settings = normalize_accuracy_calibration_settings(settings_state)
    runtime = dict(state.get("runtime") or {})
    session_day = session_day or _session_day_for(now)
    closed_frame = _closed_rows_for_session(owned_closed if owned_closed is not None else pd.DataFrame(), session_day=session_day)
    outcomes = _selected_outcomes(closed_frame)
    sample_count = len(outcomes)
    pnl_values = [_coerce_float(item.get("realized_pnl"), 0.0) for item in outcomes]
    wins = sum(1 for value in pnl_values if value > 0)
    hit_rate = wins / sample_count if sample_count else 0.0
    calibrated_expectancy = sum(pnl_values) / sample_count if sample_count else 0.0
    total_pnl = sum(pnl_values)
    confidence_errors = [
        _coerce_float(item.get("confidence_error"), 0.0)
        for item in outcomes
        if item.get("confidence_error") is not None
    ]
    confidence_error = sum(confidence_errors) / len(confidence_errors) if confidence_errors else None
    edge_errors = [
        _coerce_float(item.get("edge_error_bps"), 0.0)
        for item in outcomes
        if item.get("edge_error_bps") is not None
    ]
    edge_to_cost_error_bps = sum(edge_errors) / len(edge_errors) if edge_errors else None
    slippage_values = [_coerce_float(item.get("slippage_bps"), 0.0) for item in outcomes if item.get("slippage_bps") is not None]
    avg_slippage = sum(slippage_values) / len(slippage_values) if slippage_values else None
    worst_slippage = max(slippage_values) if slippage_values else None
    best_patterns, weak_patterns, pattern_lookup = _pattern_breakdown(outcomes)
    rejected_summary = _rejected_candidate_summary(runtime, session_day=session_day)

    accuracy_score = 50.0
    if sample_count:
        accuracy_score += (hit_rate - 0.5) * 60.0
        accuracy_score += max(min(calibrated_expectancy / 10.0, 25.0), -25.0)
        if confidence_error is not None:
            accuracy_score += max(min((0.35 - confidence_error) * 70.0, 18.0), -24.0)
        if avg_slippage is not None:
            accuracy_score -= min(max(avg_slippage - 10.0, 0.0) * 0.8, 20.0)
    decision_pnl_accuracy = round(max(0.0, min(100.0, accuracy_score)), 2)

    if not settings["accuracy_calibration_enabled"]:
        status = "disabled"
        label = "Accuracy calibration disabled"
    elif normalized_profile_key != ACCURACY_PERSONAL_PAPER_PROFILE and not settings["accuracy_calibration_apply_to_live"]:
        status = "not_applicable"
        label = "Paper scope only"
    elif sample_count < int(settings["accuracy_calibration_min_samples"]):
        status = "collecting"
        label = "Collecting samples"
    elif decision_pnl_accuracy < 45.0 or calibrated_expectancy < 0.0 or (confidence_error is not None and confidence_error > 0.45):
        status = "weak"
        label = "Calibration weak"
    elif decision_pnl_accuracy >= 65.0 and calibrated_expectancy >= 0.0 and (confidence_error is None or confidence_error <= 0.35):
        status = "calibrated"
        label = "Calibration clean"
    else:
        status = "watch"
        label = "Calibration watch"

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if status == "weak":
        blockers.append(
            {
                "key": "decision_pnl_accuracy_weak",
                "detail": "Selected automation trades are not proving positive decision-PnL accuracy after costs.",
            }
        )
    if status == "collecting":
        warnings.append(
            {
                "key": "insufficient_samples",
                "detail": (
                    f"{sample_count} selected close(s) available; "
                    f"{settings['accuracy_calibration_min_samples']} are required before aggressive penalties apply."
                ),
            }
        )
    if confidence_error is not None and confidence_error > 0.40:
        warnings.append(
            {
                "key": "confidence_miscalibrated",
                "detail": f"Average confidence error is {confidence_error:.2f}; overconfident misses should be penalized.",
            }
        )
    if avg_slippage is not None and avg_slippage > 20.0:
        warnings.append(
            {
                "key": "slippage_drag",
                "detail": f"Average selected-trade slippage is {avg_slippage:.1f} bps.",
            }
        )

    recommendations: list[dict[str, Any]] = []
    skipped_changes: list[dict[str, Any]] = [
        {
            "field": "baseline_settings",
            "reason": "Accuracy calibration is ranking/advisory only; no baseline settings were auto-tuned.",
        }
    ]
    if status == "weak":
        recommendations.extend(
            [
                {
                    "field": "min_edge_to_cost_ratio",
                    "direction": "tighten",
                    "reason": "Decision-PnL accuracy is weak after costs.",
                },
                {
                    "field": "cycle_entry_rank_limit",
                    "direction": "reduce_or_hold",
                    "reason": "Keep entries concentrated until selected candidates prove positive expectancy.",
                },
                {
                    "field": "max_spread_bps",
                    "direction": "tighten",
                    "reason": "Slippage and spread drag reduce decision-PnL accuracy.",
                },
            ]
        )
    elif status == "calibrated":
        recommendations.append(
            {
                "field": "candidate_ranking",
                "direction": "use_calibrated_patterns",
                "reason": "Clean paper evidence can safely boost patterns with positive calibrated expectancy.",
            }
        )
    else:
        recommendations.append(
            {
                "field": "sample_collection",
                "direction": "continue",
                "reason": "More closed paper outcomes are needed before changing ranking pressure materially.",
            }
        )

    daily_objective = dict(runtime.get("daily_objective_last_report") or {})
    objective_impact = {
        "daily_objective_status": daily_objective.get("status"),
        "target_progress_pct": daily_objective.get("target_progress_pct"),
        "target_gap": daily_objective.get("target_gap"),
        "accuracy_helped_target": total_pnl > 0,
    }

    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": normalized_profile_key,
            "session_day": session_day,
            "evaluated_at": _serialize_datetime(now),
            "run_source": str(run_source or "manual").strip().lower() or "manual",
            "sample_count": sample_count,
            "min_samples": int(settings["accuracy_calibration_min_samples"]),
            "decision_pnl_accuracy": decision_pnl_accuracy,
            "calibrated_expectancy": round(calibrated_expectancy, 4),
            "hit_rate": round(hit_rate, 4),
            "confidence_error": round(confidence_error, 4) if confidence_error is not None else None,
            "edge_to_cost_error_bps": round(edge_to_cost_error_bps, 4) if edge_to_cost_error_bps is not None else None,
            "total_pnl": round(total_pnl, 4),
            "average_slippage_bps": round(avg_slippage, 4) if avg_slippage is not None else None,
            "worst_slippage_bps": round(worst_slippage, 4) if worst_slippage is not None else None,
            "selected_vs_rejected_delta": rejected_summary["selected_vs_rejected_delta"],
            "selected_candidate_count": rejected_summary["selected_candidate_count"],
            "rejected_candidate_count": rejected_summary["rejected_candidate_count"],
            "missed_opportunity_count": rejected_summary["missed_opportunity_count"],
            "best_patterns": best_patterns,
            "weak_patterns": weak_patterns,
            "pattern_lookup": pattern_lookup,
            "blockers": blockers,
            "warnings": warnings,
            "recommendations": recommendations,
            "skipped_changes": skipped_changes,
            "objective_impact": objective_impact,
            "apply_to_live": bool(settings["accuracy_calibration_apply_to_live"]),
        }
    )


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=ACCURACY_NOTE_OWNER,
            limit=ACCURACY_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required = {
        "automation-ai",
        "accuracy-calibration",
        "decision-pnl",
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
    rows = []
    for item in items[:8]:
        key = str(item.get("pattern_key") or item.get("field") or item.get("key") or "item").replace("_", " ")
        detail = str(item.get("reason") or item.get("detail") or "").strip()
        if "expectancy" in item:
            detail = f"expectancy ${float(item.get('expectancy') or 0.0):.2f}, hit {float(item.get('hit_rate') or 0.0) * 100:.0f}%"
        rows.append(f"- {key}: {detail}" if detail else f"- {key}")
    return rows


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    impact = dict(report.get("objective_impact") or {})
    lines = [
        f"Decision-PnL accuracy calibration for {getattr(tenant, 'name', None) or getattr(tenant, 'slug', '') or 'tenant'}",
        "",
        f"- Profile: {profile_key}",
        f"- Session: {report.get('session_day')}",
        f"- Status: {report.get('status')}",
        f"- Decision-PnL accuracy: {float(report.get('decision_pnl_accuracy') or 0.0):.2f}",
        f"- Calibrated expectancy: ${float(report.get('calibrated_expectancy') or 0.0):.2f}",
        f"- Hit rate: {float(report.get('hit_rate') or 0.0) * 100:.2f}%",
        f"- Confidence error: {report.get('confidence_error') if report.get('confidence_error') is not None else '--'}",
        f"- Selected vs rejected expected delta: ${float(report.get('selected_vs_rejected_delta') or 0.0):.2f}",
        f"- Daily objective impact: {'helped' if impact.get('accuracy_helped_target') else 'not helped'}",
        f"- Target progress: {impact.get('target_progress_pct') if impact.get('target_progress_pct') is not None else '--'}%",
        "",
        "Best calibrated patterns",
    ]
    lines.extend(_format_rows(list(report.get("best_patterns") or []), empty="No best patterns yet."))
    lines.extend(["", "Weak calibrated patterns"])
    lines.extend(_format_rows(list(report.get("weak_patterns") or []), empty="No weak patterns yet."))
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
        "accuracy-calibration",
        "decision-pnl",
        _profile_tag(profile_key),
        f"session-{session_day}",
    ]
    title = f"Decision-PnL accuracy calibration - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = (
        str(report.get("related_note_id") or report.get("note_id") or "").strip()
        or _find_existing_note_id(profile_key, session_day)
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": ACCURACY_NOTE_OWNER,
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
        "sample_count",
        "min_samples",
        "decision_pnl_accuracy",
        "calibrated_expectancy",
        "hit_rate",
        "confidence_error",
        "edge_to_cost_error_bps",
        "total_pnl",
        "average_slippage_bps",
        "worst_slippage_bps",
        "selected_vs_rejected_delta",
        "selected_candidate_count",
        "rejected_candidate_count",
        "missed_opportunity_count",
        "best_patterns",
        "weak_patterns",
        "pattern_lookup",
        "blockers",
        "warnings",
        "recommendations",
        "skipped_changes",
        "objective_impact",
        "note_id",
        "related_note_id",
        "apply_to_live",
    }
    runtime["accuracy_calibration_last_report"] = serialize_value(
        {key: report.get(key) for key in summary_keys if key in report}
    )
    runtime["accuracy_calibration_last_run_at"] = report.get("evaluated_at")
    runtime["accuracy_calibration_last_note_id"] = report.get("related_note_id") or report.get("note_id")
    runtime["accuracy_calibration_note_session_day"] = report.get("session_day")
    runtime["accuracy_calibration_last_error"] = None
    history = list(runtime.get("accuracy_calibration_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "decision_pnl_accuracy": report.get("decision_pnl_accuracy"),
            "calibrated_expectancy": report.get("calibrated_expectancy"),
            "sample_count": report.get("sample_count"),
            "note_id": report.get("related_note_id") or report.get("note_id"),
            "run_source": report.get("run_source"),
        },
    )
    runtime["accuracy_calibration_history"] = serialize_value(history[:ACCURACY_HISTORY_LIMIT])
    if db is not None and write_note:
        record_audit_event(
            db,
            event_type="trade_automation.accuracy_calibrated",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": normalized_profile_key,
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "decision_pnl_accuracy": report.get("decision_pnl_accuracy"),
                "calibrated_expectancy": report.get("calibrated_expectancy"),
                "sample_count": report.get("sample_count"),
                "note_id": report.get("related_note_id") or report.get("note_id"),
                "run_source": report.get("run_source"),
            },
        )
    return serialize_value(report)


def run_accuracy_calibration_review(
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
    report = build_accuracy_calibration_report(
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
        try:
            value = pd.to_numeric(candidate.get(key), errors="coerce")
        except Exception:
            continue
        if pd.notna(value):
            return float(value)
    return None


def _pattern_lookup(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    runtime = dict((state or {}).get("runtime") or {})
    report = dict(runtime.get("accuracy_calibration_last_report") or {})
    lookup = dict(report.get("pattern_lookup") or {})
    if lookup:
        return {str(key): dict(value or {}) for key, value in lookup.items() if isinstance(value, dict)}
    return {}


def score_accuracy_candidate(
    candidate: dict[str, Any],
    *,
    state: dict[str, Any],
    current_equity: float | None = None,
) -> dict[str, Any]:
    settings_state = dict((state or {}).get("settings") or {})
    settings = normalize_accuracy_calibration_settings(settings_state)
    profile_key = _normalize_profile_key((state or {}).get("profile_key") or ACCURACY_PERSONAL_PAPER_PROFILE)
    if not settings["accuracy_calibration_enabled"]:
        return {}
    if profile_key != ACCURACY_PERSONAL_PAPER_PROFILE and not settings["accuracy_calibration_apply_to_live"]:
        return {}
    _ = current_equity
    runtime = dict((state or {}).get("runtime") or {})
    report = dict(runtime.get("accuracy_calibration_last_report") or {})
    sample_count = _coerce_int(report.get("sample_count"), 0)
    base_score = _coerce_float(
        candidate.get("daily_objective_score"),
        (
            _coerce_float(candidate.get("portfolio_score"), _coerce_float(candidate.get("ranking_score"), 0.0))
            + _coerce_float(candidate.get("execution_score"), _coerce_float(candidate.get("setup_score"), 0.0))
        )
        / 2.0,
    )
    if sample_count < int(settings["accuracy_calibration_min_samples"]):
        return {
            "accuracy_calibrated_score": round(base_score, 2),
            "accuracy_candidate_penalty": 0.0,
            "accuracy_candidate_boost": 0.0,
            "accuracy_rank_reason": "Collecting decision-PnL samples; no aggressive calibration penalty applied.",
        }
    pattern = _candidate_pattern(candidate)
    pattern_stats = _pattern_lookup(state).get(pattern, {})
    decision_accuracy = _coerce_float(report.get("decision_pnl_accuracy"), 50.0)
    expectancy = _coerce_float(pattern_stats.get("expectancy"), _coerce_float(report.get("calibrated_expectancy"), 0.0))
    pattern_hit_rate = _coerce_float(pattern_stats.get("hit_rate"), _coerce_float(report.get("hit_rate"), 0.0))
    confidence = _candidate_confidence(candidate)
    confidence_error = _coerce_float(report.get("confidence_error"), 0.0)
    max_penalty = float(settings["accuracy_calibration_max_candidate_penalty"])
    penalty = 0.0
    boost = 0.0
    if decision_accuracy < 50.0:
        penalty += min((50.0 - decision_accuracy) * 0.35, max_penalty)
    if expectancy < 0.0:
        penalty += min(abs(expectancy) / 2.0, max_penalty)
    if confidence is not None and confidence >= 0.75 and confidence_error > 0.40:
        penalty += min((confidence - 0.70) * 40.0, max_penalty)
    if pattern_stats and pattern_hit_rate < 0.4:
        penalty += min((0.4 - pattern_hit_rate) * 35.0, max_penalty)
    if decision_accuracy >= 65.0 and expectancy > 0.0 and pattern_hit_rate >= 0.5:
        boost += min(expectancy / 2.0, 12.0)
        boost += min((pattern_hit_rate - 0.5) * 20.0, 8.0)
    penalty = min(max_penalty, max(0.0, penalty))
    calibrated_score = max(0.0, base_score + boost - penalty)
    raw_edge = _candidate_numeric(candidate, "expected_edge_bps", "edge_bps", "forecast_edge_bps") or 0.0
    calibrated_edge = max(0.0, raw_edge * max(0.0, 1.0 + (boost / 100.0) - (penalty / 100.0)))
    return {
        "accuracy_calibrated_score": round(calibrated_score, 2),
        "accuracy_calibrated_expected_edge_bps": round(calibrated_edge, 4),
        "accuracy_candidate_penalty": round(penalty, 2),
        "accuracy_candidate_boost": round(boost, 2),
        "accuracy_pattern_key": pattern,
        "accuracy_pattern_expectancy": round(expectancy, 4),
        "accuracy_pattern_hit_rate": round(pattern_hit_rate, 4),
        "accuracy_rank_reason": "Decision-PnL calibration applied to setup/session/bucket evidence.",
    }


def apply_accuracy_calibration_candidate_overlay(
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
        item.update(score_accuracy_candidate(item, state=state, current_equity=current_equity))
        annotated.append(item)
    return annotated
