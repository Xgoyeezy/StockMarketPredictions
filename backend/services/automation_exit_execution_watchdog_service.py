from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import DomainEventLog, OrderEventRecord, Tenant
from backend.services import notes_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

MARKET_TIMEZONE = ZoneInfo("America/New_York")
EXIT_WATCHDOG_NOTE_OWNER = "automation-ai"
EXIT_WATCHDOG_HISTORY_LIMIT = 12
EXIT_WATCHDOG_NOTE_LIMIT = 250
EXIT_WATCHDOG_PERSONAL_PAPER_PROFILE = "personal_paper"
EXIT_WATCHDOG_PERSONAL_LIVE_PROFILE = "personal_live"

EXIT_WATCHDOG_SETTINGS_DEFAULTS: dict[str, Any] = {
    "exit_watchdog_enabled": True,
    "exit_watchdog_apply_to_live": False,
    "exit_watchdog_max_confirmation_seconds": 60,
    "exit_watchdog_max_partial_minutes": 5,
    "exit_watchdog_block_entries_on_unconfirmed_exit": True,
}

_TERMINAL_ORDER_EVENTS = {"order.closed", "order.canceled"}
_PARTIAL_ORDER_EVENTS = {"order.partially_closed"}
_FAILED_ORDER_EVENTS = {
    "order.rejected",
    "order.failed",
    "order.blocked",
    "order.close_failed",
}
_OPTION_EXIT_SUBMITTED_EVENTS = {"options.paper_exit_submitted"}
_OPTION_EXIT_FAILED_EVENTS = {"options.paper_exit_blocked"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = pd.Timestamp(cleaned).to_pydatetime()
        except Exception:
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


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


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


def _normalize_profile_key(profile_key: str | None) -> str:
    return str(profile_key or EXIT_WATCHDOG_PERSONAL_PAPER_PROFILE).strip().lower()


def _profile_tag(profile_key: str) -> str:
    cleaned = str(profile_key or "").strip().lower().replace(":", "-") or EXIT_WATCHDOG_PERSONAL_PAPER_PROFILE
    return f"profile-{cleaned}"


def normalize_exit_watchdog_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    defaults = EXIT_WATCHDOG_SETTINGS_DEFAULTS
    return {
        "exit_watchdog_enabled": _coerce_bool(
            state.get("exit_watchdog_enabled"),
            bool(defaults["exit_watchdog_enabled"]),
        ),
        "exit_watchdog_apply_to_live": _coerce_bool(
            state.get("exit_watchdog_apply_to_live"),
            bool(defaults["exit_watchdog_apply_to_live"]),
        ),
        "exit_watchdog_max_confirmation_seconds": _clamp_int(
            state.get("exit_watchdog_max_confirmation_seconds"),
            int(defaults["exit_watchdog_max_confirmation_seconds"]),
            minimum=5,
            maximum=3600,
        ),
        "exit_watchdog_max_partial_minutes": _clamp_int(
            state.get("exit_watchdog_max_partial_minutes"),
            int(defaults["exit_watchdog_max_partial_minutes"]),
            minimum=1,
            maximum=240,
        ),
        "exit_watchdog_block_entries_on_unconfirmed_exit": _coerce_bool(
            state.get("exit_watchdog_block_entries_on_unconfirmed_exit"),
            bool(defaults["exit_watchdog_block_entries_on_unconfirmed_exit"]),
        ),
    }


def normalize_exit_watchdog_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    history = [
        serialize_value(item)
        for item in list(runtime.get("exit_watchdog_history") or [])[:EXIT_WATCHDOG_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "exit_watchdog_last_report": serialize_value(runtime.get("exit_watchdog_last_report") or {}),
        "exit_watchdog_last_note_id": str(runtime.get("exit_watchdog_last_note_id") or "").strip() or None,
        "exit_watchdog_note_session_day": str(runtime.get("exit_watchdog_note_session_day") or "").strip() or None,
        "exit_watchdog_last_run_at": _serialize_datetime(_parse_datetime(runtime.get("exit_watchdog_last_run_at"))),
        "exit_watchdog_last_error": str(runtime.get("exit_watchdog_last_error") or "").strip() or None,
        "exit_watchdog_history": history,
    }


def build_exit_watchdog_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    settings = normalize_exit_watchdog_settings(state.get("settings"))
    runtime = normalize_exit_watchdog_runtime(state.get("runtime"))
    report = dict(runtime.get("exit_watchdog_last_report") or {})
    if not report:
        return {
            "status": "not_run" if settings["exit_watchdog_enabled"] else "disabled",
            "label": "Not run" if settings["exit_watchdog_enabled"] else "Disabled",
            "enabled": settings["exit_watchdog_enabled"],
            "apply_to_live": settings["exit_watchdog_apply_to_live"],
            "entries_blocked": False,
            "pending_exit_count": 0,
            "confirmed_exit_count": 0,
            "stuck_exit_count": 0,
            "related_note_id": runtime.get("exit_watchdog_last_note_id"),
            "history": runtime.get("exit_watchdog_history") or [],
        }
    report.setdefault("enabled", settings["exit_watchdog_enabled"])
    report.setdefault("apply_to_live", settings["exit_watchdog_apply_to_live"])
    report.setdefault("related_note_id", runtime.get("exit_watchdog_last_note_id"))
    report["history"] = runtime.get("exit_watchdog_history") or []
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


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _match_key_set(item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field in ("trade_id", "order_id", "broker_order_id"):
        value = _clean_text(item.get(field))
        if value:
            keys.add(f"{field}:{value}")
            keys.add(value)
    ticker = _clean_text(item.get("ticker")).upper()
    if ticker:
        keys.add(f"ticker:{ticker}")
    return keys


def _matches_action(candidate: dict[str, Any], action_keys: set[str]) -> bool:
    if not action_keys:
        return False
    return bool(_match_key_set(candidate) & action_keys)


def _serialize_order_event(row: OrderEventRecord) -> dict[str, Any]:
    payload = dict(row.payload_json or {})
    payload_trade = dict(payload.get("trade") or {})
    return {
        "id": row.id,
        "trade_id": row.trade_id,
        "ticker": row.ticker,
        "event_key": row.event_key,
        "status": row.status,
        "book_state": row.book_state,
        "route_state": row.route_state,
        "detail": row.detail,
        "payload": serialize_value(payload),
        "payload_trade_id": _clean_text(payload_trade.get("trade_id") or payload.get("trade_id")),
        "payload_order_id": _clean_text(payload_trade.get("order_id") or payload.get("order_id")),
        "created_at": _serialize_datetime(row.created_at),
    }


def _load_order_events(db: Session | None, tenant: Tenant, *, session_day: str, limit: int = 500) -> list[dict[str, Any]]:
    if db is None:
        return []
    start, end = _session_bounds_utc(session_day)
    rows = (
        db.execute(
            select(OrderEventRecord)
            .where(OrderEventRecord.tenant_id == tenant.id)
            .where(OrderEventRecord.created_at >= start, OrderEventRecord.created_at <= end)
            .order_by(OrderEventRecord.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [_serialize_order_event(row) for row in rows]


def _serialize_domain_event(row: DomainEventLog) -> dict[str, Any]:
    payload = dict(row.payload_json or {})
    return {
        "id": row.id,
        "event_type": row.event_type,
        "aggregate_id": row.aggregate_id,
        "status": row.status,
        "payload": serialize_value(payload),
        "trade_id": _clean_text(payload.get("trade_id")),
        "order_id": _clean_text(payload.get("order_id")),
        "ticker": _clean_text(payload.get("ticker")).upper(),
        "detail": _clean_text(payload.get("detail")),
        "created_at": _serialize_datetime(row.created_at),
    }


def _load_option_exit_events(db: Session | None, tenant: Tenant, *, session_day: str, limit: int = 500) -> list[dict[str, Any]]:
    if db is None:
        return []
    start, end = _session_bounds_utc(session_day)
    rows = (
        db.execute(
            select(DomainEventLog)
            .where(DomainEventLog.tenant_id == tenant.id)
            .where(DomainEventLog.event_type.in_(sorted(_OPTION_EXIT_SUBMITTED_EVENTS | _OPTION_EXIT_FAILED_EVENTS)))
            .where(DomainEventLog.created_at >= start, DomainEventLog.created_at <= end)
            .order_by(DomainEventLog.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [_serialize_domain_event(row) for row in rows]


def _closed_ledger_matches(action: dict[str, Any], closed_frame: pd.DataFrame | None, requested_at: datetime | None) -> dict[str, Any] | None:
    if closed_frame is None or closed_frame.empty:
        return None
    action_keys = _match_key_set(action)
    rows = closed_frame.copy()
    for row in rows.to_dict(orient="records"):
        if not _matches_action(row, action_keys):
            continue
        closed_at = _parse_datetime(row.get("closed_at") or row.get("updated_at") or row.get("created_at"))
        if requested_at and closed_at and closed_at < requested_at:
            continue
        return {
            "source": "closed_trade_ledger",
            "trade_id": _clean_text(row.get("trade_id")) or None,
            "order_id": _clean_text(row.get("order_id")) or None,
            "ticker": _clean_text(row.get("ticker")).upper() or None,
            "created_at": _serialize_datetime(closed_at),
            "status": _clean_text(row.get("status") or "closed").lower() or "closed",
        }
    return None


def _latest_matching_event(
    action: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    requested_at: datetime | None,
) -> dict[str, Any] | None:
    action_keys = _match_key_set(action)
    matches: list[dict[str, Any]] = []
    for item in events:
        candidate = {
            "trade_id": item.get("trade_id") or item.get("payload_trade_id"),
            "order_id": item.get("payload_order_id"),
            "ticker": item.get("ticker"),
        }
        if not _matches_action(candidate, action_keys):
            continue
        event_at = _parse_datetime(item.get("created_at"))
        if requested_at and event_at and event_at < requested_at:
            continue
        matches.append(item)
    if not matches:
        return None
    return matches[0]


def _evaluate_exit_action(
    action: dict[str, Any],
    *,
    requested_at: datetime,
    now: datetime,
    closed_frame: pd.DataFrame | None,
    order_events: list[dict[str, Any]],
    option_exit_events: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    elapsed_seconds = max(0.0, float((now - requested_at).total_seconds()))
    terminal_ledger = _closed_ledger_matches(action, closed_frame, requested_at)
    latest_order_event = _latest_matching_event(action, order_events, requested_at=requested_at)
    latest_option_event = _latest_matching_event(action, option_exit_events, requested_at=requested_at)
    event_key = str((latest_order_event or {}).get("event_key") or "").strip().lower()
    event_status = str((latest_order_event or {}).get("status") or "").strip().lower()
    option_event_type = str((latest_option_event or {}).get("event_type") or "").strip().lower()

    if terminal_ledger or event_key in _TERMINAL_ORDER_EVENTS or event_status in {"closed", "canceled"}:
        status = "confirmed"
        detail = "Defensive exit has terminal order or ledger evidence."
        terminal = terminal_ledger or latest_order_event
    elif event_key in _FAILED_ORDER_EVENTS or event_status in {"rejected", "failed", "blocked"} or option_event_type in _OPTION_EXIT_FAILED_EVENTS:
        status = "failed"
        detail = "Defensive exit has failed or blocked evidence."
        terminal = latest_order_event or latest_option_event
    elif event_key in _PARTIAL_ORDER_EVENTS or event_status == "partially_closed":
        partial_at = _parse_datetime((latest_order_event or {}).get("created_at")) or requested_at
        partial_minutes = max(0.0, float((now - partial_at).total_seconds() / 60.0))
        if partial_minutes >= float(settings["exit_watchdog_max_partial_minutes"]):
            status = "stuck_partial"
            detail = "Defensive exit is still partial beyond the configured partial-close window."
        else:
            status = "partial"
            detail = "Defensive exit has partial-close evidence and is still inside the confirmation window."
        terminal = latest_order_event
    elif option_event_type in _OPTION_EXIT_SUBMITTED_EVENTS:
        if elapsed_seconds >= float(settings["exit_watchdog_max_confirmation_seconds"]):
            status = "stuck"
            detail = "Option defensive exit was submitted but has no terminal order or ledger evidence yet."
        else:
            status = "pending"
            detail = "Option defensive exit submission evidence exists and is still awaiting terminal confirmation."
        terminal = latest_option_event
    elif elapsed_seconds >= float(settings["exit_watchdog_max_confirmation_seconds"]):
        status = "stuck"
        detail = "Defensive exit has no terminal order or ledger evidence within the confirmation window."
        terminal = None
    else:
        status = "pending"
        detail = "Defensive exit is waiting for order-event or ledger confirmation."
        terminal = None

    return {
        "trade_id": _clean_text(action.get("trade_id")) or None,
        "order_id": _clean_text(action.get("order_id")) or None,
        "ticker": _clean_text(action.get("ticker")).upper() or None,
        "requested_at": _serialize_datetime(requested_at),
        "elapsed_seconds": round(elapsed_seconds, 4),
        "status": status,
        "detail": detail,
        "terminal_evidence": serialize_value(terminal or {}),
        "latest_order_event": serialize_value(latest_order_event or {}),
        "latest_option_exit_event": serialize_value(latest_option_event or {}),
        "action": _clean_text(action.get("action") or "EXIT FULLY NOW").upper() or "EXIT FULLY NOW",
        "reason": _clean_text(action.get("reason")) or None,
    }


def build_exit_watchdog_report(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    owned_closed: pd.DataFrame | None,
    order_events: list[dict[str, Any]] | None = None,
    option_exit_events: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    run_source: str = "cycle",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_profile_key = _normalize_profile_key(profile_key)
    settings = normalize_exit_watchdog_settings(state.get("settings"))
    session_day = _session_day_for(now)
    runtime = dict(state.get("runtime") or {})
    loss_report = dict(runtime.get("loss_containment_last_report") or {})
    defensive_actions = list(loss_report.get("defensive_actions") or [])
    requested_at = _parse_datetime(loss_report.get("evaluated_at")) or now
    allowed_scope = normalized_profile_key == EXIT_WATCHDOG_PERSONAL_PAPER_PROFILE or bool(
        settings["exit_watchdog_apply_to_live"]
    )

    evaluations: list[dict[str, Any]] = []
    if settings["exit_watchdog_enabled"] and allowed_scope:
        for action in defensive_actions[:20]:
            if not isinstance(action, dict):
                continue
            evaluations.append(
                _evaluate_exit_action(
                    action,
                    requested_at=requested_at,
                    now=now,
                    closed_frame=owned_closed,
                    order_events=list(order_events or []),
                    option_exit_events=list(option_exit_events or []),
                    settings=settings,
                )
            )

    pending = [item for item in evaluations if item["status"] == "pending"]
    partial = [item for item in evaluations if item["status"] == "partial"]
    confirmed = [item for item in evaluations if item["status"] == "confirmed"]
    failed = [item for item in evaluations if item["status"] == "failed"]
    stuck = [item for item in evaluations if item["status"] in {"stuck", "stuck_partial"}]
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for item in failed:
        blockers.append(
            {
                "key": "exit_failed",
                "ticker": item.get("ticker"),
                "detail": item.get("detail") or "Defensive exit failed.",
            }
        )
    for item in stuck:
        blockers.append(
            {
                "key": item["status"],
                "ticker": item.get("ticker"),
                "detail": item.get("detail") or "Defensive exit is stuck.",
            }
        )
    for item in pending + partial:
        warnings.append(
            {
                "key": f"exit_{item['status']}",
                "ticker": item.get("ticker"),
                "detail": item.get("detail") or "Defensive exit is awaiting confirmation.",
            }
        )

    live_scope_blocked = (
        normalized_profile_key == EXIT_WATCHDOG_PERSONAL_LIVE_PROFILE
        and not bool(settings["exit_watchdog_apply_to_live"])
    )
    if live_scope_blocked and defensive_actions:
        warnings.append(
            {
                "key": "live_scope_advisory_only",
                "detail": "Exit watchdog is paper-first; live profile findings are advisory unless live scope is enabled.",
            }
        )

    entries_blocked = bool(
        settings["exit_watchdog_enabled"]
        and allowed_scope
        and settings["exit_watchdog_block_entries_on_unconfirmed_exit"]
        and (blockers or stuck or failed)
    )
    if not settings["exit_watchdog_enabled"]:
        status = "disabled"
        label = "Exit watchdog disabled"
    elif not allowed_scope:
        status = "not_applicable"
        label = "Paper scope only"
    elif failed:
        status = "halt"
        label = "Exit failed"
    elif stuck:
        status = "blocked"
        label = "Exit confirmation stuck"
    elif pending or partial:
        status = "watch"
        label = "Exit confirmation pending"
    elif defensive_actions:
        status = "clean"
        label = "Defensive exits confirmed"
    else:
        status = "clean"
        label = "No defensive exits pending"

    worst_delay = max([float(item.get("elapsed_seconds") or 0.0) for item in evaluations] or [0.0])
    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": normalized_profile_key,
            "session_day": session_day,
            "evaluated_at": _serialize_datetime(now),
            "run_source": str(run_source or "cycle").strip().lower() or "cycle",
            "enabled": bool(settings["exit_watchdog_enabled"]),
            "apply_to_live": bool(settings["exit_watchdog_apply_to_live"]),
            "entries_blocked": bool(entries_blocked),
            "pending_exit_count": len(pending) + len(partial),
            "confirmed_exit_count": len(confirmed),
            "stuck_exit_count": len(stuck),
            "failed_exit_count": len(failed),
            "defensive_exit_count": len(defensive_actions),
            "worst_delay_seconds": round(float(worst_delay), 4),
            "max_confirmation_seconds": int(settings["exit_watchdog_max_confirmation_seconds"]),
            "max_partial_minutes": int(settings["exit_watchdog_max_partial_minutes"]),
            "exit_evaluations": evaluations[:20],
            "blockers": blockers[:20],
            "warnings": warnings[:20],
            "effective_overlays": [
                {
                    "field": "new_entries",
                    "before": "allowed",
                    "effective": "blocked" if entries_blocked else "allowed",
                    "reason": "Defensive exit confirmation is missing or failed."
                    if entries_blocked
                    else "Exit evidence is clean or still inside watch-only timing.",
                }
            ],
            "skipped_changes": [
                {
                    "field": "broker_close_path",
                    "reason": "Exit watchdog is evidence-only and does not create a new broker close path.",
                }
            ],
        }
    )


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=EXIT_WATCHDOG_NOTE_OWNER,
            limit=EXIT_WATCHDOG_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "exit-watchdog",
        "loss-containment",
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
    for item in items[:10]:
        key = str(item.get("ticker") or item.get("key") or item.get("status") or "item").replace("_", " ")
        detail = str(item.get("detail") or item.get("reason") or item.get("status") or "").strip()
        rows.append(f"- {key}: {detail}" if detail else f"- {key}")
    return rows


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    lines = [
        f"Exit execution watchdog review for {getattr(tenant, 'name', None) or getattr(tenant, 'slug', '') or 'tenant'}",
        "",
        f"- Profile: {profile_key}",
        f"- Session: {report.get('session_day')}",
        f"- Status: {report.get('status')}",
        f"- Defensive exits: {int(report.get('defensive_exit_count') or 0)}",
        f"- Confirmed exits: {int(report.get('confirmed_exit_count') or 0)}",
        f"- Pending exits: {int(report.get('pending_exit_count') or 0)}",
        f"- Stuck exits: {int(report.get('stuck_exit_count') or 0)}",
        f"- Failed exits: {int(report.get('failed_exit_count') or 0)}",
        f"- Worst delay: {float(report.get('worst_delay_seconds') or 0.0):.0f}s",
        f"- New entries blocked: {'yes' if report.get('entries_blocked') else 'no'}",
        "",
        "Exit evaluations",
    ]
    lines.extend(_format_note_rows(list(report.get("exit_evaluations") or []), empty="No defensive exits pending."))
    if report.get("blockers"):
        lines.extend(["", "Blockers"])
        lines.extend(_format_note_rows(list(report.get("blockers") or []), empty="No blockers."))
    if report.get("warnings"):
        lines.extend(["", "Warnings"])
        lines.extend(_format_note_rows(list(report.get("warnings") or []), empty="No warnings."))
    lines.extend(["", "Skipped changes"])
    lines.extend(_format_note_rows(list(report.get("skipped_changes") or []), empty="No skipped changes."))
    return "\n".join(lines).strip()


def _sync_exit_watchdog_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "exit-watchdog",
        "loss-containment",
        _profile_tag(profile_key),
        f"session-{session_day}",
    ]
    title = f"Exit execution watchdog - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = (
        str(report.get("related_note_id") or report.get("note_id") or "").strip()
        or _find_existing_note_id(profile_key, session_day)
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": EXIT_WATCHDOG_NOTE_OWNER,
        "note_type": "risk_review",
        "priority": "high" if report.get("entries_blocked") or report.get("status") == "halt" else "medium",
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
        note_id = _sync_exit_watchdog_note(tenant=tenant, profile_key=normalized_profile_key, report=report)
        if note_id:
            report["note_id"] = note_id
            report["related_note_id"] = note_id
    runtime = state.setdefault("runtime", {})
    runtime["exit_watchdog_last_report"] = serialize_value(report)
    runtime["exit_watchdog_last_run_at"] = report.get("evaluated_at")
    runtime["exit_watchdog_last_note_id"] = report.get("related_note_id") or report.get("note_id")
    runtime["exit_watchdog_note_session_day"] = report.get("session_day")
    runtime["exit_watchdog_last_error"] = None
    history = list(runtime.get("exit_watchdog_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "pending_exit_count": report.get("pending_exit_count"),
            "stuck_exit_count": report.get("stuck_exit_count"),
            "failed_exit_count": report.get("failed_exit_count"),
            "entries_blocked": report.get("entries_blocked"),
            "note_id": report.get("related_note_id") or report.get("note_id"),
            "run_source": report.get("run_source"),
        },
    )
    runtime["exit_watchdog_history"] = serialize_value(history[:EXIT_WATCHDOG_HISTORY_LIMIT])
    if db is not None and write_note:
        record_audit_event(
            db,
            event_type="trade_automation.exit_watchdog_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": normalized_profile_key,
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "pending_exit_count": report.get("pending_exit_count"),
                "stuck_exit_count": report.get("stuck_exit_count"),
                "failed_exit_count": report.get("failed_exit_count"),
                "entries_blocked": report.get("entries_blocked"),
                "note_id": report.get("related_note_id") or report.get("note_id"),
                "run_source": report.get("run_source"),
            },
        )
    return serialize_value(report)


def evaluate_exit_watchdog_entry_gate(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    owned_closed: pd.DataFrame | None = None,
    now: datetime | None = None,
    actor: Any = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_profile_key = _normalize_profile_key(profile_key)
    session_day = _session_day_for(now)
    report = build_exit_watchdog_report(
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        owned_closed=owned_closed,
        order_events=_load_order_events(db, tenant, session_day=session_day),
        option_exit_events=_load_option_exit_events(db, tenant, session_day=session_day),
        now=now,
        run_source="cycle",
    )
    should_write_note = bool(report.get("entries_blocked") or report.get("status") in {"halt", "blocked"})
    if normalized_profile_key != EXIT_WATCHDOG_PERSONAL_PAPER_PROFILE and not bool(report.get("apply_to_live")):
        should_write_note = False
    return _persist_report(
        db,
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        report=report,
        actor=actor,
        write_note=should_write_note,
    )


def run_exit_watchdog_review(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_profile_key = _normalize_profile_key(profile_key)
    tenant_id = str(getattr(tenant, "id", "") or "").strip()
    closed_frame = _owned_rows(sdm.read_closed_trades(), tenant_id=tenant_id, profile_key=normalized_profile_key)
    session_day = _session_day_for(now)
    report = build_exit_watchdog_report(
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        owned_closed=closed_frame,
        order_events=_load_order_events(db, tenant, session_day=session_day),
        option_exit_events=_load_option_exit_events(db, tenant, session_day=session_day),
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
