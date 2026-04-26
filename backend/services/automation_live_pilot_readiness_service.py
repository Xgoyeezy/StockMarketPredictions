from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.saas import Tenant
from backend.services import automation_ai_review_service, notes_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

LIVE_PILOT_READINESS_NOTE_OWNER = "automation-ai"
LIVE_PILOT_READINESS_HISTORY_LIMIT = 8
LIVE_PILOT_READINESS_NOTE_LIMIT = 250
LIVE_PILOT_READINESS_STALE_HOURS = 72.0
LIVE_PILOT_PERSONAL_PAPER_PROFILE = "personal_paper"
LIVE_PILOT_PERSONAL_LIVE_PROFILE = "personal_live"

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


def _profile_tag(profile_key: str) -> str:
    return "profile-" + str(profile_key or LIVE_PILOT_PERSONAL_PAPER_PROFILE).strip().lower().replace(":", "-")


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def _issue(key: str, detail: str, *, component: str, severity: str = "blocker") -> dict[str, Any]:
    return {
        "key": key,
        "component": component,
        "severity": severity,
        "detail": detail,
    }


def _report_status(report: dict[str, Any] | None) -> str:
    if not isinstance(report, dict) or not report:
        return "missing"
    return str(report.get("status") or "missing").strip().lower() or "missing"


def _report_note_id(report: dict[str, Any] | None) -> str | None:
    if not isinstance(report, dict):
        return None
    value = str(report.get("related_note_id") or report.get("note_id") or "").strip()
    return value or None


def _report_timestamp(report: dict[str, Any] | None, *keys: str) -> datetime | None:
    if not isinstance(report, dict):
        return None
    for key in keys or ("evaluated_at", "last_run_at", "checked_at"):
        parsed = _parse_datetime(report.get(key))
        if parsed is not None:
            return parsed
    return None


def _age_hours(now: datetime, timestamp: datetime | None) -> float | None:
    if timestamp is None:
        return None
    return max(0.0, (now - timestamp).total_seconds() / 3600.0)


def normalize_live_pilot_readiness_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("live_pilot_readiness_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("live_pilot_readiness_history") or [])[:LIVE_PILOT_READINESS_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "live_pilot_readiness_last_report": serialize_value(last_report),
        "live_pilot_readiness_last_note_id": str(runtime.get("live_pilot_readiness_last_note_id") or "").strip()
        or None,
        "live_pilot_readiness_note_session_day": str(
            runtime.get("live_pilot_readiness_note_session_day") or ""
        ).strip()
        or None,
        "live_pilot_readiness_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("live_pilot_readiness_last_run_at"))
        ),
        "live_pilot_readiness_last_error": str(runtime.get("live_pilot_readiness_last_error") or "").strip()
        or None,
        "live_pilot_readiness_history": history,
    }


def build_live_pilot_readiness_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = normalize_live_pilot_readiness_runtime((state or {}).get("runtime"))
    report = dict(runtime.get("live_pilot_readiness_last_report") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "paper_evidence_status": "missing",
            "lifecycle_canary_status": "missing",
            "broker_live_gate_status": "unknown",
            "safety_lock_status": "unknown",
            "required_operator_actions": [],
            "blockers": [],
            "warnings": [],
            "manual_action_required": False,
            "evaluated_at": None,
            "related_note_id": runtime.get("live_pilot_readiness_last_note_id"),
            "note_id": runtime.get("live_pilot_readiness_last_note_id"),
            "last_run_at": runtime.get("live_pilot_readiness_last_run_at"),
            "last_error": runtime.get("live_pilot_readiness_last_error"),
        }
    report.setdefault("related_note_id", runtime.get("live_pilot_readiness_last_note_id"))
    report.setdefault("note_id", runtime.get("live_pilot_readiness_last_note_id"))
    report.setdefault("last_run_at", runtime.get("live_pilot_readiness_last_run_at"))
    report.setdefault("last_error", runtime.get("live_pilot_readiness_last_error"))
    return serialize_value(report)


def _live_credentials_present() -> bool:
    return bool(
        (settings.alpaca_live_api_key_id or settings.alpaca_api_key_id)
        and (settings.alpaca_live_api_secret_key or settings.alpaca_api_secret_key)
    )


