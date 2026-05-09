from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.services import equity_snapshot_service, notes_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

MARKET_TIMEZONE = ZoneInfo("America/New_York")
REPLAY_LAB_NOTE_OWNER = "automation-ai"
REPLAY_LAB_HISTORY_LIMIT = 12
REPLAY_LAB_NOTE_LIMIT = 250
REPLAY_LAB_PERSONAL_PAPER_PROFILE = "personal_paper"
REPLAY_LAB_POST_CLOSE_BUFFER_MINUTES = 15

REPLAY_LAB_SETTINGS_DEFAULTS: dict[str, Any] = {
    "replay_lab_enabled": True,
    "replay_lab_auto_review_enabled": True,
    "replay_lab_window_sessions": 20,
    "replay_lab_min_trades": 10,
    "replay_lab_apply_to_live": False,
    "replay_lab_max_recommended_setting_changes": 3,
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


def _clamp_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    return max(int(minimum), min(int(maximum), _coerce_int(value, default)))


def _normalize_profile_key(profile_key: str | None) -> str:
    return str(profile_key or REPLAY_LAB_PERSONAL_PAPER_PROFILE).strip().lower()


def _profile_tag(profile_key: str) -> str:
    cleaned = _normalize_profile_key(profile_key).replace(":", "-") or REPLAY_LAB_PERSONAL_PAPER_PROFILE
    return f"profile-{cleaned}"


def _session_day_for(now: datetime | None = None) -> str:
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(MARKET_TIMEZONE).date().isoformat()


def _session_bounds_utc(session_day: str) -> tuple[datetime, datetime]:
    day = date.fromisoformat(session_day)
    start_local = datetime.combine(day, time.min, tzinfo=MARKET_TIMEZONE)
    end_local = start_local.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _recent_trading_days(now: datetime, count: int) -> list[str]:
    cursor = now.astimezone(MARKET_TIMEZONE).date()
    days: list[str] = []
    while len(days) < max(1, int(count)):
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor = cursor.fromordinal(cursor.toordinal() - 1)
    return days


def replay_lab_review_session_day_for(value: datetime | None = None, *, forced: bool = False) -> tuple[str, bool]:
    now = value or _utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(MARKET_TIMEZONE)
    session_day = local.date().isoformat()
    review_time = datetime.combine(
        local.date(),
        time(16, REPLAY_LAB_POST_CLOSE_BUFFER_MINUTES),
        tzinfo=MARKET_TIMEZONE,
    )
    return session_day, bool(forced or local >= review_time)


def _review_time_for_session_day(session_day: str) -> datetime:
    local_review = datetime.combine(
        date.fromisoformat(session_day),
        time(16, REPLAY_LAB_POST_CLOSE_BUFFER_MINUTES),
        tzinfo=MARKET_TIMEZONE,
    )
    return local_review.astimezone(timezone.utc)


def _next_trading_day_after(session_day: str) -> str:
    cursor = date.fromisoformat(session_day)
    while True:
        cursor = cursor.fromordinal(cursor.toordinal() + 1)
        if cursor.weekday() < 5:
            return cursor.isoformat()


def next_eligible_replay_lab_review_at(value: datetime | None = None) -> datetime:
    now = value or _utc_now()
    session_day, review_open = replay_lab_review_session_day_for(now)
    if review_open:
        return now
    return _review_time_for_session_day(session_day)


def next_eligible_replay_lab_review_after_session(session_day: str) -> datetime:
    return _review_time_for_session_day(_next_trading_day_after(session_day))


def normalize_replay_lab_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    return {
        "replay_lab_enabled": _coerce_bool(
            state.get("replay_lab_enabled"),
            bool(REPLAY_LAB_SETTINGS_DEFAULTS["replay_lab_enabled"]),
        ),
        "replay_lab_auto_review_enabled": _coerce_bool(
            state.get("replay_lab_auto_review_enabled"),
            bool(REPLAY_LAB_SETTINGS_DEFAULTS["replay_lab_auto_review_enabled"]),
        ),
        "replay_lab_window_sessions": _clamp_int(
            state.get("replay_lab_window_sessions"),
            int(REPLAY_LAB_SETTINGS_DEFAULTS["replay_lab_window_sessions"]),
            minimum=1,
            maximum=60,
        ),
        "replay_lab_min_trades": _clamp_int(
            state.get("replay_lab_min_trades"),
            int(REPLAY_LAB_SETTINGS_DEFAULTS["replay_lab_min_trades"]),
            minimum=1,
            maximum=500,
        ),
        "replay_lab_apply_to_live": _coerce_bool(
            state.get("replay_lab_apply_to_live"),
            bool(REPLAY_LAB_SETTINGS_DEFAULTS["replay_lab_apply_to_live"]),
        ),
        "replay_lab_max_recommended_setting_changes": _clamp_int(
            state.get("replay_lab_max_recommended_setting_changes"),
            int(REPLAY_LAB_SETTINGS_DEFAULTS["replay_lab_max_recommended_setting_changes"]),
            minimum=0,
            maximum=12,
        ),
    }


def normalize_replay_lab_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    history = [
        serialize_value(item)
        for item in list(runtime.get("replay_lab_history") or [])[:REPLAY_LAB_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "replay_lab_last_report": serialize_value(runtime.get("replay_lab_last_report") or {}),
        "replay_lab_last_note_id": str(runtime.get("replay_lab_last_note_id") or "").strip() or None,
        "replay_lab_note_session_day": str(runtime.get("replay_lab_note_session_day") or "").strip() or None,
        "replay_lab_last_run_at": _serialize_datetime(_parse_datetime(runtime.get("replay_lab_last_run_at"))),
        "replay_lab_last_scheduled_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("replay_lab_last_scheduled_run_at"))
        ),
        "replay_lab_last_scheduled_session_day": str(
            runtime.get("replay_lab_last_scheduled_session_day") or ""
        ).strip() or None,
        "replay_lab_next_eligible_run_at": (
            _serialize_datetime(_parse_datetime(runtime.get("replay_lab_next_eligible_run_at")))
            or _serialize_datetime(next_eligible_replay_lab_review_at())
        ),
        "replay_lab_last_skipped_reason": str(runtime.get("replay_lab_last_skipped_reason") or "").strip() or None,
        "replay_lab_last_error": str(runtime.get("replay_lab_last_error") or "").strip() or None,
        "replay_lab_history": history,
    }


def build_replay_lab_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    settings = normalize_replay_lab_settings(state.get("settings"))
    runtime = normalize_replay_lab_runtime(state.get("runtime"))
    report = dict(runtime.get("replay_lab_last_report") or {})
    if not report:
        return {
            "status": "not_run" if settings["replay_lab_enabled"] else "disabled",
            "label": "Not run" if settings["replay_lab_enabled"] else "Disabled",
            "enabled": settings["replay_lab_enabled"],
            "auto_review_enabled": settings["replay_lab_auto_review_enabled"],
            "window_sessions": settings["replay_lab_window_sessions"],
            "min_trades": settings["replay_lab_min_trades"],
            "apply_to_live": settings["replay_lab_apply_to_live"],
            "sample_count": 0,
            "recommendations": [],
            "blockers": [],
            "warnings": [],
            "related_note_id": runtime.get("replay_lab_last_note_id"),
            "next_eligible_run_at": runtime.get("replay_lab_next_eligible_run_at"),
            "history": runtime.get("replay_lab_history") or [],
        }
    report.setdefault("enabled", settings["replay_lab_enabled"])
    report.setdefault("auto_review_enabled", settings["replay_lab_auto_review_enabled"])
    report.setdefault("window_sessions", settings["replay_lab_window_sessions"])
    report.setdefault("min_trades", settings["replay_lab_min_trades"])
    report.setdefault("apply_to_live", settings["replay_lab_apply_to_live"])
    report["related_note_id"] = report.get("related_note_id") or runtime.get("replay_lab_last_note_id")
    report["next_eligible_run_at"] = runtime.get("replay_lab_next_eligible_run_at")
    report["history"] = runtime.get("replay_lab_history") or []
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


def _row_float(row: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    return _coerce_float(_row_value(row, *keys), default)


def _row_optional_float(row: dict[str, Any], *keys: str) -> float | None:
    value = _row_value(row, *keys)
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return float(parsed)


def _spread_bps_from_row(row: dict[str, Any]) -> tuple[float | None, str | None]:
    direct = _row_optional_float(row, "accuracy_spread_bps", "spread_bps", "bid_ask_spread_bps")
    if direct is not None:
        return abs(direct), "spread_bps"
    pct = _row_optional_float(row, "contract_spread_pct", "spread_pct", "bid_ask_spread_pct")
    if pct is None:
        return None, None
    # Columns named pct appear in both fractional form (0.01 = 1%) and percent form (1 = 1%).
    bps = pct * 10000.0 if abs(pct) <= 1.0 else pct * 100.0
    return abs(bps), "spread_pct"


def _slippage_bps_from_row(row: dict[str, Any]) -> tuple[float | None, str | None]:
    value = _row_optional_float(
        row,
        "slippage_bps",
        "average_slippage_bps",
        "entry_slippage_bps",
        "fill_slippage_bps",
        "execution_slippage_bps",
    )
    if value is None:
        return None, None
    return abs(value), "slippage_bps"


def _pattern_key(row: dict[str, Any]) -> str:
    direct = str(_row_value(row, "accuracy_pattern_key", "pattern_key") or "").strip().lower()
    if direct:
        return direct
    bucket = str(_row_value(row, "proxy_correlation_bucket", "correlation_bucket") or "unknown").strip().lower()
    instrument = str(_row_value(row, "automation_instrument_type", "instrument_type") or "equity").strip().lower()
    session = str(_row_value(row, "session_label", "market_session", "time_of_day_bucket") or "session").strip().lower()
    return "|".join(part.replace(" ", "_") for part in (bucket, instrument, session) if part)


def _session_for_row(row: dict[str, Any], fallback: str) -> str:
    parsed = _parse_datetime(_row_value(row, "closed_at", "created_at", "opened_at"))
    if parsed is None:
        return fallback
    return parsed.astimezone(MARKET_TIMEZONE).date().isoformat()


def _trade_outcomes(frame: pd.DataFrame, *, fallback_session_day: str) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        pnl = _row_float(row, "realized_pnl", "pnl", "net_pnl")
        position_cost = max(
            _row_float(row, "position_cost", "projected_position_cost", "notional", "max_risk_dollars", default=0.0),
            1.0,
        )
        spread_bps, spread_source = _spread_bps_from_row(row)
        slippage_bps, slippage_source = _slippage_bps_from_row(row)
        rank_value = _row_value(row, "portfolio_rank", "board_rank", "rank")
        edge_value = _row_value(row, "accuracy_edge_to_cost_ratio", "edge_to_cost_ratio")
        setup_score = _row_optional_float(row, "setup_score", "candidate_score", "portfolio_score")
        outcomes.append(
            {
                "trade_id": str(_row_value(row, "trade_id", "id") or "").strip() or None,
                "ticker": str(_row_value(row, "ticker", "symbol") or "").strip().upper() or None,
                "session_day": _session_for_row(row, fallback_session_day),
                "realized_pnl": pnl,
                "position_cost": position_cost,
                "rank": _coerce_int(rank_value, 999),
                "rank_available": rank_value is not None,
                "edge_to_cost_ratio": _coerce_float(edge_value, 0.0),
                "edge_to_cost_available": edge_value is not None,
                "spread_bps": spread_bps,
                "spread_available": spread_bps is not None,
                "spread_source": spread_source,
                "slippage_bps": slippage_bps,
                "slippage_available": slippage_bps is not None,
                "slippage_source": slippage_source,
                "setup_score": setup_score,
                "setup_grade": str(_row_value(row, "setup_grade") or "").strip() or None,
                "conviction_label": str(_row_value(row, "conviction_label") or "").strip() or None,
                "pattern_key": _pattern_key(row),
                "max_adverse_excursion": _row_float(row, "max_adverse_excursion", "max_adverse_excursion_pct"),
                "max_favorable_excursion": _row_float(row, "max_favorable_excursion", "max_favorable_excursion_pct"),
            }
        )
    return outcomes


def _outcome_data_quality(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    sample_count = len(outcomes)
    total_notional = sum(max(_coerce_float(item.get("position_cost"), 0.0), 0.0) for item in outcomes)
    total_pnl = sum(_coerce_float(item.get("realized_pnl"), 0.0) for item in outcomes)
    missing_spread = sum(1 for item in outcomes if not item.get("spread_available"))
    missing_slippage = sum(1 for item in outcomes if not item.get("slippage_available"))
    missing_edge = sum(1 for item in outcomes if not item.get("edge_to_cost_available"))
    missing_rank = sum(1 for item in outcomes if not item.get("rank_available"))
    return {
        "sample_count": sample_count,
        "total_notional": round(total_notional, 4),
        "realized_edge_bps": round((total_pnl / total_notional) * 10000.0, 4) if total_notional > 0 else 0.0,
        "missing_spread_count": missing_spread,
        "missing_slippage_count": missing_slippage,
        "missing_edge_count": missing_edge,
        "missing_rank_count": missing_rank,
        "spread_coverage": round((sample_count - missing_spread) / sample_count, 4) if sample_count else 0.0,
        "slippage_coverage": round((sample_count - missing_slippage) / sample_count, 4) if sample_count else 0.0,
        "edge_coverage": round((sample_count - missing_edge) / sample_count, 4) if sample_count else 0.0,
        "rank_coverage": round((sample_count - missing_rank) / sample_count, 4) if sample_count else 0.0,
    }


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return round(abs(worst), 4)


def _pnl_summary(outcomes: list[dict[str, Any]], *, target_dollars: float, loss_budget_dollars: float) -> dict[str, Any]:
    pnl_values = [_coerce_float(item.get("realized_pnl"), 0.0) for item in outcomes]
    sample_count = len(pnl_values)
    wins = sum(1 for value in pnl_values if value > 0)
    by_session: dict[str, float] = {}
    for item in outcomes:
        session_day = str(item.get("session_day") or "")
        by_session[session_day] = by_session.get(session_day, 0.0) + _coerce_float(item.get("realized_pnl"), 0.0)
    return {
        "sample_count": sample_count,
        "total_pnl": round(sum(pnl_values), 4),
        "expectancy": round(sum(pnl_values) / sample_count, 4) if sample_count else 0.0,
        "hit_rate": round(wins / sample_count, 4) if sample_count else 0.0,
        "max_drawdown": _max_drawdown(pnl_values),
        "target_hit_rate": round(
            sum(1 for value in by_session.values() if value >= target_dollars) / len(by_session),
            4,
        )
        if by_session
        else 0.0,
        "loss_budget_breaches": sum(1 for value in by_session.values() if value <= -abs(loss_budget_dollars)),
        "session_count": len(by_session),
        "session_pnl": {key: round(value, 4) for key, value in sorted(by_session.items())},
    }


def _scale_outcomes_to_notional_cap(outcomes: list[dict[str, Any]], cap: float) -> list[dict[str, Any]]:
    capped: list[dict[str, Any]] = []
    cap = max(float(cap or 0.0), 0.0)
    for item in outcomes:
        row = dict(item)
        position_cost = max(_coerce_float(row.get("position_cost"), 1.0), 1.0)
        if cap > 0 and position_cost > cap:
            scale = cap / position_cost
            row["position_cost"] = cap
            row["realized_pnl"] = _coerce_float(row.get("realized_pnl"), 0.0) * scale
            row["notional_scale"] = round(scale, 6)
        else:
            row["notional_scale"] = 1.0
        capped.append(row)
    return capped


def _stress_failure_count(outcomes: list[dict[str, Any]], *, target_dollars: float, loss_budget_dollars: float) -> int:
    return sum(
        1
        for item in _stress_results(outcomes, target_dollars=target_dollars, loss_budget_dollars=loss_budget_dollars)
        if item.get("status") == "failed"
    )


def _recommended_stress_safe_cap(
    outcomes: list[dict[str, Any]],
    *,
    current_notional_cap: float,
    target_dollars: float,
    loss_budget_dollars: float,
) -> float:
    observed_notional = sorted(
        {
            round(_coerce_float(item.get("position_cost"), 0.0), 2)
            for item in outcomes
            if _coerce_float(item.get("position_cost"), 0.0) > 0
        },
        reverse=True,
    )
    candidates = [
        current_notional_cap if current_notional_cap > 0 else 0.0,
        *observed_notional[:6],
        25000.0,
        20000.0,
        15000.0,
        12500.0,
        10000.0,
        7500.0,
        5000.0,
        2500.0,
        1000.0,
    ]
    unique_candidates = sorted({round(value, 2) for value in candidates if value >= 100.0}, reverse=True)
    for cap in unique_candidates:
        capped = _scale_outcomes_to_notional_cap(outcomes, cap)
        if _stress_failure_count(capped, target_dollars=target_dollars, loss_budget_dollars=loss_budget_dollars) == 0:
            return cap
    return 100.0


def _candidate_counterfactual(runtime: dict[str, Any]) -> dict[str, Any]:
    history = [item for item in list(runtime.get("accuracy_candidate_history") or []) if isinstance(item, dict)]
    selected = [item for item in history if _coerce_bool(item.get("selected"), False)]
    rejected = [item for item in history if not _coerce_bool(item.get("selected"), False)]
    selected_expected = [_coerce_float(item.get("expected_pnl"), 0.0) for item in selected]
    rejected_expected = [_coerce_float(item.get("expected_pnl"), 0.0) for item in rejected]
    selected_avg = sum(selected_expected) / len(selected_expected) if selected_expected else 0.0
    rejected_avg = sum(rejected_expected) / len(rejected_expected) if rejected_expected else 0.0
    missed = [
        item
        for item in rejected
        if _coerce_bool(item.get("auto_entry_eligible"), False)
        and _coerce_float(item.get("expected_pnl"), 0.0) > max(selected_avg, 0.0)
    ]
    return {
        "available": bool(history),
        "selected_candidate_count": len(selected),
        "rejected_candidate_count": len(rejected),
        "selected_expected_pnl_avg": round(selected_avg, 4),
        "rejected_expected_pnl_avg": round(rejected_avg, 4),
        "selected_vs_rejected_delta": round(selected_avg - rejected_avg, 4),
        "missed_opportunity_count": len(missed),
        "top_missed_opportunities": serialize_value(missed[:6]),
    }


def _settings_sensitivity(
    outcomes: list[dict[str, Any]],
    *,
    settings_state: dict[str, Any],
    target_dollars: float,
    loss_budget_dollars: float,
) -> list[dict[str, Any]]:
    rank_limit = max(1, _coerce_int(settings_state.get("cycle_entry_rank_limit"), 2))
    edge_floor = max(0.0, _coerce_float(settings_state.get("min_edge_to_cost_ratio"), 2.5))
    spread_cap = max(0.0, _coerce_float(settings_state.get("max_spread_bps"), 25.0))
    current_notional_cap = max(_coerce_float(settings_state.get("max_notional_per_trade"), 0.0), 0.0)
    stress_safe_cap = _recommended_stress_safe_cap(
        outcomes,
        current_notional_cap=current_notional_cap,
        target_dollars=target_dollars,
        loss_budget_dollars=loss_budget_dollars,
    )
    setup_scores = [
        _coerce_float(item.get("setup_score"), 0.0)
        for item in outcomes
        if item.get("setup_score") is not None
    ]
    setup_cutoff = sorted(setup_scores)[len(setup_scores) // 2] if setup_scores else None
    rank_rows = [item for item in outcomes if _coerce_int(item.get("rank"), 999) <= max(1, rank_limit - 1)]
    rank_detail = "Keep only higher-ranked entries."
    if not rank_rows and setup_cutoff is not None:
        rank_rows = [item for item in outcomes if _coerce_float(item.get("setup_score"), 0.0) >= setup_cutoff]
        rank_detail = "Rank telemetry is missing, so replay keeps the stronger setup-score half as a proxy."
    edge_rows = [
        item
        for item in outcomes
        if item.get("edge_to_cost_available")
        and _coerce_float(item.get("edge_to_cost_ratio"), 0.0) >= edge_floor + 0.5
    ]
    edge_detail = "Require stronger edge-to-cost evidence."
    if not edge_rows and setup_cutoff is not None:
        edge_rows = [item for item in outcomes if _coerce_float(item.get("setup_score"), 0.0) >= setup_cutoff]
        edge_detail = "Edge telemetry is missing, so replay uses stronger setup-score evidence as a conservative proxy."
    spread_rows = [
        item
        for item in outcomes
        if item.get("spread_available")
        and _coerce_float(item.get("spread_bps"), 0.0) <= max(spread_cap * 0.75, 1.0)
    ]
    scenarios: list[tuple[str, list[dict[str, Any]], str, dict[str, Any]]] = [
        (
            "rank_limit_minus_one",
            rank_rows,
            rank_detail,
            {},
        ),
        (
            "edge_floor_plus_half",
            edge_rows,
            edge_detail,
            {},
        ),
        (
            "spread_cap_25pct_tighter",
            spread_rows,
            "Reject trades with worse spread evidence.",
            {},
        ),
        (
            "notional_cap_stress_safe",
            _scale_outcomes_to_notional_cap(outcomes, stress_safe_cap),
            f"Replay current trades with a ${stress_safe_cap:,.0f} paper notional cap.",
            {
                "recommended_cap": round(stress_safe_cap, 2),
                "current_cap": round(current_notional_cap, 2) if current_notional_cap > 0 else None,
            },
        ),
    ]
    results: list[dict[str, Any]] = []
    baseline = _pnl_summary(outcomes, target_dollars=target_dollars, loss_budget_dollars=loss_budget_dollars)
    for name, rows, detail, extra in scenarios:
        summary = _pnl_summary(rows, target_dollars=target_dollars, loss_budget_dollars=loss_budget_dollars)
        stress = _stress_results(rows, target_dollars=target_dollars, loss_budget_dollars=loss_budget_dollars)
        results.append(
            {
                "scenario": name,
                "detail": detail,
                "sample_count": summary["sample_count"],
                "pnl_delta": round(summary["total_pnl"] - baseline["total_pnl"], 4),
                "expectancy_delta": round(summary["expectancy"] - baseline["expectancy"], 4),
                "max_drawdown_delta": round(summary["max_drawdown"] - baseline["max_drawdown"], 4),
                "loss_budget_breaches": summary["loss_budget_breaches"],
                "stress_failures": sum(1 for item in stress if item.get("status") == "failed"),
                "worst_stress_drawdown": max(
                    [_coerce_float(item.get("max_drawdown"), 0.0) for item in stress],
                    default=0.0,
                ),
                "summary": summary,
                **extra,
            }
        )
    return results


def _stress_results(
    outcomes: list[dict[str, Any]],
    *,
    target_dollars: float,
    loss_budget_dollars: float,
    transaction_cost: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    transaction_cost = dict(transaction_cost or {})
    observed_cost = max(_coerce_float(transaction_cost.get("estimated_vs_realized_cost_error_bps"), 0.0), 0.0)
    shocks = [
        ("slippage_plus_15bps", "Add 15 bps execution cost to every trade.", 15.0, 0.0),
        ("spread_plus_25bps", "Add 25 bps spread shock to every trade.", 25.0, 0.0),
        ("delayed_exit_giveback", "Subtract 25% of winners to model delayed exits.", 0.0, 0.25),
        ("correlated_drawdown", "Double losses in repeated ticker/bucket clusters.", 0.0, 0.0),
    ]
    if observed_cost > 0 and _coerce_int(transaction_cost.get("sample_count"), 0) >= _coerce_int(
        transaction_cost.get("min_samples"), 1
    ):
        shocks.insert(
            0,
            (
                "observed_cost_drift",
                f"Apply observed paper cost drift of {observed_cost:.1f} bps.",
                observed_cost,
                0.0,
            ),
        )
    for name, detail, bps_cost, giveback in shocks:
        shocked: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for item in outcomes:
            key = f"{item.get('ticker')}|{item.get('pattern_key')}"
            counts[key] = counts.get(key, 0) + 1
        for item in outcomes:
            pnl = _coerce_float(item.get("realized_pnl"), 0.0)
            cost = max(_coerce_float(item.get("position_cost"), 1.0), 1.0) * bps_cost / 10000.0
            if giveback and pnl > 0:
                pnl -= pnl * giveback
            if name == "correlated_drawdown":
                key = f"{item.get('ticker')}|{item.get('pattern_key')}"
                if counts.get(key, 0) >= 3 and pnl < 0:
                    pnl *= 2.0
            row = dict(item)
            row["realized_pnl"] = pnl - cost
            shocked.append(row)
        summary = _pnl_summary(shocked, target_dollars=target_dollars, loss_budget_dollars=loss_budget_dollars)
        hard_failed = bool(
            summary["loss_budget_breaches"]
            or _coerce_float(summary.get("max_drawdown"), 0.0) > abs(loss_budget_dollars)
        )
        status = "failed" if hard_failed else ("warning" if summary["total_pnl"] < 0 else "passed")
        scenarios.append(
            {
                "scenario": name,
                "detail": detail,
                "status": status,
                "total_pnl": summary["total_pnl"],
                "target_hit_rate": summary["target_hit_rate"],
                "max_drawdown": summary["max_drawdown"],
                "loss_budget_breaches": summary["loss_budget_breaches"],
            }
        )
    return scenarios


def _pattern_breakdown(outcomes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in outcomes:
        grouped.setdefault(str(item.get("pattern_key") or "unknown"), []).append(item)
    rows: list[dict[str, Any]] = []
    for key, items in grouped.items():
        pnl_values = [_coerce_float(item.get("realized_pnl"), 0.0) for item in items]
        sample_count = len(items)
        wins = sum(1 for value in pnl_values if value > 0)
        avg_slippage = [
            _coerce_float(item.get("slippage_bps"), 0.0)
            for item in items
            if item.get("slippage_bps") is not None
        ]
        rows.append(
            {
                "pattern_key": key,
                "sample_count": sample_count,
                "expectancy": round(sum(pnl_values) / sample_count, 4) if sample_count else 0.0,
                "total_pnl": round(sum(pnl_values), 4),
                "hit_rate": round(wins / sample_count, 4) if sample_count else 0.0,
                "average_slippage_bps": round(sum(avg_slippage) / len(avg_slippage), 4) if avg_slippage else None,
            }
        )
    return (
        sorted(rows, key=lambda item: (item["expectancy"], item["hit_rate"], item["sample_count"]), reverse=True)[:6],
        sorted(rows, key=lambda item: (item["expectancy"], item["hit_rate"], -item["sample_count"]))[:6],
    )


def _recent_order_event_count(db: Session | None, *, tenant: Tenant, session_days: list[str]) -> int:
    if db is None or not session_days:
        return 0
    start_at, _ = _session_bounds_utc(session_days[-1])
    _, end_at = _session_bounds_utc(session_days[0])
    try:
        rows = db.scalars(
            select(OrderEventRecord)
            .where(OrderEventRecord.tenant_id == tenant.id)
            .where(OrderEventRecord.created_at >= start_at, OrderEventRecord.created_at <= end_at)
        ).all()
    except Exception:
        return 0
    return len(rows)


def _latest_equity_snapshot(tenant: Tenant, profile_key: str) -> dict[str, Any] | None:
    try:
        return equity_snapshot_service.get_latest_trade_automation_equity_snapshot(
            tenant_id=str(getattr(tenant, "id", "") or ""),
            tenant_slug=str(getattr(tenant, "slug", "") or ""),
            profile_key=profile_key,
        )
    except Exception:
        return None


def _recommendations(
    *,
    report_status: str,
    baseline: dict[str, Any],
    stress: list[dict[str, Any]],
    sensitivity: list[dict[str, Any]],
    accuracy: dict[str, Any],
    reconciliation: dict[str, Any],
    data_quality: dict[str, Any],
    settings_state: dict[str, Any],
    max_changes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    failed_stress = [item for item in stress if item.get("status") == "failed"]
    mitigated_stress = next(
        (
            item
            for item in sensitivity
            if str(item.get("scenario") or "").startswith("notional_cap")
            and _coerce_int(item.get("stress_failures"), 0) == 0
        ),
        None,
    )
    weak_accuracy = str(accuracy.get("status") or "").lower() in {"weak", "blocked"}
    reconciliation_bad = str(reconciliation.get("status") or "").lower() in {"blocked", "mismatch", "error"}
    weak_replay = _coerce_float(baseline.get("total_pnl"), 0.0) <= 0 or _coerce_float(
        baseline.get("expectancy"), 0.0
    ) <= 0
    current_spread_cap = _coerce_float(settings_state.get("max_spread_bps"), 25.0)
    current_edge_floor = _coerce_float(settings_state.get("min_edge_to_cost_ratio"), 2.5)
    current_risk_percent = _coerce_float(settings_state.get("risk_percent"), 0.5)
    current_rank_limit = _coerce_int(settings_state.get("cycle_entry_rank_limit"), 2)
    require_edge_fields = _coerce_bool(settings_state.get("require_edge_fields"), False)
    recommendations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = [
        {
            "field": "baseline_settings",
            "reason": "Replay lab is advisory only; no baseline settings were changed.",
        }
    ]
    if report_status in {"collecting", "disabled", "not_applicable"}:
        recommendations.append(
            {
                "field": "sample_collection",
                "direction": "continue",
                "reason": "More paper trades are required before replay recommendations should drive risk changes.",
            }
        )
        skipped.append({"field": "capacity", "reason": "Capacity expansion skipped while replay sample is incomplete."})
        return recommendations[:max_changes], skipped
    if baseline.get("loss_budget_breaches") or failed_stress or weak_replay or weak_accuracy or reconciliation_bad:
        recommended_cap = _coerce_float(mitigated_stress.get("recommended_cap"), 0.0) if mitigated_stress else 0.0
        current_cap = _coerce_float(mitigated_stress.get("current_cap"), 0.0) if mitigated_stress else 0.0
        if failed_stress and recommended_cap > 0 and (current_cap <= 0 or current_cap > recommended_cap):
            recommendations.append(
                {
                    "field": "max_notional_per_trade",
                    "direction": f"reduce_to_{recommended_cap:.0f}",
                    "reason": (
                        "Stress replay only fits inside the daily loss budget after capping paper position size."
                    ),
                }
            )
        if current_spread_cap > 20.0:
            recommendations.append(
                {
                    "field": "max_spread_bps",
                    "direction": "tighten_to_20",
                    "reason": "Replay or stress evidence shows spread/slippage can turn outcomes negative.",
                }
            )
        else:
            skipped.append({"field": "max_spread_bps", "reason": "Spread cap is already at or below 20 bps."})
        if current_edge_floor < 3.0:
            recommendations.append(
                {
                    "field": "min_edge_to_cost_ratio",
                    "direction": "tighten_to_3",
                    "reason": "Require more edge before taking new paper risk.",
                }
            )
        else:
            skipped.append({"field": "min_edge_to_cost_ratio", "reason": "Edge-to-cost floor is already 3x or higher."})
        if current_risk_percent > 0.25:
            recommendations.append(
                {
                    "field": "risk_percent",
                    "direction": "reduce_to_0.25",
                    "reason": "Realized edge is thin relative to stressed execution costs.",
                }
            )
        else:
            skipped.append({"field": "risk_percent", "reason": "Risk percent is already at or below 0.25%."})
        if baseline.get("loss_budget_breaches") or weak_accuracy or reconciliation_bad:
            if current_rank_limit > 1:
                recommendations.append(
                    {
                        "field": "cycle_entry_rank_limit",
                        "direction": "reduce_to_1",
                        "reason": "Concentrate on only the strongest ranked entries until blockers clear.",
                    }
                )
            recommendations.append(
                {
                    "field": "loss_containment_time_stop_minutes",
                    "direction": "tighten",
                    "reason": "Weak replay evidence suggests stale winners and losers need faster review.",
                }
            )
        if _coerce_int(data_quality.get("missing_edge_count"), 0) > 0 and not require_edge_fields:
            recommendations.append(
                {
                    "field": "entry_edge_telemetry",
                    "direction": "require",
                    "reason": "Replay cannot verify edge-to-cost discipline for every historical paper trade.",
                }
            )
        if not recommendations:
            recommendations.append(
                {
                    "field": "paper_risk_controls",
                    "direction": "hold",
                    "reason": "Current paper cap, spread, edge, and risk controls already match replay mitigation.",
                }
            )
        skipped.append({"field": "capacity", "reason": "Capacity increase skipped because replay safety evidence is weak."})
    else:
        best_sensitivity = sorted(
            sensitivity,
            key=lambda item: (
                _coerce_float(item.get("pnl_delta"), 0.0),
                -_coerce_float(item.get("max_drawdown_delta"), 0.0),
            ),
            reverse=True,
        )[:1]
        if best_sensitivity and _coerce_float(best_sensitivity[0].get("pnl_delta"), 0.0) > 0:
            recommendations.append(
                {
                    "field": "candidate_filter",
                    "direction": best_sensitivity[0].get("scenario"),
                    "reason": "Sensitivity replay improved net PnL without increasing loss-budget breaches.",
                }
            )
        recommendations.extend(
            [
                {
                    "field": "cycle_entry_rank_limit",
                    "direction": "consider_small_increase",
                    "reason": "Clean replay and stress evidence may support one more qualified paper candidate.",
                },
                {
                    "field": "max_daily_entries",
                    "direction": "consider_small_increase",
                    "reason": "Only for paper and only while reconciliation, accuracy, and loss budgets remain clean.",
                },
            ]
        )
    return recommendations[: max(0, max_changes)], skipped


def build_replay_lab_report(
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
    settings = normalize_replay_lab_settings(settings_state)
    runtime = dict(state.get("runtime") or {})
    session_day = _session_day_for(now)
    window_days = _recent_trading_days(now, int(settings["replay_lab_window_sessions"]))
    closed_frame = _windowed_closed_rows(owned_closed if owned_closed is not None else pd.DataFrame(), session_days=window_days)
    outcomes = _trade_outcomes(closed_frame, fallback_session_day=session_day)
    data_quality = _outcome_data_quality(outcomes)
    equity_base = max(
        _coerce_float(state.get("__actual_funds"), _coerce_float(settings_state.get("account_size"), 100000.0)),
        1.0,
    )
    target_dollars = max(
        _coerce_float(settings_state.get("daily_profit_target_dollars"), 1000.0),
        equity_base * _coerce_float(settings_state.get("daily_profit_target_pct"), 1.0) / 100.0,
        1.0,
    )
    loss_budget_dollars = max(equity_base * _coerce_float(settings_state.get("daily_loss_budget_pct"), 0.5) / 100.0, 1.0)
    baseline = _pnl_summary(outcomes, target_dollars=target_dollars, loss_budget_dollars=loss_budget_dollars)
    transaction_cost = dict(runtime.get("transaction_cost_calibration_last_report") or {})
    stress = _stress_results(
        outcomes,
        target_dollars=target_dollars,
        loss_budget_dollars=loss_budget_dollars,
        transaction_cost=transaction_cost,
    )
    sensitivity = _settings_sensitivity(
        outcomes,
        settings_state=settings_state,
        target_dollars=target_dollars,
        loss_budget_dollars=loss_budget_dollars,
    )
    counterfactual = _candidate_counterfactual(runtime)
    best_patterns, weak_patterns = _pattern_breakdown(outcomes)
    accuracy = dict(runtime.get("accuracy_calibration_last_report") or {})
    reconciliation = dict(runtime.get("paper_broker_reconciliation_last_report") or {})
    loss_containment = dict(runtime.get("loss_containment_last_report") or {})
    exit_watchdog = dict(runtime.get("exit_watchdog_last_report") or runtime.get("exit_execution_watchdog_last_report") or {})
    equity_snapshot = _latest_equity_snapshot(tenant, normalized_profile_key)
    order_event_count = _recent_order_event_count(db, tenant=tenant, session_days=window_days)

    warnings: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    failed_stress = [item for item in stress if item.get("status") == "failed"]
    mitigating_sensitivity = next(
        (
            item
            for item in sensitivity
            if str(item.get("scenario") or "").startswith("notional_cap")
            and _coerce_int(item.get("stress_failures"), 0) == 0
        ),
        None,
    )
    stress_mitigated = bool(failed_stress and mitigating_sensitivity)
    if not settings["replay_lab_enabled"]:
        status = "disabled"
        label = "Replay lab disabled"
    elif normalized_profile_key != REPLAY_LAB_PERSONAL_PAPER_PROFILE and not settings["replay_lab_apply_to_live"]:
        status = "not_applicable"
        label = "Paper scope only"
    elif baseline["sample_count"] < int(settings["replay_lab_min_trades"]):
        status = "collecting"
        label = "Collecting replay sample"
        warnings.append(
            {
                "key": "insufficient_replay_sample",
                "detail": (
                    f"{baseline['sample_count']} closed paper trade(s) available; "
                    f"{settings['replay_lab_min_trades']} required before capacity recommendations are trusted."
                ),
            }
        )
    elif baseline["loss_budget_breaches"] or (failed_stress and not stress_mitigated):
        status = "blocked"
        label = "Replay stress failed"
    elif stress_mitigated:
        status = "warning"
        label = "Replay stress mitigated"
    elif baseline["total_pnl"] <= 0 or baseline["expectancy"] <= 0:
        status = "warning"
        label = "Replay expectancy weak"
    else:
        status = "clean"
        label = "Replay evidence clean"

    if not counterfactual["available"]:
        warnings.append(
            {
                "key": "candidate_history_missing",
                "detail": "Candidate counterfactual replay is limited because accuracy candidate history is missing.",
            }
        )
    if baseline["sample_count"]:
        if _coerce_int(data_quality.get("missing_spread_count"), 0) > 0:
            warnings.append(
                {
                    "key": "spread_telemetry_missing",
                    "detail": (
                        f"{data_quality['missing_spread_count']} trade(s) are missing spread telemetry; "
                        "spread stress remains conservative."
                    ),
                }
            )
        if _coerce_int(data_quality.get("missing_edge_count"), 0) > 0:
            warnings.append(
                {
                    "key": "edge_telemetry_missing",
                    "detail": (
                        f"{data_quality['missing_edge_count']} trade(s) are missing edge-to-cost telemetry; "
                        "candidate counterfactuals should not drive capacity expansion yet."
                    ),
                }
            )
        if _coerce_float(data_quality.get("realized_edge_bps"), 0.0) < 10.0:
            warnings.append(
                {
                    "key": "thin_realized_edge",
                    "detail": (
                        f"Realized net edge is {data_quality['realized_edge_bps']} bps, "
                        "which is thin versus normal execution stress."
                    ),
                }
            )
    if stress_mitigated and mitigating_sensitivity:
        warnings.append(
            {
                "key": "stress_failures_mitigated",
                "detail": (
                    f"{len(failed_stress)} stress scenario(s) failed at historical size but pass loss-budget checks "
                    f"with a ${float(mitigating_sensitivity.get('recommended_cap') or 0.0):,.0f} paper notional cap."
                ),
            }
        )
    if str(reconciliation.get("status") or "").lower() in {"blocked", "mismatch", "error"}:
        blockers.append(
            {
                "key": "paper_reconciliation_blocked",
                "detail": "Paper broker reconciliation is not clean; replay cannot recommend capacity increases.",
            }
        )
    if bool(loss_containment.get("entries_blocked")):
        blockers.append(
            {
                "key": "loss_containment_blocked",
                "detail": "Loss containment blocked entries in the replay window.",
            }
        )
    if bool(exit_watchdog.get("entries_blocked")) or _coerce_int(exit_watchdog.get("stuck_exit_count"), 0) > 0:
        blockers.append(
            {
                "key": "exit_watchdog_blocked",
                "detail": "Exit watchdog evidence has unconfirmed defensive exits.",
            }
        )
    if failed_stress and not stress_mitigated:
        blockers.append(
            {
                "key": "stress_failures",
                "detail": f"{len(failed_stress)} deterministic stress scenario(s) failed.",
            }
        )
    if blockers and status not in {"disabled", "not_applicable", "collecting"}:
        status = "blocked"
        label = "Replay blockers present"

    recommendations, skipped_changes = _recommendations(
        report_status=status,
        baseline=baseline,
        stress=stress,
        sensitivity=sensitivity,
        accuracy=accuracy,
        reconciliation=reconciliation,
        data_quality=data_quality,
        settings_state=settings_state,
        max_changes=int(settings["replay_lab_max_recommended_setting_changes"]),
    )
    best_replay = sorted(
        sensitivity,
        key=lambda item: _coerce_float(item.get("pnl_delta"), 0.0),
        reverse=True,
    )[:1]
    replay_pnl = (
        round(baseline["total_pnl"] + _coerce_float(best_replay[0].get("pnl_delta"), 0.0), 4)
        if best_replay
        else baseline["total_pnl"]
    )
    accuracy_delta = _coerce_float(accuracy.get("decision_pnl_accuracy"), 0.0) - 50.0 if accuracy else None

    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": normalized_profile_key,
            "session_day": session_day,
            "evaluated_at": _serialize_datetime(now),
            "run_source": str(run_source or "manual").strip().lower() or "manual",
            "session_window": window_days,
            "window_sessions": int(settings["replay_lab_window_sessions"]),
            "min_trades": int(settings["replay_lab_min_trades"]),
            "sample_count": baseline["sample_count"],
            "baseline_pnl": baseline["total_pnl"],
            "replay_pnl": replay_pnl,
            "target_hit_rate": baseline["target_hit_rate"],
            "max_drawdown": baseline["max_drawdown"],
            "loss_budget_breaches": baseline["loss_budget_breaches"],
            "accuracy_delta": round(accuracy_delta, 4) if accuracy_delta is not None else None,
            "baseline": baseline,
            "candidate_counterfactual": counterfactual,
            "data_quality": data_quality,
            "transaction_cost_calibration": {
                "status": transaction_cost.get("status"),
                "sample_count": transaction_cost.get("sample_count"),
                "cost_error_bps": transaction_cost.get("estimated_vs_realized_cost_error_bps"),
                "slippage_error_bps": transaction_cost.get("slippage_error_bps"),
            },
            "settings_sensitivity": sensitivity,
            "stress_results": stress,
            "best_patterns": best_patterns,
            "weak_patterns": weak_patterns,
            "recommendations": recommendations,
            "skipped_changes": skipped_changes,
            "blockers": blockers,
            "warnings": warnings,
            "order_event_count": order_event_count,
            "equity_snapshot": serialize_value(equity_snapshot or {}),
            "paper_reconciliation_status": reconciliation.get("status"),
            "loss_containment_status": loss_containment.get("status"),
            "exit_watchdog_status": exit_watchdog.get("status"),
            "apply_to_live": bool(settings["replay_lab_apply_to_live"]),
        }
    )


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=REPLAY_LAB_NOTE_OWNER,
            limit=REPLAY_LAB_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required = {
        "automation-ai",
        "replay-lab",
        "what-if",
        "daily-objective",
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
        key = str(item.get("field") or item.get("scenario") or item.get("pattern_key") or item.get("key") or "item")
        detail = str(item.get("reason") or item.get("detail") or "").strip()
        if "pnl_delta" in item:
            detail = f"PnL delta ${float(item.get('pnl_delta') or 0.0):,.2f}; {detail}".strip()
        rows.append(f"- {key.replace('_', ' ')}: {detail}" if detail else f"- {key.replace('_', ' ')}")
    return rows


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    lines = [
        f"Paper replay lab review for {getattr(tenant, 'name', None) or getattr(tenant, 'slug', '') or 'tenant'}",
        "",
        f"- Profile: {profile_key}",
        f"- Session: {report.get('session_day')}",
        f"- Status: {report.get('status')}",
        f"- Sample: {report.get('sample_count')}/{report.get('min_trades')} closed trades",
        f"- Baseline PnL: ${float(report.get('baseline_pnl') or 0.0):,.2f}",
        f"- Best replay PnL: ${float(report.get('replay_pnl') or 0.0):,.2f}",
        f"- Target-hit rate: {float(report.get('target_hit_rate') or 0.0) * 100:.2f}%",
        f"- Max drawdown: ${float(report.get('max_drawdown') or 0.0):,.2f}",
        f"- Loss-budget breaches: {int(report.get('loss_budget_breaches') or 0)}",
        f"- Realized net edge: {float((report.get('data_quality') or {}).get('realized_edge_bps') or 0.0):.2f} bps",
        "",
        "Settings sensitivity",
    ]
    lines.extend(_format_rows(list(report.get("settings_sensitivity") or []), empty="No sensitivity replay available."))
    lines.extend(["", "Stress results"])
    lines.extend(_format_rows(list(report.get("stress_results") or []), empty="No stress replay available."))
    lines.extend(["", "Recommendations"])
    lines.extend(_format_rows(list(report.get("recommendations") or []), empty="No setting recommendations."))
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
        "replay-lab",
        "what-if",
        "daily-objective",
        _profile_tag(profile_key),
        f"session-{session_day}",
    ]
    title = f"Paper replay lab - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = (
        str(report.get("related_note_id") or report.get("note_id") or "").strip()
        or _find_existing_note_id(profile_key, session_day)
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": REPLAY_LAB_NOTE_OWNER,
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
        "min_trades",
        "sample_count",
        "baseline_pnl",
        "replay_pnl",
        "target_hit_rate",
        "max_drawdown",
        "loss_budget_breaches",
        "accuracy_delta",
        "baseline",
        "candidate_counterfactual",
        "data_quality",
        "transaction_cost_calibration",
        "settings_sensitivity",
        "stress_results",
        "best_patterns",
        "weak_patterns",
        "recommendations",
        "skipped_changes",
        "blockers",
        "warnings",
        "order_event_count",
        "equity_snapshot",
        "paper_reconciliation_status",
        "loss_containment_status",
        "exit_watchdog_status",
        "note_id",
        "related_note_id",
        "apply_to_live",
    }
    runtime["replay_lab_last_report"] = serialize_value(
        {key: report.get(key) for key in summary_keys if key in report}
    )
    runtime["replay_lab_last_run_at"] = report.get("evaluated_at")
    runtime["replay_lab_last_note_id"] = report.get("related_note_id") or report.get("note_id")
    runtime["replay_lab_note_session_day"] = report.get("session_day")
    runtime["replay_lab_last_error"] = None
    history = list(runtime.get("replay_lab_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "sample_count": report.get("sample_count"),
            "baseline_pnl": report.get("baseline_pnl"),
            "replay_pnl": report.get("replay_pnl"),
            "note_id": report.get("related_note_id") or report.get("note_id"),
            "run_source": report.get("run_source"),
        },
    )
    runtime["replay_lab_history"] = serialize_value(history[:REPLAY_LAB_HISTORY_LIMIT])
    if db is not None and write_note:
        record_audit_event(
            db,
            event_type="trade_automation.replay_lab_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": normalized_profile_key,
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "sample_count": report.get("sample_count"),
                "baseline_pnl": report.get("baseline_pnl"),
                "replay_pnl": report.get("replay_pnl"),
                "note_id": report.get("related_note_id") or report.get("note_id"),
                "run_source": report.get("run_source"),
            },
        )
    return serialize_value(report)


def run_replay_lab_review(
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
    report = build_replay_lab_report(
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
