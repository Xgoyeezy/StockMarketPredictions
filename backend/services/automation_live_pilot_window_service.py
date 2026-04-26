from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.core.config import settings
from backend.models.saas import BrokerageLinkedAccount, Tenant
from backend.schemas import CloseTradeRequest, OpenTradeRequest
from backend.services import automation_ai_review_service, notes_service
from backend.services.audit_service import record_audit_event
from backend.services.exceptions import ValidationServiceError
from backend.services.execution.provider_registry import get_execution_adapter_for
from backend.services.serialization import serialize_value
from backend.services.trade_service import _record_order_event

LIVE_PILOT_WINDOW_NOTE_OWNER = "automation-ai"
LIVE_PILOT_WINDOW_HISTORY_LIMIT = 8
LIVE_PILOT_WINDOW_NOTE_LIMIT = 250
LIVE_PILOT_WINDOW_PAPER_PROFILE = "personal_paper"
LIVE_PILOT_WINDOW_LIVE_PROFILE = "personal_live"
LIVE_PILOT_WINDOW_MAX_NOTIONAL_CEILING = 50.0
LIVE_PILOT_WINDOW_MIN_QUANTITY = 0.001
LIVE_PILOT_WINDOW_SETTINGS_DEFAULTS = {
    "live_pilot_window_enabled": False,
    "live_pilot_window_max_notional": 50.0,
    "live_pilot_window_max_session_orders": 1,
    "live_pilot_window_approval_ttl_minutes": 10,
    "live_pilot_window_duration_minutes": 60,
    "live_pilot_window_require_limit": True,
}

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE


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


def _coerce_float(
    value: Any,
    default: float = 0.0,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    if pd.isna(parsed):
        parsed = float(default)
    if minimum is not None:
        parsed = max(float(minimum), parsed)
    if maximum is not None:
        parsed = min(float(maximum), parsed)
    return float(parsed)


def _coerce_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(minimum), min(int(maximum), parsed))


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def _profile_tag(profile_key: str) -> str:
    return "profile-" + str(profile_key or LIVE_PILOT_WINDOW_PAPER_PROFILE).strip().lower().replace(":", "-")


def _issue(
    key: str,
    detail: str,
    *,
    component: str = "live_pilot_window",
    severity: str = "blocker",
) -> dict[str, Any]:
    return {"key": key, "component": component, "severity": severity, "detail": detail}


def normalize_live_pilot_window_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(settings_state or {})
    return {
        "live_pilot_window_enabled": _coerce_bool(
            raw.get("live_pilot_window_enabled"),
            LIVE_PILOT_WINDOW_SETTINGS_DEFAULTS["live_pilot_window_enabled"],
        ),
        "live_pilot_window_max_notional": _coerce_float(
            raw.get("live_pilot_window_max_notional"),
            LIVE_PILOT_WINDOW_SETTINGS_DEFAULTS["live_pilot_window_max_notional"],
            minimum=1.0,
            maximum=LIVE_PILOT_WINDOW_MAX_NOTIONAL_CEILING,
        ),
        "live_pilot_window_max_session_orders": _coerce_int(
            raw.get("live_pilot_window_max_session_orders"),
            LIVE_PILOT_WINDOW_SETTINGS_DEFAULTS["live_pilot_window_max_session_orders"],
            minimum=1,
            maximum=1,
        ),
        "live_pilot_window_approval_ttl_minutes": _coerce_int(
            raw.get("live_pilot_window_approval_ttl_minutes"),
            LIVE_PILOT_WINDOW_SETTINGS_DEFAULTS["live_pilot_window_approval_ttl_minutes"],
            minimum=1,
            maximum=30,
        ),
        "live_pilot_window_duration_minutes": _coerce_int(
            raw.get("live_pilot_window_duration_minutes"),
            LIVE_PILOT_WINDOW_SETTINGS_DEFAULTS["live_pilot_window_duration_minutes"],
            minimum=5,
            maximum=240,
        ),
        "live_pilot_window_require_limit": True,
    }


def normalize_live_pilot_window_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("live_pilot_window_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    approval = runtime.get("live_pilot_window_approval")
    if not isinstance(approval, dict):
        approval = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("live_pilot_window_history") or [])[:LIVE_PILOT_WINDOW_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "live_pilot_window_last_report": serialize_value(last_report),
        "live_pilot_window_last_note_id": str(runtime.get("live_pilot_window_last_note_id") or "").strip()
        or None,
        "live_pilot_window_note_session_day": str(runtime.get("live_pilot_window_note_session_day") or "").strip()
        or None,
        "live_pilot_window_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("live_pilot_window_last_run_at"))
        ),
        "live_pilot_window_last_error": str(runtime.get("live_pilot_window_last_error") or "").strip() or None,
        "live_pilot_window_approval": serialize_value(approval),
        "live_pilot_window_history": history,
    }


def build_live_pilot_window_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    settings_snapshot = normalize_live_pilot_window_settings((state or {}).get("settings"))
    runtime = normalize_live_pilot_window_runtime((state or {}).get("runtime"))
    report = dict(runtime.get("live_pilot_window_last_report") or {})
    approval = dict(runtime.get("live_pilot_window_approval") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "current_step": "idle",
            "approval_status": str(approval.get("status") or "missing"),
            "approval_expires_at": approval.get("expires_at"),
            "window_expires_at": approval.get("window_expires_at"),
            "approval_id": approval.get("approval_id"),
            "selected_candidate": approval.get("selected_candidate") or {},
            "symbol": approval.get("symbol"),
            "side": approval.get("side") or "buy",
            "notional_cap": settings_snapshot["live_pilot_window_max_notional"],
            "session_order_cap": settings_snapshot["live_pilot_window_max_session_orders"],
            "broker_order_id": None,
            "local_order_id": None,
            "local_trade_id": None,
            "terminal_state": None,
            "position_evidence": {},
            "exit_evidence": {},
            "cancel_evidence": {},
            "reconciliation_status": "not_run",
            "blockers": [],
            "warnings": [],
            "related_note_id": runtime.get("live_pilot_window_last_note_id"),
            "note_id": runtime.get("live_pilot_window_last_note_id"),
            "last_run_at": runtime.get("live_pilot_window_last_run_at"),
            "last_error": runtime.get("live_pilot_window_last_error"),
            "manual_action_required": False,
            "settings": settings_snapshot,
        }
    report.setdefault("approval_id", approval.get("approval_id"))
    report.setdefault("approval_expires_at", approval.get("expires_at"))
    report.setdefault("window_expires_at", approval.get("window_expires_at"))
    report.setdefault("approval_status", approval.get("status"))
    report.setdefault("related_note_id", runtime.get("live_pilot_window_last_note_id"))
    report.setdefault("note_id", runtime.get("live_pilot_window_last_note_id"))
    report.setdefault("last_run_at", runtime.get("live_pilot_window_last_run_at"))
    report.setdefault("last_error", runtime.get("live_pilot_window_last_error"))
    report.setdefault("settings", settings_snapshot)
    return serialize_value(report)


