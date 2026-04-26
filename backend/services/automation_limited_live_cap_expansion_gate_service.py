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

LIMITED_LIVE_CAP_EXPANSION_GATE_NOTE_OWNER = "automation-ai"
LIMITED_LIVE_CAP_EXPANSION_GATE_HISTORY_LIMIT = 12
LIMITED_LIVE_CAP_EXPANSION_GATE_NOTE_LIMIT = 250
LIMITED_LIVE_CAP_EXPANSION_GATE_MAX_NOTIONAL_CEILING = 250.0
LIMITED_LIVE_CAP_EXPANSION_GATE_PAPER_PROFILE = "personal_paper"
LIMITED_LIVE_CAP_EXPANSION_GATE_LIVE_PROFILE = "personal_live"

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE

LIMITED_LIVE_CAP_EXPANSION_GATE_SETTINGS_DEFAULTS: dict[str, Any] = {
    "limited_live_cap_expansion_enabled": False,
    "limited_live_cap_expansion_max_notional": LIMITED_LIVE_CAP_EXPANSION_GATE_MAX_NOTIONAL_CEILING,
    "limited_live_cap_expansion_duration_minutes": 60,
    "limited_live_cap_expansion_approval_ttl_minutes": 10,
    "limited_live_cap_expansion_max_session_orders": 1,
    "limited_live_cap_expansion_require_limit": True,
    "limited_live_cap_expansion_auto_expand_enabled": False,
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
    return "profile-" + str(profile_key or LIMITED_LIVE_CAP_EXPANSION_GATE_PAPER_PROFILE).strip().lower().replace(":", "-")


def _issue(key: str, detail: str, *, component: str = "limited_live_cap_expansion_gate", severity: str = "blocker") -> dict[str, Any]:
    return {"key": key, "component": component, "severity": severity, "detail": detail}


def _safe_step(report: dict[str, Any], step: str, status: str, detail: str, **extra: Any) -> None:
    report.setdefault("steps", []).append(serialize_value({"step": step, "status": status, "detail": detail, **extra}))
    report["current_step"] = step


def _live_credentials_present() -> bool:
    return bool(
        (settings.alpaca_live_api_key_id or settings.alpaca_api_key_id)
        and (settings.alpaca_live_api_secret_key or settings.alpaca_api_secret_key)
    )


def normalize_limited_live_cap_expansion_gate_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    return {
        "limited_live_cap_expansion_enabled": _coerce_bool(
            state.get("limited_live_cap_expansion_enabled"),
            bool(LIMITED_LIVE_CAP_EXPANSION_GATE_SETTINGS_DEFAULTS["limited_live_cap_expansion_enabled"]),
        ),
        "limited_live_cap_expansion_max_notional": _coerce_float(
            state.get("limited_live_cap_expansion_max_notional"),
            float(LIMITED_LIVE_CAP_EXPANSION_GATE_SETTINGS_DEFAULTS["limited_live_cap_expansion_max_notional"]),
            minimum=1.0,
            maximum=LIMITED_LIVE_CAP_EXPANSION_GATE_MAX_NOTIONAL_CEILING,
        ),
        "limited_live_cap_expansion_duration_minutes": _coerce_int(
            state.get("limited_live_cap_expansion_duration_minutes"),
            int(LIMITED_LIVE_CAP_EXPANSION_GATE_SETTINGS_DEFAULTS["limited_live_cap_expansion_duration_minutes"]),
            minimum=5,
            maximum=240,
        ),
        "limited_live_cap_expansion_approval_ttl_minutes": _coerce_int(
            state.get("limited_live_cap_expansion_approval_ttl_minutes"),
            int(LIMITED_LIVE_CAP_EXPANSION_GATE_SETTINGS_DEFAULTS["limited_live_cap_expansion_approval_ttl_minutes"]),
            minimum=1,
            maximum=30,
        ),
        "limited_live_cap_expansion_max_session_orders": _coerce_int(
            state.get("limited_live_cap_expansion_max_session_orders"),
            int(LIMITED_LIVE_CAP_EXPANSION_GATE_SETTINGS_DEFAULTS["limited_live_cap_expansion_max_session_orders"]),
            minimum=1,
            maximum=1,
        ),
        "limited_live_cap_expansion_require_limit": _coerce_bool(
            state.get("limited_live_cap_expansion_require_limit"),
            bool(LIMITED_LIVE_CAP_EXPANSION_GATE_SETTINGS_DEFAULTS["limited_live_cap_expansion_require_limit"]),
        ),
        "limited_live_cap_expansion_auto_expand_enabled": False,
    }


def normalize_limited_live_cap_expansion_gate_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("limited_live_cap_expansion_gate_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    approval = runtime.get("limited_live_cap_expansion_gate_approval")
    if not isinstance(approval, dict):
        approval = {}
    allowance = runtime.get("limited_live_cap_expansion_gate_allowance")
    if not isinstance(allowance, dict):
        allowance = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("limited_live_cap_expansion_gate_history") or [])[
            :LIMITED_LIVE_CAP_EXPANSION_GATE_HISTORY_LIMIT
        ]
        if isinstance(item, dict)
    ]
    return {
        "limited_live_cap_expansion_gate_last_report": serialize_value(last_report),
        "limited_live_cap_expansion_gate_last_note_id": str(
            runtime.get("limited_live_cap_expansion_gate_last_note_id") or ""
        ).strip()
        or None,
        "limited_live_cap_expansion_gate_note_session_day": str(
            runtime.get("limited_live_cap_expansion_gate_note_session_day") or ""
        ).strip()
        or None,
        "limited_live_cap_expansion_gate_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("limited_live_cap_expansion_gate_last_run_at"))
        ),
        "limited_live_cap_expansion_gate_approval": serialize_value(approval),
        "limited_live_cap_expansion_gate_allowance": serialize_value(allowance),
        "limited_live_cap_expansion_gate_history": history,
        "limited_live_cap_expansion_gate_last_error": str(
            runtime.get("limited_live_cap_expansion_gate_last_error") or ""
        ).strip()
        or None,
    }


