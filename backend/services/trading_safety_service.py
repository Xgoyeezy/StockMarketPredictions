from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.core.config import settings as app_settings
from backend.services.serialization import serialize_value
from backend.services.session_policy import SessionProfile

SAFETY_STATE_READY = "ready"
SAFETY_STATE_DEGRADED = "degraded"
SAFETY_STATE_BLOCKED = "blocked"
SAFETY_STATE_KILLED = "killed"

SAFETY_STATE_SEVERITY = {
    SAFETY_STATE_READY: 0,
    SAFETY_STATE_DEGRADED: 1,
    SAFETY_STATE_BLOCKED: 2,
    SAFETY_STATE_KILLED: 3,
}

SAFETY_EVENT_PREFLIGHT = "trading_safety.preflight"
SAFETY_EVENT_STATE_CHANGED = "trading_safety.state_changed"
SAFETY_EVENT_LEDGER_COMPACTED = "trading_safety.ledger_compacted"
SAFETY_EVENT_SUMMARY_GENERATED = "trading_safety.summary_generated"
SAFETY_EVENT_OBJECTIVE_LOCK = "trading_safety.objective_lock"
SAFETY_EVENT_LOSS_LOCK = "trading_safety.loss_lock"
SAFETY_EVENT_ROUTE_BLOCK = "trading_safety.route_block"

SAFETY_EVENT_TYPES = {
    SAFETY_EVENT_PREFLIGHT,
    SAFETY_EVENT_STATE_CHANGED,
    SAFETY_EVENT_LEDGER_COMPACTED,
    SAFETY_EVENT_SUMMARY_GENERATED,
    SAFETY_EVENT_OBJECTIVE_LOCK,
    SAFETY_EVENT_LOSS_LOCK,
    SAFETY_EVENT_ROUTE_BLOCK,
    "trading_safety.ledger_decode_error",
}

_LEDGER_DIR = Path("runtime") / "trading-safety"
_HFT_BASE_DIR = Path("hft_system") / "data"
_LAST_STATE_FILENAME = "latest_state.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safety_state_severity(status: str | None) -> int:
    return SAFETY_STATE_SEVERITY.get(str(status or "").strip().lower(), SAFETY_STATE_SEVERITY[SAFETY_STATE_DEGRADED])


def strongest_safety_state(statuses: list[str] | tuple[str, ...]) -> str:
    if not statuses:
        return SAFETY_STATE_READY
    return max((str(status or SAFETY_STATE_DEGRADED).strip().lower() for status in statuses), key=safety_state_severity)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized == normalized else default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _market_session_detail(session: dict[str, Any]) -> dict[str, Any]:
    phase = str(session.get("phase") or "").strip().lower()
    session_mode = str(session.get("session_mode") or "").strip().lower()
    new_entries_allowed = bool(session.get("new_entries_allowed", True))
    regular_session = bool(session.get("regular_session") or session_mode == "regular" or "regular" in phase)
    cleanup_window = bool(session.get("cleanup_window"))
    if regular_session and new_entries_allowed and not cleanup_window:
        detail = "regular_open"
        message = "Regular-session paper entries can be considered after all risk gates pass."
    elif cleanup_window or "post" in phase or "close" in phase:
        detail = "post_close_review"
        message = "New entries are stopped; position management and review remain allowed."
    elif "pre" in phase or session_mode == "pre_market":
        detail = "pre_market_waiting"
        message = "Waiting for the configured entry-eligible market window."
    else:
        detail = "market_closed"
        message = "The current session is closed for new unattended paper entries."
    return {
        "detail": detail,
        "phase": phase or None,
        "session_mode": session_mode or None,
        "new_entries_allowed": new_entries_allowed,
        "regular_session": regular_session,
        "cleanup_window": cleanup_window,
        "message": message,
    }


def _route_evidence(route: str, broker_routes: dict[str, Any] | None = None) -> dict[str, Any]:
    route = str(route or "broker_paper").strip().lower() or "broker_paper"
    paper_route = dict((broker_routes or {}).get("broker_paper") or {})
    allowed = route == "broker_paper"
    return {
        "active_route": route,
        "allowed_routes": ["broker_paper"],
        "provider_allowlist": ["alpaca"],
        "alpaca_paper_only": allowed,
        "paper_mode_asserted": allowed,
        "route_mismatch_blocker": None if allowed else "non_alpaca_paper_route",
        "paper_route_connected": bool(paper_route.get("connected", True)),
        "paper_route_detail": paper_route.get("detail") or "Alpaca paper execution is the only unattended route.",
        "live_autonomy_blocked": True,
        "non_alpaca_unattended_blocked": route != "broker_paper",
    }