def _paper_canary_blockers(report: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = report.get("blockers")
    return [item for item in list(blockers or []) if isinstance(item, dict)]


def _evidence_summary(*, now: datetime, report: dict[str, Any], timestamp_keys: tuple[str, ...]) -> dict[str, Any]:
    status = _report_status(report)
    timestamp = _report_timestamp(report, *timestamp_keys)
    age = _age_hours(now, timestamp)
    return {
        "status": status,
        "evaluated_at": _serialize_datetime(timestamp),
        "age_hours": age,
        "stale": bool(age is None or age > LIVE_PILOT_READINESS_STALE_HOURS),
        "note_id": _report_note_id(report),
    }


def _aggregate_report(
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None,
    rollout_readiness: dict[str, Any] | None,
    now: datetime,
    session_day: str,
) -> dict[str, Any]:
    runtime = dict(paper_state.get("runtime") or {})
    paper_settings = dict(paper_state.get("settings") or {})
    live_state = live_state if isinstance(live_state, dict) else {}
    live_settings = dict(live_state.get("settings") or {})
    rollout = dict(rollout_readiness or {})

    paper_canary_report = dict(runtime.get("paper_canary_last_report") or {})
    lifecycle_report = dict(runtime.get("paper_order_lifecycle_canary_last_report") or {})
    broker_report = dict(runtime.get("paper_broker_reconciliation_last_report") or {})
    state_control_report = dict(runtime.get("state_control_last_evaluation") or {})
    shadow_report = dict(runtime.get("state_control_shadow_last_report") or {})

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    required_actions: list[dict[str, Any]] = []

    paper_canary_status = _report_status(paper_canary_report)
    lifecycle_status = _report_status(lifecycle_report)
    broker_status = _report_status(broker_report)
    shadow_status = _report_status(shadow_report)
    state_control_state = str(
        state_control_report.get("state") or runtime.get("state_control_state") or "unknown"
    ).strip().lower()

    if paper_canary_status != "ready":
        blockers.append(
            _issue(
                "paper_canary_not_ready",
                f"Paper canary status is {paper_canary_status}; it must be ready before requesting a live pilot.",
                component="paper_canary",
            )
        )
    if lifecycle_status != "ready":
        blockers.append(
            _issue(
                "lifecycle_canary_not_ready",
                f"Paper order lifecycle canary status is {lifecycle_status}; clean multi-session lifecycle evidence is required.",
                component="paper_order_lifecycle_canary",
            )
        )
    broker_blockers = _paper_canary_blockers(broker_report)
    if broker_status in {"blocked", "failed", "fail"} or broker_blockers:
        blockers.append(
            _issue(
                "paper_broker_reconciliation_blocked",
                "Paper broker reconciliation has unresolved local/broker mismatch evidence.",
                component="paper_broker_reconciliation",
            )
        )
    elif broker_status not in {"clean", "warning"}:
        blockers.append(
            _issue(
                "paper_broker_reconciliation_missing",
                f"Paper broker reconciliation status is {broker_status}; a clean recent reconciliation is required.",
                component="paper_broker_reconciliation",
            )
        )

    if state_control_state == "halt" or _coerce_bool(runtime.get("state_control_halt_active"), False):
        blockers.append(
            _issue(
                "state_control_halt",
                "State control is halted or has an active halt lock.",
                component="state_control",
            )
        )
    if shadow_status != "pass":
        blockers.append(
            _issue(
                "shadow_validation_failed" if shadow_status not in {"missing", "not_run"} else "shadow_validation_missing",
                f"State-control shadow validation status is {shadow_status}; passing shadow validation is required.",
                component="state_control_shadow_validation",
            )
        )
    elif int(shadow_report.get("failed_count") or 0) > 0:
        blockers.append(
            _issue(
                "shadow_validation_failed",
                "State-control shadow validation has at least one failed scenario.",
                component="state_control_shadow_validation",
            )
        )

    live_credentials = _live_credentials_present()
    live_enabled = bool(settings.alpaca_live_trading_enabled)
    live_route = str(live_settings.get("execution_intent") or "").strip().lower()
    rollout_allows_live = bool(rollout.get("allows_live_rollout"))
    if not live_credentials:
        blockers.append(
            _issue(
                "live_broker_credentials_missing",
                "Live Alpaca credentials are not configured for the live pilot route.",
                component="broker_live_gate",
            )
        )
    if not live_enabled:
        blockers.append(
            _issue(
                "live_broker_disabled",
                "Live broker trading is disabled in server configuration.",
                component="broker_live_gate",
            )
        )
    if live_route != "broker_live":
        blockers.append(
            _issue(
                "live_profile_route_not_live",
                "The personal live profile is not configured for broker_live routing.",
                component="broker_live_gate",
            )
        )
    if not rollout_allows_live:
        blockers.append(
            _issue(
                "broker_live_gate_locked",
                "Existing broker-live rollout readiness gate is locked.",
                component="broker_live_gate",
            )
        )

    paper_kill = _coerce_bool(paper_settings.get("kill_switch"), False)
    live_kill = _coerce_bool(live_settings.get("kill_switch"), False)
    if paper_kill or live_kill:
        blockers.append(
            _issue(
                "safety_lock_active",
                "A paper or live profile kill switch is active. Readiness review cannot clear safety locks.",
                component="safety_lock",
            )
        )

    evidence = {
        "paper_canary": _evidence_summary(
            now=now,
            report=paper_canary_report,
            timestamp_keys=("evaluated_at", "last_run_at"),
        ),
        "lifecycle_canary": _evidence_summary(
            now=now,
            report=lifecycle_report,
            timestamp_keys=("evaluated_at", "last_run_at"),
        ),
        "paper_broker_reconciliation": _evidence_summary(
            now=now,
            report=broker_report,
            timestamp_keys=("checked_at", "evaluated_at", "last_run_at"),
        ),
        "state_control": _evidence_summary(
            now=now,
            report=state_control_report,
            timestamp_keys=("evaluated_at", "last_review_at"),
        ),
        "shadow_validation": _evidence_summary(
            now=now,
            report=shadow_report,
            timestamp_keys=("evaluated_at", "last_run_at"),
        ),
    }
    for key, item in evidence.items():
        if item.get("status") in {"missing", "not_run"}:
            continue
        if item.get("stale"):
            warnings.append(
                _issue(
                    f"{key}_stale",
                    f"{key.replace('_', ' ').title()} evidence is stale or missing a timestamp.",
                    component=key,
                    severity="warning",
                )
            )

    paper_note_coverage = dict(paper_canary_report.get("note_coverage") or {})
    lifecycle_note_coverage = dict(lifecycle_report.get("note_coverage") or {})
    if paper_note_coverage and float(paper_note_coverage.get("ratio") or 0.0) < 1.0:
        warnings.append(
            _issue(
                "paper_note_coverage_incomplete",
                "Paper canary note coverage is incomplete.",
                component="notes",
                severity="warning",
            )
        )
    if lifecycle_note_coverage and float(lifecycle_note_coverage.get("ratio") or 0.0) < 1.0:
        warnings.append(
            _issue(
                "lifecycle_note_coverage_incomplete",
                "Lifecycle canary note coverage is incomplete.",
                component="notes",
                severity="warning",
            )
        )

    if not _coerce_bool(live_settings.get("enabled"), False):
        required_actions.append(
            {
                "key": "operator_enable_live_profile",
                "detail": "After approval, an operator must explicitly enable the personal live profile.",
            }
        )
    if not _coerce_bool(live_settings.get("armed"), False):
        required_actions.append(
            {
                "key": "operator_arm_live_profile",
                "detail": "After approval, an operator must explicitly arm the personal live profile.",
            }
        )
    required_actions.append(
        {
            "key": "operator_approval_required",
            "detail": "Live pilot approval must be granted manually; this review only reports readiness.",
        }
    )

    broker_live_gate_status = "open" if live_credentials and live_enabled and rollout_allows_live else "locked"
    safety_lock_status = "locked" if paper_kill or live_kill or state_control_state == "halt" else "clear"
    status = "blocked" if blockers else "warning" if warnings else "ready_to_request_approval"
    label = {
        "ready_to_request_approval": "Ready to request live pilot approval",
        "warning": "Warnings before live pilot approval",
        "blocked": "Blocked",
    }.get(status, status.replace("_", " ").title())

    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": LIVE_PILOT_PERSONAL_PAPER_PROFILE,
            "live_profile_key": LIVE_PILOT_PERSONAL_LIVE_PROFILE,
            "tenant_id": getattr(tenant, "id", None),
            "session_day": session_day,
            "evaluated_at": _serialize_datetime(now),
            "paper_evidence_status": paper_canary_status,
            "lifecycle_canary_status": lifecycle_status,
            "paper_broker_reconciliation_status": broker_status,
            "state_control_status": state_control_state,
            "shadow_validation_status": shadow_status,
            "broker_live_gate_status": broker_live_gate_status,
            "safety_lock_status": safety_lock_status,
            "required_operator_actions": required_actions,
            "blockers": blockers,
            "warnings": warnings,
            "manual_action_required": bool(blockers or required_actions),
            "paper_evidence": {
                "paper_canary": {
                    **evidence["paper_canary"],
                    "clean_session_count": paper_canary_report.get("clean_session_count"),
                    "required_clean_sessions": paper_canary_report.get("required_clean_sessions"),
                    "blocker_count": len(paper_canary_report.get("blockers") or []),
                },
                "lifecycle_canary": {
                    **evidence["lifecycle_canary"],
                    "clean_session_count": lifecycle_report.get("clean_session_count"),
                    "required_clean_sessions": lifecycle_report.get("required_clean_sessions"),
                    "blocker_count": len(lifecycle_report.get("blockers") or []),
                },
                "paper_broker_reconciliation": {
                    **evidence["paper_broker_reconciliation"],
                    "blocker_count": len(broker_report.get("blockers") or []),
                    "matched_count": broker_report.get("matched_count"),
                },
                "state_control": {
                    **evidence["state_control"],
                    "state": state_control_state,
                    "score": state_control_report.get("score"),
                },
                "shadow_validation": {
                    **evidence["shadow_validation"],
                    "scenario_count": shadow_report.get("scenario_count"),
                    "failed_count": shadow_report.get("failed_count"),
                },
            },
            "live_route_config": {
                "profile_key": LIVE_PILOT_PERSONAL_LIVE_PROFILE,
                "execution_intent": live_route or None,
                "credentials_configured": live_credentials,
                "server_live_trading_enabled": live_enabled,
                "rollout_allows_live": rollout_allows_live,
                "enabled": _coerce_bool(live_settings.get("enabled"), False),
                "armed": _coerce_bool(live_settings.get("armed"), False),
                "kill_switch": live_kill,
            },
            "safety_locks": {
                "paper_kill_switch": paper_kill,
                "live_kill_switch": live_kill,
                "state_control_halt_active": _coerce_bool(runtime.get("state_control_halt_active"), False),
            },
            "rollout_readiness": serialize_value(rollout),
        }
    )


