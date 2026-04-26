from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.core.config import settings
from backend.models.saas import BrokerageLinkedAccount, Tenant
from backend.services import automation_ai_review_service, notes_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

LIMITED_LIVE_ROLLOUT_NOTE_OWNER = "automation-ai"
LIMITED_LIVE_ROLLOUT_HISTORY_LIMIT = 12
LIMITED_LIVE_ROLLOUT_NOTE_LIMIT = 250
LIMITED_LIVE_ROLLOUT_MAX_NOTIONAL_CEILING = 100.0
LIMITED_LIVE_ROLLOUT_PAPER_PROFILE = "personal_paper"
LIMITED_LIVE_ROLLOUT_LIVE_PROFILE = "personal_live"

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE

LIMITED_LIVE_ROLLOUT_SETTINGS_DEFAULTS: dict[str, Any] = {
    "limited_live_rollout_enabled": False,
    "limited_live_rollout_max_notional": LIMITED_LIVE_ROLLOUT_MAX_NOTIONAL_CEILING,
    "limited_live_rollout_max_session_orders": 1,
    "limited_live_rollout_duration_minutes": 60,
    "limited_live_rollout_require_limit": True,
    "limited_live_rollout_approval_ttl_minutes": 10,
    "limited_live_rollout_auto_expand_enabled": False,
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


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


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
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = float(default)
    if minimum is not None:
        numeric = max(float(minimum), numeric)
    if maximum is not None:
        numeric = min(float(maximum), numeric)
    return numeric


def _coerce_int(value: Any, default: int = 0, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        numeric = int(default)
    if minimum is not None:
        numeric = max(int(minimum), numeric)
    if maximum is not None:
        numeric = min(int(maximum), numeric)
    return numeric


def _profile_tag(profile_key: str) -> str:
    return "profile-" + str(profile_key or LIMITED_LIVE_ROLLOUT_PAPER_PROFILE).strip().lower().replace(":", "-")


def _issue(key: str, detail: str, *, component: str = "limited_live_rollout", severity: str = "blocker") -> dict[str, Any]:
    return {"key": key, "component": component, "severity": severity, "detail": detail}


def _safe_step(report: dict[str, Any], step: str, status: str, detail: str, **extra: Any) -> None:
    report.setdefault("steps", []).append(serialize_value({"step": step, "status": status, "detail": detail, **extra}))
    report["current_step"] = step


def _live_credentials_present() -> bool:
    return bool(
        (settings.alpaca_live_api_key_id or settings.alpaca_api_key_id)
        and (settings.alpaca_live_api_secret_key or settings.alpaca_api_secret_key)
    )


def normalize_limited_live_rollout_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    return {
        "limited_live_rollout_enabled": _coerce_bool(
            state.get("limited_live_rollout_enabled"),
            bool(LIMITED_LIVE_ROLLOUT_SETTINGS_DEFAULTS["limited_live_rollout_enabled"]),
        ),
        "limited_live_rollout_max_notional": _coerce_float(
            state.get("limited_live_rollout_max_notional"),
            float(LIMITED_LIVE_ROLLOUT_SETTINGS_DEFAULTS["limited_live_rollout_max_notional"]),
            minimum=1.0,
            maximum=LIMITED_LIVE_ROLLOUT_MAX_NOTIONAL_CEILING,
        ),
        "limited_live_rollout_max_session_orders": _coerce_int(
            state.get("limited_live_rollout_max_session_orders"),
            int(LIMITED_LIVE_ROLLOUT_SETTINGS_DEFAULTS["limited_live_rollout_max_session_orders"]),
            minimum=1,
            maximum=1,
        ),
        "limited_live_rollout_duration_minutes": _coerce_int(
            state.get("limited_live_rollout_duration_minutes"),
            int(LIMITED_LIVE_ROLLOUT_SETTINGS_DEFAULTS["limited_live_rollout_duration_minutes"]),
            minimum=5,
            maximum=240,
        ),
        "limited_live_rollout_require_limit": _coerce_bool(
            state.get("limited_live_rollout_require_limit"),
            bool(LIMITED_LIVE_ROLLOUT_SETTINGS_DEFAULTS["limited_live_rollout_require_limit"]),
        ),
        "limited_live_rollout_approval_ttl_minutes": _coerce_int(
            state.get("limited_live_rollout_approval_ttl_minutes"),
            int(LIMITED_LIVE_ROLLOUT_SETTINGS_DEFAULTS["limited_live_rollout_approval_ttl_minutes"]),
            minimum=1,
            maximum=30,
        ),
        "limited_live_rollout_auto_expand_enabled": False,
    }


def normalize_limited_live_rollout_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("limited_live_rollout_gate_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    approval = runtime.get("limited_live_rollout_gate_approval")
    if not isinstance(approval, dict):
        approval = {}
    allowance = runtime.get("limited_live_rollout_gate_allowance")
    if not isinstance(allowance, dict):
        allowance = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("limited_live_rollout_gate_history") or [])[:LIMITED_LIVE_ROLLOUT_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "limited_live_rollout_gate_last_report": serialize_value(last_report),
        "limited_live_rollout_gate_last_note_id": str(runtime.get("limited_live_rollout_gate_last_note_id") or "").strip()
        or None,
        "limited_live_rollout_gate_note_session_day": str(
            runtime.get("limited_live_rollout_gate_note_session_day") or ""
        ).strip()
        or None,
        "limited_live_rollout_gate_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("limited_live_rollout_gate_last_run_at"))
        ),
        "limited_live_rollout_gate_approval": serialize_value(approval),
        "limited_live_rollout_gate_allowance": serialize_value(allowance),
        "limited_live_rollout_gate_history": history,
        "limited_live_rollout_gate_last_error": str(runtime.get("limited_live_rollout_gate_last_error") or "").strip()
        or None,
    }


def _allowance_active(allowance: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or _utc_now()
    if str(allowance.get("status") or "").strip().lower() != "active":
        return False
    expires_at = _parse_datetime(allowance.get("expires_at"))
    return bool(expires_at and expires_at > now)


def build_limited_live_rollout_gate_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    settings_snapshot = normalize_limited_live_rollout_settings((state or {}).get("settings"))
    runtime = normalize_limited_live_rollout_runtime((state or {}).get("runtime"))
    report = dict(runtime.get("limited_live_rollout_gate_last_report") or {})
    approval = dict(runtime.get("limited_live_rollout_gate_approval") or {})
    allowance = dict(runtime.get("limited_live_rollout_gate_allowance") or {})
    active = _allowance_active(allowance)
    if not report:
        report = {
            "status": "not_prepared",
            "label": "Limited-live rollout gate not prepared",
            "profile_key": LIMITED_LIVE_ROLLOUT_PAPER_PROFILE,
            "live_profile_key": LIMITED_LIVE_ROLLOUT_LIVE_PROFILE,
            "approval_state": str(approval.get("status") or "missing").strip().lower() or "missing",
            "approval_expires_at": approval.get("expires_at"),
            "rollout_active": active,
            "rollout_expires_at": allowance.get("expires_at"),
            "caps": {
                "max_notional": settings_snapshot["limited_live_rollout_max_notional"],
                "max_session_orders": settings_snapshot["limited_live_rollout_max_session_orders"],
                "duration_minutes": settings_snapshot["limited_live_rollout_duration_minutes"],
                "require_limit": settings_snapshot["limited_live_rollout_require_limit"],
            },
            "consumed_order_count": _coerce_int(allowance.get("consumed_order_count"), 0, minimum=0),
            "candidate_order_evidence": {},
            "blockers": [],
            "warnings": [],
            "rollback_state": str(allowance.get("status") or "inactive").strip().lower() or "inactive",
            "manual_action_required": False,
            "related_note_id": runtime.get("limited_live_rollout_gate_last_note_id"),
            "note_id": runtime.get("limited_live_rollout_gate_last_note_id"),
            "evaluated_at": runtime.get("limited_live_rollout_gate_last_run_at"),
        }
    report.setdefault("enabled", settings_snapshot["limited_live_rollout_enabled"])
    report.setdefault("auto_expand_enabled", False)
    report.setdefault("approval_state", str(approval.get("status") or "missing").strip().lower() or "missing")
    report.setdefault("approval_expires_at", approval.get("expires_at"))
    report.setdefault("rollout_active", active)
    report.setdefault("rollout_expires_at", allowance.get("expires_at"))
    report.setdefault(
        "caps",
        {
            "max_notional": settings_snapshot["limited_live_rollout_max_notional"],
            "max_session_orders": settings_snapshot["limited_live_rollout_max_session_orders"],
            "duration_minutes": settings_snapshot["limited_live_rollout_duration_minutes"],
            "require_limit": settings_snapshot["limited_live_rollout_require_limit"],
        },
    )
    report.setdefault("consumed_order_count", _coerce_int(allowance.get("consumed_order_count"), 0, minimum=0))
    report.setdefault("rollback_state", str(allowance.get("status") or "inactive").strip().lower() or "inactive")
    report.setdefault("related_note_id", runtime.get("limited_live_rollout_gate_last_note_id"))
    report.setdefault("note_id", runtime.get("limited_live_rollout_gate_last_note_id"))
    report.setdefault("last_error", runtime.get("limited_live_rollout_gate_last_error"))
    return serialize_value(report)


def _candidate_from_runtime(runtime: dict[str, Any]) -> dict[str, Any] | None:
    candidate = runtime.get("last_candidate")
    if not isinstance(candidate, dict):
        decision = runtime.get("last_decision")
        if isinstance(decision, dict) and isinstance(decision.get("candidate"), dict):
            candidate = decision.get("candidate")
    if not isinstance(candidate, dict) or not candidate:
        return None
    ticker = str(candidate.get("ticker") or candidate.get("symbol") or "").strip().upper()
    if not ticker:
        return None
    reject_reason = str(candidate.get("reject_reason") or candidate.get("block_reason") or "").strip()
    return serialize_value(
        {
            "ticker": ticker,
            "symbol": ticker,
            "portfolio_rank": candidate.get("portfolio_rank") or candidate.get("rank"),
            "alpha_score": candidate.get("alpha_score"),
            "execution_score": candidate.get("execution_score"),
            "portfolio_score": candidate.get("portfolio_score"),
            "edge_to_cost_ratio": candidate.get("edge_to_cost_ratio"),
            "auto_entry_eligible": candidate.get("auto_entry_eligible"),
            "reject_reason": reject_reason or None,
        }
    )


def _unresolved_live_pilot_report(runtime: dict[str, Any]) -> bool:
    for key in (
        "live_pilot_soak_last_report",
        "live_pilot_expansion_last_report",
        "live_pilot_window_last_report",
    ):
        report = runtime.get(key)
        if not isinstance(report, dict) or not report:
            continue
        terminal_state = str(report.get("terminal_state") or "").strip().lower()
        status = str(report.get("status") or "").strip().lower()
        has_order = bool(report.get("broker_order_id") or report.get("local_order_id") or report.get("order_id"))
        if report.get("manual_action_required") and has_order:
            return True
        if has_order and terminal_state not in {"canceled", "closed"} and status not in {"completed", "warning"}:
            return True
    return False


def _row_value(row: Any, key: str) -> Any:
    try:
        return row.get(key)
    except AttributeError:
        return None


def _row_is_uncontrolled_live(row: Any) -> bool:
    profile = str(_row_value(row, "automation_profile_key") or "").strip().lower()
    intent = str(_row_value(row, "automation_execution_intent") or _row_value(row, "execution_intent") or "").strip().lower()
    if profile != LIMITED_LIVE_ROLLOUT_LIVE_PROFILE and intent != "broker_live":
        return False
    known_markers = (
        "live_pilot_soak_id",
        "live_pilot_expansion_id",
        "live_pilot_window_id",
        "limited_live_rollout_id",
        "limited_live_cap_expansion_id",
        "limited_live_next_tier_cap_id",
    )
    return not any(str(_row_value(row, key) or "").strip() for key in known_markers)


def _uncontrolled_live_ledger_counts() -> dict[str, int]:
    counts = {"pending": 0, "open": 0}
    try:
        pending = sdm.read_pending_orders()
        if pending is not None and not getattr(pending, "empty", True):
            counts["pending"] = sum(1 for _, row in pending.iterrows() if _row_is_uncontrolled_live(row))
    except Exception:
        counts["pending"] = 0
    try:
        open_rows = sdm.read_open_trades()
        if open_rows is not None and not getattr(open_rows, "empty", True):
            counts["open"] = sum(1 for _, row in open_rows.iterrows() if _row_is_uncontrolled_live(row))
    except Exception:
        counts["open"] = 0
    return counts


def _promotion_is_stale(promotion: dict[str, Any], now: datetime) -> bool:
    evaluated_at = _parse_datetime(promotion.get("evaluated_at") or promotion.get("last_run_at"))
    stale_after_days = _coerce_int(promotion.get("stale_after_days"), 2, minimum=1, maximum=30)
    if evaluated_at is None:
        return True
    return (now - evaluated_at).total_seconds() > stale_after_days * 86400


def _preflight_issues(
    *,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None,
    rollout_readiness: dict[str, Any] | None,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    settings_snapshot = normalize_limited_live_rollout_settings(paper_state.get("settings"))
    runtime = paper_state.setdefault("runtime", {})
    promotion = dict(runtime.get("live_pilot_promotion_report_last_report") or {})
    live_state = live_state if isinstance(live_state, dict) else {}
    live_settings = dict(live_state.get("settings") or {})
    rollout = dict(rollout_readiness or {})

    if not settings_snapshot["limited_live_rollout_enabled"]:
        blockers.append(
            _issue(
                "limited_live_rollout_disabled",
                "Limited-live rollout gate is disabled. Turn on limited_live_rollout_enabled before preparing approval.",
            )
        )
    if str(promotion.get("status") or "").strip().lower() != "ready_to_request_limited_live_rollout":
        blockers.append(
            _issue(
                "promotion_report_not_ready",
                f"Live pilot promotion report status is {promotion.get('status') or 'missing'}; it must be ready before rollout approval.",
                component="live_pilot_promotion",
            )
        )
    elif _promotion_is_stale(promotion, now):
        blockers.append(
            _issue(
                "promotion_report_stale",
                "Live pilot promotion report evidence is stale; run the promotion report again before rollout approval.",
                component="live_pilot_promotion",
            )
        )
    if str(promotion.get("broker_live_gate_status") or "").strip().lower() != "open":
        blockers.append(_issue("broker_live_gate_not_open", "The promotion report does not show an open broker-live gate.", component="broker_live_gate"))
    if str(promotion.get("safety_lock_status") or "").strip().lower() not in {"clear", "none"}:
        blockers.append(_issue("safety_lock_active", "The promotion report shows an active safety lock.", component="safety_lock"))
    if promotion.get("blockers"):
        blockers.append(_issue("promotion_report_has_blockers", "The latest promotion report still has unresolved blockers.", component="live_pilot_promotion"))
    if not bool(rollout.get("allows_live_rollout")):
        blockers.append(_issue("broker_live_rollout_locked", "Existing broker-live rollout readiness gate is locked.", component="broker_live_gate"))
    if not _live_credentials_present():
        blockers.append(_issue("live_broker_credentials_missing", "Live Alpaca credentials are not configured.", component="broker_live_gate"))
    if not bool(settings.alpaca_live_trading_enabled):
        blockers.append(_issue("live_broker_disabled", "Live broker trading is disabled in server configuration.", component="broker_live_gate"))
    if str(live_settings.get("execution_intent") or "").strip().lower() != "broker_live":
        blockers.append(_issue("live_profile_route_not_live", "The personal live profile is not configured for broker_live routing.", component="broker_live_gate"))
    if _coerce_bool(live_settings.get("kill_switch"), False) or _coerce_bool(dict(paper_state.get("settings") or {}).get("kill_switch"), False):
        blockers.append(_issue("kill_switch_active", "A paper or live kill switch is active. The rollout gate cannot clear safety locks.", component="safety_lock"))
    if str(runtime.get("state_control_state") or "").strip().lower() == "halt" or _coerce_bool(runtime.get("state_control_halt_active"), False):
        blockers.append(_issue("state_control_halt", "State-control is halted; rollout activation cannot proceed.", component="state_control"))
    if _unresolved_live_pilot_report(runtime):
        blockers.append(_issue("prior_live_pilot_unresolved", "Recent live pilot evidence is unresolved and needs operator review."))

    ledger_counts = _uncontrolled_live_ledger_counts()
    if ledger_counts["pending"] or ledger_counts["open"]:
        blockers.append(
            _issue(
                "uncontrolled_live_exposure",
                "Existing uncontrolled live order or position evidence is present.",
                component="broker_live_gate",
            )
        )
    candidate = _candidate_from_runtime(runtime)
    if candidate is None:
        warnings.append(
            _issue(
                "candidate_missing",
                "No current ranked entry candidate is available yet; activation can proceed, but the live cycle will still require a clean candidate.",
                component="candidate",
                severity="warning",
            )
        )
    elif candidate.get("reject_reason") or candidate.get("auto_entry_eligible") is False:
        warnings.append(
            _issue(
                "candidate_needs_review",
                "The latest candidate telemetry is not clean; the live route will re-check candidates before any capped entry.",
                component="candidate",
                severity="warning",
            )
        )
    return blockers, warnings, settings_snapshot, promotion, candidate


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIMITED_LIVE_ROLLOUT_NOTE_OWNER,
            limit=LIMITED_LIVE_ROLLOUT_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "limited-live-rollout",
        "live-pilot-promotion",
        "supervised-live-pilot-canary",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    caps = dict(report.get("caps") or {})
    candidate = dict(report.get("candidate_order_evidence") or {}).get("candidate") or report.get("selected_candidate") or {}
    lines = [
        f"Automation limited-live rollout gate for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Approval: {str(report.get('approval_state') or 'missing').upper()}",
        f"Approval expires: {report.get('approval_expires_at') or '--'}",
        f"Rollout active: {'yes' if report.get('rollout_active') else 'no'}",
        f"Rollout expires: {report.get('rollout_expires_at') or '--'}",
        f"Max notional: ${float(caps.get('max_notional') or report.get('notional_cap') or 0.0):.2f}",
        f"Max session orders: {caps.get('max_session_orders') or report.get('max_session_orders') or '--'}",
        f"Consumed orders: {report.get('consumed_order_count') or 0}",
        f"Limit-only: {'yes' if caps.get('require_limit', True) else 'no'}",
        f"Rollback state: {report.get('rollback_state') or '--'}",
        f"Candidate: {dict(candidate).get('ticker') or dict(candidate).get('symbol') or '--'}",
        "",
        "Prepare only creates short-lived approval. Activate only creates a runtime allowance. Rollback only disables that allowance. This gate does not place, cancel, or close orders, enable or arm automation, clear locks, tune settings, change broker-live gates, or permit autonomous expansion.",
        "",
        "Blockers",
    ]
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    if blockers:
        lines.extend(f"- {item.get('component')}: {item.get('key')}. {item.get('detail')}" for item in blockers[:14])
    else:
        lines.append("- None.")
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    lines.extend(["", "Warnings"])
    if warnings:
        lines.extend(f"- {item.get('component')}: {item.get('key')}. {item.get('detail')}" for item in warnings[:14])
    else:
        lines.append("- None.")
    lines.extend(["", "Steps"])
    for step in list(report.get("steps") or [])[:12]:
        if isinstance(step, dict):
            lines.append(f"- {step.get('step')}: {step.get('status')}. {step.get('detail')}")
    return "\n".join(lines).strip()


def _sync_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "limited-live-rollout",
        "live-pilot-promotion",
        "supervised-live-pilot-canary",
        _profile_tag(profile_key),
        _profile_tag(LIMITED_LIVE_ROLLOUT_LIVE_PROFILE),
        f"session-{session_day}",
    ]
    title = f"Automation limited-live rollout gate - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = str(report.get("related_note_id") or report.get("note_id") or "").strip() or _find_existing_note_id(profile_key, session_day)
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": LIMITED_LIVE_ROLLOUT_NOTE_OWNER,
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
    elif success_status == "approved":
        status = "approved"
    elif success_status == "active":
        status = "active"
    elif success_status == "rolled_back":
        status = "rolled_back"
    elif warnings:
        status = "warning"
    else:
        status = success_status
    report["status"] = status
    report["label"] = {
        "approved": "Limited-live rollout approved",
        "active": "Limited-live rollout active",
        "rolled_back": "Limited-live rollout rolled back",
        "warning": "Limited-live rollout warning",
        "blocked": "Limited-live rollout blocked",
    }.get(status, "Limited-live rollout gate")
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
        "evaluated_at",
        "current_step",
        "promotion_status",
        "broker_live_gate_status",
        "safety_lock_status",
        "approval_id",
        "approval_state",
        "approval_expires_at",
        "rollout_id",
        "rollout_active",
        "rollout_expires_at",
        "caps",
        "notional_cap",
        "max_session_orders",
        "consumed_order_count",
        "candidate_order_evidence",
        "selected_candidate",
        "blockers",
        "warnings",
        "rollback_state",
        "rollback_reason",
        "manual_action_required",
        "note_id",
        "related_note_id",
        "steps",
    }
    runtime["limited_live_rollout_gate_last_report"] = serialize_value(
        {key: report.get(key) for key in summary_keys if key in report}
    )
    runtime["limited_live_rollout_gate_last_note_id"] = note_id
    runtime["limited_live_rollout_gate_note_session_day"] = report.get("session_day")
    runtime["limited_live_rollout_gate_last_run_at"] = report.get("evaluated_at")
    runtime["limited_live_rollout_gate_last_error"] = None
    history = list(runtime.get("limited_live_rollout_gate_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "rollout_active": report.get("rollout_active"),
            "consumed_order_count": report.get("consumed_order_count"),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "note_id": note_id,
        },
    )
    runtime["limited_live_rollout_gate_history"] = serialize_value(history[:LIMITED_LIVE_ROLLOUT_HISTORY_LIMIT])
    if db is not None:
        record_audit_event(
            db,
            event_type=audit_event_type,
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "live_profile_key": LIMITED_LIVE_ROLLOUT_LIVE_PROFILE,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "rollout_active": report.get("rollout_active"),
                "consumed_order_count": report.get("consumed_order_count"),
                "note_id": note_id,
            },
        )
    return serialize_value(report)