def _allowance_active(allowance: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or _utc_now()
    if str(allowance.get("status") or "").strip().lower() != "active":
        return False
    expires_at = _parse_datetime(allowance.get("expires_at"))
    return bool(expires_at and expires_at > now)


def build_limited_live_cap_expansion_gate_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    settings_snapshot = normalize_limited_live_cap_expansion_gate_settings((state or {}).get("settings"))
    runtime = normalize_limited_live_cap_expansion_gate_runtime((state or {}).get("runtime"))
    report = dict(runtime.get("limited_live_cap_expansion_gate_last_report") or {})
    approval = dict(runtime.get("limited_live_cap_expansion_gate_approval") or {})
    allowance = dict(runtime.get("limited_live_cap_expansion_gate_allowance") or {})
    active = _allowance_active(allowance)
    if not report:
        current_cap = _coerce_float(dict((state or {}).get("settings") or {}).get("limited_live_rollout_max_notional"), 100.0)
        report = {
            "status": "not_prepared",
            "label": "Limited-live cap expansion gate not prepared",
            "profile_key": LIMITED_LIVE_CAP_EXPANSION_GATE_PAPER_PROFILE,
            "live_profile_key": LIMITED_LIVE_CAP_EXPANSION_GATE_LIVE_PROFILE,
            "approval_state": str(approval.get("status") or "missing").strip().lower() or "missing",
            "approval_expires_at": approval.get("expires_at"),
            "expansion_active": active,
            "expansion_expires_at": allowance.get("expires_at"),
            "current_max_notional": current_cap,
            "expanded_max_notional": settings_snapshot["limited_live_cap_expansion_max_notional"],
            "caps": {
                "current_max_notional": current_cap,
                "expanded_max_notional": settings_snapshot["limited_live_cap_expansion_max_notional"],
                "max_session_orders": settings_snapshot["limited_live_cap_expansion_max_session_orders"],
                "duration_minutes": settings_snapshot["limited_live_cap_expansion_duration_minutes"],
                "require_limit": settings_snapshot["limited_live_cap_expansion_require_limit"],
            },
            "consumed_order_count": _coerce_int(allowance.get("consumed_order_count"), 0, minimum=0),
            "blockers": [],
            "warnings": [],
            "rollback_state": str(allowance.get("status") or "inactive").strip().lower() or "inactive",
            "manual_action_required": False,
            "related_note_id": runtime.get("limited_live_cap_expansion_gate_last_note_id"),
            "note_id": runtime.get("limited_live_cap_expansion_gate_last_note_id"),
            "evaluated_at": runtime.get("limited_live_cap_expansion_gate_last_run_at"),
        }
    report.setdefault("enabled", settings_snapshot["limited_live_cap_expansion_enabled"])
    report.setdefault("auto_expand_enabled", False)
    report.setdefault("approval_state", str(approval.get("status") or "missing").strip().lower() or "missing")
    report.setdefault("approval_expires_at", approval.get("expires_at"))
    report.setdefault("expansion_active", active)
    report.setdefault("expansion_expires_at", allowance.get("expires_at"))
    report.setdefault(
        "caps",
        {
            "expanded_max_notional": settings_snapshot["limited_live_cap_expansion_max_notional"],
            "max_session_orders": settings_snapshot["limited_live_cap_expansion_max_session_orders"],
            "duration_minutes": settings_snapshot["limited_live_cap_expansion_duration_minutes"],
            "require_limit": settings_snapshot["limited_live_cap_expansion_require_limit"],
        },
    )
    report.setdefault("consumed_order_count", _coerce_int(allowance.get("consumed_order_count"), 0, minimum=0))
    report.setdefault("rollback_state", str(allowance.get("status") or "inactive").strip().lower() or "inactive")
    report.setdefault("related_note_id", runtime.get("limited_live_cap_expansion_gate_last_note_id"))
    report.setdefault("note_id", runtime.get("limited_live_cap_expansion_gate_last_note_id"))
    report.setdefault("last_error", runtime.get("limited_live_cap_expansion_gate_last_error"))
    return serialize_value(report)


def _report_timestamp(report: dict[str, Any]) -> datetime | None:
    for key in ("evaluated_at", "checked_at", "last_run_at", "at"):
        parsed = _parse_datetime(report.get(key))
        if parsed is not None:
            return parsed
    return None


def _report_is_stale(report: dict[str, Any], now: datetime, *, default_days: int = 2) -> bool:
    timestamp = _report_timestamp(report)
    stale_after_days = _coerce_int(report.get("stale_after_days"), default_days, minimum=1, maximum=30)
    if timestamp is None:
        return True
    return (now - timestamp).total_seconds() > stale_after_days * 86400


def _row_value(row: Any, key: str) -> Any:
    try:
        return row.get(key)
    except AttributeError:
        return None


def _row_is_uncontrolled_live(row: Any) -> bool:
    profile = str(_row_value(row, "automation_profile_key") or "").strip().lower()
    intent = str(_row_value(row, "automation_execution_intent") or _row_value(row, "execution_intent") or "").strip().lower()
    if profile != LIMITED_LIVE_CAP_EXPANSION_GATE_LIVE_PROFILE and intent != "broker_live":
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


def _active_rollout_allowance(runtime: dict[str, Any], now: datetime) -> dict[str, Any]:
    allowance = dict(runtime.get("limited_live_rollout_gate_allowance") or {})
    if str(allowance.get("status") or "").strip().lower() != "active":
        return {}
    expires_at = _parse_datetime(allowance.get("expires_at"))
    if expires_at is None or expires_at <= now:
        return {}
    if str(allowance.get("session_day") or "").strip() != _session_day_for(now):
        return {}
    return allowance


def _preflight_issues(
    *,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None,
    rollout_readiness: dict[str, Any] | None,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    gate_settings = normalize_limited_live_cap_expansion_gate_settings(paper_state.get("settings"))
    runtime = paper_state.setdefault("runtime", {})
    report = dict(runtime.get("limited_live_cap_expansion_report_last_report") or {})
    rollout_gate_report = dict(runtime.get("limited_live_rollout_gate_last_report") or {})
    rollout_canary = dict(runtime.get("limited_live_rollout_canary_last_report") or {})
    live_state = live_state if isinstance(live_state, dict) else {}
    live_settings = dict(live_state.get("settings") or {})
    rollout = dict(rollout_readiness or {})
    rollout_allowance = _active_rollout_allowance(runtime, now)

    if not gate_settings["limited_live_cap_expansion_enabled"]:
        blockers.append(
            _issue(
                "limited_live_cap_expansion_disabled",
                "Limited-live cap expansion gate is disabled. Turn on limited_live_cap_expansion_enabled before preparing approval.",
            )
        )
    if str(report.get("status") or "").strip().lower() != "ready_to_request_cap_expansion":
        blockers.append(
            _issue(
                "cap_expansion_report_not_ready",
                f"Limited-live cap expansion report status is {report.get('status') or 'missing'}; it must be ready before expansion approval.",
                component="limited_live_cap_expansion_report",
            )
        )
    elif _report_is_stale(report, now):
        blockers.append(
            _issue(
                "cap_expansion_report_stale",
                "Limited-live cap expansion report evidence is stale; run the report again before expansion approval.",
                component="limited_live_cap_expansion_report",
            )
        )
    if report.get("blockers"):
        blockers.append(
            _issue(
                "cap_expansion_report_has_blockers",
                "The latest cap expansion report still has unresolved blockers.",
                component="limited_live_cap_expansion_report",
            )
        )
    if str(report.get("broker_live_gate_status") or report.get("broker_gate_status") or "").strip().lower() != "open":
        blockers.append(_issue("broker_live_gate_not_open", "The cap expansion report does not show an open broker-live gate.", component="broker_live_gate"))
    if str(report.get("safety_lock_status") or "").strip().lower() not in {"clear", "none"}:
        blockers.append(_issue("safety_lock_active", "The cap expansion report shows an active safety lock.", component="safety_lock"))
    if str(report.get("state_control_status") or "").strip().lower() == "halt" or str(runtime.get("state_control_state") or "").strip().lower() == "halt":
        blockers.append(_issue("state_control_halt", "State-control is halted; cap expansion cannot proceed.", component="state_control"))
    if str(rollout_canary.get("status") or "").strip().lower() == "blocked" or rollout_canary.get("blockers"):
        blockers.append(_issue("rollout_canary_blocked", "Limited-live rollout canary has unresolved blocker evidence.", component="limited_live_rollout_canary"))
    if str(rollout_gate_report.get("status") or "").strip().lower() == "blocked" or rollout_gate_report.get("blockers"):
        blockers.append(_issue("rollout_gate_blocked", "Limited-live rollout gate has unresolved blocker evidence.", component="limited_live_rollout"))
    if not rollout_allowance:
        blockers.append(
            _issue(
                "limited_live_rollout_inactive",
                "A base limited-live rollout allowance must be active before cap expansion can be activated.",
                component="limited_live_rollout",
            )
        )
    if not bool(rollout.get("allows_live_rollout")):
        blockers.append(_issue("broker_live_rollout_locked", "Existing broker-live rollout readiness gate is locked.", component="broker_live_gate"))
    if not _live_credentials_present():
        blockers.append(_issue("live_broker_credentials_missing", "Live Alpaca credentials are not configured.", component="broker_live_gate"))
    if not bool(settings.alpaca_live_trading_enabled):
        blockers.append(_issue("live_broker_disabled", "Live broker trading is disabled in server configuration.", component="broker_live_gate"))
    if str(live_settings.get("execution_intent") or "").strip().lower() != "broker_live":
        blockers.append(_issue("live_profile_route_not_live", "The personal live profile is not configured for broker_live routing.", component="broker_live_gate"))
    if _coerce_bool(live_settings.get("kill_switch"), False) or _coerce_bool(dict(paper_state.get("settings") or {}).get("kill_switch"), False):
        blockers.append(_issue("kill_switch_active", "A paper or live kill switch is active. The cap expansion gate cannot clear safety locks.", component="safety_lock"))

    ledger_counts = _uncontrolled_live_ledger_counts()
    if ledger_counts["pending"] or ledger_counts["open"]:
        blockers.append(
            _issue(
                "uncontrolled_live_exposure",
                "Existing uncontrolled live order or position evidence is present.",
                component="broker_live_gate",
            )
        )

    current_cap = _coerce_float(
        rollout_allowance.get("max_notional")
        or dict(rollout_allowance.get("caps") or {}).get("max_notional")
        or dict(paper_state.get("settings") or {}).get("limited_live_rollout_max_notional"),
        100.0,
        minimum=1.0,
    )
    report_cap = _coerce_float(
        report.get("recommended_next_max_notional") or report.get("target_max_notional"),
        gate_settings["limited_live_cap_expansion_max_notional"],
        minimum=1.0,
    )
    expanded_cap = min(
        gate_settings["limited_live_cap_expansion_max_notional"],
        report_cap,
        LIMITED_LIVE_CAP_EXPANSION_GATE_MAX_NOTIONAL_CEILING,
    )
    if expanded_cap <= current_cap + 0.01:
        blockers.append(
            _issue(
                "no_higher_cap_available",
                f"The requested expanded cap (${expanded_cap:.2f}) does not exceed the current limited-live cap (${current_cap:.2f}).",
            )
        )
    cap_summary = {
        "current_max_notional": current_cap,
        "expanded_max_notional": expanded_cap,
        "report_recommended_max_notional": report_cap,
    }
    return blockers, warnings, gate_settings, report, rollout_allowance, cap_summary


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIMITED_LIVE_CAP_EXPANSION_GATE_NOTE_OWNER,
            limit=LIMITED_LIVE_CAP_EXPANSION_GATE_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "limited-live-cap-expansion-gate",
        "limited-live-cap-expansion",
        "limited-live-rollout",
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
    lines = [
        f"Automation limited-live cap expansion gate for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Approval: {str(report.get('approval_state') or 'missing').upper()}",
        f"Approval expires: {report.get('approval_expires_at') or '--'}",
        f"Expansion active: {'yes' if report.get('expansion_active') else 'no'}",
        f"Expansion expires: {report.get('expansion_expires_at') or '--'}",
        f"Current cap: ${float(caps.get('current_max_notional') or report.get('current_max_notional') or 0.0):.2f}",
        f"Expanded cap: ${float(caps.get('expanded_max_notional') or report.get('expanded_max_notional') or 0.0):.2f}",
        f"Max session orders: {caps.get('max_session_orders') or report.get('max_session_orders') or '--'}",
        f"Consumed orders: {report.get('consumed_order_count') or 0}",
        f"Limit-only: {'yes' if caps.get('require_limit', True) else 'no'}",
        f"Rollback state: {report.get('rollback_state') or '--'}",
        "",
        "Prepare only creates short-lived approval. Activate only creates a runtime expanded-cap allowance. Rollback only disables that allowance. This gate does not place, cancel, or close orders, enable or arm automation, clear locks, tune settings, change broker-live gates, or permit autonomous expansion.",
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
        "limited-live-cap-expansion-gate",
        "limited-live-cap-expansion",
        "limited-live-rollout",
        _profile_tag(profile_key),
        _profile_tag(LIMITED_LIVE_CAP_EXPANSION_GATE_LIVE_PROFILE),
        f"session-{session_day}",
    ]
    title = f"Automation limited-live cap expansion gate - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = str(report.get("related_note_id") or report.get("note_id") or "").strip() or _find_existing_note_id(profile_key, session_day)
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": LIMITED_LIVE_CAP_EXPANSION_GATE_NOTE_OWNER,
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
        "approved": "Limited-live cap expansion approved",
        "active": "Limited-live cap expansion active",
        "rolled_back": "Limited-live cap expansion rolled back",
        "warning": "Limited-live cap expansion warning",
        "blocked": "Limited-live cap expansion blocked",
    }.get(status, "Limited-live cap expansion gate")
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
        "cap_expansion_report_status",
        "broker_live_gate_status",
        "safety_lock_status",
        "state_control_status",
        "approval_id",
        "approval_state",
        "approval_expires_at",
        "expansion_id",
        "expansion_active",
        "expansion_expires_at",
        "current_max_notional",
        "expanded_max_notional",
        "caps",
        "consumed_order_count",
        "blockers",
        "warnings",
        "rollback_state",
        "rollback_reason",
        "manual_action_required",
        "related_note_id",
        "note_id",
    }
    runtime["limited_live_cap_expansion_gate_last_report"] = serialize_value(
        {key: deepcopy(report.get(key)) for key in summary_keys if key in report}
        | {"steps": serialize_value(list(report.get("steps") or [])[:12])}
    )
    runtime["limited_live_cap_expansion_gate_last_note_id"] = note_id
    runtime["limited_live_cap_expansion_gate_note_session_day"] = report.get("session_day")
    runtime["limited_live_cap_expansion_gate_last_run_at"] = report.get("evaluated_at")
    history = list(runtime.get("limited_live_cap_expansion_gate_history") or [])
    history.insert(0, serialize_value(runtime["limited_live_cap_expansion_gate_last_report"]))
    runtime["limited_live_cap_expansion_gate_history"] = history[:LIMITED_LIVE_CAP_EXPANSION_GATE_HISTORY_LIMIT]
    runtime["limited_live_cap_expansion_gate_last_error"] = None
    if db is not None:
        record_audit_event(
            db,
            event_type=audit_event_type,
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "live_profile_key": LIMITED_LIVE_CAP_EXPANSION_GATE_LIVE_PROFILE,
                "status": report["status"],
                "approval_id": report.get("approval_id"),
                "expansion_id": report.get("expansion_id"),
                "current_max_notional": report.get("current_max_notional"),
                "expanded_max_notional": report.get("expanded_max_notional"),
                "blockers": report.get("blockers") or [],
                "warnings": report.get("warnings") or [],
                "note_id": note_id,
            },
        )
    return serialize_value(report)