def _find_existing_readiness_note_id(session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=LIVE_PILOT_READINESS_NOTE_OWNER,
            limit=LIVE_PILOT_READINESS_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "live-pilot-readiness",
        "paper-canary",
        "state-control",
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _build_note_body(*, tenant: Tenant, report: dict[str, Any]) -> str:
    live_config = dict(report.get("live_route_config") or {})
    paper = dict(report.get("paper_evidence") or {})
    lines = [
        f"Automation live pilot readiness review for {tenant.name or tenant.slug}",
        f"Session day: {report.get('session_day')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Paper evidence: {str(report.get('paper_evidence_status') or 'missing').upper()}",
        f"Lifecycle evidence: {str(report.get('lifecycle_canary_status') or 'missing').upper()}",
        f"Paper broker reconciliation: {str(report.get('paper_broker_reconciliation_status') or 'missing').upper()}",
        f"State control: {str(report.get('state_control_status') or 'unknown').replace('_', ' ')}",
        f"Shadow validation: {str(report.get('shadow_validation_status') or 'missing').upper()}",
        f"Broker-live gate: {str(report.get('broker_live_gate_status') or 'unknown').replace('_', ' ')}",
        f"Safety locks: {str(report.get('safety_lock_status') or 'unknown').replace('_', ' ')}",
        (
            "Live route: "
            f"intent {live_config.get('execution_intent') or '--'} | "
            f"credentials {'configured' if live_config.get('credentials_configured') else 'missing'} | "
            f"server live {'enabled' if live_config.get('server_live_trading_enabled') else 'disabled'} | "
            f"rollout {'open' if live_config.get('rollout_allows_live') else 'locked'}"
        ),
        "",
        "This readiness gate is advisory. It does not place live orders, cancel live orders, enable live trading, arm automation, clear kill switches, clear broker-live gates, or tune baseline settings.",
        "",
        "Required operator actions",
    ]
    actions = [item for item in list(report.get("required_operator_actions") or []) if isinstance(item, dict)]
    if actions:
        lines.extend(f"- {item.get('key')}: {item.get('detail')}" for item in actions[:12])
    else:
        lines.append("- None.")
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    lines.extend(["", "Blockers"])
    if blockers:
        lines.extend(f"- {item.get('component')}: {item.get('key')}. {item.get('detail')}" for item in blockers[:12])
    else:
        lines.append("- None.")
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    lines.extend(["", "Warnings"])
    if warnings:
        lines.extend(f"- {item.get('component')}: {item.get('key')}. {item.get('detail')}" for item in warnings[:12])
    else:
        lines.append("- None.")
    lines.extend(["", "Evidence"])
    for key in (
        "paper_canary",
        "lifecycle_canary",
        "paper_broker_reconciliation",
        "state_control",
        "shadow_validation",
    ):
        item = dict(paper.get(key) or {})
        lines.append(
            f"- {key.replace('_', ' ')}: {str(item.get('status') or item.get('state') or 'missing').upper()} | "
            f"at {item.get('evaluated_at') or '--'} | note {item.get('note_id') or '--'}"
        )
    return "\n".join(lines).strip()


def _sync_readiness_note(*, tenant: Tenant, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "live-pilot-readiness",
        "paper-canary",
        "state-control",
        f"session-{session_day}",
    ]
    title = f"Automation live pilot readiness - {session_day}"
    body = _build_note_body(tenant=tenant, report=report)
    note_id = str(report.get("related_note_id") or report.get("note_id") or "").strip() or _find_existing_readiness_note_id(
        session_day
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": LIVE_PILOT_READINESS_NOTE_OWNER,
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


def run_live_pilot_readiness_review(
    db: Session | None,
    *,
    tenant: Tenant,
    paper_state: dict[str, Any],
    live_state: dict[str, Any] | None,
    rollout_readiness: dict[str, Any] | None = None,
    actor: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    session_day = _session_day_for(now)
    paper_settings_before = deepcopy(dict(paper_state.get("settings") or {}))
    live_settings_before = deepcopy(dict((live_state or {}).get("settings") or {}))
    protected_keys = ("enabled", "armed", "kill_switch", "execution_intent")
    protected_before = {
        "paper": {key: paper_settings_before.get(key) for key in protected_keys},
        "live": {key: live_settings_before.get(key) for key in protected_keys},
    }
    report = _aggregate_report(
        tenant=tenant,
        paper_state=paper_state,
        live_state=live_state,
        rollout_readiness=rollout_readiness,
        now=now,
        session_day=session_day,
    )
    note_id = _sync_readiness_note(tenant=tenant, report=report)
    if note_id:
        report["note_id"] = note_id
        report["related_note_id"] = note_id
    protected_after = {
        "paper": {key: dict(paper_state.get("settings") or {}).get(key) for key in protected_keys},
        "live": {key: dict((live_state or {}).get("settings") or {}).get(key) for key in protected_keys},
    }
    report["baseline_settings_mutated"] = protected_before != protected_after

    runtime = paper_state.setdefault("runtime", {})
    summary_keys = {
        "status",
        "label",
        "profile_key",
        "live_profile_key",
        "session_day",
        "evaluated_at",
        "paper_evidence_status",
        "lifecycle_canary_status",
        "paper_broker_reconciliation_status",
        "state_control_status",
        "shadow_validation_status",
        "broker_live_gate_status",
        "safety_lock_status",
        "required_operator_actions",
        "blockers",
        "warnings",
        "manual_action_required",
        "paper_evidence",
        "live_route_config",
        "safety_locks",
        "rollout_readiness",
        "note_id",
        "related_note_id",
        "baseline_settings_mutated",
    }
    summary = {key: report.get(key) for key in summary_keys if key in report}
    runtime["live_pilot_readiness_last_report"] = serialize_value(summary)
    runtime["live_pilot_readiness_last_note_id"] = note_id
    runtime["live_pilot_readiness_note_session_day"] = session_day
    runtime["live_pilot_readiness_last_run_at"] = report.get("evaluated_at")
    runtime["live_pilot_readiness_last_error"] = None
    history = list(runtime.get("live_pilot_readiness_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": session_day,
            "status": report.get("status"),
            "paper_evidence_status": report.get("paper_evidence_status"),
            "lifecycle_canary_status": report.get("lifecycle_canary_status"),
            "broker_live_gate_status": report.get("broker_live_gate_status"),
            "safety_lock_status": report.get("safety_lock_status"),
            "blocker_count": len(report.get("blockers") or []),
            "warning_count": len(report.get("warnings") or []),
            "note_id": note_id,
        },
    )
    runtime["live_pilot_readiness_history"] = serialize_value(history[:LIVE_PILOT_READINESS_HISTORY_LIMIT])
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.live_pilot_readiness_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": LIVE_PILOT_PERSONAL_PAPER_PROFILE,
                "live_profile_key": LIVE_PILOT_PERSONAL_LIVE_PROFILE,
                "session_day": session_day,
                "status": report.get("status"),
                "blocker_count": len(report.get("blockers") or []),
                "warning_count": len(report.get("warnings") or []),
                "note_id": note_id,
                "baseline_settings_mutated": report.get("baseline_settings_mutated"),
            },
        )
    return serialize_value(report)