def prepare_limited_live_rollout(
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
    blockers, warnings, rollout_settings, promotion, candidate = _preflight_issues(
        paper_state=paper_state,
        live_state=live_state,
        rollout_readiness=rollout_readiness,
        now=now,
    )
    approval_id = str(uuid4())
    expires_at = now + timedelta(minutes=int(rollout_settings["limited_live_rollout_approval_ttl_minutes"]))
    rollout_expires_at = now + timedelta(minutes=int(rollout_settings["limited_live_rollout_duration_minutes"]))
    caps = {
        "max_notional": min(float(rollout_settings["limited_live_rollout_max_notional"]), LIMITED_LIVE_ROLLOUT_MAX_NOTIONAL_CEILING),
        "max_session_orders": int(rollout_settings["limited_live_rollout_max_session_orders"]),
        "duration_minutes": int(rollout_settings["limited_live_rollout_duration_minutes"]),
        "require_limit": bool(rollout_settings["limited_live_rollout_require_limit"]),
    }
    report: dict[str, Any] = {
        "status": "running",
        "profile_key": LIMITED_LIVE_ROLLOUT_PAPER_PROFILE,
        "live_profile_key": LIMITED_LIVE_ROLLOUT_LIVE_PROFILE,
        "session_day": session_day,
        "evaluated_at": _serialize_datetime(now),
        "current_step": "preflight",
        "promotion_status": promotion.get("status"),
        "broker_live_gate_status": promotion.get("broker_live_gate_status"),
        "safety_lock_status": promotion.get("safety_lock_status"),
        "approval_id": approval_id,
        "approval_state": "blocked" if blockers else "approved",
        "approval_expires_at": _serialize_datetime(expires_at),
        "rollout_id": None,
        "rollout_active": False,
        "rollout_expires_at": _serialize_datetime(rollout_expires_at),
        "caps": caps,
        "notional_cap": caps["max_notional"],
        "max_session_orders": caps["max_session_orders"],
        "consumed_order_count": 0,
        "candidate_order_evidence": {"candidate": candidate or {}, "orders": []},
        "selected_candidate": candidate or {},
        "blockers": blockers,
        "warnings": warnings,
        "rollback_state": "inactive",
        "steps": [],
    }
    if blockers:
        _safe_step(report, "preflight", "blocked", "Limited-live rollout approval was blocked before activation was reachable.")
    else:
        _safe_step(report, "preflight", "approved", "Limited-live rollout approval is fresh; activation remains a separate manual action.")
        paper_state.setdefault("runtime", {})["limited_live_rollout_gate_approval"] = {
            "approval_id": approval_id,
            "status": "approved",
            "approved_at": _serialize_datetime(now),
            "expires_at": _serialize_datetime(expires_at),
            "rollout_expires_at": _serialize_datetime(rollout_expires_at),
            "session_day": session_day,
            "caps": caps,
            "candidate": candidate or {},
            "consumed_at": None,
        }
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=LIMITED_LIVE_ROLLOUT_PAPER_PROFILE,
        linked_account=linked_account,
        actor=actor,
        report=report,
        success_status="approved",
        audit_event_type="trade_automation.limited_live_rollout_prepared",
    )