def _build_account_safety_evidence(snapshot: dict[str, Any], settings_state: dict[str, Any]) -> dict[str, Any]:
    account_summary = dict(
        snapshot.get("account_summary")
        or snapshot.get("account")
        or snapshot.get("broker_account")
        or {}
    )
    configured_floor = _safe_float(settings_state.get("account_size"), 100000.0)
    equity = max(
        _safe_float(account_summary.get("equity")),
        _safe_float(account_summary.get("portfolio_value")),
        _safe_float(snapshot.get("__actual_funds")),
        configured_floor,
    )
    buying_power = _safe_float(account_summary.get("buying_power"), _safe_float(snapshot.get("buying_power"), 0.0))
    drift_pct = abs(equity - configured_floor) / configured_floor * 100.0 if configured_floor > 0 else 0.0
    buying_power_ratio = buying_power / equity if equity > 0 and buying_power > 0 else None
    return {
        "configured_account_floor": round(configured_floor, 4),
        "effective_equity": round(equity, 4),
        "buying_power": round(buying_power, 4),
        "buying_power_is_ceiling": True,
        "equity_drift_pct": round(drift_pct, 4),
        "equity_drift_warning": bool(drift_pct >= 25.0),
        "buying_power_ratio": round(buying_power_ratio, 4) if buying_power_ratio is not None else None,
        "buying_power_warning": bool(buying_power_ratio is not None and buying_power_ratio >= 4.0),
        "paper_account_mode": "alpaca_paper",
        "secrets_exposed": False,
    }


def _build_objective_risk_evidence(snapshot: dict[str, Any], settings_state: dict[str, Any], runtime_state: dict[str, Any]) -> dict[str, Any]:
    objective = dict(snapshot.get("daily_objective") or runtime_state.get("daily_objective_last_report") or {})
    objective_timeframe = str(objective.get("objective_timeframe") or settings_state.get("objective_timeframe") or "weekly").strip().lower()
    target_pct = _safe_float(
        objective.get("target_pct")
        or settings_state.get("weekly_profit_target_max_pct")
        or settings_state.get("daily_profit_target_pct"),
        2.0 if objective_timeframe == "weekly" else 1.0,
    )
    target_min_pct = _safe_float(
        objective.get("target_min_pct") or settings_state.get("weekly_profit_target_min_pct"),
        1.0 if objective_timeframe == "weekly" else target_pct,
    )
    configured_floor = _safe_float(
        objective.get("target_min_dollars")
        or settings_state.get("weekly_profit_target_min_dollars")
        or settings_state.get("daily_profit_target_dollars"),
        1000.0,
    )
    configured_stretch = _safe_float(
        objective.get("target_dollars")
        or settings_state.get("weekly_profit_target_max_dollars")
        or settings_state.get("daily_profit_target_dollars"),
        2000.0 if objective_timeframe == "weekly" else 1000.0,
    )
    equity = _safe_float(snapshot.get("account_safety_equity") or settings_state.get("account_size"), 100000.0)
    min_pct_target = max(equity * (target_min_pct / 100.0), 0.0)
    pct_target = max(equity * (target_pct / 100.0), 0.0)
    effective_min_target = _safe_float(objective.get("target_min_dollars"), max(configured_floor, min_pct_target))
    effective_target = _safe_float(objective.get("target_dollars"), max(configured_stretch, pct_target))
    loss_budget_pct = _safe_float(objective.get("loss_budget_pct") or settings_state.get("daily_loss_budget_pct"), 0.5)
    max_daily_entries = _safe_int(settings_state.get("max_daily_entries"), _safe_int(settings_state.get("max_entries_per_day"), 0))
    entries_today = _safe_int(runtime_state.get("opened_today_count") or runtime_state.get("entries_today") or runtime_state.get("orders_today"))
    entries_remaining = max(max_daily_entries - entries_today, 0) if max_daily_entries else None
    entry_block_reason = str(objective.get("entry_block_reason") or "").strip().lower()
    target_locked = bool(objective.get("target_reached")) or entry_block_reason == "target_reached_protect_streak"
    loss_locked = bool("loss" in entry_block_reason or str(objective.get("status") or "").strip().lower() == "loss_budget_locked")
    return {
        "objective_source": "daily_objective_snapshot" if snapshot.get("daily_objective") else "runtime_or_settings",
        "objective_timeframe": objective_timeframe,
        "objective_mode": objective.get("objective_mode") or ("collective_account_weekly_1_to_2pct" if objective_timeframe == "weekly" else "collective_account_daily_pct"),
        "objective_range_label": objective.get("objective_range_label") or ("1-2% weekly" if objective_timeframe == "weekly" else f"{target_pct:g}% daily"),
        "target_min_pct": target_min_pct,
        "target_min_dollars": round(effective_min_target, 4),
        "target_pct": target_pct,
        "target_floor_dollars": round(configured_floor, 4),
        "target_pct_amount": round(pct_target, 4),
        "target_dollars": round(effective_target, 4),
        "target_recalculated_from_equity": True,
        "target_floor_applied": configured_floor >= pct_target,
        "target_lock_active": target_locked,
        "target_lock_event_type": SAFETY_EVENT_OBJECTIVE_LOCK if target_locked else None,
        "loss_budget_pct": loss_budget_pct,
        "loss_budget_dollars": round(equity * (loss_budget_pct / 100.0), 4),
        "loss_lock_active": loss_locked,
        "loss_lock_event_type": SAFETY_EVENT_LOSS_LOCK if loss_locked else None,
        "entry_block_reason": entry_block_reason or None,
        "daily_entries_remaining": entries_remaining,
        "orders_per_minute_limit": _safe_int(settings_state.get("max_orders_per_minute"), 0) or None,
        "rejects_per_day_limit": _safe_int(settings_state.get("max_rejected_orders_per_day"), 0) or None,
        "not_a_guarantee": "The 1-2% weekly objective is an operating target, not a return guarantee." if objective_timeframe == "weekly" else "The daily objective is an operating target, not a return guarantee.",
    }