def prepare_limited_live_cap_expansion(
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
    blockers, warnings, gate_settings, cap_report, rollout_allowance, cap_summary = _preflight_issues(
        paper_state=paper_state,
        live_state=live_state,
        rollout_readiness=rollout_readiness,
        now=now,
    )
    approval_id = str(uuid4())
    expires_at = now + timedelta(minutes=int(gate_settings["limited_live_cap_expansion_approval_ttl_minutes"]))
    expansion_expires_at = now + timedelta(minutes=int(gate_settings["limited_live_cap_expansion_duration_minutes"]))
    caps = {
        "current_max_notional": cap_summary["current_max_notional"],
        "expanded_max_notional": cap_summary["expanded_max_notional"],
        "max_session_orders": int(gate_settings["limited_live_cap_expansion_max_session_orders"]),
        "duration_minutes": int(gate_settings["limited_live_cap_expansion_duration_minutes"]),
        "require_limit": bool(gate_settings["limited_live_cap_expansion_require_limit"]),
    }
    report: dict[str, Any] = {
        "status": "running",
        "profile_key": LIMITED_LIVE_CAP_EXPANSION_GATE_PAPER_PROFILE,
        "live_profile_key": LIMITED_LIVE_CAP_EXPANSION_GATE_LIVE_PROFILE,
        "session_day": session_day,
        "evaluated_at": _serialize_datetime(now),
        "current_step": "preflight",
        "cap_expansion_report_status": cap_report.get("status"),
        "broker_live_gate_status": cap_report.get("broker_live_gate_status") or cap_report.get("broker_gate_status"),
        "safety_lock_status": cap_report.get("safety_lock_status"),
        "state_control_status": cap_report.get("state_control_status"),
        "approval_id": approval_id,
        "approval_state": "blocked" if blockers else "approved",
        "approval_expires_at": _serialize_datetime(expires_at),
        "expansion_id": None,
        "expansion_active": False,
        "expansion_expires_at": _serialize_datetime(expansion_expires_at),
        "base_rollout_id": rollout_allowance.get("rollout_id"),
        "current_max_notional": caps["current_max_notional"],
        "expanded_max_notional": caps["expanded_max_notional"],
        "caps": caps,
        "max_session_orders": caps["max_session_orders"],
        "consumed_order_count": 0,
        "blockers": blockers,
        "warnings": warnings,
        "rollback_state": "inactive",
        "steps": [],
    }
    if blockers:
        _safe_step(report, "preflight", "blocked", "Limited-live cap expansion approval was blocked before activation was reachable.")
    else:
        _safe_step(report, "preflight", "approved", "Limited-live cap expansion approval is fresh; activation remains a separate manual action.")
        paper_state.setdefault("runtime", {})["limited_live_cap_expansion_gate_approval"] = {
            "approval_id": approval_id,
            "status": "approved",
            "approved_at": _serialize_datetime(now),
            "expires_at": _serialize_datetime(expires_at),
            "expansion_expires_at": _serialize_datetime(expansion_expires_at),
            "session_day": session_day,
            "caps": caps,
            "base_rollout_id": rollout_allowance.get("rollout_id"),
            "consumed_at": None,
        }
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=LIMITED_LIVE_CAP_EXPANSION_GATE_PAPER_PROFILE,
        linked_account=linked_account,
        actor=actor,
        report=report,
        success_status="approved",
        audit_event_type="trade_automation.limited_live_cap_expansion_prepared",
    )