def activate_limited_live_rollout(
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
    approval = dict(runtime.get("limited_live_rollout_gate_approval") or {})
    blockers, warnings, rollout_settings, promotion, candidate = _preflight_issues(
        paper_state=paper_state,
        live_state=live_state,
        rollout_readiness=rollout_readiness,
        now=now,
    )
    approval_id = str(approval.get("approval_id") or "").strip()
    expires_at = _parse_datetime(approval.get("expires_at"))
    approval_status = str(approval.get("status") or "").strip().lower()
    if not approval_id or approval_status != "approved":
        blockers.append(_issue("approval_missing", "Prepare limited-live rollout before activation."))
    elif expires_at is None or expires_at < now:
        blockers.append(_issue("approval_expired", "The limited-live rollout approval expired. Prepare it again."))
    elif approval.get("consumed_at"):
        blockers.append(_issue("approval_consumed", "The limited-live rollout approval was already consumed. Prepare a fresh approval."))

    caps = dict(approval.get("caps") or {})
    if not caps:
        caps = {
            "max_notional": min(
                float(rollout_settings["limited_live_rollout_max_notional"]),
                LIMITED_LIVE_ROLLOUT_MAX_NOTIONAL_CEILING,
            ),
            "max_session_orders": int(rollout_settings["limited_live_rollout_max_session_orders"]),
            "duration_minutes": int(rollout_settings["limited_live_rollout_duration_minutes"]),
            "require_limit": bool(rollout_settings["limited_live_rollout_require_limit"]),
        }
    session_day = str(approval.get("session_day") or "").strip() or _session_day_for(now)
    rollout_id = str(uuid4())
    rollout_expires_at = now + timedelta(minutes=int(caps.get("duration_minutes") or rollout_settings["limited_live_rollout_duration_minutes"]))
    report: dict[str, Any] = {
        "status": "running",
        "profile_key": LIMITED_LIVE_ROLLOUT_PAPER_PROFILE,
        "live_profile_key": LIMITED_LIVE_ROLLOUT_LIVE_PROFILE,
        "session_day": session_day,
        "evaluated_at": _serialize_datetime(now),
        "current_step": "activate",
        "promotion_status": promotion.get("status"),
        "broker_live_gate_status": promotion.get("broker_live_gate_status"),
        "safety_lock_status": promotion.get("safety_lock_status"),
        "approval_id": approval_id or None,
        "approval_state": approval_status or "missing",
        "approval_expires_at": approval.get("expires_at"),
        "rollout_id": rollout_id,
        "rollout_active": False,
        "rollout_expires_at": _serialize_datetime(rollout_expires_at),
        "caps": caps,
        "notional_cap": caps.get("max_notional"),
        "max_session_orders": caps.get("max_session_orders"),
        "consumed_order_count": 0,
        "candidate_order_evidence": {"candidate": dict(approval.get("candidate") or candidate or {}), "orders": []},
        "selected_candidate": dict(approval.get("candidate") or candidate or {}),
        "blockers": blockers,
        "warnings": warnings,
        "rollback_state": "inactive",
        "steps": [],
    }
    if blockers:
        _safe_step(report, "activate", "blocked", "Limited-live rollout activation was blocked before any runtime allowance was written.")
    else:
        allowance = {
            "rollout_id": rollout_id,
            "approval_id": approval_id,
            "status": "active",
            "active": True,
            "activated_at": _serialize_datetime(now),
            "expires_at": _serialize_datetime(rollout_expires_at),
            "session_day": session_day,
            "caps": caps,
            "max_notional": caps.get("max_notional"),
            "max_session_orders": caps.get("max_session_orders"),
            "require_limit": bool(caps.get("require_limit", True)),
            "consumed_order_count": 0,
            "consumed_orders": [],
            "rollback_reason": None,
            "rolled_back_at": None,
        }
        runtime["limited_live_rollout_gate_allowance"] = serialize_value(allowance)
        approval["consumed_at"] = _serialize_datetime(now)
        approval["activated_rollout_id"] = rollout_id
        runtime["limited_live_rollout_gate_approval"] = serialize_value(approval)
        report["rollout_active"] = True
        report["approval_state"] = "consumed"
        _safe_step(report, "activate", "active", "Runtime-only limited-live rollout allowance is active inside hard caps.")
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=LIMITED_LIVE_ROLLOUT_PAPER_PROFILE,
        linked_account=linked_account,
        actor=actor,
        report=report,
        success_status="active",
        audit_event_type="trade_automation.limited_live_rollout_activated",
    )


