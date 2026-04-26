from __future__ import annotations

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
from backend.services.trade_service import _record_order_event, sync_pending_orders_from_broker

LIVE_PILOT_SOAK_NOTE_OWNER = "automation-ai"
LIVE_PILOT_SOAK_HISTORY_LIMIT = 8
LIVE_PILOT_SOAK_NOTE_LIMIT = 250
LIVE_PILOT_PAPER_PROFILE = "personal_paper"
LIVE_PILOT_LIVE_PROFILE = "personal_live"
LIVE_PILOT_MAX_NOTIONAL_CEILING = 10.0
LIVE_PILOT_MIN_QUANTITY = 0.001
LIVE_PILOT_SETTINGS_DEFAULTS = {
    "live_pilot_soak_enabled": False,
    "live_pilot_max_notional": 10.0,
    "live_pilot_symbol": "SPY",
    "live_pilot_approval_ttl_minutes": 15,
    "live_pilot_cancel_timeout_seconds": 30,
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


def _coerce_float(value: Any, default: float = 0.0, *, minimum: float | None = None, maximum: float | None = None) -> float:
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
    return "profile-" + str(profile_key or LIVE_PILOT_PAPER_PROFILE).strip().lower().replace(":", "-")


def _issue(key: str, detail: str, *, component: str = "live_pilot_soak", severity: str = "blocker") -> dict[str, Any]:
    return {"key": key, "component": component, "severity": severity, "detail": detail}


def normalize_live_pilot_soak_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(settings_state or {})
    symbol = str(raw.get("live_pilot_symbol") or LIVE_PILOT_SETTINGS_DEFAULTS["live_pilot_symbol"]).strip().upper()
    if not symbol or len(symbol) > 12:
        symbol = LIVE_PILOT_SETTINGS_DEFAULTS["live_pilot_symbol"]
    max_notional = _coerce_float(
        raw.get("live_pilot_max_notional"),
        LIVE_PILOT_SETTINGS_DEFAULTS["live_pilot_max_notional"],
        minimum=1.0,
        maximum=LIVE_PILOT_MAX_NOTIONAL_CEILING,
    )
    return {
        "live_pilot_soak_enabled": _coerce_bool(
            raw.get("live_pilot_soak_enabled"),
            LIVE_PILOT_SETTINGS_DEFAULTS["live_pilot_soak_enabled"],
        ),
        "live_pilot_max_notional": max_notional,
        "live_pilot_symbol": symbol,
        "live_pilot_approval_ttl_minutes": _coerce_int(
            raw.get("live_pilot_approval_ttl_minutes"),
            LIVE_PILOT_SETTINGS_DEFAULTS["live_pilot_approval_ttl_minutes"],
            minimum=1,
            maximum=60,
        ),
        "live_pilot_cancel_timeout_seconds": _coerce_int(
            raw.get("live_pilot_cancel_timeout_seconds"),
            LIVE_PILOT_SETTINGS_DEFAULTS["live_pilot_cancel_timeout_seconds"],
            minimum=5,
            maximum=120,
        ),
    }


def normalize_live_pilot_soak_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("live_pilot_soak_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    approval = runtime.get("live_pilot_soak_approval")
    if not isinstance(approval, dict):
        approval = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("live_pilot_soak_history") or [])[:LIVE_PILOT_SOAK_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "live_pilot_soak_last_report": serialize_value(last_report),
        "live_pilot_soak_last_note_id": str(runtime.get("live_pilot_soak_last_note_id") or "").strip()
        or None,
        "live_pilot_soak_note_session_day": str(runtime.get("live_pilot_soak_note_session_day") or "").strip()
        or None,
        "live_pilot_soak_last_run_at": _serialize_datetime(_parse_datetime(runtime.get("live_pilot_soak_last_run_at"))),
        "live_pilot_soak_last_error": str(runtime.get("live_pilot_soak_last_error") or "").strip() or None,
        "live_pilot_soak_approval": serialize_value(approval),
        "live_pilot_soak_history": history,
    }


def build_live_pilot_soak_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    settings_snapshot = normalize_live_pilot_soak_settings((state or {}).get("settings"))
    runtime = normalize_live_pilot_soak_runtime((state or {}).get("runtime"))
    report = dict(runtime.get("live_pilot_soak_last_report") or {})
    approval = dict(runtime.get("live_pilot_soak_approval") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "current_step": "idle",
            "approval_status": str(approval.get("status") or "missing"),
            "approval_expires_at": approval.get("expires_at"),
            "approval_id": approval.get("approval_id"),
            "symbol": settings_snapshot["live_pilot_symbol"],
            "notional_cap": settings_snapshot["live_pilot_max_notional"],
            "broker_order_id": None,
            "local_order_id": None,
            "fill_evidence": {},
            "cancel_evidence": {},
            "close_evidence": {},
            "reconciliation_status": "not_run",
            "blockers": [],
            "warnings": [],
            "related_note_id": runtime.get("live_pilot_soak_last_note_id"),
            "note_id": runtime.get("live_pilot_soak_last_note_id"),
            "last_run_at": runtime.get("live_pilot_soak_last_run_at"),
            "last_error": runtime.get("live_pilot_soak_last_error"),
            "manual_action_required": False,
            "settings": settings_snapshot,
        }
    report.setdefault("approval_id", approval.get("approval_id"))
    report.setdefault("approval_expires_at", approval.get("expires_at"))
    report.setdefault("approval_status", approval.get("status"))
    report.setdefault("related_note_id", runtime.get("live_pilot_soak_last_note_id"))
    report.setdefault("note_id", runtime.get("live_pilot_soak_last_note_id"))
    report.setdefault("last_run_at", runtime.get("live_pilot_soak_last_run_at"))
    report.setdefault("last_error", runtime.get("live_pilot_soak_last_error"))
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


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIVE_PILOT_SOAK_NOTE_OWNER,
            limit=LIVE_PILOT_SOAK_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "live-pilot-soak",
        "live-pilot-readiness",
        "paper-canary",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    lines = [
        f"Automation tiny live pilot soak for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Current step: {str(report.get('current_step') or '').replace('_', ' ')}",
        f"Approval expires: {report.get('approval_expires_at') or '--'}",
        f"Symbol: {report.get('symbol') or '--'}",
        f"Notional cap: {report.get('notional_cap') or '--'}",
        f"Reference price: {report.get('reference_price') or '--'}",
        f"Limit price: {report.get('limit_price') or '--'}",
        f"Quantity: {report.get('quantity') or '--'}",
        f"Local order id: {report.get('local_order_id') or '--'}",
        f"Broker order id: {report.get('broker_order_id') or '--'}",
        f"Broker status: {report.get('broker_status') or '--'}",
        f"Reconciliation status: {report.get('reconciliation_status') or 'not_run'}",
        "",
        "This live pilot soak is manual-only. Prepare never places an order. Run may submit and cancel exactly one tiny live limit order after fresh approval. It does not enable live automation, arm profiles, clear kill switches, clear broker-live gates, tune baseline settings, or permit unattended live cycles.",
        "",
        "Blockers",
    ]
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    if blockers:
        lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in blockers[:12])
    else:
        lines.append("- None.")
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    lines.extend(["", "Warnings"])
    if warnings:
        lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in warnings[:12])
    else:
        lines.append("- None.")
    steps = [item for item in list(report.get("steps") or []) if isinstance(item, dict)]
    lines.extend(["", "Steps"])
    if steps:
        lines.extend(f"- {item.get('step')}: {item.get('status')}. {item.get('detail')}" for item in steps[:18])
    else:
        lines.append("- No live pilot steps ran.")
    for heading, key in (
        ("Fill evidence", "fill_evidence"),
        ("Cancel evidence", "cancel_evidence"),
        ("Close evidence", "close_evidence"),
    ):
        evidence = dict(report.get(key) or {})
        if not evidence:
            continue
        lines.extend(["", heading])
        for item_key, item_value in evidence.items():
            lines.append(f"- {str(item_key).replace('_', ' ')}: {item_value}")
    return "\n".join(lines).strip()


