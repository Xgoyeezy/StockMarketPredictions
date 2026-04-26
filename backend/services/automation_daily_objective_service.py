from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, Tenant
from backend.services import notes_service, risk_control_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

MARKET_TIMEZONE = ZoneInfo("America/New_York")
DAILY_OBJECTIVE_NOTE_OWNER = "automation-ai"
DAILY_OBJECTIVE_HISTORY_LIMIT = 12
DAILY_OBJECTIVE_NOTE_LIMIT = 250
DAILY_OBJECTIVE_PERSONAL_PAPER_PROFILE = "personal_paper"
DAILY_OBJECTIVE_PERSONAL_LIVE_PROFILE = "personal_live"

DAILY_OBJECTIVE_SETTINGS_DEFAULTS: dict[str, Any] = {
    "daily_objective_enabled": True,
    "daily_profit_target_pct": 1.0,
    "daily_profit_target_dollars": 1000.0,
    "daily_loss_budget_pct": 0.5,
    "daily_objective_apply_to_live": False,
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


def _clamp_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    return max(float(minimum), min(float(maximum), _coerce_float(value, default)))


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
    return f"profile-{str(profile_key or '').strip().lower().replace(':', '-') or DAILY_OBJECTIVE_PERSONAL_PAPER_PROFILE}"


def _normalize_profile_key(profile_key: str | None) -> str:
    return str(profile_key or DAILY_OBJECTIVE_PERSONAL_PAPER_PROFILE).strip().lower()


def normalize_daily_objective_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    return {
        "daily_objective_enabled": _coerce_bool(
            state.get("daily_objective_enabled"),
            bool(DAILY_OBJECTIVE_SETTINGS_DEFAULTS["daily_objective_enabled"]),
        ),
        "daily_profit_target_pct": _clamp_float(
            state.get("daily_profit_target_pct"),
            float(DAILY_OBJECTIVE_SETTINGS_DEFAULTS["daily_profit_target_pct"]),
            minimum=0.1,
            maximum=10.0,
        ),
        "daily_profit_target_dollars": _clamp_float(
            state.get("daily_profit_target_dollars"),
            float(DAILY_OBJECTIVE_SETTINGS_DEFAULTS["daily_profit_target_dollars"]),
            minimum=1.0,
            maximum=1_000_000.0,
        ),
        "daily_loss_budget_pct": _clamp_float(
            state.get("daily_loss_budget_pct"),
            float(DAILY_OBJECTIVE_SETTINGS_DEFAULTS["daily_loss_budget_pct"]),
            minimum=0.1,
            maximum=10.0,
        ),
        "daily_objective_apply_to_live": _coerce_bool(
            state.get("daily_objective_apply_to_live"),
            bool(DAILY_OBJECTIVE_SETTINGS_DEFAULTS["daily_objective_apply_to_live"]),
        ),
    }


def normalize_daily_objective_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    history = [
        serialize_value(item)
        for item in list(runtime.get("daily_objective_history") or [])[:DAILY_OBJECTIVE_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "daily_objective_last_report": serialize_value(runtime.get("daily_objective_last_report") or {}),
        "daily_objective_last_note_id": str(runtime.get("daily_objective_last_note_id") or "").strip() or None,
        "daily_objective_note_session_day": str(runtime.get("daily_objective_note_session_day") or "").strip() or None,
        "daily_objective_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("daily_objective_last_run_at"))
        ),
        "daily_objective_last_error": str(runtime.get("daily_objective_last_error") or "").strip() or None,
        "daily_objective_history": history,
    }


def build_daily_objective_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    settings = normalize_daily_objective_settings(state.get("settings"))
    runtime = normalize_daily_objective_runtime(state.get("runtime"))
    report = dict(runtime.get("daily_objective_last_report") or {})
    if not report:
        return {
            "status": "not_run" if settings["daily_objective_enabled"] else "disabled",
            "label": "Not run" if settings["daily_objective_enabled"] else "Disabled",
            "enabled": settings["daily_objective_enabled"],
            "target_dollars": settings["daily_profit_target_dollars"],
            "target_pct": settings["daily_profit_target_pct"],
            "loss_budget_pct": settings["daily_loss_budget_pct"],
            "apply_to_live": settings["daily_objective_apply_to_live"],
            "target_reached": False,
            "entries_blocked": False,
            "related_note_id": runtime.get("daily_objective_last_note_id"),
            "history": runtime.get("daily_objective_history") or [],
        }
    report.setdefault("enabled", settings["daily_objective_enabled"])
    report.setdefault("target_dollars", settings["daily_profit_target_dollars"])
    report.setdefault("target_pct", settings["daily_profit_target_pct"])
    report.setdefault("loss_budget_pct", settings["daily_loss_budget_pct"])
    report.setdefault("apply_to_live", settings["daily_objective_apply_to_live"])
    report["history"] = runtime.get("daily_objective_history") or []
    report["related_note_id"] = report.get("related_note_id") or runtime.get("daily_objective_last_note_id")
    return serialize_value(report)


def _owned_rows(frame: pd.DataFrame | None, *, tenant_id: str | None, profile_key: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame
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


def _daily_realized_pnl(closed_frame: pd.DataFrame, *, session_day: str) -> tuple[float, int, int, list[str]]:
    if closed_frame.empty:
        return 0.0, 0, 0, []
    timestamps = pd.to_datetime(closed_frame.get("closed_at", pd.Series(dtype=str)), errors="coerce", utc=True)
    start, end = _session_bounds_utc(session_day)
    if timestamps.notna().any():
        mask = timestamps.ge(start) & timestamps.le(end)
        frame = closed_frame[mask]
    else:
        frame = closed_frame
    if frame.empty:
        return 0.0, 0, 0, []
    pnl = pd.to_numeric(frame.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    tickers = []
    if "ticker" in frame.columns:
        tickers = [
            str(item or "").strip().upper()
            for item in frame["ticker"].tolist()
            if str(item or "").strip()
        ]
    return float(pnl.sum()), int((pnl > 0).sum()), int((pnl < 0).sum()), tickers[:12]


def _target_amount(settings_state: dict[str, Any], equity_base: float) -> tuple[float, float]:
    settings = normalize_daily_objective_settings(settings_state)
    pct_amount = max(float(equity_base), 1.0) * (float(settings["daily_profit_target_pct"]) / 100.0)
    explicit_amount = float(settings["daily_profit_target_dollars"])
    return max(explicit_amount, 1.0), max(pct_amount, 1.0)


def _build_recommendations(
    *,
    status: str,
    total_pnl: float,
    risk_budget_used_pct: float,
    target_progress_pct: float,
    clean_candidate_count: int,
    control_plane: dict[str, Any],
    settings_state: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    recommendations: list[dict[str, Any]] = []
    skipped_changes: list[dict[str, Any]] = []
    state_control_state = str(control_plane.get("state") or settings_state.get("state_control_state") or "").lower()
    weak_state = state_control_state in {"watch", "de_risk", "halt"}
    if status == "loss_budget_locked" or total_pnl < 0 or risk_budget_used_pct >= 70.0 or weak_state:
        recommendations.extend(
            [
                {
                    "field": "min_edge_to_cost_ratio",
                    "direction": "tighten",
                    "reason": "Risk budget, slippage, or state-control evidence is weak; favor cleaner edge over capacity.",
                },
                {
                    "field": "max_spread_bps",
                    "direction": "tighten",
                    "reason": "Reducing spread tolerance is the safest response when the objective is behind schedule.",
                },
                {
                    "field": "cycle_entry_rank_limit",
                    "direction": "reduce_or_hold",
                    "reason": "Keep entries concentrated in the strongest current candidate until evidence improves.",
                },
            ]
        )
        skipped_changes.append(
            {
                "field": "capacity",
                "reason": "Capacity expansion skipped because risk evidence is not clean.",
            }
        )
    elif clean_candidate_count > 0 and target_progress_pct >= 0.0:
        recommendations.extend(
            [
                {
                    "field": "cycle_entry_rank_limit",
                    "direction": "consider_small_increase",
                    "reason": "Paper evidence is clean; a small capacity increase may help close the remaining target gap.",
                },
                {
                    "field": "max_daily_entries",
                    "direction": "consider_small_increase",
                    "reason": "Only after clean fills and acceptable slippage, allow one more qualified paper entry.",
                },
            ]
        )
        skipped_changes.append(
            {
                "field": "baseline_settings",
                "reason": "Daily objective review is advisory; no baseline settings were auto-tuned.",
            }
        )
    else:
        skipped_changes.append(
            {
                "field": "capacity",
                "reason": "No clean target-qualified candidate evidence is available yet.",
            }
        )
    return recommendations, skipped_changes


def build_daily_objective_report(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    owned_open: pd.DataFrame | None,
    owned_pending: pd.DataFrame | None,
    owned_closed: pd.DataFrame | None,
    monitored_open: pd.DataFrame | None = None,
    effective_funds: float | None = None,
    clean_candidate_count: int | None = None,
    now: datetime | None = None,
    run_source: str = "cycle",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_profile_key = _normalize_profile_key(profile_key)
    settings_state = dict(state.get("settings") or {})
    settings = normalize_daily_objective_settings(settings_state)
    runtime = dict(state.get("runtime") or {})
    session_day = _session_day_for(now)
    equity_base = max(
        _coerce_float(
            effective_funds,
            _coerce_float(state.get("__actual_funds"), _coerce_float(settings_state.get("account_size"), 10000.0)),
        ),
        1.0,
    )
    target_dollars, target_pct_amount = _target_amount(settings_state, equity_base)
    loss_budget_dollars = max(equity_base * (float(settings["daily_loss_budget_pct"]) / 100.0), 1.0)

    closed_frame = owned_closed if owned_closed is not None else pd.DataFrame()
    realized_pnl, winning_trades, losing_trades, traded_tickers = _daily_realized_pnl(
        closed_frame,
        session_day=session_day,
    )
    open_frame = owned_open if owned_open is not None else pd.DataFrame()
    unrealized_pnl = risk_control_service.estimate_unrealized_pnl(open_frame, monitored_open)
    total_pnl = realized_pnl + unrealized_pnl
    target_gap = max(target_dollars - total_pnl, 0.0)
    target_progress_pct = (total_pnl / target_dollars * 100.0) if target_dollars > 0 else 0.0
    loss_budget_used_pct = max((-total_pnl / loss_budget_dollars * 100.0), 0.0)
    target_reached = total_pnl >= target_dollars
    entries_blocked = bool(settings["daily_objective_enabled"] and total_pnl <= -loss_budget_dollars)

    if not settings["daily_objective_enabled"]:
        status = "disabled"
        label = "Daily objective disabled"
    elif normalized_profile_key != DAILY_OBJECTIVE_PERSONAL_PAPER_PROFILE and not settings["daily_objective_apply_to_live"]:
        status = "not_applicable"
        label = "Paper scope only"
    elif entries_blocked:
        status = "loss_budget_locked"
        label = "Loss budget locked"
    elif target_reached:
        status = "target_reached"
        label = "Target reached"
    else:
        status = "tracking"
        label = "Tracking objective"

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if entries_blocked:
        blockers.append(
            {
                "key": "daily_loss_budget_lock",
                "detail": (
                    f"Daily objective PnL is {total_pnl:,.2f}, breaching the "
                    f"{settings['daily_loss_budget_pct']:.2f}% loss budget."
                ),
            }
        )
    elif loss_budget_used_pct >= 70.0:
        warnings.append(
            {
                "key": "loss_budget_warning",
                "detail": f"Daily loss budget usage is {loss_budget_used_pct:.1f}%.",
            }
        )

    control_plane = dict(runtime.get("state_control_last_review") or {})
    if not control_plane:
        control_plane = {
            "state": runtime.get("state_control_state"),
            "score": runtime.get("state_control_score"),
        }
    if str(control_plane.get("state") or "").strip().lower() in {"watch", "de_risk", "halt"}:
        warnings.append(
            {
                "key": "state_control_not_healthy",
                "detail": f"State-control is {control_plane.get('state')}; do not loosen capacity from objective pressure.",
            }
        )
    accuracy_calibration = dict(runtime.get("accuracy_calibration_last_report") or {})
    weak_accuracy = str(accuracy_calibration.get("status") or "").strip().lower() in {"weak", "blocked"}
    if weak_accuracy:
        warnings.append(
            {
                "key": "accuracy_calibration_weak",
                "detail": (
                    "Decision-PnL calibration is weak; daily objective pressure must not loosen capacity."
                ),
            }
        )
    loss_containment = dict(runtime.get("loss_containment_last_report") or {})
    if bool(loss_containment.get("entries_blocked")):
        blockers.append(
            {
                "key": "loss_containment_blocked",
                "detail": "Loss containment is blocking new entries; objective pressure must not add risk.",
            }
        )
    elif _coerce_float(loss_containment.get("open_heat_pct"), 0.0) > 0:
        warnings.append(
            {
                "key": "loss_containment_open_heat",
                "detail": f"Open heat is {_coerce_float(loss_containment.get('open_heat_pct'), 0.0):.2f}% of equity.",
            }
        )

    candidate_count = int(clean_candidate_count or 0)
    recommendations, skipped_changes = _build_recommendations(
        status=status,
        total_pnl=total_pnl,
        risk_budget_used_pct=loss_budget_used_pct,
        target_progress_pct=target_progress_pct,
        clean_candidate_count=candidate_count,
        control_plane=control_plane,
        settings_state=settings_state,
    )
    if weak_accuracy:
        recommendations = [
            item
            for item in recommendations
            if str(item.get("direction") or "").strip().lower() not in {"consider_small_increase"}
        ]
        recommendations.append(
            {
                "field": "candidate_ranking",
                "direction": "tighten_calibrated_patterns",
                "reason": "Weak decision-PnL accuracy overrides target-gap capacity pressure.",
            }
        )
        skipped_changes.append(
            {
                "field": "capacity",
                "reason": "Capacity expansion skipped because accuracy calibration is weak.",
            }
        )
    effective_overlays = [
        {
            "field": "new_entries",
            "before": "allowed",
            "effective": "blocked" if entries_blocked else "allowed",
            "reason": "Daily loss budget hard stop" if entries_blocked else "Daily objective target-only policy",
        },
        {
            "field": "candidate_ranking",
            "before": "portfolio/execution score",
            "effective": "objective_quality_score",
            "reason": "Prefer candidates with stronger edge/cost, spread, liquidity, and target-gap contribution.",
        },
    ]
    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": normalized_profile_key,
            "session_day": session_day,
            "evaluated_at": _serialize_datetime(now),
            "run_source": str(run_source or "cycle").strip().lower() or "cycle",
            "target_dollars": round(float(target_dollars), 2),
            "target_pct": float(settings["daily_profit_target_pct"]),
            "target_pct_amount": round(float(target_pct_amount), 2),
            "realized_pnl": round(float(realized_pnl), 2),
            "unrealized_pnl": round(float(unrealized_pnl), 2),
            "total_pnl": round(float(total_pnl), 2),
            "target_progress_pct": round(float(target_progress_pct), 2),
            "target_gap": round(float(target_gap), 2),
            "loss_budget_pct": float(settings["daily_loss_budget_pct"]),
            "loss_budget_dollars": round(float(loss_budget_dollars), 2),
            "loss_budget_used_pct": round(float(loss_budget_used_pct), 2),
            "target_reached": bool(target_reached),
            "entries_blocked": bool(entries_blocked),
            "clean_candidate_count": candidate_count,
            "open_position_count": int(len(open_frame)),
            "pending_order_count": int(len(owned_pending) if owned_pending is not None else 0),
            "winning_trade_count": int(winning_trades),
            "losing_trade_count": int(losing_trades),
            "traded_tickers": traded_tickers,
            "blockers": blockers,
            "warnings": warnings,
            "recommendations": recommendations,
            "skipped_changes": skipped_changes,
            "effective_overlays": effective_overlays,
            "apply_to_live": bool(settings["daily_objective_apply_to_live"]),
        }
    )


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=DAILY_OBJECTIVE_NOTE_OWNER,
            limit=DAILY_OBJECTIVE_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "daily-objective",
        "return-target",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags") or []}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _format_note_rows(items: list[dict[str, Any]], *, empty: str) -> list[str]:
    if not items:
        return [f"- {empty}"]
    rows = []
    for item in items[:8]:
        key = str(item.get("field") or item.get("key") or item.get("direction") or "item").replace("_", " ")
        detail = str(item.get("reason") or item.get("detail") or "").strip()
        rows.append(f"- {key}: {detail}" if detail else f"- {key}")
    return rows


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    lines = [
        f"Daily objective review for {getattr(tenant, 'name', None) or getattr(tenant, 'slug', '') or 'tenant'}",
        "",
        f"- Profile: {profile_key}",
        f"- Session: {report.get('session_day')}",
        f"- Status: {report.get('status')}",
        f"- Target: ${float(report.get('target_dollars') or 0.0):,.2f} ({float(report.get('target_pct') or 0.0):.2f}%)",
        f"- PnL: ${float(report.get('total_pnl') or 0.0):,.2f} realized/unrealized",
        f"- Progress: {float(report.get('target_progress_pct') or 0.0):.2f}%",
        f"- Target gap: ${float(report.get('target_gap') or 0.0):,.2f}",
        f"- Loss budget: ${float(report.get('loss_budget_dollars') or 0.0):,.2f} ({float(report.get('loss_budget_used_pct') or 0.0):.2f}% used)",
        f"- Target reached: {'yes' if report.get('target_reached') else 'no'}",
        f"- New entries blocked: {'yes' if report.get('entries_blocked') else 'no'}",
        "",
        "Effective overlays",
    ]
    lines.extend(_format_note_rows(list(report.get("effective_overlays") or []), empty="No objective overlay changes."))
    lines.extend(["", "Recommendations"])
    lines.extend(_format_note_rows(list(report.get("recommendations") or []), empty="No setting changes recommended."))
    lines.extend(["", "Skipped changes"])
    lines.extend(_format_note_rows(list(report.get("skipped_changes") or []), empty="No skipped changes."))
    if report.get("blockers"):
        lines.extend(["", "Blockers"])
        lines.extend(_format_note_rows(list(report.get("blockers") or []), empty="No blockers."))
    if report.get("warnings"):
        lines.extend(["", "Warnings"])
        lines.extend(_format_note_rows(list(report.get("warnings") or []), empty="No warnings."))
    return "\n".join(lines).strip()


def _sync_daily_objective_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "daily-objective",
        "return-target",
        _profile_tag(profile_key),
        f"session-{session_day}",
    ]
    title = f"Daily objective review - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = (
        str(report.get("related_note_id") or report.get("note_id") or "").strip()
        or _find_existing_note_id(profile_key, session_day)
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": DAILY_OBJECTIVE_NOTE_OWNER,
        "note_type": "risk_review",
        "priority": "high" if report.get("entries_blocked") or report.get("blockers") else "medium",
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
        note_id = _sync_daily_objective_note(tenant=tenant, profile_key=normalized_profile_key, report=report)
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
        "target_dollars",
        "target_pct",
        "target_pct_amount",
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
        "target_progress_pct",
        "target_gap",
        "loss_budget_pct",
        "loss_budget_dollars",
        "loss_budget_used_pct",
        "target_reached",
        "entries_blocked",
        "clean_candidate_count",
        "open_position_count",
        "pending_order_count",
        "winning_trade_count",
        "losing_trade_count",
        "blockers",
        "warnings",
        "recommendations",
        "skipped_changes",
        "effective_overlays",
        "note_id",
        "related_note_id",
        "apply_to_live",
    }
    runtime["daily_objective_last_report"] = serialize_value(
        {key: report.get(key) for key in summary_keys if key in report}
    )
    runtime["daily_objective_last_run_at"] = report.get("evaluated_at")
    runtime["daily_objective_last_note_id"] = report.get("related_note_id") or report.get("note_id")
    runtime["daily_objective_note_session_day"] = report.get("session_day")
    runtime["daily_objective_last_error"] = None
    history = list(runtime.get("daily_objective_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "total_pnl": report.get("total_pnl"),
            "target_progress_pct": report.get("target_progress_pct"),
            "loss_budget_used_pct": report.get("loss_budget_used_pct"),
            "entries_blocked": report.get("entries_blocked"),
            "note_id": report.get("related_note_id") or report.get("note_id"),
            "run_source": report.get("run_source"),
        },
    )
    runtime["daily_objective_history"] = serialize_value(history[:DAILY_OBJECTIVE_HISTORY_LIMIT])
    if db is not None and write_note:
        record_audit_event(
            db,
            event_type="trade_automation.daily_objective_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": normalized_profile_key,
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "target_dollars": report.get("target_dollars"),
                "target_progress_pct": report.get("target_progress_pct"),
                "loss_budget_used_pct": report.get("loss_budget_used_pct"),
                "entries_blocked": report.get("entries_blocked"),
                "note_id": report.get("related_note_id") or report.get("note_id"),
                "run_source": report.get("run_source"),
            },
        )
    return serialize_value(report)