def _live_credentials_present() -> bool:
    return bool(
        (settings.alpaca_live_api_key_id or settings.alpaca_api_key_id)
        and (settings.alpaca_live_api_secret_key or settings.alpaca_api_secret_key)
    )


def _safe_step(report: dict[str, Any], step: str, status: str, detail: str, **extra: Any) -> None:
    report.setdefault("steps", []).append(serialize_value({"step": step, "status": status, "detail": detail, **extra}))
    report["current_step"] = step


def _candidate_from_runtime(runtime: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    candidate = runtime.get("last_candidate")
    if not isinstance(candidate, dict):
        decision = runtime.get("last_decision")
        if isinstance(decision, dict) and isinstance(decision.get("candidate"), dict):
            candidate = decision.get("candidate")
    if not isinstance(candidate, dict) or not candidate:
        return None, [_issue("candidate_missing", "No current ranked entry candidate is available for the supervised live pilot window.")]

    ticker = str(candidate.get("ticker") or candidate.get("symbol") or "").strip().upper()
    if not ticker:
        blockers.append(_issue("candidate_symbol_missing", "The selected candidate does not include a ticker."))
    if candidate.get("auto_entry_eligible") is False:
        blockers.append(_issue("candidate_not_auto_eligible", "The selected candidate is not auto-entry eligible."))
    reject_reason = str(candidate.get("reject_reason") or candidate.get("block_reason") or "").strip()
    if reject_reason:
        blockers.append(_issue("candidate_rejected", f"The selected candidate is rejected: {reject_reason}"))
    side = str(
        candidate.get("broker_side")
        or candidate.get("side")
        or ("sell" if str(candidate.get("thesis_direction") or "").lower() in {"bearish", "short"} else "buy")
    ).strip().lower()
    if side not in {"buy", "long"}:
        blockers.append(_issue("unsupported_candidate_side", "V1 supervised live pilot window only supports a long buy pilot order."))

    summary = {
        "ticker": ticker,
        "symbol": ticker,
        "side": "buy",
        "portfolio_rank": candidate.get("portfolio_rank") or candidate.get("rank"),
        "alpha_score": candidate.get("alpha_score"),
        "execution_score": candidate.get("execution_score"),
        "portfolio_score": candidate.get("portfolio_score"),
        "edge_to_cost_ratio": candidate.get("edge_to_cost_ratio"),
        "entry_price": candidate.get("entry_price") or candidate.get("entry_low_price"),
        "entry_high_price": candidate.get("entry_high_price"),
        "auto_entry_eligible": candidate.get("auto_entry_eligible"),
        "reject_reason": reject_reason or None,
    }
    return serialize_value(summary), blockers


def _session_order_count(runtime: dict[str, Any], session_day: str) -> int:
    count = 0
    for item in list(runtime.get("live_pilot_window_history") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("session_day") or "").strip() != session_day:
            continue
        if item.get("broker_order_id") or item.get("local_order_id"):
            count += 1
    last_report = runtime.get("live_pilot_window_last_report")
    if isinstance(last_report, dict) and str(last_report.get("session_day") or "").strip() == session_day:
        if last_report.get("broker_order_id") or last_report.get("local_order_id"):
            count += 1
    return count


def _row_value(row: Any, key: str) -> Any:
    try:
        return row.get(key)
    except AttributeError:
        return None


def _row_matches_order(row: Any, order_id: str) -> bool:
    wanted = str(order_id or "").strip()
    if not wanted:
        return False
    candidates = {
        str(_row_value(row, "order_id") or "").strip(),
        str(_row_value(row, "broker_client_order_id") or "").strip(),
    }
    return wanted in candidates


def _row_is_window_owned(row: Any) -> bool:
    return bool(
        str(_row_value(row, "live_pilot_window_id") or "").strip()
        or str(_row_value(row, "automation_entry_reason") or "").strip().lower() == "live_pilot_window"
        or str(_row_value(row, "route_family") or "").strip().lower() == "live_pilot_window"
    )


def _window_owned_rows(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        if _row_is_window_owned(row):
            rows.append({str(key): serialize_value(value) for key, value in row.to_dict().items()})
    return rows


def _unresolved_window_ledger() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        pending = _window_owned_rows(sdm.read_pending_orders())
    except Exception:
        pending = []
    try:
        open_rows = _window_owned_rows(sdm.read_open_trades())
    except Exception:
        open_rows = []
    return pending, open_rows


def _unresolved_window_report(runtime: dict[str, Any]) -> bool:
    report = runtime.get("live_pilot_window_last_report")
    if not isinstance(report, dict):
        return False
    if not (report.get("broker_order_id") or report.get("local_order_id")):
        return False
    terminal_state = str(report.get("terminal_state") or "").strip().lower()
    return terminal_state not in {"canceled", "closed"}


def _preflight_issues(
    *,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None,
    rollout_readiness: dict[str, Any] | None,
    now: datetime,
    check_unresolved: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    settings_snapshot = normalize_live_pilot_window_settings(paper_state.get("settings"))
    runtime = paper_state.setdefault("runtime", {})
    live_state = live_state if isinstance(live_state, dict) else {}
    live_settings = dict(live_state.get("settings") or {})
    readiness = dict(runtime.get("live_pilot_readiness_last_report") or {})
    expansion_canary = dict(runtime.get("live_pilot_expansion_canary_last_report") or {})
    rollout = dict(rollout_readiness or {})
    session_day = _session_day_for(now)

    if not settings_snapshot["live_pilot_window_enabled"]:
        blockers.append(
            _issue(
                "live_pilot_window_disabled",
                "Supervised live pilot window is disabled. Turn on live_pilot_window_enabled before preparing a pilot window.",
            )
        )
    if str(expansion_canary.get("status") or "").strip().lower() != "ready" or expansion_canary.get("blockers"):
        blockers.append(
            _issue(
                "live_pilot_expansion_canary_not_ready",
                f"Live pilot expansion canary status is {expansion_canary.get('status') or 'missing'}; clean multi-session expansion evidence is required.",
                component="live_pilot_expansion_canary",
            )
        )
    if str(readiness.get("status") or "").strip().lower() != "ready_to_request_approval" or readiness.get("blockers"):
        blockers.append(
            _issue(
                "live_pilot_readiness_not_ready",
                f"Live pilot readiness status is {readiness.get('status') or 'missing'}; run readiness review until it is clean.",
                component="live_pilot_readiness",
            )
        )
    if str(readiness.get("broker_live_gate_status") or "").strip().lower() != "open":
        blockers.append(_issue("broker_live_gate_locked", "The broker-live readiness gate is not open.", component="broker_live_gate"))
    if str(readiness.get("safety_lock_status") or "").strip().lower() not in {"clear", "none"}:
        blockers.append(_issue("safety_lock_active", "A readiness safety lock is active.", component="safety_lock"))
    if not _live_credentials_present():
        blockers.append(_issue("live_broker_credentials_missing", "Live Alpaca credentials are not configured.", component="broker_live_gate"))
    if not bool(settings.alpaca_live_trading_enabled):
        blockers.append(_issue("live_broker_disabled", "Live broker trading is disabled in server configuration.", component="broker_live_gate"))
    if str(live_settings.get("execution_intent") or "").strip().lower() != "broker_live":
        blockers.append(_issue("live_profile_route_not_live", "The personal live profile is not configured for broker_live routing.", component="broker_live_gate"))
    if not bool(rollout.get("allows_live_rollout")):
        blockers.append(_issue("broker_live_rollout_locked", "Existing broker-live rollout readiness gate is locked.", component="broker_live_gate"))
    if _coerce_bool(live_settings.get("enabled"), False) or _coerce_bool(live_settings.get("armed"), False):
        blockers.append(_issue("live_profile_enabled_or_armed", "The personal live automation profile must remain disabled and disarmed."))
    if _coerce_bool(live_settings.get("kill_switch"), False) or _coerce_bool(dict(paper_state.get("settings") or {}).get("kill_switch"), False):
        blockers.append(_issue("kill_switch_active", "A paper or live kill switch is active. The pilot window service cannot clear safety locks."))
    if check_unresolved and _unresolved_window_report(runtime):
        blockers.append(_issue("prior_window_unresolved", "The prior supervised live pilot window has unresolved broker/local evidence."))
    if check_unresolved:
        pending, open_rows = _unresolved_window_ledger()
        if pending or open_rows:
            blockers.append(_issue("existing_live_pilot_window_exposure", "Existing supervised live pilot order or position evidence is unresolved."))
    if _session_order_count(runtime, session_day) >= settings_snapshot["live_pilot_window_max_session_orders"]:
        blockers.append(_issue("session_order_cap_reached", "The supervised live pilot window session order cap has already been reached."))

    candidate, candidate_blockers = _candidate_from_runtime(runtime)
    blockers.extend(candidate_blockers)
    if candidate and not candidate_blockers:
        warnings.append(
            _issue(
                "candidate_requires_operator_review",
                "Review the selected candidate before entering the supervised live pilot window.",
                severity="warning",
            )
        )

    return blockers, warnings, settings_snapshot, readiness, candidate


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIVE_PILOT_WINDOW_NOTE_OWNER,
            limit=LIVE_PILOT_WINDOW_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "supervised-live-pilot",
        "live-pilot-expansion-canary",
        "live-pilot-readiness",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    candidate = dict(report.get("selected_candidate") or {})
    lines = [
        f"Automation supervised live pilot window for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Approval: {str(report.get('approval_status') or 'missing').upper()}",
        f"Approval expires: {report.get('approval_expires_at') or '--'}",
        f"Window expires: {report.get('window_expires_at') or '--'}",
        f"Candidate: {candidate.get('ticker') or report.get('symbol') or '--'} rank {candidate.get('portfolio_rank') or '--'}",
        f"Side: {report.get('side') or 'buy'}",
        f"Notional cap: ${float(report.get('notional_cap') or 0.0):.2f}",
        f"Limit price: {report.get('limit_price') or '--'}",
        f"Quantity: {report.get('quantity') or '--'}",
        f"Broker order: {report.get('broker_order_id') or '--'}",
        f"Local order: {report.get('local_order_id') or '--'}",
        f"Local trade: {report.get('local_trade_id') or '--'}",
        f"Terminal state: {report.get('terminal_state') or '--'}",
        f"Reconciliation: {report.get('reconciliation_status') or 'not_run'}",
        "",
        "This supervised live pilot is operator-approved and one-order only. Prepare never places an order. Entry may submit one capped live limit order. Exit/cancel may touch only the pilot-owned order or position. It does not enable or arm live automation, clear locks, tune settings, schedule orders, change broker-live gates, or allow autonomous live entries.",
        "",
        "Blockers",
    ]
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    if blockers:
        lines.extend(f"- {item.get('key')}: {item.get('detail')}" for item in blockers[:12])
    else:
        lines.append("- None.")
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    lines.extend(["", "Warnings"])
    if warnings:
        lines.extend(f"- {item.get('key')}: {item.get('detail')}" for item in warnings[:12])
    else:
        lines.append("- None.")
    lines.extend(["", "Steps"])
    for step in list(report.get("steps") or [])[:12]:
        if not isinstance(step, dict):
            continue
        lines.append(f"- {step.get('step')}: {step.get('status')}. {step.get('detail')}")
    return "\n".join(lines).strip()


def _sync_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "supervised-live-pilot",
        "live-pilot-expansion-canary",
        "live-pilot-readiness",
        _profile_tag(profile_key),
        f"session-{session_day}",
    ]
    title = f"Automation supervised live pilot window - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = str(report.get("related_note_id") or report.get("note_id") or "").strip() or _find_existing_note_id(profile_key, session_day)
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": LIVE_PILOT_WINDOW_NOTE_OWNER,
        "note_type": "risk_review",
        "priority": "high" if report.get("blockers") or report.get("manual_action_required") else "medium",
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


def _reference_price(symbol: str) -> float:
    price = float(sdm.get_live_price(symbol))
    if pd.isna(price) or price <= 0:
        raise ValidationServiceError(f"Could not resolve a reliable live reference price for {symbol}.")
    return float(price)


def _candidate_limit_price(candidate: dict[str, Any], reference_price: float) -> float:
    for key in ("entry_high_price", "entry_price", "entry_low_price"):
        value = _coerce_float(candidate.get(key), 0.0, minimum=0.0)
        if value > 0:
            return round(min(reference_price, value), 2)
    return round(reference_price, 2)


def _quantity_for_notional(notional_cap: float, limit_price: float) -> float:
    if limit_price <= 0:
        return LIVE_PILOT_WINDOW_MIN_QUANTITY
    raw = math.floor((float(notional_cap) / float(limit_price)) * 1000.0) / 1000.0
    return max(LIVE_PILOT_WINDOW_MIN_QUANTITY, round(raw, 3))


def _find_pending_order(order_id: str) -> dict[str, Any] | None:
    try:
        frame = sdm.read_pending_orders()
    except Exception:
        return None
    if frame is None or getattr(frame, "empty", True):
        return None
    for _, row in frame.iterrows():
        if _row_matches_order(row, order_id):
            return {str(key): serialize_value(value) for key, value in row.to_dict().items()}
    return None


def _find_open_trade(order_id: str) -> tuple[int | None, dict[str, Any] | None]:
    try:
        frame = sdm.read_open_trades()
    except Exception:
        return None, None
    if frame is None or getattr(frame, "empty", True):
        return None, None
    for idx, row in frame.reset_index(drop=True).iterrows():
        if _row_matches_order(row, order_id):
            return int(idx), {str(key): serialize_value(value) for key, value in row.to_dict().items()}
    return None, None


def _has_order(frame: pd.DataFrame, order_id: str) -> bool:
    if frame is None or getattr(frame, "empty", True):
        return False
    for _, row in frame.iterrows():
        if _row_matches_order(row, order_id):
            return True
    return False


def _reconciliation_status(order_id: str, terminal_state: str | None) -> tuple[str, list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    pending = sdm.read_pending_orders()
    open_trades = sdm.read_open_trades()
    if terminal_state == "working":
        if not _has_order(pending, order_id):
            blockers.append(_issue("pending_order_missing", "The live pilot order is expected to be working but no local pending row exists."))
        return ("working" if not blockers else "blocked"), blockers
    if terminal_state == "open":
        if not _has_order(open_trades, order_id):
            blockers.append(_issue("open_trade_missing", "The live pilot order filled but no local open trade row exists."))
        return ("open" if not blockers else "blocked"), blockers
    if terminal_state == "canceled":
        if _has_order(pending, order_id):
            blockers.append(_issue("pending_order_after_cancel", "The local pending ledger still contains the canceled live pilot order."))
        if _has_order(open_trades, order_id):
            blockers.append(_issue("open_trade_after_cancel", "The local open ledger shows a position after cancellation."))
    elif terminal_state == "closed":
        if _has_order(open_trades, order_id):
            blockers.append(_issue("open_trade_after_close", "The local open ledger still contains the closed live pilot position."))
    else:
        blockers.append(_issue("terminal_state_missing", "Supervised live pilot terminal state could not be confirmed."))
    return ("blocked" if blockers else "clean"), blockers


def _apply_live_markers(
    *,
    tenant: Tenant,
    window_id: str,
    trade_id: str,
    order_id: str,
    position_opened: bool,
) -> dict[str, Any] | None:
    markers = {
        "automation_origin": "trade_automation",
        "automation_tenant_id": tenant.id,
        "automation_tenant_slug": tenant.slug,
        "automation_profile_key": LIVE_PILOT_WINDOW_LIVE_PROFILE,
        "automation_execution_intent": "broker_live",
        "automation_entry_reason": "live_pilot_window",
        "route_family": "live_pilot_window",
        "live_pilot_window_id": window_id,
    }
    if position_opened:
        return sdm.update_open_trade(markers, trade_id=trade_id, order_id=order_id)
    return sdm.update_pending_order(order_id, markers)


def _finalize_report(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None,
    actor: Any,
    report: dict[str, Any],
    success_status: str,
    audit_event_type: str,
) -> dict[str, Any]:
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    if blockers:
        status = "blocked"
    elif success_status in {"entered", "approved"}:
        status = success_status
    elif warnings and success_status not in {"completed"}:
        status = "warning"
    else:
        status = success_status
    report["status"] = status
    report["label"] = {
        "approved": "Supervised live pilot approved",
        "entered": "Supervised live pilot entered",
        "completed": "Supervised live pilot completed",
        "warning": "Supervised live pilot warning",
        "blocked": "Supervised live pilot blocked",
    }.get(status, "Supervised live pilot")
    terminal_state = str(report.get("terminal_state") or "").strip().lower()
    report["manual_action_required"] = bool(
        blockers
        or report.get("manual_action_required")
        or (status == "entered" and terminal_state not in {"canceled", "closed"})
    )
    note_id = _sync_note(tenant=tenant, profile_key=profile_key, report=report)
    if note_id:
        report["note_id"] = note_id
        report["related_note_id"] = note_id
    runtime = state.setdefault("runtime", {})
    summary_keys = {
        "status",
        "label",
        "profile_key",
        "live_profile_key",
        "session_day",
        "checked_at",
        "current_step",
        "approval_id",
        "approval_status",
        "approval_expires_at",
        "window_expires_at",
        "selected_candidate",
        "symbol",
        "side",
        "notional_cap",
        "session_order_cap",
        "reference_price",
        "limit_price",
        "quantity",
        "estimated_notional",
        "local_order_id",
        "local_trade_id",
        "broker_order_id",
        "broker_status",
        "terminal_state",
        "position_evidence",
        "exit_evidence",
        "cancel_evidence",
        "reconciliation_status",
        "blockers",
        "warnings",
        "note_id",
        "related_note_id",
        "manual_action_required",
    }
    runtime["live_pilot_window_last_report"] = serialize_value(
        {key: report.get(key) for key in summary_keys if key in report}
    )
    runtime["live_pilot_window_last_note_id"] = note_id
    runtime["live_pilot_window_note_session_day"] = report.get("session_day")
    runtime["live_pilot_window_last_run_at"] = report.get("checked_at")
    runtime["live_pilot_window_last_error"] = None
    history = list(runtime.get("live_pilot_window_history") or [])
    if report.get("broker_order_id") or report.get("status") in {"completed", "blocked"}:
        history.insert(
            0,
            {
                "at": report.get("checked_at"),
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "terminal_state": report.get("terminal_state"),
                "reconciliation_status": report.get("reconciliation_status"),
                "broker_order_id": report.get("broker_order_id"),
                "local_order_id": report.get("local_order_id"),
                "symbol": report.get("symbol"),
                "blocker_count": len(blockers),
                "warning_count": len(warnings),
                "blockers": serialize_value(blockers[:5]),
                "warnings": serialize_value(warnings[:5]),
                "note_id": note_id,
            },
        )
    runtime["live_pilot_window_history"] = serialize_value(history[:LIVE_PILOT_WINDOW_HISTORY_LIMIT])
    if db is not None:
        record_audit_event(
            db,
            event_type=audit_event_type,
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "live_profile_key": LIVE_PILOT_WINDOW_LIVE_PROFILE,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "terminal_state": report.get("terminal_state"),
                "broker_order_id": report.get("broker_order_id"),
                "order_id": report.get("local_order_id"),
                "note_id": note_id,
            },
        )
    return serialize_value(report)


def prepare_live_pilot_window(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None,
    rollout_readiness: dict[str, Any] | None = None,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    session_day = _session_day_for(now)
    blockers, warnings, window_settings, readiness, candidate = _preflight_issues(
        paper_state=paper_state,
        live_state=live_state,
        rollout_readiness=rollout_readiness,
        now=now,
    )
    approval_id = str(uuid4())
    expires_at = now + timedelta(minutes=int(window_settings["live_pilot_window_approval_ttl_minutes"]))
    window_expires_at = now + timedelta(minutes=int(window_settings["live_pilot_window_duration_minutes"]))
    symbol = str((candidate or {}).get("ticker") or "").strip().upper() or None
    report: dict[str, Any] = {
        "status": "running",
        "profile_key": LIVE_PILOT_WINDOW_PAPER_PROFILE,
        "live_profile_key": LIVE_PILOT_WINDOW_LIVE_PROFILE,
        "session_day": session_day,
        "checked_at": _serialize_datetime(now),
        "current_step": "preflight",
        "approval_id": approval_id,
        "approval_status": "blocked" if blockers else "approved",
        "approval_expires_at": _serialize_datetime(expires_at),
        "window_expires_at": _serialize_datetime(window_expires_at),
        "selected_candidate": candidate or {},
        "symbol": symbol,
        "side": "buy",
        "notional_cap": window_settings["live_pilot_window_max_notional"],
        "session_order_cap": window_settings["live_pilot_window_max_session_orders"],
        "local_order_id": None,
        "local_trade_id": None,
        "broker_order_id": None,
        "terminal_state": None,
        "position_evidence": {},
        "exit_evidence": {},
        "cancel_evidence": {},
        "reconciliation_status": "not_run",
        "readiness_status": readiness.get("status"),
        "blockers": blockers,
        "warnings": warnings,
        "steps": [],
    }
    if blockers:
        _safe_step(report, "preflight", "blocked", "Supervised live pilot window approval was blocked before any order path was reachable.")
    else:
        _safe_step(report, "preflight", "approved", "Supervised live pilot window approval is fresh and entry remains a separate manual action.")
        paper_state.setdefault("runtime", {})["live_pilot_window_approval"] = {
            "approval_id": approval_id,
            "status": "approved",
            "approved_at": _serialize_datetime(now),
            "expires_at": _serialize_datetime(expires_at),
            "window_expires_at": _serialize_datetime(window_expires_at),
            "session_day": session_day,
            "selected_candidate": candidate,
            "symbol": symbol,
            "side": "buy",
            "notional_cap": window_settings["live_pilot_window_max_notional"],
            "session_order_cap": window_settings["live_pilot_window_max_session_orders"],
            "consumed_at": None,
            "broker_order_id": None,
            "order_id": None,
            "trade_id": None,
        }
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
        linked_account=linked_account,
        actor=actor,
        report=report,
        success_status="approved",
        audit_event_type="trade_automation.live_pilot_window_prepared",
    )


def run_live_pilot_window_entry(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None,
    rollout_readiness: dict[str, Any] | None = None,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    runtime = paper_state.setdefault("runtime", {})
    approval = dict(runtime.get("live_pilot_window_approval") or {})
    session_day = _session_day_for(now)
    blockers, warnings, window_settings, readiness, current_candidate = _preflight_issues(
        paper_state=paper_state,
        live_state=live_state,
        rollout_readiness=rollout_readiness,
        now=now,
    )
    approval_id = str(approval.get("approval_id") or "").strip()
    expires_at = _parse_datetime(approval.get("expires_at"))
    window_expires_at = _parse_datetime(approval.get("window_expires_at"))
    approval_status = str(approval.get("status") or "").strip().lower()
    if not approval_id or approval_status != "approved":
        blockers.append(_issue("approval_missing", "Prepare the supervised live pilot window before entering a live order."))
    elif expires_at is None or expires_at < now:
        blockers.append(_issue("approval_expired", "The supervised live pilot window approval expired. Prepare it again before entry."))
    elif approval.get("consumed_at"):
        blockers.append(_issue("approval_consumed", "The supervised live pilot window approval was already consumed. Prepare a fresh approval."))

    selected_candidate = dict(approval.get("selected_candidate") or current_candidate or {})
    symbol = str(approval.get("symbol") or selected_candidate.get("ticker") or "").strip().upper()
    notional_cap = min(
        _coerce_float(
            approval.get("notional_cap"),
            window_settings["live_pilot_window_max_notional"],
            minimum=1.0,
            maximum=LIVE_PILOT_WINDOW_MAX_NOTIONAL_CEILING,
        ),
        LIVE_PILOT_WINDOW_MAX_NOTIONAL_CEILING,
    )
    trade_id = str(uuid4())
    order_id = str(uuid4())
    route_correlation_id = str(uuid4())
    report: dict[str, Any] = {
        "status": "running",
        "profile_key": LIVE_PILOT_WINDOW_PAPER_PROFILE,
        "live_profile_key": LIVE_PILOT_WINDOW_LIVE_PROFILE,
        "session_day": session_day,
        "checked_at": _serialize_datetime(now),
        "current_step": "preflight",
        "approval_id": approval_id or None,
        "approval_status": approval_status or "missing",
        "approval_expires_at": approval.get("expires_at"),
        "window_expires_at": _serialize_datetime(window_expires_at),
        "selected_candidate": selected_candidate,
        "symbol": symbol,
        "side": "buy",
        "notional_cap": notional_cap,
        "session_order_cap": window_settings["live_pilot_window_max_session_orders"],
        "local_order_id": order_id,
        "local_trade_id": trade_id,
        "route_correlation_id": route_correlation_id,
        "broker_order_id": None,
        "broker_status": None,
        "terminal_state": None,
        "position_evidence": {},
        "exit_evidence": {},
        "cancel_evidence": {},
        "reconciliation_status": "not_run",
        "readiness_status": readiness.get("status"),
        "blockers": blockers,
        "warnings": warnings,
        "steps": [],
    }
    if blockers:
        _safe_step(report, "preflight", "blocked", "Supervised live pilot entry was blocked before any order path was reachable.")
        return _finalize_report(
            db,
            tenant=tenant,
            state=paper_state,
            profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
            linked_account=linked_account,
            actor=actor,
            report=report,
            success_status="entered",
            audit_event_type="trade_automation.live_pilot_window_entered",
        )

    try:
        reference_price = _reference_price(symbol)
    except Exception as exc:
        report["blockers"].append(_issue("reference_price_missing", str(exc) or "Live reference price is unavailable."))
        _safe_step(report, "preflight", "blocked", "Could not resolve live reference price.")
        return _finalize_report(
            db,
            tenant=tenant,
            state=paper_state,
            profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
            linked_account=linked_account,
            actor=actor,
            report=report,
            success_status="entered",
            audit_event_type="trade_automation.live_pilot_window_entered",
        )

    limit_price = _candidate_limit_price(selected_candidate, reference_price)
    quantity = _quantity_for_notional(notional_cap, limit_price)
    estimated_notional = float(quantity * limit_price)
    report.update(
        {
            "reference_price": reference_price,
            "limit_price": limit_price,
            "quantity": quantity,
            "estimated_notional": estimated_notional,
        }
    )
    if estimated_notional > notional_cap + 0.01:
        report["blockers"].append(
            _issue(
                "notional_cap_too_low",
                f"The computed pilot order would use about ${estimated_notional:.2f}, above the ${notional_cap:.2f} cap.",
            )
        )
        _safe_step(report, "preflight", "blocked", "Computed quantity exceeds the notional cap.")
        return _finalize_report(
            db,
            tenant=tenant,
            state=paper_state,
            profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
            linked_account=linked_account,
            actor=actor,
            report=report,
            success_status="entered",
            audit_event_type="trade_automation.live_pilot_window_entered",
        )

    adapter = get_execution_adapter_for("alpaca_live")
    try:
        ensure = getattr(adapter, "_ensure_credentials", None)
        if callable(ensure):
            ensure()
    except Exception as exc:
        report["blockers"].append(_issue("live_credentials_rejected", str(exc) or "Alpaca live credentials were rejected."))
        _safe_step(report, "preflight", "blocked", "Live adapter credential check failed.")
        return _finalize_report(
            db,
            tenant=tenant,
            state=paper_state,
            profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
            linked_account=linked_account,
            actor=actor,
            report=report,
            success_status="entered",
            audit_event_type="trade_automation.live_pilot_window_entered",
        )

    request = OpenTradeRequest(
        ticker=symbol,
        interval="1m",
        horizon=1,
        live_price=limit_price,
        account_size=notional_cap,
        risk_percent=0.01,
        requested_quantity=quantity,
        instrument_type="equity",
        broker_side="buy",
        execution_intent="broker_live",
        order_type="limit",
        time_in_force="day",
        limit_price=limit_price,
        fractional_shares_only=True,
        regular_hours_only=True,
        route_family="live_pilot_window",
        route_version="v1",
        automation_entry_reason="live_pilot_window",
        thesis_direction="bullish",
        source="live_pilot_window",
    )
    analysis = {
        "ticker": symbol,
        "interval": "1m",
        "signal": "SUPERVISED LIVE PILOT",
        "confidence": 0.01,
        "current_price": limit_price,
        "entry_price": limit_price,
        "target_price": round(limit_price * 1.01, 2),
        "stop_loss": round(limit_price * 0.99, 2),
        "automation_entry_reason": "live_pilot_window",
    }
    position = {
        "suggested_contracts": quantity,
        "total_position_cost": estimated_notional,
        "max_risk_dollars": estimated_notional,
    }
    order_ticket = {
        "trade_id": trade_id,
        "order_id": order_id,
        "route_correlation_id": route_correlation_id,
        "instrument_type": "equity",
        "contract_symbol": f"EQUITY:{symbol}",
        "broker_side": "BUY",
        "order_type": "limit",
        "time_in_force": "day",
        "limit_price": limit_price,
        "fractional_shares_only": True,
        "route_family": "live_pilot_window",
        "route_version": "v1",
        "source": "live_pilot_window",
        "automation_entry_reason": "live_pilot_window",
        "tenant_id": tenant.id,
        "tenant_slug": tenant.slug,
    }

    _safe_step(report, "entry", "running", "Submitting one capped broker-live limit order for the supervised pilot window.")
    _record_order_event(
        db,
        tenant=tenant,
        actor=actor,
        trade_id=trade_id,
        ticker=symbol,
        event_key="order.submitted",
        status="submitting",
        order_type="limit",
        time_in_force="day",
        route_state="submitting",
        book_state="pending",
        detail="Submitting operator-approved supervised live pilot limit order.",
        payload={
            "order_id": order_id,
            "route_correlation_id": route_correlation_id,
            "automation_profile_key": LIVE_PILOT_WINDOW_LIVE_PROFILE,
            "live_pilot_window_id": approval_id,
            "selected_candidate": selected_candidate,
            "request": serialize_value(request.model_dump()),
        },
    )
    try:
        submit_result = adapter.submit_order(
            request=request,
            report=analysis,
            live_price=limit_price,
            position=position,
            trade_id=trade_id,
            order_id=order_id,
            order_ticket=order_ticket,
        )
    except Exception as exc:
        report["blockers"].append(_issue("submit_failed", str(exc) or "Broker-live order submission failed."))
        _safe_step(report, "entry", "blocked", "Broker-live pilot entry submission failed.")
        return _finalize_report(
            db,
            tenant=tenant,
            state=paper_state,
            profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
            linked_account=linked_account,
            actor=actor,
            report=report,
            success_status="entered",
            audit_event_type="trade_automation.live_pilot_window_entered",
        )

    runtime["live_pilot_window_approval"] = {
        **approval,
        "status": "consumed",
        "consumed_at": _serialize_datetime(now),
        "broker_order_id": submit_result.broker_order_id,
        "order_id": order_id,
        "trade_id": trade_id,
    }
    marker_row = _apply_live_markers(
        tenant=tenant,
        window_id=approval_id,
        trade_id=trade_id,
        order_id=order_id,
        position_opened=bool(submit_result.position_opened),
    )
    if marker_row is None:
        report["warnings"].append(
            _issue(
                "marker_update_missing",
                "Live pilot order was submitted but automation marker metadata could not be written.",
                severity="warning",
            )
        )
    report["broker_order_id"] = submit_result.broker_order_id
    report["broker_status"] = submit_result.broker_status
    report["terminal_state"] = "open" if submit_result.position_opened else "working"
    if submit_result.position_opened:
        report["position_evidence"] = {
            "state": "open",
            "broker_order_id": submit_result.broker_order_id,
            "broker_status": submit_result.broker_status,
            "order_id": order_id,
            "trade_id": trade_id,
        }
    _safe_step(
        report,
        "entry",
        "filled" if submit_result.position_opened else "accepted",
        "Supervised live pilot order filled and now needs explicit operator exit." if submit_result.position_opened else "Supervised live pilot order is working and now needs explicit operator cancel/exit.",
        broker_order_id=submit_result.broker_order_id,
        broker_status=submit_result.broker_status,
    )
    _record_order_event(
        db,
        tenant=tenant,
        actor=actor,
        trade_id=trade_id,
        ticker=symbol,
        event_key="order.filled" if submit_result.position_opened else "order.accepted",
        status="filled" if submit_result.position_opened else "working",
        order_type="limit",
        time_in_force="day",
        route_state="filled" if submit_result.position_opened else "accepted",
        book_state="open" if submit_result.position_opened else "pending",
        detail="Supervised live pilot order filled." if submit_result.position_opened else "Supervised live pilot order is working.",
        payload={
            "order_id": order_id,
            "route_correlation_id": route_correlation_id,
            "automation_profile_key": LIVE_PILOT_WINDOW_LIVE_PROFILE,
            "live_pilot_window_id": approval_id,
            "execution": {
                "adapter": submit_result.broker_name,
                "broker_order_id": submit_result.broker_order_id,
                "broker_status": submit_result.broker_status,
            },
            "record": serialize_value(submit_result.record),
        },
    )
    reconciliation_status, reconciliation_blockers = _reconciliation_status(order_id, report.get("terminal_state"))
    report["reconciliation_status"] = reconciliation_status
    if reconciliation_blockers:
        report["blockers"].extend(reconciliation_blockers)
    report["manual_action_required"] = True
    _safe_step(report, "reconciliation", reconciliation_status, "Local supervised live pilot entry reconciliation completed.")
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
        linked_account=linked_account,
        actor=actor,
        report=report,
        success_status="entered",
        audit_event_type="trade_automation.live_pilot_window_entered",
    )


def run_live_pilot_window_exit(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    runtime = paper_state.setdefault("runtime", {})
    last_report = dict(runtime.get("live_pilot_window_last_report") or {})
    approval = dict(runtime.get("live_pilot_window_approval") or {})
    session_day = _session_day_for(now)
    order_id = str(last_report.get("local_order_id") or approval.get("order_id") or "").strip()
    trade_id = str(last_report.get("local_trade_id") or approval.get("trade_id") or "").strip()
    broker_order_id = str(last_report.get("broker_order_id") or approval.get("broker_order_id") or "").strip()
    symbol = str(last_report.get("symbol") or approval.get("symbol") or "").strip().upper()
    report = {
        **last_report,
        "status": "running",
        "session_day": session_day,
        "checked_at": _serialize_datetime(now),
        "current_step": "exit",
        "approval_id": approval.get("approval_id") or last_report.get("approval_id"),
        "approval_status": str(approval.get("status") or last_report.get("approval_status") or "consumed"),
        "local_order_id": order_id or None,
        "local_trade_id": trade_id or None,
        "broker_order_id": broker_order_id or None,
        "symbol": symbol or last_report.get("symbol"),
        "blockers": [],
        "warnings": list(last_report.get("warnings") or []),
        "steps": list(last_report.get("steps") or []),
    }
    terminal_state = str(last_report.get("terminal_state") or "").strip().lower()
    if not order_id or not broker_order_id:
        report["blockers"].append(_issue("pilot_order_missing", "No pilot-owned live order evidence is available to exit or cancel."))
    if terminal_state in {"canceled", "closed"}:
        report["blockers"].append(_issue("pilot_already_terminal", "The supervised live pilot window is already terminal."))
    live_settings = dict((live_state or {}).get("settings") or {})
    if _coerce_bool(live_settings.get("enabled"), False) or _coerce_bool(live_settings.get("armed"), False):
        report["blockers"].append(_issue("live_profile_enabled_or_armed", "The personal live automation profile must remain disabled and disarmed."))
    if report["blockers"]:
        _safe_step(report, "exit", "blocked", "Supervised live pilot exit was blocked before touching broker-live APIs.")
        return _finalize_report(
            db,
            tenant=tenant,
            state=paper_state,
            profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
            linked_account=linked_account,
            actor=actor,
            report=report,
            success_status="completed",
            audit_event_type="trade_automation.live_pilot_window_exited",
        )

    adapter = get_execution_adapter_for("alpaca_live")
    pending = _find_pending_order(order_id)
    open_index, open_trade = _find_open_trade(order_id)
    if pending:
        _safe_step(report, "cancel", "running", "Canceling the pilot-owned working live order.")
        try:
            cancel_result = adapter.cancel_order(order_id=order_id)
        except Exception as exc:
            report["blockers"].append(_issue("cancel_failed", str(exc) or "Broker-live cancellation failed."))
            report["manual_action_required"] = True
            _safe_step(report, "cancel", "blocked", "Broker-live pilot cancellation failed.")
            return _finalize_report(
                db,
                tenant=tenant,
                state=paper_state,
                profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
                linked_account=linked_account,
                actor=actor,
                report=report,
                success_status="completed",
                audit_event_type="trade_automation.live_pilot_window_exited",
            )
        if cancel_result is None:
            report["blockers"].append(_issue("cancel_missing", "Broker-live cancellation returned no local terminal order evidence."))
            report["manual_action_required"] = True
            _safe_step(report, "cancel", "blocked", "Broker-live pilot cancellation did not produce terminal evidence.")
            return _finalize_report(
                db,
                tenant=tenant,
                state=paper_state,
                profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
                linked_account=linked_account,
                actor=actor,
                report=report,
                success_status="completed",
                audit_event_type="trade_automation.live_pilot_window_exited",
            )
        report["terminal_state"] = "canceled"
        report["broker_status"] = cancel_result.broker_status or report.get("broker_status")
        report["cancel_evidence"] = {
            "canceled": True,
            "broker_order_id": cancel_result.broker_order_id,
            "broker_status": cancel_result.broker_status,
            "order_id": order_id,
        }
        _safe_step(report, "cancel", "canceled", "Pilot-owned working live order was canceled.")
        _record_order_event(
            db,
            tenant=tenant,
            actor=actor,
            trade_id=trade_id or order_id,
            ticker=symbol,
            event_key="order.canceled",
            status="canceled",
            order_type="limit",
            time_in_force="day",
            route_state="canceled",
            book_state="flat",
            detail="Canceled the pilot-owned supervised live order.",
            payload={
                "order_id": order_id,
                "automation_profile_key": LIVE_PILOT_WINDOW_LIVE_PROFILE,
                "live_pilot_window_id": approval.get("approval_id") or last_report.get("approval_id"),
                "order": serialize_value(cancel_result.canceled_order),
                "execution": {
                    "adapter": cancel_result.broker_name,
                    "broker_order_id": cancel_result.broker_order_id,
                    "broker_status": cancel_result.broker_status,
                },
            },
            audit_event_type="trade.order_canceled",
        )
    elif open_trade and open_index is not None:
        close_price = _coerce_float(
            open_trade.get("actual_fill_price") or open_trade.get("broker_filled_avg_price") or open_trade.get("live_price_at_open"),
            _coerce_float(last_report.get("limit_price"), 1.0, minimum=0.0001),
            minimum=0.0001,
        )
        _safe_step(report, "close", "running", "Closing the pilot-owned live position through the quantity-scoped live close path.")
        try:
            close_result = adapter.close_position(
                request=CloseTradeRequest(
                    trade_index=open_index,
                    close_underlying_price=close_price,
                    close_contract_mid=max(close_price / 100.0, 0.0001),
                    close_fraction=0.999,
                ),
                target_trade=open_trade,
            )
        except Exception as exc:
            report["blockers"].append(_issue("close_failed", str(exc) or "Broker-live close failed for the pilot-owned position."))
            report["manual_action_required"] = True
            _safe_step(report, "close", "blocked", "Broker-live pilot close failed.")
            return _finalize_report(
                db,
                tenant=tenant,
                state=paper_state,
                profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
                linked_account=linked_account,
                actor=actor,
                report=report,
                success_status="completed",
                audit_event_type="trade_automation.live_pilot_window_exited",
            )
        report["terminal_state"] = "closed"
        report["exit_evidence"] = {
            "closed": True,
            "broker_order_id": close_result.broker_order_id,
            "broker_status": close_result.broker_status,
            "order_id": order_id,
            "trade_id": trade_id,
        }
        _safe_step(report, "close", "closed", "Pilot-owned live position was closed.")
        _record_order_event(
            db,
            tenant=tenant,
            actor=actor,
            trade_id=trade_id or order_id,
            ticker=symbol,
            event_key="order.closed",
            status="closed",
            order_type="market",
            time_in_force="day",
            route_state="closed",
            book_state="flat",
            detail="Closed the pilot-owned supervised live position.",
            payload={
                "order_id": order_id,
                "automation_profile_key": LIVE_PILOT_WINDOW_LIVE_PROFILE,
                "live_pilot_window_id": approval.get("approval_id") or last_report.get("approval_id"),
                "trade": serialize_value(close_result.closed_trade),
                "execution": {
                    "adapter": close_result.broker_name,
                    "broker_order_id": close_result.broker_order_id,
                    "broker_status": close_result.broker_status,
                },
            },
            audit_event_type="trade.order_closed",
        )
    else:
        report["blockers"].append(_issue("terminal_target_missing", "No pilot-owned pending order or open trade row was found for exit."))
        report["manual_action_required"] = True
        _safe_step(report, "exit", "blocked", "Could not locate pilot-owned terminal target.")
        return _finalize_report(
            db,
            tenant=tenant,
            state=paper_state,
            profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
            linked_account=linked_account,
            actor=actor,
            report=report,
            success_status="completed",
            audit_event_type="trade_automation.live_pilot_window_exited",
        )

    _safe_step(report, "reconciliation", "running", "Reconciling local supervised live pilot ledger state.")
    reconciliation_status, reconciliation_blockers = _reconciliation_status(order_id, report.get("terminal_state"))
    report["reconciliation_status"] = reconciliation_status
    if reconciliation_blockers:
        report["blockers"].extend(reconciliation_blockers)
        report["manual_action_required"] = True
    _safe_step(report, "reconciliation", reconciliation_status, "Local supervised live pilot reconciliation completed.")
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=LIVE_PILOT_WINDOW_PAPER_PROFILE,
        linked_account=linked_account,
        actor=actor,
        report=report,
        success_status="completed",
        audit_event_type="trade_automation.live_pilot_window_exited",
    )