def _sync_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "live-pilot-soak",
        "live-pilot-readiness",
        "paper-canary",
        _profile_tag(profile_key),
        f"session-{session_day}",
    ]
    title = f"Automation tiny live pilot soak - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = str(report.get("related_note_id") or report.get("note_id") or "").strip() or _find_existing_note_id(
        profile_key,
        session_day,
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": LIVE_PILOT_SOAK_NOTE_OWNER,
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


def _preflight_issues(
    *,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None,
    rollout_readiness: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    paper_settings = dict(paper_state.get("settings") or {})
    live_settings = dict((live_state or {}).get("settings") or {})
    runtime = dict(paper_state.get("runtime") or {})
    soak_settings = normalize_live_pilot_soak_settings(paper_settings)
    readiness = dict(runtime.get("live_pilot_readiness_last_report") or {})
    rollout = dict(rollout_readiness or {})

    if not soak_settings["live_pilot_soak_enabled"]:
        blockers.append(
            _issue(
                "live_pilot_soak_disabled",
                "The tiny live pilot soak is disabled. Turn on live_pilot_soak_enabled before preparing approval.",
                component="settings",
            )
        )
    readiness_status = str(readiness.get("status") or "").strip().lower()
    if readiness_status != "ready_to_request_approval":
        blockers.append(
            _issue(
                "live_readiness_not_ready",
                f"Live pilot readiness status is {readiness_status or 'missing'}; run readiness review until it is ready_to_request_approval.",
                component="live_pilot_readiness",
            )
        )
    if str(readiness.get("broker_live_gate_status") or "").strip().lower() not in {"open", ""}:
        blockers.append(
            _issue(
                "broker_live_gate_locked",
                "Live pilot readiness reports that the broker-live gate is locked.",
                component="broker_live_gate",
            )
        )
    if str(readiness.get("safety_lock_status") or "").strip().lower() not in {"clear", ""}:
        blockers.append(
            _issue(
                "safety_lock_active",
                "Live pilot readiness reports an active safety lock.",
                component="safety_lock",
            )
        )
    if not bool(rollout.get("allows_live_rollout")):
        blockers.append(
            _issue(
                "broker_live_rollout_locked",
                "Existing broker-live rollout readiness does not allow live routing.",
                component="broker_live_gate",
            )
        )
    if str(live_settings.get("execution_intent") or "").strip().lower() != "broker_live":
        blockers.append(
            _issue(
                "live_profile_route_not_live",
                "The personal live profile is not configured for broker_live routing.",
                component="live_profile",
            )
        )
    if _coerce_bool(live_settings.get("enabled"), False) or _coerce_bool(live_settings.get("armed"), False):
        blockers.append(
            _issue(
                "live_profile_enabled_or_armed",
                "Live automation must stay disabled and disarmed during the tiny live soak.",
                component="live_profile",
            )
        )
    if _coerce_bool(paper_settings.get("kill_switch"), False) or _coerce_bool(live_settings.get("kill_switch"), False):
        blockers.append(
            _issue(
                "kill_switch_active",
                "A paper or live profile kill switch is active. The live soak cannot clear safety locks.",
                component="safety_lock",
            )
        )
    if not bool(settings.alpaca_live_trading_enabled):
        blockers.append(
            _issue(
                "live_trading_disabled",
                "Server live trading is disabled. Turn on ALPACA_LIVE_TRADING_ENABLED before preparing the live soak.",
                component="broker_live_gate",
            )
        )
    if not _live_credentials_present():
        blockers.append(
            _issue(
                "live_credentials_missing",
                "Alpaca live credentials are not configured.",
                component="broker_live_gate",
            )
        )
    last_report = dict(runtime.get("live_pilot_soak_last_report") or {})
    if last_report.get("broker_order_id") and last_report.get("manual_action_required"):
        blockers.append(
            _issue(
                "prior_live_soak_requires_review",
                "The previous live soak touched a broker order and still requires manual review.",
                component="live_pilot_soak",
            )
        )

    if readiness.get("warnings"):
        warnings.append(
            _issue(
                "readiness_warnings_present",
                "Live pilot readiness has warnings. Review the readiness note before running the live soak.",
                component="live_pilot_readiness",
                severity="warning",
            )
        )
    return blockers, warnings, soak_settings, readiness


def _build_soak_analysis(symbol: str, interval: str, live_price: float) -> dict[str, Any]:
    target = live_price + max(live_price * 0.01, 0.01)
    invalidation = max(0.0001, live_price - max(live_price * 0.01, 0.01))
    return {
        "ticker": symbol,
        "interval": interval,
        "verdict": "BULLISH",
        "alignment_label": "live_pilot_soak",
        "conviction_label": "operator_validation",
        "setup_score": 1.0,
        "alpha_score": 1.0,
        "execution_score": 1.0,
        "portfolio_score": 1.0,
        "edge_to_cost_ratio": 1.0,
        "proxy_correlation_bucket": "live_pilot_soak",
        "portfolio_rank": 1,
        "auto_entry_eligible": False,
        "setup_grade": "SOAK",
        "trade_decision": "Manual tiny live pilot soak.",
        "reject_reason": "",
        "event_risk": False,
        "option_plan": {
            "recommended_contract": None,
            "expected_underlying_target": target,
            "invalidation_price": invalidation,
            "take_profit_1": 0.01,
            "take_profit_2": 0.02,
        },
        "forecast": {"forecast_horizon_bars": 1},
    }


def _find_open_trade(order_id: str) -> tuple[int | None, dict[str, Any] | None]:
    open_trades = sdm.read_open_trades()
    if open_trades.empty or "order_id" not in open_trades.columns:
        return None, None
    matches = open_trades["order_id"].astype(str).str.strip() == str(order_id or "").strip()
    if not matches.any():
        return None, None
    index = int(open_trades.index[matches][0])
    return index, open_trades.loc[index].to_dict()


def _has_order(frame: pd.DataFrame, order_id: str) -> bool:
    if frame.empty or "order_id" not in frame.columns:
        return False
    return bool(frame["order_id"].astype(str).str.strip().eq(str(order_id or "").strip()).any())


def _live_reconciliation_status(order_id: str, terminal_state: str | None) -> tuple[str, list[dict[str, Any]]]:
    pending = sdm.read_pending_orders()
    open_trades = sdm.read_open_trades()
    closed = sdm.read_closed_trades()
    blockers: list[dict[str, Any]] = []
    if terminal_state == "canceled":
        if _has_order(pending, order_id):
            blockers.append(_issue("pending_order_after_cancel", "The local pending ledger still contains the canceled live soak order."))
        if _has_order(open_trades, order_id):
            blockers.append(_issue("open_trade_after_cancel", "The local open ledger shows a position after the cancel path."))
    elif terminal_state == "closed":
        if _has_order(open_trades, order_id):
            blockers.append(_issue("open_trade_after_close", "The local open ledger still contains the filled live soak position."))
        if not _has_order(closed, order_id):
            blockers.append(_issue("closed_trade_missing", "The local closed ledger is missing the closed live soak position."))
    else:
        blockers.append(_issue("terminal_state_missing", "Live soak terminal state could not be confirmed."))
    return ("blocked" if blockers else "clean"), blockers


def _apply_live_markers(
    *,
    tenant: Tenant,
    soak_id: str,
    trade_id: str,
    order_id: str,
    position_opened: bool,
) -> dict[str, Any] | None:
    markers = {
        "automation_origin": "trade_automation",
        "automation_tenant_id": tenant.id,
        "automation_tenant_slug": tenant.slug,
        "automation_profile_key": LIVE_PILOT_LIVE_PROFILE,
        "automation_execution_intent": "broker_live",
        "live_pilot_soak_id": soak_id,
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
    elif warnings and success_status != "approved":
        status = "warning"
    else:
        status = success_status
    report["status"] = status
    report["label"] = {
        "approved": "Tiny live soak approved",
        "completed": "Tiny live soak completed",
        "warning": "Tiny live soak warning",
        "blocked": "Tiny live soak blocked",
    }.get(status, "Tiny live pilot soak")
    report["manual_action_required"] = bool(blockers or report.get("manual_action_required"))
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
        "symbol",
        "notional_cap",
        "reference_price",
        "limit_price",
        "quantity",
        "local_order_id",
        "local_trade_id",
        "broker_order_id",
        "broker_status",
        "terminal_state",
        "reconciliation_status",
        "fill_evidence",
        "cancel_evidence",
        "close_evidence",
        "blockers",
        "warnings",
        "note_id",
        "related_note_id",
        "manual_action_required",
    }
    runtime["live_pilot_soak_last_report"] = serialize_value({key: report.get(key) for key in summary_keys if key in report})
    runtime["live_pilot_soak_last_note_id"] = note_id
    runtime["live_pilot_soak_note_session_day"] = report.get("session_day")
    runtime["live_pilot_soak_last_run_at"] = report.get("checked_at")
    runtime["live_pilot_soak_last_error"] = None
    history = list(runtime.get("live_pilot_soak_history") or [])
    history.insert(
        0,
        {
            "at": report.get("checked_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "terminal_state": report.get("terminal_state"),
            "reconciliation_status": report.get("reconciliation_status"),
            "broker_order_id": report.get("broker_order_id"),
            "order_id": report.get("local_order_id"),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "blockers": serialize_value(blockers[:5]),
            "warnings": serialize_value(warnings[:5]),
            "note_id": note_id,
        },
    )
    runtime["live_pilot_soak_history"] = serialize_value(history[:LIVE_PILOT_SOAK_HISTORY_LIMIT])
    if db is not None:
        record_audit_event(
            db,
            event_type=audit_event_type,
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "live_profile_key": LIVE_PILOT_LIVE_PROFILE,
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


def prepare_live_pilot_soak(
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
    blockers, warnings, soak_settings, readiness = _preflight_issues(
        paper_state=paper_state,
        live_state=live_state,
        rollout_readiness=rollout_readiness,
    )
    approval_id = str(uuid4())
    expires_at = now + timedelta(minutes=int(soak_settings["live_pilot_approval_ttl_minutes"]))
    report: dict[str, Any] = {
        "status": "running",
        "profile_key": LIVE_PILOT_PAPER_PROFILE,
        "live_profile_key": LIVE_PILOT_LIVE_PROFILE,
        "session_day": session_day,
        "checked_at": _serialize_datetime(now),
        "current_step": "preflight",
        "approval_id": approval_id,
        "approval_status": "blocked" if blockers else "approved",
        "approval_expires_at": _serialize_datetime(expires_at),
        "symbol": soak_settings["live_pilot_symbol"],
        "notional_cap": soak_settings["live_pilot_max_notional"],
        "cancel_timeout_seconds": soak_settings["live_pilot_cancel_timeout_seconds"],
        "broker_order_id": None,
        "local_order_id": None,
        "terminal_state": None,
        "reconciliation_status": "not_run",
        "fill_evidence": {},
        "cancel_evidence": {},
        "close_evidence": {},
        "readiness_status": readiness.get("status"),
        "blockers": blockers,
        "warnings": warnings,
        "steps": [],
    }
    if blockers:
        _safe_step(report, "preflight", "blocked", "Live pilot soak approval was blocked before any order path was reachable.")
    else:
        _safe_step(report, "preflight", "approved", "Live pilot soak approval is fresh and order placement remains a separate manual action.")
        paper_state.setdefault("runtime", {})["live_pilot_soak_approval"] = {
            "approval_id": approval_id,
            "status": "approved",
            "approved_at": _serialize_datetime(now),
            "expires_at": _serialize_datetime(expires_at),
            "session_day": session_day,
            "symbol": soak_settings["live_pilot_symbol"],
            "notional_cap": soak_settings["live_pilot_max_notional"],
            "cancel_timeout_seconds": soak_settings["live_pilot_cancel_timeout_seconds"],
            "consumed_at": None,
            "broker_order_id": None,
            "order_id": None,
        }
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=LIVE_PILOT_PAPER_PROFILE,
        linked_account=linked_account,
        actor=actor,
        report=report,
        success_status="approved",
        audit_event_type="trade_automation.live_pilot_soak_prepared",
    )


def _resolve_reference_price(symbol: str) -> float:
    price = float(sdm.get_live_price(symbol))
    if pd.isna(price) or price <= 0:
        raise ValidationServiceError(f"Could not resolve a reliable live reference price for {symbol}.")
    return float(price)


def run_live_pilot_soak(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None,
    rollout_readiness: dict[str, Any] | None = None,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    current_user: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    runtime = paper_state.setdefault("runtime", {})
    approval = dict(runtime.get("live_pilot_soak_approval") or {})
    session_day = _session_day_for(now)
    blockers, warnings, soak_settings, readiness = _preflight_issues(
        paper_state=paper_state,
        live_state=live_state,
        rollout_readiness=rollout_readiness,
    )
    approval_id = str(approval.get("approval_id") or "").strip()
    expires_at = _parse_datetime(approval.get("expires_at"))
    approval_status = str(approval.get("status") or "").strip().lower()
    if not approval_id or approval_status != "approved":
        blockers.append(_issue("approval_missing", "Prepare the live pilot soak before running the tiny live order."))
    elif expires_at is None or expires_at < now:
        blockers.append(_issue("approval_expired", "The live pilot soak approval expired. Prepare it again before running."))
    elif approval.get("consumed_at"):
        blockers.append(_issue("approval_consumed", "The live pilot soak approval was already consumed. Prepare a fresh approval."))

    symbol = str(approval.get("symbol") or soak_settings["live_pilot_symbol"]).strip().upper()
    notional_cap = min(
        _coerce_float(approval.get("notional_cap"), soak_settings["live_pilot_max_notional"], minimum=1.0, maximum=LIVE_PILOT_MAX_NOTIONAL_CEILING),
        LIVE_PILOT_MAX_NOTIONAL_CEILING,
    )
    trade_id = str(uuid4())
    order_id = str(uuid4())
    route_correlation_id = str(uuid4())
    report: dict[str, Any] = {
        "status": "running",
        "profile_key": LIVE_PILOT_PAPER_PROFILE,
        "live_profile_key": LIVE_PILOT_LIVE_PROFILE,
        "session_day": session_day,
        "checked_at": _serialize_datetime(now),
        "current_step": "preflight",
        "approval_id": approval_id or None,
        "approval_status": approval_status or "missing",
        "approval_expires_at": approval.get("expires_at"),
        "symbol": symbol,
        "notional_cap": notional_cap,
        "cancel_timeout_seconds": int(approval.get("cancel_timeout_seconds") or soak_settings["live_pilot_cancel_timeout_seconds"]),
        "local_order_id": order_id,
        "local_trade_id": trade_id,
        "route_correlation_id": route_correlation_id,
        "broker_order_id": None,
        "broker_status": None,
        "terminal_state": None,
        "reconciliation_status": "not_run",
        "fill_evidence": {},
        "cancel_evidence": {},
        "close_evidence": {},
        "readiness_status": readiness.get("status"),
        "blockers": blockers,
        "warnings": warnings,
        "steps": [],
    }
    if blockers:
        _safe_step(report, "preflight", "blocked", "Live pilot soak run was blocked before any order path was reachable.")
        return _finalize_report(
            db,
            tenant=tenant,
            state=paper_state,
            profile_key=LIVE_PILOT_PAPER_PROFILE,
            linked_account=linked_account,
            actor=actor,
            report=report,
            success_status="completed",
            audit_event_type="trade_automation.live_pilot_soaked",
        )

    try:
        reference_price = _resolve_reference_price(symbol)
    except Exception as exc:
        report["blockers"].append(_issue("reference_price_missing", str(exc) or "Live reference price is unavailable."))
        _safe_step(report, "preflight", "blocked", "Could not resolve live reference price.")
        return _finalize_report(
            db,
            tenant=tenant,
            state=paper_state,
            profile_key=LIVE_PILOT_PAPER_PROFILE,
            linked_account=linked_account,
            actor=actor,
            report=report,
            success_status="completed",
            audit_event_type="trade_automation.live_pilot_soaked",
        )
    limit_price = round(max(reference_price * 0.95, 0.01), 2)
    quantity = LIVE_PILOT_MIN_QUANTITY
    estimated_notional = float(quantity * limit_price)
    report.update(
        {
            "reference_price": reference_price,
            "limit_price": limit_price,
            "quantity": quantity,
            "estimated_notional": estimated_notional,
        }
    )
    if estimated_notional > notional_cap:
        report["blockers"].append(
            _issue(
                "notional_cap_too_low",
                f"The smallest 0.001 share order would use about ${estimated_notional:.2f}, above the ${notional_cap:.2f} cap.",
            )
        )
        _safe_step(report, "preflight", "blocked", "Smallest supported quantity exceeds the notional cap.")
        return _finalize_report(
            db,
            tenant=tenant,
            state=paper_state,
            profile_key=LIVE_PILOT_PAPER_PROFILE,
            linked_account=linked_account,
            actor=actor,
            report=report,
            success_status="completed",
            audit_event_type="trade_automation.live_pilot_soaked",
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
            profile_key=LIVE_PILOT_PAPER_PROFILE,
            linked_account=linked_account,
            actor=actor,
            report=report,
            success_status="completed",
            audit_event_type="trade_automation.live_pilot_soaked",
        )

    interval = "1m"
    request = OpenTradeRequest(
        ticker=symbol,
        interval=interval,
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
        route_family="live_pilot_soak",
        route_version="v1",
        automation_entry_reason="live_pilot_soak",
        thesis_direction="bullish",
        source="live_pilot_soak",
    )
    analysis = _build_soak_analysis(symbol, interval, limit_price)
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
        "route_family": "live_pilot_soak",
        "route_version": "v1",
        "source": "live_pilot_soak",
        "automation_entry_reason": "live_pilot_soak",
        "tenant_id": tenant.id,
        "tenant_slug": tenant.slug,
    }

    _safe_step(report, "submit", "running", "Submitting one tiny non-marketable broker-live limit order.")
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
        detail="Submitting tiny live pilot soak limit order.",
        payload={
            "order_id": order_id,
            "route_correlation_id": route_correlation_id,
            "automation_profile_key": LIVE_PILOT_LIVE_PROFILE,
            "live_pilot_soak_id": approval_id,
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
        _safe_step(report, "submit", "blocked", "Broker-live order submission failed.")
        return _finalize_report(
            db,
            tenant=tenant,
            state=paper_state,
            profile_key=LIVE_PILOT_PAPER_PROFILE,
            linked_account=linked_account,
            actor=actor,
            report=report,
            success_status="completed",
            audit_event_type="trade_automation.live_pilot_soaked",
        )

    runtime["live_pilot_soak_approval"] = {
        **approval,
        "status": "consumed",
        "consumed_at": _serialize_datetime(now),
        "broker_order_id": submit_result.broker_order_id,
        "order_id": order_id,
    }
    marker_row = _apply_live_markers(
        tenant=tenant,
        soak_id=approval_id,
        trade_id=trade_id,
        order_id=order_id,
        position_opened=bool(submit_result.position_opened),
    )
    if marker_row is None:
        report["warnings"].append(_issue("marker_update_missing", "Live soak order was submitted but automation marker metadata could not be written.", severity="warning"))
    report["broker_order_id"] = submit_result.broker_order_id
    report["broker_status"] = submit_result.broker_status
    _safe_step(
        report,
        "submit",
        "filled" if submit_result.position_opened else "accepted",
        "Broker-live order filled immediately." if submit_result.position_opened else "Broker-live order accepted as working.",
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
        detail="Live pilot soak order filled immediately." if submit_result.position_opened else "Live pilot soak order is working.",
        payload={
            "order_id": order_id,
            "route_correlation_id": route_correlation_id,
            "automation_profile_key": LIVE_PILOT_LIVE_PROFILE,
            "live_pilot_soak_id": approval_id,
            "execution": {
                "adapter": submit_result.broker_name,
                "broker_order_id": submit_result.broker_order_id,
                "broker_status": submit_result.broker_status,
            },
            "record": serialize_value(submit_result.record),
        },
    )

    opened_record = submit_result.record if submit_result.position_opened else None
    if not submit_result.position_opened:
        sync_state = ""
        if current_user is not None:
            _safe_step(report, "sync", "running", "Syncing the live pilot soak order from broker state.")
            sync_result = sync_pending_orders_from_broker(db=db, current_user=current_user, order_id=order_id)
            sync_items = list((sync_result or {}).get("items") or [])
            sync_item = sync_items[0] if sync_items else {}
            sync_state = str(sync_item.get("state") or "").strip().lower()
            _safe_step(report, "sync", sync_state or "checked", sync_item.get("detail") or "Broker sync completed.")
            if sync_state == "filled":
                report["fill_evidence"] = {
                    "state": "filled",
                    "broker_order_id": sync_item.get("broker_order_id"),
                    "broker_status": sync_item.get("broker_status"),
                    "slippage_bps": sync_item.get("slippage_bps"),
                }
                _, opened_record = _find_open_trade(order_id)
        if sync_state != "filled":
            _safe_step(report, "cancel", "running", "Canceling the unfilled live pilot soak order.")
            try:
                cancel_result = adapter.cancel_order(order_id=order_id)
            except Exception as exc:
                report["blockers"].append(_issue("cancel_failed", str(exc) or "Broker-live cancellation failed."))
                _safe_step(report, "cancel", "blocked", "Broker-live cancellation failed.")
                report["manual_action_required"] = True
                return _finalize_report(
                    db,
                    tenant=tenant,
                    state=paper_state,
                    profile_key=LIVE_PILOT_PAPER_PROFILE,
                    linked_account=linked_account,
                    actor=actor,
                    report=report,
                    success_status="completed",
                    audit_event_type="trade_automation.live_pilot_soaked",
                )
            if cancel_result is None:
                report["blockers"].append(_issue("cancel_missing", "Broker-live cancellation returned no local terminal order evidence."))
                _safe_step(report, "cancel", "blocked", "Broker-live cancellation did not produce local terminal evidence.")
                report["manual_action_required"] = True
                return _finalize_report(
                    db,
                    tenant=tenant,
                    state=paper_state,
                    profile_key=LIVE_PILOT_PAPER_PROFILE,
                    linked_account=linked_account,
                    actor=actor,
                    report=report,
                    success_status="completed",
                    audit_event_type="trade_automation.live_pilot_soaked",
                )
            report["terminal_state"] = "canceled"
            report["broker_status"] = cancel_result.broker_status or report.get("broker_status")
            report["cancel_evidence"] = {
                "canceled": True,
                "broker_order_id": cancel_result.broker_order_id,
                "broker_status": cancel_result.broker_status,
                "order_id": order_id,
            }
            _safe_step(report, "cancel", "canceled", "Unfilled live pilot soak order was canceled.")
            _record_order_event(
                db,
                tenant=tenant,
                actor=actor,
                trade_id=trade_id,
                ticker=symbol,
                event_key="order.canceled",
                status="canceled",
                order_type="limit",
                time_in_force="day",
                route_state="canceled",
                book_state="flat",
                detail="Canceled the unfilled live pilot soak order.",
                payload={
                    "order_id": order_id,
                    "route_correlation_id": route_correlation_id,
                    "automation_profile_key": LIVE_PILOT_LIVE_PROFILE,
                    "live_pilot_soak_id": approval_id,
                    "order": serialize_value(cancel_result.canceled_order),
                    "execution": {
                        "adapter": cancel_result.broker_name,
                        "broker_order_id": cancel_result.broker_order_id,
                        "broker_status": cancel_result.broker_status,
                    },
                },
                audit_event_type="trade.order_canceled",
            )

    if opened_record:
        _, opened_record = _find_open_trade(order_id)
        if not opened_record:
            report["blockers"].append(_issue("fill_ledger_missing", "Broker-live fill was observed but no local open trade row was found."))
            _safe_step(report, "fill", "blocked", "Local open trade evidence is missing after broker-live fill.")
            report["manual_action_required"] = True
            return _finalize_report(
                db,
                tenant=tenant,
                state=paper_state,
                profile_key=LIVE_PILOT_PAPER_PROFILE,
                linked_account=linked_account,
                actor=actor,
                report=report,
                success_status="completed",
                audit_event_type="trade_automation.live_pilot_soaked",
            )
        report["fill_evidence"] = {
            "state": "filled",
            "broker_order_id": opened_record.get("broker_order_id"),
            "broker_status": opened_record.get("broker_status"),
            "order_id": order_id,
            "trade_id": trade_id,
        }
        open_index, target_trade = _find_open_trade(order_id)
        if open_index is None or target_trade is None:
            report["blockers"].append(_issue("close_target_missing", "Could not resolve the local open trade index for the live pilot close."))
            _safe_step(report, "close", "blocked", "Local close target is missing.")
            report["manual_action_required"] = True
            return _finalize_report(
                db,
                tenant=tenant,
                state=paper_state,
                profile_key=LIVE_PILOT_PAPER_PROFILE,
                linked_account=linked_account,
                actor=actor,
                report=report,
                success_status="completed",
                audit_event_type="trade_automation.live_pilot_soaked",
            )
        close_price = _coerce_float(
            target_trade.get("actual_fill_price") or target_trade.get("broker_filled_avg_price") or target_trade.get("live_price_at_open"),
            limit_price,
            minimum=0.0001,
        )
        _safe_step(report, "close", "running", "Closing the unexpected live pilot soak fill through the quantity-scoped live close path.")
        try:
            close_result = adapter.close_position(
                request=CloseTradeRequest(
                    trade_index=open_index,
                    close_underlying_price=close_price,
                    close_contract_mid=max(close_price / 100.0, 0.0001),
                    close_fraction=0.999,
                ),
                target_trade=target_trade,
            )
        except Exception as exc:
            report["blockers"].append(_issue("close_failed", str(exc) or "Broker-live close failed after the live pilot fill."))
            _safe_step(report, "close", "blocked", "Broker-live close failed after fill.")
            report["manual_action_required"] = True
            return _finalize_report(
                db,
                tenant=tenant,
                state=paper_state,
                profile_key=LIVE_PILOT_PAPER_PROFILE,
                linked_account=linked_account,
                actor=actor,
                report=report,
                success_status="completed",
                audit_event_type="trade_automation.live_pilot_soaked",
            )
        report["terminal_state"] = "closed"
        report["close_evidence"] = {
            "closed": True,
            "broker_order_id": close_result.broker_order_id,
            "broker_status": close_result.broker_status,
            "order_id": order_id,
            "trade_id": trade_id,
        }
        _safe_step(report, "close", "closed", "Unexpected live pilot soak fill was closed.")
        _record_order_event(
            db,
            tenant=tenant,
            actor=actor,
            trade_id=trade_id,
            ticker=symbol,
            event_key="order.closed",
            status="closed",
            order_type="market",
            time_in_force="day",
            route_state="closed",
            book_state="flat",
            detail="Closed the unexpected live pilot soak fill.",
            payload={
                "order_id": order_id,
                "route_correlation_id": route_correlation_id,
                "automation_profile_key": LIVE_PILOT_LIVE_PROFILE,
                "live_pilot_soak_id": approval_id,
                "trade": serialize_value(close_result.closed_trade),
                "execution": {
                    "adapter": close_result.broker_name,
                    "broker_order_id": close_result.broker_order_id,
                    "broker_status": close_result.broker_status,
                },
            },
            audit_event_type="trade.order_closed",
        )

    _safe_step(report, "reconciliation", "running", "Reconciling local live pilot soak ledger state.")
    reconciliation_status, reconciliation_blockers = _live_reconciliation_status(order_id, report.get("terminal_state"))
    report["reconciliation_status"] = reconciliation_status
    if reconciliation_blockers:
        report["blockers"].extend(reconciliation_blockers)
        report["manual_action_required"] = True
    _safe_step(report, "reconciliation", reconciliation_status, "Local live pilot soak reconciliation completed.")
    if not report.get("terminal_state"):
        report["terminal_state"] = "checked"
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=LIVE_PILOT_PAPER_PROFILE,
        linked_account=linked_account,
        actor=actor,
        report=report,
        success_status="completed",
        audit_event_type="trade_automation.live_pilot_soaked",
    )