def rollback_limited_live_rollout(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    runtime = paper_state.setdefault("runtime", {})
    allowance = dict(runtime.get("limited_live_rollout_gate_allowance") or {})
    session_day = str(allowance.get("session_day") or "").strip() or _session_day_for(now)
    rollback_reason = str(reason or "operator_rollback").strip() or "operator_rollback"
    if allowance:
        allowance["status"] = "rolled_back"
        allowance["active"] = False
        allowance["rolled_back_at"] = _serialize_datetime(now)
        allowance["rollback_reason"] = rollback_reason
    else:
        allowance = {
            "status": "rolled_back",
            "active": False,
            "session_day": session_day,
            "rolled_back_at": _serialize_datetime(now),
            "rollback_reason": rollback_reason,
            "consumed_order_count": 0,
            "consumed_orders": [],
        }
    runtime["limited_live_rollout_gate_allowance"] = serialize_value(allowance)
    caps = dict(allowance.get("caps") or {})
    report = {
        "status": "running",
        "profile_key": LIMITED_LIVE_ROLLOUT_PAPER_PROFILE,
        "live_profile_key": LIMITED_LIVE_ROLLOUT_LIVE_PROFILE,
        "session_day": session_day,
        "evaluated_at": _serialize_datetime(now),
        "current_step": "rollback",
        "approval_id": allowance.get("approval_id"),
        "approval_state": "inactive",
        "rollout_id": allowance.get("rollout_id"),
        "rollout_active": False,
        "rollout_expires_at": allowance.get("expires_at"),
        "caps": caps,
        "notional_cap": caps.get("max_notional") or allowance.get("max_notional"),
        "max_session_orders": caps.get("max_session_orders") or allowance.get("max_session_orders"),
        "consumed_order_count": _coerce_int(allowance.get("consumed_order_count"), 0, minimum=0),
        "candidate_order_evidence": {
            "orders": serialize_value(list(allowance.get("consumed_orders") or [])),
        },
        "blockers": [],
        "warnings": [],
        "rollback_state": "rolled_back",
        "rollback_reason": rollback_reason,
        "steps": [],
    }
    _safe_step(report, "rollback", "rolled_back", "Runtime-only limited-live rollout allowance was disabled.")
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=LIMITED_LIVE_ROLLOUT_PAPER_PROFILE,
        linked_account=linked_account,
        actor=actor,
        report=report,
        success_status="rolled_back",
        audit_event_type="trade_automation.limited_live_rollout_rolled_back",
    )