def activate_limited_live_cap_expansion(
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
    approval = dict(runtime.get("limited_live_cap_expansion_gate_approval") or {})
    active_allowance = dict(runtime.get("limited_live_cap_expansion_gate_allowance") or {})
    blockers, warnings, gate_settings, cap_report, rollout_allowance, cap_summary = _preflight_issues(
        paper_state=paper_state,
        live_state=live_state,
        rollout_readiness=rollout_readiness,
        now=now,
    )
    approval_id = str(approval.get("approval_id") or "").strip()
    expires_at = _parse_datetime(approval.get("expires_at"))
    approval_status = str(approval.get("status") or "").strip().lower()
    if not approval_id or approval_status != "approved":
        blockers.append(_issue("approval_missing", "Prepare limited-live cap expansion before activation."))
    elif expires_at is None or expires_at < now:
        blockers.append(_issue("approval_expired", "The limited-live cap expansion approval expired. Prepare it again."))
    elif approval.get("consumed_at"):
        blockers.append(_issue("approval_consumed", "The limited-live cap expansion approval was already consumed. Prepare a fresh approval."))
    if _allowance_active(active_allowance, now):
        blockers.append(_issue("cap_expansion_already_active", "A limited-live cap expansion allowance is already active. Roll it back before activating a new one."))

    caps = dict(approval.get("caps") or {})
    if not caps:
        caps = {
            "current_max_notional": cap_summary["current_max_notional"],
            "expanded_max_notional": cap_summary["expanded_max_notional"],
            "max_session_orders": int(gate_settings["limited_live_cap_expansion_max_session_orders"]),
            "duration_minutes": int(gate_settings["limited_live_cap_expansion_duration_minutes"]),
            "require_limit": bool(gate_settings["limited_live_cap_expansion_require_limit"]),
        }
    session_day = str(approval.get("session_day") or "").strip() or _session_day_for(now)
    expansion_id = str(uuid4())
    expansion_expires_at = now + timedelta(minutes=int(caps.get("duration_minutes") or gate_settings["limited_live_cap_expansion_duration_minutes"]))
    report: dict[str, Any] = {
        "status": "running",
        "profile_key": LIMITED_LIVE_CAP_EXPANSION_GATE_PAPER_PROFILE,
        "live_profile_key": LIMITED_LIVE_CAP_EXPANSION_GATE_LIVE_PROFILE,
        "session_day": session_day,
        "evaluated_at": _serialize_datetime(now),
        "current_step": "activate",
        "cap_expansion_report_status": cap_report.get("status"),
        "broker_live_gate_status": cap_report.get("broker_live_gate_status") or cap_report.get("broker_gate_status"),
        "safety_lock_status": cap_report.get("safety_lock_status"),
        "state_control_status": cap_report.get("state_control_status"),
        "approval_id": approval_id or None,
        "approval_state": approval_status or "missing",
        "approval_expires_at": approval.get("expires_at"),
        "expansion_id": expansion_id,
        "expansion_active": False,
        "expansion_expires_at": _serialize_datetime(expansion_expires_at),
        "base_rollout_id": approval.get("base_rollout_id") or rollout_allowance.get("rollout_id"),
        "current_max_notional": caps.get("current_max_notional"),
        "expanded_max_notional": caps.get("expanded_max_notional"),
        "caps": caps,
        "max_session_orders": caps.get("max_session_orders"),
        "consumed_order_count": 0,
        "blockers": blockers,
        "warnings": warnings,
        "rollback_state": "inactive",
        "steps": [],
    }
    if blockers:
        _safe_step(report, "activate", "blocked", "Limited-live cap expansion activation was blocked before any runtime allowance was written.")
    else:
        allowance = {
            "expansion_id": expansion_id,
            "approval_id": approval_id,
            "base_rollout_id": approval.get("base_rollout_id") or rollout_allowance.get("rollout_id"),
            "status": "active",
            "active": True,
            "activated_at": _serialize_datetime(now),
            "expires_at": _serialize_datetime(expansion_expires_at),
            "session_day": session_day,
            "caps": caps,
            "current_max_notional": caps.get("current_max_notional"),
            "expanded_max_notional": caps.get("expanded_max_notional"),
            "max_notional": caps.get("expanded_max_notional"),
            "max_session_orders": caps.get("max_session_orders"),
            "require_limit": bool(caps.get("require_limit", True)),
            "consumed_order_count": 0,
            "consumed_orders": [],
            "rollback_reason": None,
            "rolled_back_at": None,
        }
        runtime["limited_live_cap_expansion_gate_allowance"] = serialize_value(allowance)
        approval["consumed_at"] = _serialize_datetime(now)
        approval["activated_expansion_id"] = expansion_id
        runtime["limited_live_cap_expansion_gate_approval"] = serialize_value(approval)
        report["expansion_active"] = True
        report["approval_state"] = "consumed"
        _safe_step(report, "activate", "active", "Runtime-only limited-live cap expansion allowance is active inside hard caps.")
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=LIMITED_LIVE_CAP_EXPANSION_GATE_PAPER_PROFILE,
        linked_account=linked_account,
        actor=actor,
        report=report,
        success_status="active",
        audit_event_type="trade_automation.limited_live_cap_expansion_activated",
    )