def _build_order_guard_evidence(snapshot: dict[str, Any], settings_state: dict[str, Any], counts: dict[str, Any]) -> dict[str, Any]:
    open_positions = _safe_int(counts.get("open_positions"))
    pending_orders = _safe_int(counts.get("pending_orders"))
    max_open_positions = _safe_int(settings_state.get("max_open_positions"), 0)
    max_pending = _safe_int(settings_state.get("max_pending_orders"), 0)
    return {
        "pending_order_count": pending_orders,
        "open_position_count": open_positions,
        "max_open_positions": max_open_positions or None,
        "max_pending_orders": max_pending or None,
        "stale_pending_order_threshold_minutes": _safe_int(settings_state.get("stale_pending_order_minutes"), 15),
        "stale_open_position_threshold_minutes": _safe_int(settings_state.get("stale_open_position_minutes"), 390),
        "duplicate_order_guard": True,
        "max_orders_per_minute_guard": True,
        "max_rejected_orders_per_day_guard": True,
        "global_open_position_cap_blocked": bool(max_open_positions and open_positions >= max_open_positions),
        "pending_order_review_needed": bool(max_pending and pending_orders > max_pending),
        "open_heat_pct": _safe_float(snapshot.get("open_heat_pct"), 0.0),
        "correlation_heat": serialize_value(snapshot.get("correlation_heat") or {}),
        "sector_heat": serialize_value(snapshot.get("sector_heat") or {}),
    }


def _build_trade_proof_evidence() -> dict[str, Any]:
    return {
        "no_forced_trades": True,
        "no_live_order_autonomy": True,
        "no_signal_direct_to_broker": True,
        "no_average_down_proof_required": True,
        "pyramiding_only_if_profitable_proof_required": True,
        "paper_receipt_required": True,
        "audit_ledger_required": True,
    }


def _normalize_day(day: date | str | None) -> str:
    if isinstance(day, date):
        return day.isoformat()
    normalized = str(day or datetime.now(timezone.utc).date().isoformat()).strip()
    if not normalized:
        normalized = datetime.now(timezone.utc).date().isoformat()
    date.fromisoformat(normalized)
    return normalized


def _ledger_path(ledger_dir: Path | str, day: date | str | None = None) -> Path:
    return Path(ledger_dir) / f"{_normalize_day(day)}.jsonl"


def _latest_state_path(ledger_dir: Path | str) -> Path:
    return Path(ledger_dir) / _LAST_STATE_FILENAME


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_cursor(cursor: int | str | None) -> int:
    if cursor is None or cursor == "":
        return 0
    try:
        return max(0, int(cursor))
    except (TypeError, ValueError):
        return 0