def evaluate_limited_live_rollout_entry_gate(
    state: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    order_type: str | None = None,
    estimated_notional: float | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    state = state if isinstance(state, dict) else {}
    settings_snapshot = normalize_limited_live_rollout_settings(state.get("settings"))
    runtime = dict(state.get("runtime") or {})
    allowance = dict(runtime.get("limited_live_rollout_gate_allowance") or {})
    if not settings_snapshot["limited_live_rollout_enabled"]:
        return {"allowed": False, "reason": "limited_live_rollout_disabled", "detail": "Limited-live rollout gate is disabled."}
    if not _allowance_active(allowance, now):
        return {
            "allowed": False,
            "reason": "limited_live_rollout_inactive",
            "detail": "No active unexpired limited-live rollout allowance is present.",
            "allowance": serialize_value(allowance),
        }
    session_day = _session_day_for(now)
    if str(allowance.get("session_day") or "").strip() != session_day:
        return {
            "allowed": False,
            "reason": "limited_live_rollout_session_mismatch",
            "detail": "The active limited-live rollout allowance belongs to a different New York session.",
            "allowance": serialize_value(allowance),
        }
    caps = dict(allowance.get("caps") or {})
    max_orders = _coerce_int(allowance.get("max_session_orders") or caps.get("max_session_orders"), 1, minimum=1, maximum=1)
    consumed = _coerce_int(allowance.get("consumed_order_count"), 0, minimum=0)
    if consumed >= max_orders:
        return {
            "allowed": False,
            "reason": "limited_live_rollout_order_cap_exhausted",
            "detail": "The limited-live rollout session order cap is exhausted.",
            "allowance": serialize_value(allowance),
            "consumed_order_count": consumed,
            "max_session_orders": max_orders,
        }
    require_limit = _coerce_bool(allowance.get("require_limit", caps.get("require_limit")), True)
    normalized_order_type = str(order_type or "").strip().lower()
    if require_limit and normalized_order_type and normalized_order_type != "limit":
        return {
            "allowed": False,
            "reason": "limited_live_rollout_limit_required",
            "detail": "Limited-live rollout entries must use limit routing.",
            "allowance": serialize_value(allowance),
        }
    max_notional = _coerce_float(
        allowance.get("max_notional") or caps.get("max_notional"),
        LIMITED_LIVE_ROLLOUT_MAX_NOTIONAL_CEILING,
        minimum=1.0,
        maximum=LIMITED_LIVE_ROLLOUT_MAX_NOTIONAL_CEILING,
    )
    if estimated_notional is not None and float(estimated_notional) > max_notional + 0.01:
        return {
            "allowed": False,
            "reason": "limited_live_rollout_notional_cap",
            "detail": f"Estimated live entry notional exceeds the limited-live cap of ${max_notional:.2f}.",
            "allowance": serialize_value(allowance),
            "estimated_notional": float(estimated_notional),
            "max_notional": max_notional,
        }
    if str(runtime.get("state_control_state") or "").strip().lower() == "halt" or _coerce_bool(runtime.get("state_control_halt_active"), False):
        return {
            "allowed": False,
            "reason": "limited_live_rollout_state_control_halt",
            "detail": "State-control halt disables the active limited-live allowance until a new approval cycle.",
            "allowance": serialize_value(allowance),
        }
    return {
        "allowed": True,
        "reason": "limited_live_rollout_active",
        "detail": "Active limited-live rollout allowance permits one capped limit live entry.",
        "allowance": serialize_value(allowance),
        "rollout_id": allowance.get("rollout_id"),
        "remaining_order_count": max(0, max_orders - consumed),
        "consumed_order_count": consumed,
        "max_session_orders": max_orders,
        "max_notional": max_notional,
        "require_limit": require_limit,
        "session_day": session_day,
    }


def apply_limited_live_rollout_entry_overlay(
    settings_state: dict[str, Any],
    gate_result: dict[str, Any] | None,
) -> dict[str, Any]:
    effective = dict(settings_state or {})
    gate_result = dict(gate_result or {})
    if not gate_result.get("allowed"):
        return effective
    max_notional = _coerce_float(gate_result.get("max_notional"), LIMITED_LIVE_ROLLOUT_MAX_NOTIONAL_CEILING, minimum=1.0, maximum=LIMITED_LIVE_ROLLOUT_MAX_NOTIONAL_CEILING)
    if max_notional > 0:
        effective["max_notional_per_trade"] = min(
            _coerce_float(effective.get("max_notional_per_trade"), max_notional, minimum=1.0),
            max_notional,
        )
    remaining = _coerce_int(gate_result.get("remaining_order_count"), 1, minimum=0, maximum=1)
    if remaining > 0:
        effective["max_daily_entries"] = min(_coerce_int(effective.get("max_daily_entries"), remaining, minimum=1), remaining)
        effective["cycle_entry_rank_limit"] = min(_coerce_int(effective.get("cycle_entry_rank_limit"), 1, minimum=1), remaining)
    return effective


def record_limited_live_rollout_order_use(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str = LIMITED_LIVE_ROLLOUT_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    order_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    runtime = state.setdefault("runtime", {})
    allowance = dict(runtime.get("limited_live_rollout_gate_allowance") or {})
    if not allowance:
        return {}
    order_record = serialize_value(order_evidence or {})
    consumed_orders = list(allowance.get("consumed_orders") or [])
    consumed_orders.append(order_record)
    allowance["consumed_orders"] = serialize_value(consumed_orders)
    allowance["consumed_order_count"] = _coerce_int(allowance.get("consumed_order_count"), 0, minimum=0) + 1
    allowance["last_order_at"] = _serialize_datetime(now)
    runtime["limited_live_rollout_gate_allowance"] = serialize_value(allowance)
    last_report = dict(runtime.get("limited_live_rollout_gate_last_report") or {})
    caps = dict(allowance.get("caps") or last_report.get("caps") or {})
    last_report.update(
        {
            "status": "active" if _allowance_active(allowance, now) else str(allowance.get("status") or "inactive"),
            "evaluated_at": _serialize_datetime(now),
            "rollout_active": _allowance_active(allowance, now),
            "rollout_id": allowance.get("rollout_id"),
            "rollout_expires_at": allowance.get("expires_at"),
            "caps": caps,
            "consumed_order_count": allowance["consumed_order_count"],
            "candidate_order_evidence": {
                **dict(last_report.get("candidate_order_evidence") or {}),
                "orders": serialize_value(consumed_orders),
            },
            "rollback_state": str(allowance.get("status") or "active").strip().lower(),
        }
    )
    note_id = _sync_note(tenant=tenant, profile_key=profile_key, report=last_report)
    if note_id:
        last_report["note_id"] = note_id
        last_report["related_note_id"] = note_id
        runtime["limited_live_rollout_gate_last_note_id"] = note_id
    runtime["limited_live_rollout_gate_last_report"] = serialize_value(last_report)
    runtime["limited_live_rollout_gate_last_run_at"] = last_report.get("evaluated_at")
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.limited_live_rollout_order_recorded",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "live_profile_key": LIMITED_LIVE_ROLLOUT_LIVE_PROFILE,
                "rollout_id": allowance.get("rollout_id"),
                "order": order_record,
                "consumed_order_count": allowance["consumed_order_count"],
                "note_id": note_id,
            },
        )
    return serialize_value(last_report)