def rollback_limited_live_cap_expansion(
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
    allowance = dict(runtime.get("limited_live_cap_expansion_gate_allowance") or {})
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
    runtime["limited_live_cap_expansion_gate_allowance"] = serialize_value(allowance)
    caps = dict(allowance.get("caps") or {})
    report = {
        "status": "running",
        "profile_key": LIMITED_LIVE_CAP_EXPANSION_GATE_PAPER_PROFILE,
        "live_profile_key": LIMITED_LIVE_CAP_EXPANSION_GATE_LIVE_PROFILE,
        "session_day": session_day,
        "evaluated_at": _serialize_datetime(now),
        "current_step": "rollback",
        "approval_id": allowance.get("approval_id"),
        "approval_state": "inactive",
        "expansion_id": allowance.get("expansion_id"),
        "expansion_active": False,
        "expansion_expires_at": allowance.get("expires_at"),
        "base_rollout_id": allowance.get("base_rollout_id"),
        "current_max_notional": caps.get("current_max_notional") or allowance.get("current_max_notional"),
        "expanded_max_notional": caps.get("expanded_max_notional") or allowance.get("expanded_max_notional"),
        "caps": caps,
        "max_session_orders": caps.get("max_session_orders") or allowance.get("max_session_orders"),
        "consumed_order_count": _coerce_int(allowance.get("consumed_order_count"), 0, minimum=0),
        "blockers": [],
        "warnings": [],
        "rollback_state": "rolled_back",
        "rollback_reason": rollback_reason,
        "steps": [],
    }
    _safe_step(report, "rollback", "rolled_back", "Runtime-only limited-live cap expansion allowance was disabled.")
    return _finalize_report(
        db,
        tenant=tenant,
        state=paper_state,
        profile_key=LIMITED_LIVE_CAP_EXPANSION_GATE_PAPER_PROFILE,
        linked_account=linked_account,
        actor=actor,
        report=report,
        success_status="rolled_back",
        audit_event_type="trade_automation.limited_live_cap_expansion_rolled_back",
    )


def _base_gate_max_notional(base_gate_result: dict[str, Any] | None) -> float:
    gate = dict(base_gate_result or {})
    allowance = dict(gate.get("allowance") or {})
    caps = dict(allowance.get("caps") or {})
    return _coerce_float(gate.get("max_notional") or allowance.get("max_notional") or caps.get("max_notional"), 100.0, minimum=1.0)


def evaluate_limited_live_cap_expansion_entry_gate(
    state: dict[str, Any] | None,
    *,
    base_gate_result: dict[str, Any] | None,
    now: datetime | None = None,
    order_type: str | None = None,
    estimated_notional: float | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    state = state if isinstance(state, dict) else {}
    runtime = dict(state.get("runtime") or {})
    settings_snapshot = normalize_limited_live_cap_expansion_gate_settings(state.get("settings"))
    base_gate = dict(base_gate_result or {})
    base_cap = _base_gate_max_notional(base_gate)
    if not bool(base_gate.get("allowed")):
        return {
            "allowed": False,
            "reason": base_gate.get("reason") or "limited_live_rollout_gate_locked",
            "detail": base_gate.get("detail") or "Base limited-live rollout allowance must be active before cap expansion.",
            "expansion_active": False,
            "max_notional": base_cap,
            "base_gate": serialize_value(base_gate),
        }
    allowance = dict(runtime.get("limited_live_cap_expansion_gate_allowance") or {})
    expansion_active = bool(settings_snapshot["limited_live_cap_expansion_enabled"] and _allowance_active(allowance, now))
    if not expansion_active:
        if estimated_notional is not None and float(estimated_notional) > base_cap + 0.01:
            return {
                "allowed": False,
                "reason": "limited_live_cap_expansion_required",
                "detail": f"Estimated live entry notional exceeds the base limited-live cap of ${base_cap:.2f}; activate cap expansion before routing a larger entry.",
                "expansion_active": False,
                "estimated_notional": float(estimated_notional),
                "max_notional": base_cap,
            }
        return {
            "allowed": True,
            "reason": "limited_live_rollout_cap_active",
            "detail": "Base limited-live cap applies because no active cap expansion allowance is present.",
            "expansion_active": False,
            "max_notional": base_cap,
            "base_max_notional": base_cap,
        }
    session_day = _session_day_for(now)
    if str(allowance.get("session_day") or "").strip() != session_day:
        return {
            "allowed": False,
            "reason": "limited_live_cap_expansion_session_mismatch",
            "detail": "The active cap expansion allowance belongs to a different New York session.",
            "expansion_active": False,
            "allowance": serialize_value(allowance),
            "max_notional": base_cap,
        }
    caps = dict(allowance.get("caps") or {})
    max_orders = _coerce_int(allowance.get("max_session_orders") or caps.get("max_session_orders"), 1, minimum=1, maximum=1)
    consumed = _coerce_int(allowance.get("consumed_order_count"), 0, minimum=0)
    if consumed >= max_orders:
        return {
            "allowed": False,
            "reason": "limited_live_cap_expansion_order_cap_exhausted",
            "detail": "The limited-live cap expansion session order cap is exhausted.",
            "expansion_active": True,
            "allowance": serialize_value(allowance),
            "consumed_order_count": consumed,
            "max_session_orders": max_orders,
            "max_notional": base_cap,
        }
    require_limit = _coerce_bool(allowance.get("require_limit", caps.get("require_limit")), True)
    normalized_order_type = str(order_type or "").strip().lower()
    if require_limit and normalized_order_type and normalized_order_type != "limit":
        return {
            "allowed": False,
            "reason": "limited_live_cap_expansion_limit_required",
            "detail": "Limited-live cap expansion entries must use limit routing.",
            "expansion_active": True,
            "allowance": serialize_value(allowance),
            "max_notional": base_cap,
        }
    expanded_cap = _coerce_float(
        allowance.get("max_notional") or allowance.get("expanded_max_notional") or caps.get("expanded_max_notional"),
        base_cap,
        minimum=base_cap,
        maximum=LIMITED_LIVE_CAP_EXPANSION_GATE_MAX_NOTIONAL_CEILING,
    )
    if estimated_notional is not None and float(estimated_notional) > expanded_cap + 0.01:
        return {
            "allowed": False,
            "reason": "limited_live_cap_expansion_notional_cap",
            "detail": f"Estimated live entry notional exceeds the expanded limited-live cap of ${expanded_cap:.2f}.",
            "expansion_active": True,
            "estimated_notional": float(estimated_notional),
            "max_notional": expanded_cap,
            "allowance": serialize_value(allowance),
        }
    if str(runtime.get("state_control_state") or "").strip().lower() == "halt" or _coerce_bool(runtime.get("state_control_halt_active"), False):
        return {
            "allowed": False,
            "reason": "limited_live_cap_expansion_state_control_halt",
            "detail": "State-control halt disables the active cap expansion allowance until a new approval cycle.",
            "expansion_active": True,
            "allowance": serialize_value(allowance),
            "max_notional": base_cap,
        }
    return {
        "allowed": True,
        "reason": "limited_live_cap_expansion_active",
        "detail": "Active cap expansion allowance permits one higher capped limit live entry.",
        "expansion_active": True,
        "allowance": serialize_value(allowance),
        "cap_expansion_id": allowance.get("expansion_id"),
        "base_rollout_id": allowance.get("base_rollout_id"),
        "remaining_order_count": max(0, max_orders - consumed),
        "consumed_order_count": consumed,
        "max_session_orders": max_orders,
        "max_notional": expanded_cap,
        "base_max_notional": base_cap,
        "require_limit": require_limit,
        "session_day": session_day,
    }


def apply_limited_live_cap_expansion_entry_overlay(
    settings_state: dict[str, Any],
    gate_result: dict[str, Any] | None,
) -> dict[str, Any]:
    effective = dict(settings_state or {})
    gate_result = dict(gate_result or {})
    if not gate_result.get("allowed"):
        return effective
    max_notional = _coerce_float(gate_result.get("max_notional"), 0.0, minimum=0.0)
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


def record_limited_live_cap_expansion_order_use(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str = LIMITED_LIVE_CAP_EXPANSION_GATE_PAPER_PROFILE,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    order_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    runtime = state.setdefault("runtime", {})
    allowance = dict(runtime.get("limited_live_cap_expansion_gate_allowance") or {})
    if not allowance:
        return {}
    order_record = serialize_value(order_evidence or {})
    consumed_orders = list(allowance.get("consumed_orders") or [])
    consumed_orders.append(order_record)
    allowance["consumed_orders"] = serialize_value(consumed_orders)
    allowance["consumed_order_count"] = _coerce_int(allowance.get("consumed_order_count"), 0, minimum=0) + 1
    allowance["last_order_at"] = _serialize_datetime(now)
    runtime["limited_live_cap_expansion_gate_allowance"] = serialize_value(allowance)
    last_report = dict(runtime.get("limited_live_cap_expansion_gate_last_report") or {})
    caps = dict(allowance.get("caps") or last_report.get("caps") or {})
    last_report.update(
        {
            "status": "active" if _allowance_active(allowance, now) else str(allowance.get("status") or "inactive"),
            "evaluated_at": _serialize_datetime(now),
            "expansion_active": _allowance_active(allowance, now),
            "expansion_id": allowance.get("expansion_id"),
            "expansion_expires_at": allowance.get("expires_at"),
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
        runtime["limited_live_cap_expansion_gate_last_note_id"] = note_id
    runtime["limited_live_cap_expansion_gate_last_report"] = serialize_value(last_report)
    runtime["limited_live_cap_expansion_gate_last_run_at"] = last_report.get("evaluated_at")
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.limited_live_cap_expansion_order_recorded",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "live_profile_key": LIMITED_LIVE_CAP_EXPANSION_GATE_LIVE_PROFILE,
                "expansion_id": allowance.get("expansion_id"),
                "order": order_record,
                "consumed_order_count": allowance["consumed_order_count"],
                "note_id": note_id,
            },
        )
    return serialize_value(last_report)


def disable_limited_live_cap_expansion_allowance_for_fault(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    reason: str,
) -> dict[str, Any]:
    now = now or _utc_now()
    runtime = state.setdefault("runtime", {})
    allowance = dict(runtime.get("limited_live_cap_expansion_gate_allowance") or {})
    if not allowance or str(allowance.get("status") or "").strip().lower() != "active":
        return serialize_value(allowance)
    allowance["status"] = "disabled"
    allowance["active"] = False
    allowance["disabled_at"] = _serialize_datetime(now)
    allowance["disabled_reason"] = str(reason or "hard_fault").strip() or "hard_fault"
    runtime["limited_live_cap_expansion_gate_allowance"] = serialize_value(allowance)
    last_report = dict(runtime.get("limited_live_cap_expansion_gate_last_report") or {})
    last_report.update(
        {
            "status": "blocked",
            "expansion_active": False,
            "rollback_state": "disabled",
            "rollback_reason": allowance["disabled_reason"],
            "evaluated_at": _serialize_datetime(now),
            "blockers": list(last_report.get("blockers") or [])
            + [_issue("limited_live_cap_expansion_disabled_for_fault", "Runtime cap expansion allowance was disabled after a hard safety fault.")],
            "manual_action_required": True,
        }
    )
    runtime["limited_live_cap_expansion_gate_last_report"] = serialize_value(last_report)
    return serialize_value(allowance)