def _load_ledger_records(target: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not target.exists():
        return records
    for line_number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                payload.setdefault("line_number", line_number)
                records.append(payload)
            else:
                raise json.JSONDecodeError("ledger row was not an object", line, 0)
        except json.JSONDecodeError:
            records.append(
                {
                    "created_at": None,
                    "event_type": "trading_safety.ledger_decode_error",
                    "status": SAFETY_STATE_DEGRADED,
                    "message": "A ledger line could not be decoded.",
                    "line_number": line_number,
                    "raw": line[:500],
                }
            )
    return records


def _record_matches_filters(record: dict[str, Any], *, event_type: str | None, status: str | None, tenant_slug: str | None = None) -> bool:
    if event_type and str(record.get("event_type") or "") != event_type:
        return False
    if status and str(record.get("status") or "").strip().lower() != str(status).strip().lower():
        return False
    if tenant_slug and str(record.get("tenant_slug") or "").strip().lower() != str(tenant_slug).strip().lower():
        return False
    return True


def append_trading_safety_ledger_event(
    event_type: str,
    *,
    status: str,
    message: str,
    tenant_slug: str | None = None,
    metadata: dict[str, Any] | None = None,
    ledger_dir: Path | str = _LEDGER_DIR,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = now or datetime.now(timezone.utc)
    record = {
        "created_at": timestamp.isoformat(),
        "event_type": str(event_type or "trading_safety.event"),
        "status": str(status or SAFETY_STATE_DEGRADED),
        "message": str(message or ""),
        "tenant_slug": str(tenant_slug or "").strip() or None,
        "metadata": serialize_value(metadata or {}),
    }
    target_dir = Path(ledger_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{timestamp.astimezone(timezone.utc).date().isoformat()}.jsonl"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True))
        handle.write("\n")
    return record


def _parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_trading_safety_ledger(
    *,
    ledger_dir: Path | str = _LEDGER_DIR,
    day: date | str | None = None,
    limit: int = 100,
    cursor: int | str | None = None,
    event_type: str | None = None,
    status: str | None = None,
    tenant_slug: str | None = None,
) -> dict[str, Any]:
    normalized_day = _normalize_day(day)
    target = _ledger_path(ledger_dir, normalized_day)
    records = _load_ledger_records(target)
    filtered = [
        record
        for record in records
        if _record_matches_filters(record, event_type=event_type, status=status, tenant_slug=tenant_slug)
    ]
    bounded_limit = max(1, min(int(limit or 100), 1000))
    start = _parse_cursor(cursor)
    newest_first = list(reversed(filtered))
    items = newest_first[start:start + bounded_limit]
    next_cursor = start + len(items) if start + len(items) < len(newest_first) else None
    return {
        "day": normalized_day,
        "path": str(target),
        "items": items,
        "count": len(records),
        "filtered_count": len(filtered),
        "returned_count": len(items),
        "cursor": start,
        "next_cursor": next_cursor,
        "filters": {
            "event_type": event_type,
            "status": status,
            "tenant_slug": tenant_slug,
        },
    }


def build_trading_safety_daily_summary(
    *,
    ledger_dir: Path | str = _LEDGER_DIR,
    day: date | str | None = None,
    tenant_slug: str | None = None,
) -> dict[str, Any]:
    normalized_day = _normalize_day(day)
    target = _ledger_path(ledger_dir, normalized_day)
    records = [
        record
        for record in _load_ledger_records(target)
        if _record_matches_filters(record, event_type=None, status=None, tenant_slug=tenant_slug)
    ]
    status_counts: dict[str, int] = {}
    event_type_counts: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    latest_record = records[-1] if records else None
    for record in records:
        status = str(record.get("status") or SAFETY_STATE_DEGRADED).strip().lower() or SAFETY_STATE_DEGRADED
        event_type = str(record.get("event_type") or "trading_safety.event")
        message = str(record.get("message") or "").strip()
        status_counts[status] = status_counts.get(status, 0) + 1
        event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
        if status != SAFETY_STATE_READY and message:
            blocker_counts[message] = blocker_counts.get(message, 0) + 1
    statuses = [str(record.get("status") or SAFETY_STATE_DEGRADED) for record in records]
    strongest_status = strongest_safety_state(statuses)
    latest_status = str((latest_record or {}).get("status") or SAFETY_STATE_READY).strip().lower()
    return {
        "day": normalized_day,
        "path": str(target),
        "tenant_slug": tenant_slug,
        "record_count": len(records),
        "latest_status": latest_status,
        "strongest_status": strongest_status,
        "strongest_severity": safety_state_severity(strongest_status),
        "status_counts": status_counts,
        "event_type_counts": event_type_counts,
        "top_blockers": [
            {"message": message, "count": count}
            for message, count in sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
        "latest_event": latest_record,
        "generated_at": utc_now_iso(),
    }


def compact_trading_safety_ledger(
    *,
    ledger_dir: Path | str = _LEDGER_DIR,
    day: date | str | None = None,
    tenant_slug: str | None = None,
) -> dict[str, Any]:
    """Write a non-destructive daily summary artifact beside the JSONL ledger."""

    summary = build_trading_safety_daily_summary(ledger_dir=ledger_dir, day=day, tenant_slug=tenant_slug)
    target_dir = Path(ledger_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{summary['day']}.summary.json"
    target.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    append_trading_safety_ledger_event(
        SAFETY_EVENT_LEDGER_COMPACTED,
        status=summary.get("strongest_status") or SAFETY_STATE_READY,
        message="Trading safety ledger summary artifact written; source JSONL preserved.",
        metadata={"summary_path": str(target), "record_count": summary.get("record_count")},
        ledger_dir=ledger_dir,
    )
    return {
        **summary,
        "summary_path": str(target),
        "source_preserved": True,
    }


def read_last_known_safety_state(*, ledger_dir: Path | str = _LEDGER_DIR) -> dict[str, Any]:
    return _read_json_file(_latest_state_path(ledger_dir))


def write_safety_state_snapshot(
    payload: dict[str, Any],
    *,
    tenant_slug: str | None = None,
    ledger_dir: Path | str = _LEDGER_DIR,
) -> dict[str, Any]:
    target = _latest_state_path(ledger_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = read_last_known_safety_state(ledger_dir=ledger_dir)
    status = str(payload.get("status") or SAFETY_STATE_DEGRADED).strip().lower()
    blocker = payload.get("blocker")
    previous_status = str(previous.get("status") or "").strip().lower()
    previous_blocker = previous.get("blocker")
    changed = status != previous_status or blocker != previous_blocker
    snapshot = {
        **serialize_value(payload),
        "severity": safety_state_severity(status),
        "updated_at": utc_now_iso(),
    }
    target.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    if changed and previous:
        event_time = _parse_iso_datetime(payload.get("checked_at")) or datetime.now(timezone.utc)
        append_trading_safety_ledger_event(
            SAFETY_EVENT_STATE_CHANGED,
            status=status,
            message=str(blocker or f"Trading safety state changed to {status}."),
            tenant_slug=tenant_slug,
            metadata={
                "previous_status": previous_status or None,
                "previous_blocker": previous_blocker,
                "current_status": status,
                "current_blocker": blocker,
            },
            ledger_dir=ledger_dir,
            now=event_time,
        )
    return {
        "state_changed": changed,
        "previous_status": previous_status or None,
        "previous_blocker": previous_blocker,
        "last_known_state_path": str(target),
    }


def evaluate_trade_automation_preflight_gate(
    *,
    settings_state: dict[str, Any],
    runtime_state: dict[str, Any],
    session: dict[str, Any],
    session_profile: SessionProfile | Any,
    execution_intent: str,
    pending_order_count: int = 0,
    open_position_count: int = 0,
) -> dict[str, Any]:
    settings_state = dict(settings_state or {})
    runtime_state = dict(runtime_state or {})
    execution_intent = str(execution_intent or "broker_paper").strip().lower() or "broker_paper"
    status = SAFETY_STATE_READY
    blocker = ""
    next_action = "Continue scanning; every order still has to pass risk, sizing, and paper-route checks."
    reason = "preflight_ready"

    daily_report = dict(runtime_state.get("daily_objective_last_report") or {})
    daily_status = str(daily_report.get("status") or "").strip().lower()
    entry_block_reason = str(daily_report.get("entry_block_reason") or "").strip().lower()
    open_cap = int(settings_state.get("max_open_positions") or 0)
    max_pending = int(settings_state.get("max_pending_orders") or 0)

    if bool(settings_state.get("kill_switch")):
        status = SAFETY_STATE_KILLED
        reason = "kill_switch_active"
        blocker = "Trade-automation kill switch is active."
        next_action = "Review the last blocker and clear the kill switch only after the account state is reconciled."
    elif not bool(settings_state.get("enabled")):
        status = SAFETY_STATE_BLOCKED
        reason = "automation_disabled"
        blocker = "Automation is disabled."
        next_action = "Enable automation only when the route, risk limits, and account health are verified."
    elif not bool(settings_state.get("armed")):
        status = SAFETY_STATE_BLOCKED
        reason = "automation_disarmed"
        blocker = "Automation is not armed."
        next_action = "Arm the paper automation profile after preflight passes."
    elif execution_intent != "broker_paper":
        status = SAFETY_STATE_BLOCKED
        reason = "non_alpaca_paper_route"
        blocker = "The active route is not Alpaca paper execution."
        next_action = "Switch execution intent back to Alpaca paper before unattended automation can scan or submit."
    elif daily_status == "loss_budget_locked" or "loss_budget" in entry_block_reason:
        status = SAFETY_STATE_BLOCKED
        reason = "daily_loss_budget_lock"
        blocker = "The collective daily loss budget is locked."
        next_action = "Stop new entries for the day and review the daily safety ledger."
    elif bool(daily_report.get("target_reached")) or "target_reached" in entry_block_reason:
        status = SAFETY_STATE_BLOCKED
        reason = "objective_protect_streak"
        blocker = "The collective weekly stretch target was reached; protect-streak is blocking new entries."
        next_action = "Manage or flatten existing positions only; do not open new entries."
    elif not bool(session.get("new_entries_allowed", True)) or not bool(getattr(session_profile, "equity_entries_allowed", True)):
        status = SAFETY_STATE_BLOCKED
        reason = "session_entries_closed"
        blocker = "The current market session does not allow new equity entries."
        next_action = "Wait for the next allowed session or manage existing positions only."
    elif open_cap and open_position_count >= open_cap:
        status = SAFETY_STATE_BLOCKED
        reason = "global_open_position_cap"
        blocker = "Global open-position cap is already reached."
        next_action = "Wait for an exit or reduce exposure before opening another position."
    elif max_pending and pending_order_count > max_pending:
        status = SAFETY_STATE_DEGRADED
        reason = "pending_orders_need_review"
        blocker = "Pending paper orders are above the configured review threshold."
        next_action = "Sync or cancel stale paper orders before allowing more submissions."
    else:
        last_error = str(runtime_state.get("last_error") or "").strip()
        if last_error:
            status = SAFETY_STATE_DEGRADED
            reason = "recent_worker_error"
            blocker = last_error
            next_action = "Review the latest worker error before trusting the next cycle."

    return {
        "status": status,
        "safe_to_trade": status == SAFETY_STATE_READY,
        "reason": reason,
        "blocker": blocker or None,
        "next_action": next_action,
        "execution_intent": execution_intent,
        "session_phase": session.get("phase"),
        "session_mode": session.get("session_mode"),
        "pending_order_count": int(pending_order_count or 0),
        "open_position_count": int(open_position_count or 0),
        "daily_objective_status": daily_status or None,
    }


def build_hft_watchdog_latest(*, base_dir: Path | str = _HFT_BASE_DIR) -> dict[str, Any]:
    base = Path(base_dir)
    latest_path = base / "millisecond_watchdog" / "latest.json"
    lock_index = _build_hft_lock_index(base)
    if not latest_path.exists():
        return {
            "available": False,
            "status": "not_started",
            "message": "No HFT watchdog summary has been written yet.",
            "path": str(latest_path),
            **lock_index,
        }
    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "available": False,
            "status": SAFETY_STATE_DEGRADED,
            "message": "Latest HFT watchdog summary could not be decoded.",
            "path": str(latest_path),
            **lock_index,
        }
    return {
        "available": True,
        "path": str(latest_path),
        **lock_index,
        **serialize_value(payload),
    }


def _build_hft_lock_index(base: Path) -> dict[str, Any]:
    root = base / "millisecond_watchdog"
    lock_paths = sorted(root.glob("watchdog_*.lock.json")) if root.exists() else []
    active_locks = []
    for lock_path in lock_paths:
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"status": "decode_error"}
        try:
            heartbeat_ns = int(payload.get("heartbeat_at_ns") or payload.get("created_at_ns") or 0)
        except (TypeError, ValueError):
            heartbeat_ns = 0
        age_seconds = None
        if heartbeat_ns > 0:
            age_seconds = max((time.time_ns() - heartbeat_ns) / 1_000_000_000, 0.0)
        active_locks.append(
            {
                "path": str(lock_path),
                "run_id": payload.get("run_id"),
                "pid": payload.get("pid"),
                "status": payload.get("status"),
                "symbols": payload.get("symbols") or [],
                "heartbeat_age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
                "stale": bool(age_seconds is not None and age_seconds > 120),
            }
        )
    return {
        "active_lock_count": len(active_locks),
        "stale_lock_count": sum(1 for item in active_locks if item.get("stale")),
        "active_locks": active_locks,
    }


def _status_payload(status: str, *, blocker: str | None, next_action: str, checked_at: str) -> dict[str, Any]:
    labels = {
        SAFETY_STATE_READY: ("Ready", "positive"),
        SAFETY_STATE_DEGRADED: ("Needs attention", "warning"),
        SAFETY_STATE_BLOCKED: ("Blocked", "negative"),
        SAFETY_STATE_KILLED: ("Killed", "negative"),
    }
    label, tone = labels.get(status, ("Needs attention", "warning"))
    return {
        "status": status,
        "label": label,
        "tone": tone,
        "blocker": blocker,
        "next_action": next_action,
        "checked_at": checked_at,
    }


def _loss_containment_blocker_detail(loss_containment: dict[str, Any]) -> str | None:
    blockers = [
        item
        for item in list((loss_containment or {}).get("blockers") or [])
        if isinstance(item, dict)
    ]
    first_blocker = blockers[0] if blockers else {}
    return (
        str(first_blocker.get("detail") or "").strip()
        or str((loss_containment or {}).get("label") or "").strip()
        or None
    )


def build_trade_automation_safety_state(
    db: Session,
    *,
    current_user: Any,
    ledger_dir: Path | str = _LEDGER_DIR,
    hft_base_dir: Path | str = _HFT_BASE_DIR,
) -> dict[str, Any]:
    from backend.services import trade_automation_service
    from backend.services.alpaca_paper_readiness_service import build_alpaca_paper_readiness_snapshot
    from backend.services.job_queue_service import get_job_worker_status

    checked_at = utc_now_iso()
    snapshot = trade_automation_service.get_tenant_trade_automation_snapshot(db, current_user=current_user)
    desks = trade_automation_service.list_tenant_trade_automation_desks(db, current_user=current_user)
    worker_status = get_job_worker_status()
    settings = dict(snapshot.get("settings") or {})
    runtime = dict(snapshot.get("runtime") or {})
    session = dict(snapshot.get("session") or {})
    status_payload = dict(snapshot.get("status") or {})
    route = str(settings.get("execution_intent") or "broker_paper").strip().lower() or "broker_paper"
    counts = dict(snapshot.get("counts") or {})
    hft_latest = build_hft_watchdog_latest(base_dir=hft_base_dir)
    account_safety = _build_account_safety_evidence(snapshot, settings)
    alpaca_readiness = build_alpaca_paper_readiness_snapshot(account_summary={"equity": account_safety.get("effective_equity"), "buying_power": account_safety.get("buying_power")})
    objective_evidence = _build_objective_risk_evidence(
        {**snapshot, "account_safety_equity": account_safety.get("effective_equity")},
        settings,
        runtime,
    )
    loss_containment = dict(snapshot.get("loss_containment") or {})
    order_safety = _build_order_guard_evidence(snapshot, settings, counts)
    market_session = _market_session_detail(session)
    route_evidence = _route_evidence(route, broker_routes=dict(snapshot.get("broker_routes") or {}))
    position_promotion = dict((desks.get("global") or {}).get("position_promotion") or snapshot.get("position_promotion") or {})

    gate = evaluate_trade_automation_preflight_gate(
        settings_state=settings,
        runtime_state=runtime,
        session=session,
        session_profile=type("SessionProfileRecord", (), {"equity_entries_allowed": bool(session.get("new_entries_allowed", True))})(),
        execution_intent=route,
        pending_order_count=int(counts.get("pending_orders") or 0),
        open_position_count=int(counts.get("open_positions") or 0),
    )
    status = str(gate.get("status") or SAFETY_STATE_DEGRADED)
    blocker = gate.get("blocker")
    next_action = str(gate.get("next_action") or "Review the automation diagnostics before the next cycle.")
    degraded_reasons: list[str] = []
    kill_switch_context: dict[str, Any] = {
        "active": status == SAFETY_STATE_KILLED,
        "reason": gate.get("reason") if status == SAFETY_STATE_KILLED else None,
        "detail": None,
        "source": None,
    }

    loss_containment_detail = _loss_containment_blocker_detail(loss_containment)
    state_transition = dict(runtime.get("state_control_last_transition") or {})
    if status == SAFETY_STATE_KILLED and loss_containment_detail:
        blocker = f"Kill switch active after loss-containment halt: {loss_containment_detail}"
        next_action = (
            "Resolve the defensive position issue, confirm reconciliation is clean, then clear the kill switch manually."
        )
        degraded_reasons.append("kill_switch_loss_containment")
        kill_switch_context.update(
            {
                "detail": loss_containment_detail,
                "source": "loss_containment",
                "transition": serialize_value(state_transition) if state_transition else None,
            }
        )
    elif status == SAFETY_STATE_KILLED and state_transition:
        transition_detail = str(state_transition.get("detail") or state_transition.get("reason") or "").strip()
        if transition_detail:
            blocker = f"Kill switch active after state-control halt: {transition_detail}"
            next_action = "Review the state-control halt, confirm reconciliation is clean, then clear the kill switch manually."
            degraded_reasons.append("kill_switch_state_control")
            kill_switch_context.update(
                {
                    "detail": transition_detail,
                    "source": "state_control",
                    "transition": serialize_value(state_transition),
                }
            )

    broker_routes = dict(snapshot.get("broker_routes") or {})
    paper_route = dict(broker_routes.get("broker_paper") or {})
    if status == SAFETY_STATE_READY and not bool(route_evidence.get("alpaca_paper_only")):
        status = SAFETY_STATE_BLOCKED
        blocker = "The active unattended route is not Alpaca paper execution."
        next_action = "Switch the execution route back to Alpaca paper before market-open scanning."
        degraded_reasons.append("route_mismatch")
    if status == SAFETY_STATE_READY and not bool(paper_route.get("connected", True)):
        status = SAFETY_STATE_DEGRADED
        blocker = "Alpaca paper credentials or account health could not be confirmed."
        next_action = "Verify Alpaca paper keys and run market-open readiness before scanning."
        degraded_reasons.append("alpaca_paper_unconfirmed")

    if status == SAFETY_STATE_READY and not desks.get("items"):
        status = SAFETY_STATE_DEGRADED
        blocker = "No automation desks are configured."
        next_action = "Restore desk defaults before market-open scanning."
        degraded_reasons.append("no_desks")
    if (
        status == SAFETY_STATE_READY
        and bool(session.get("new_entries_allowed", True))
        and bool(worker_status.get("stale") or worker_status.get("status") == "running_but_stale")
    ):
        status = SAFETY_STATE_DEGRADED
        blocker = "The automation worker is running but stale."
        next_action = "Restart the backend worker/runtime before expecting due desks to scan."
        degraded_reasons.append("worker_stale")
    if status not in {SAFETY_STATE_KILLED, SAFETY_STATE_BLOCKED} and bool(loss_containment.get("entries_blocked")):
        blocker = (
            loss_containment_detail
            or "Loss containment is blocking new entries."
        )
        status = SAFETY_STATE_BLOCKED
        next_action = (
            "Refresh open-position quote evidence and rerun loss-containment review before expecting new entries."
        )
        degraded_reasons.append("loss_containment_block")

    desk_items = []
    for item in desks.get("items") or []:
        runtime_item = dict(item.get("runtime") or {})
        last_decision = item.get("last_decision") or runtime_item.get("last_decision")
        top_blocker = item.get("top_blocker") or item.get("desk_reason_for_no_trade")
        safe_to_trade = bool(item.get("enabled")) and bool(item.get("armed")) and status == SAFETY_STATE_READY and not top_blocker
        desk_items.append(
            {
                "desk_key": item.get("desk_key"),
                "label": item.get("label") or item.get("desk_key"),
                "enabled": bool(item.get("enabled")),
                "armed": bool(item.get("armed")),
                "last_scan_at": item.get("last_scan_at"),
                "next_scan_at": item.get("next_scan_at"),
                "last_decision": serialize_value(last_decision),
                "top_blocker": top_blocker,
                "open_exposure": item.get("open_exposure") or item.get("open_notional") or 0,
                "orders_today": int(item.get("orders_today") or runtime_item.get("orders_today") or 0),
                "rejects_today": int(item.get("rejects_today") or runtime_item.get("rejects_today") or 0),
                "safe_to_trade": safe_to_trade,
            }
        )

    append_trading_safety_ledger_event(
        SAFETY_EVENT_PREFLIGHT,
        status=status,
        message=str(blocker or "Trading safety preflight completed."),
        tenant_slug=(snapshot.get("tenant") or {}).get("slug"),
        metadata={
            "route": route,
            "gate": gate,
            "market_session": market_session,
            "account_safety": account_safety,
            "objective_evidence": objective_evidence,
            "order_safety": order_safety,
            "worker_status": worker_status,
            "degraded_reasons": degraded_reasons,
            "desk_count": len(desk_items),
            "safe_desk_count": sum(1 for item in desk_items if item.get("safe_to_trade")),
        },
        ledger_dir=ledger_dir,
    )

    payload = {
        **_status_payload(status, blocker=blocker, next_action=next_action, checked_at=checked_at),
        "severity": safety_state_severity(status),
        "route": {
            "active": route,
            "allowed": route == "broker_paper",
            "provider": "alpaca",
            "mode": "paper",
            "detail": "Alpaca paper execution is the only active unattended route.",
        },
        "route_enforcement": route_evidence,
        "market_session": market_session,
        "account_safety": account_safety,
        "objective_evidence": objective_evidence,
        "order_safety": order_safety,
        "position_promotion": serialize_value(position_promotion),
        "trade_proof": _build_trade_proof_evidence(),
        "preflight": gate,
        "kill_switch_context": serialize_value(kill_switch_context),
        "degraded_reasons": degraded_reasons,
        "daily_objective": serialize_value(snapshot.get("daily_objective") or {}),
        "loss_containment": serialize_value(loss_containment),
        "rate_limit_considerations": {
            "safety_state_cache_seconds": 60,
            "ledger_page_limit_max": 1000,
            "safe_for_operator_polling": True,
        },
        "desks": {
            "items": desk_items,
            "count": len(desk_items),
            "safe_to_trade_count": sum(1 for item in desk_items if item.get("safe_to_trade")),
        },
        "hft_watchdog": hft_latest,
        "worker": serialize_value(worker_status),
        "dependency_health": {
            "alpaca_paper": {
                "status": alpaca_readiness.get("status") or ("ready" if bool(route_evidence.get("paper_route_connected")) else "degraded"),
                "credentials_present": bool(app_settings.alpaca_api_key_id and app_settings.alpaca_api_secret_key),
                "paper_base_url": app_settings.alpaca_paper_trading_api_url,
                "secrets_exposed": False,
                "readiness": alpaca_readiness,
            },
            "local_db": {
                "status": "available",
                "detail": "Safety state was built from the application database and local trade ledgers.",
            },
            "hft_artifacts": {
                "status": "available" if hft_latest.get("available") else "not_started",
                "stale_lock_count": hft_latest.get("stale_lock_count"),
            },
        },
        "links": {
            "candidate_diagnostics": "/api/orgs/trade-automation/candidate-diagnostics",
            "daily_ledger": "/api/orgs/trade-automation/daily-ledger",
            "daily_summary": "/api/orgs/trade-automation/daily-safety-summary",
            "position_promotion": "/api/orgs/trade-automation/position-promotion",
            "hft_watchdog_latest": "/api/orgs/trade-automation/hft-watchdog/latest",
            "alpaca_paper_readiness": "/api/orgs/trade-automation/alpaca-paper-readiness",
        },
    }
    snapshot_meta = write_safety_state_snapshot(
        payload,
        tenant_slug=(snapshot.get("tenant") or {}).get("slug"),
        ledger_dir=ledger_dir,
    )
    return {
        **payload,
        **snapshot_meta,
    }