def evaluate_daily_objective_entry_gate(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    owned_open: pd.DataFrame | None = None,
    owned_pending: pd.DataFrame | None = None,
    owned_closed: pd.DataFrame | None = None,
    monitored_open: pd.DataFrame | None = None,
    effective_funds: float | None = None,
    clean_candidate_count: int | None = None,
    now: datetime | None = None,
    actor: Any = None,
) -> dict[str, Any]:
    normalized_profile_key = _normalize_profile_key(profile_key)
    report = build_daily_objective_report(
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        owned_open=owned_open,
        owned_pending=owned_pending,
        owned_closed=owned_closed,
        monitored_open=monitored_open,
        effective_funds=effective_funds,
        clean_candidate_count=clean_candidate_count,
        now=now,
        run_source="cycle",
    )
    should_write_note = bool(report.get("entries_blocked"))
    if normalized_profile_key != DAILY_OBJECTIVE_PERSONAL_PAPER_PROFILE and not bool(report.get("apply_to_live")):
        should_write_note = False
    _ = linked_account
    return _persist_report(
        db,
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        report=report,
        actor=actor,
        write_note=should_write_note,
    )


def run_daily_objective_review(
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
    open_frame = _owned_rows(sdm.read_open_trades(), tenant_id=tenant_id, profile_key=normalized_profile_key)
    pending_frame = _owned_rows(sdm.read_pending_orders(), tenant_id=tenant_id, profile_key=normalized_profile_key)
    closed_frame = _owned_rows(sdm.read_closed_trades(), tenant_id=tenant_id, profile_key=normalized_profile_key)
    monitored_frame = _owned_rows(sdm.monitor_open_trades(), tenant_id=tenant_id, profile_key=normalized_profile_key)
    equity_base = _coerce_float(
        state.get("__actual_funds"),
        _coerce_float(state.get("__effective_funds"), _coerce_float((state.get("settings") or {}).get("account_size"), 10000.0)),
    )
    _ = linked_account
    report = build_daily_objective_report(
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        owned_open=open_frame,
        owned_pending=pending_frame,
        owned_closed=closed_frame,
        monitored_open=monitored_frame,
        effective_funds=equity_base,
        clean_candidate_count=None,
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


def _projected_notional(candidate: dict[str, Any], settings_state: dict[str, Any], current_equity: float) -> float:
    direct = _candidate_numeric(
        candidate,
        "projected_position_cost",
        "total_position_cost",
        "position_cost",
        "projected_notional",
        "notional",
    )
    if direct is not None and direct > 0:
        return float(direct)
    max_single_pct = _coerce_float(settings_state.get("max_single_position_pct"), 12.0)
    max_notional = _coerce_float(settings_state.get("max_notional_per_trade"), current_equity * max_single_pct / 100.0)
    return max(0.0, min(max_notional, current_equity * max_single_pct / 100.0))


def _candidate_spread_bps(candidate: dict[str, Any]) -> float | None:
    spread = _candidate_numeric(candidate, "spread_bps", "bid_ask_spread_bps", "quote_spread_bps", "live_spread_bps")
    if spread is not None:
        return spread
    spread_pct = _candidate_numeric(candidate, "spread_pct", "contract_spread_pct")
    if spread_pct is not None:
        return spread_pct * 10000.0
    return None


def score_daily_objective_candidate(
    candidate: dict[str, Any],
    *,
    state: dict[str, Any],
    current_equity: float | None = None,
) -> dict[str, Any]:
    settings_state = dict((state or {}).get("settings") or {})
    settings = normalize_daily_objective_settings(settings_state)
    profile_key = _normalize_profile_key((state or {}).get("profile_key") or DAILY_OBJECTIVE_PERSONAL_PAPER_PROFILE)
    if not settings["daily_objective_enabled"]:
        return {}
    if profile_key != DAILY_OBJECTIVE_PERSONAL_PAPER_PROFILE and not settings["daily_objective_apply_to_live"]:
        return {}
    equity = max(_coerce_float(current_equity, _coerce_float(settings_state.get("account_size"), 10000.0)), 1.0)
    last_report = dict(((state or {}).get("runtime") or {}).get("daily_objective_last_report") or {})
    target_gap = _coerce_float(last_report.get("target_gap"), settings["daily_profit_target_dollars"])
    target_gap = max(target_gap, 1.0)
    portfolio_score = _coerce_float(
        candidate.get("portfolio_score"),
        _coerce_float(candidate.get("ranking_score"), _coerce_float(candidate.get("setup_score"), 0.0)),
    )
    execution_score = _coerce_float(
        candidate.get("execution_score"),
        _coerce_float(candidate.get("ranking_score"), _coerce_float(candidate.get("setup_score"), 0.0)),
    )
    edge_ratio = _coerce_float(candidate.get("edge_to_cost_ratio"), 0.0)
    edge_bps = _coerce_float(
        candidate.get("accuracy_calibrated_expected_edge_bps"),
        _coerce_float(
            candidate.get("expected_edge_bps"),
            _coerce_float(candidate.get("edge_bps"), _coerce_float(candidate.get("forecast_edge_bps"), 0.0)),
        ),
    )
    notional = _projected_notional(candidate, settings_state, equity)
    expected_pnl = max(0.0, notional * max(edge_bps, 0.0) / 10000.0)
    contribution_score = min(expected_pnl / target_gap * 100.0, 20.0)
    spread_bps = _candidate_spread_bps(candidate)
    max_spread = max(_coerce_float(settings_state.get("max_spread_bps"), 25.0), 1.0)
    spread_score = 12.0 if spread_bps is None else max(0.0, 18.0 * (1.0 - min(spread_bps / max_spread, 1.0)))
    liquidity = _candidate_numeric(
        candidate,
        "average_dollar_volume",
        "avg_dollar_volume",
        "average_daily_dollar_volume",
        "dollar_volume",
        "intraday_dollar_volume",
    )
    liquidity_score = 8.0
    if liquidity is not None:
        liquidity_score = min(max(liquidity / max(_coerce_float(settings_state.get("min_average_dollar_volume"), 1_000_000.0), 1.0), 0.0), 3.0) * 5.0
    bucket_pct = _coerce_float(candidate.get("bucket_exposure_before_pct"), 0.0)
    correlation_penalty = min(max(bucket_pct, 0.0) * 0.35, 12.0)
    edge_score = min(max(edge_ratio, 0.0) * 7.0, 24.0)
    objective_score = (
        portfolio_score * 0.32
        + execution_score * 0.28
        + edge_score
        + spread_score
        + liquidity_score
        + contribution_score
        - correlation_penalty
    )
    return {
        "daily_objective_score": round(max(objective_score, 0.0), 2),
        "daily_objective_expected_pnl": round(expected_pnl, 2),
        "daily_objective_target_gap_contribution_pct": round(min(expected_pnl / target_gap * 100.0, 100.0), 2),
        "daily_objective_spread_bps": round(spread_bps, 4) if spread_bps is not None else None,
        "daily_objective_liquidity_score": round(liquidity_score, 2),
        "daily_objective_correlation_penalty": round(correlation_penalty, 2),
        "daily_objective_rank_reason": "edge/cost, spread, liquidity, correlation, and remaining target-gap contribution",
    }


def apply_daily_objective_candidate_overlay(
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
        item.update(score_daily_objective_candidate(item, state=state, current_equity=current_equity))
        annotated.append(item)
    return annotated