def disable_limited_live_rollout_allowance_for_fault(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    reason: str,
) -> dict[str, Any]:
    now = now or _utc_now()
    runtime = state.setdefault("runtime", {})
    allowance = dict(runtime.get("limited_live_rollout_gate_allowance") or {})
    if not allowance or str(allowance.get("status") or "").strip().lower() != "active":
        return serialize_value(allowance)
    allowance["status"] = "disabled"
    allowance["active"] = False
    allowance["disabled_at"] = _serialize_datetime(now)
    allowance["disabled_reason"] = str(reason or "hard_fault").strip() or "hard_fault"
    runtime["limited_live_rollout_gate_allowance"] = serialize_value(allowance)
    last_report = dict(runtime.get("limited_live_rollout_gate_last_report") or {})
    last_report.update(
        {
            "status": "blocked",
            "rollout_active": False,
            "rollback_state": "disabled",
            "rollback_reason": allowance["disabled_reason"],
            "evaluated_at": _serialize_datetime(now),
            "blockers": list(last_report.get("blockers") or [])
            + [_issue("limited_live_rollout_disabled_for_fault", "Runtime allowance was disabled after a hard safety fault.")],
            "manual_action_required": True,
        }
    )
    runtime["limited_live_rollout_gate_last_report"] = serialize_value(last_report)
    return serialize_value(allowance)
