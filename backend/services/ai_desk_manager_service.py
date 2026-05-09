from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.saas import AsyncJob, DomainEventLog, PortfolioTargetExecutionRun, PortfolioTargetRun, Tenant, TradeApprovalIntent
from backend.schemas import (
    AiAutonomousCycleRequest,
    AiDeskControlRequest,
    AiDeskPolicyUpdateRequest,
    AiLiveIntentRequest,
    AiPaperExecutionRequest,
    AiTradePlanRequest,
    OpenTradeRequest,
    PortfolioTargetExecutionRequest,
)
from backend.services.audit_service import record_audit_event
from backend.services.brokerage_account_service import list_linked_brokerage_accounts
from backend.services.exceptions import ValidationError
from backend.services.portfolio_target_execution.service import (
    execute_portfolio_targets,
    get_latest_portfolio_target_execution,
    sync_portfolio_target_execution,
)
from backend.services.permissions import resolve_user_permissions
from backend.services.strategy_engine.events import record_domain_event, serialize_domain_event
from backend.services.strategy_engine.service import (
    build_allocator_snapshot,
    get_latest_portfolio_targets,
    get_risk_snapshot,
    list_strategy_desks,
    run_strategy_desk,
)
from backend.services.trade_service import (
    create_trade_intent_from_request,
    list_trade_intents,
    preview_trade_from_request,
)
from backend.services.tenant_service import _resolve_tenant_for_current_user, _resolve_user_for_current_user


STALE_RUN_AFTER = timedelta(hours=24)
PAPER_EVIDENCE_MAX_AGE = timedelta(days=14)
AI_DESK_POLICY_KEY = "ai_desk_policy"
AI_DESK_JOB_TYPE = "ai_desk_autonomous_cycle"
AI_DESK_LATEST_CYCLE_EVENTS = {
    "ai_desk.cycle.blocked",
    "ai_desk.cycle.completed",
    "ai_desk.cycle.failed",
}

DEFAULT_AI_DESK_POLICY: dict[str, Any] = {
    "version": "v1",
    "enabled": False,
    "armed": False,
    "kill_switch": False,
    "autonomy_boundary": "paper_plus_live_intent",
    "allowed_desks": ["macro_trend", "stat_arb"],
    "allowed_instrument_types": ["equity"],
    "allowed_sides": ["buy"],
    "allowed_order_types": ["limit"],
    "allow_paper_execution": True,
    "allow_live_intents": True,
    "allow_live_submit": False,
    "equities_only": True,
    "long_only": True,
    "limit_orders_only": True,
    "regular_hours_only": True,
    "max_risk_percent": 0.5,
    "max_notional_per_trade": None,
    "stale_run_minutes": 1440,
    "cycle_interval_minutes": 15,
    "updated_at": None,
    "updated_by": None,
}

AI_AGENT_REGISTRY: tuple[dict[str, str], ...] = (
    {"key": "market_data_sentinel", "label": "Market Data Sentinel"},
    {"key": "strategy_desk_runner", "label": "Strategy Desk Runner"},
    {"key": "signal_validator", "label": "Signal Validator"},
    {"key": "risk_allocator", "label": "Risk Allocator"},
    {"key": "trade_planner", "label": "Trade Planner"},
    {"key": "paper_execution_manager", "label": "Paper Execution Manager"},
    {"key": "reconciliation_monitor", "label": "Reconciliation Monitor"},
    {"key": "live_approval_assistant", "label": "Live Approval Assistant"},
    {"key": "audit_supervisor", "label": "Audit Supervisor"},
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _policy_digest(policy: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(policy).encode("utf-8")).hexdigest()[:24]


def _normalize_unique_text_list(values: Any) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        cleaned = str(value or "").strip().lower()
        if cleaned and cleaned not in seen:
            normalized.append(cleaned)
            seen.add(cleaned)
    return normalized


def _normalize_ai_desk_policy(raw_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = dict(DEFAULT_AI_DESK_POLICY)
    policy.update(dict(raw_policy or {}))
    allowed_desks = _normalize_unique_text_list(policy.get("allowed_desks"))
    if not allowed_desks:
        allowed_desks = list(DEFAULT_AI_DESK_POLICY["allowed_desks"])
    policy["allowed_desks"] = allowed_desks
    policy["allowed_instrument_types"] = ["equity"]
    policy["allowed_sides"] = ["buy"]
    policy["allowed_order_types"] = ["limit"]
    policy["autonomy_boundary"] = "paper_plus_live_intent"
    policy["allow_live_submit"] = False
    policy["equities_only"] = True
    policy["long_only"] = True
    policy["limit_orders_only"] = True
    policy["regular_hours_only"] = True
    policy["enabled"] = bool(policy.get("enabled"))
    policy["armed"] = bool(policy.get("armed"))
    policy["kill_switch"] = bool(policy.get("kill_switch"))
    policy["allow_paper_execution"] = bool(policy.get("allow_paper_execution", True))
    policy["allow_live_intents"] = bool(policy.get("allow_live_intents", True))
    try:
        policy["max_risk_percent"] = max(0.01, min(float(policy.get("max_risk_percent") or 0.5), 5.0))
    except (TypeError, ValueError):
        policy["max_risk_percent"] = float(DEFAULT_AI_DESK_POLICY["max_risk_percent"])
    max_notional = policy.get("max_notional_per_trade")
    try:
        policy["max_notional_per_trade"] = float(max_notional) if max_notional not in (None, "", 0) else None
    except (TypeError, ValueError):
        policy["max_notional_per_trade"] = None
    try:
        policy["stale_run_minutes"] = max(5, min(int(policy.get("stale_run_minutes") or 1440), 10080))
    except (TypeError, ValueError):
        policy["stale_run_minutes"] = int(DEFAULT_AI_DESK_POLICY["stale_run_minutes"])
    try:
        policy["cycle_interval_minutes"] = max(1, min(int(policy.get("cycle_interval_minutes") or 15), 1440))
    except (TypeError, ValueError):
        policy["cycle_interval_minutes"] = int(DEFAULT_AI_DESK_POLICY["cycle_interval_minutes"])
    return policy


def _tenant_policy(tenant: Tenant) -> dict[str, Any]:
    metadata = dict(tenant.metadata_json or {})
    return _normalize_ai_desk_policy(metadata.get(AI_DESK_POLICY_KEY) if isinstance(metadata.get(AI_DESK_POLICY_KEY), dict) else {})


def _write_tenant_policy(tenant: Tenant, policy: dict[str, Any]) -> None:
    metadata = dict(tenant.metadata_json or {})
    metadata[AI_DESK_POLICY_KEY] = _normalize_ai_desk_policy(policy)
    tenant.metadata_json = metadata
    flag_modified(tenant, "metadata_json")


def _system_current_user_for_tenant(tenant: Tenant) -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id=tenant.id,
        auth_subject=f"ai-desk-manager:{tenant.id}",
        email="ai-desk-manager@system.local",
        name="AI Desk Manager",
        provider="system",
        role="owner",
        platform_role="admin",
        mode="system",
        permissions=resolve_user_permissions(
            membership_role="owner",
            platform_role="admin",
            api_token_scopes=(),
            mode="system",
        ),
    )


def _agent_status(
    key: str,
    status: str,
    detail: str | None = None,
    *,
    blockers: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    label = next((item["label"] for item in AI_AGENT_REGISTRY if item["key"] == key), key.replace("_", " ").title())
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "blockers": list(dict.fromkeys(blockers or [])),
        "evidence": dict(evidence or {}),
    }


def _base_agent_statuses(*, status: str = "idle", detail: str | None = None) -> list[dict[str, Any]]:
    return [_agent_status(item["key"], status, detail) for item in AI_AGENT_REGISTRY]


def _upsert_agent_status(agents: list[dict[str, Any]], update: dict[str, Any]) -> None:
    for index, agent in enumerate(agents):
        if agent.get("key") == update.get("key"):
            agents[index] = update
            return
    agents.append(update)


def _make_policy_decision(
    policy: dict[str, Any],
    action: str,
    *,
    evidence: dict[str, Any] | None = None,
    require_enabled: bool = True,
    require_armed: bool = False,
    additional_blockers: list[str] | None = None,
) -> dict[str, Any]:
    blockers: list[str] = list(additional_blockers or [])
    if require_enabled and not bool(policy.get("enabled")):
        blockers.append("AI desk autonomy is disabled in policy.")
    if require_armed and not bool(policy.get("armed")):
        blockers.append("AI desk autonomy is not armed.")
    if bool(policy.get("kill_switch")):
        blockers.append("AI desk kill switch is active.")
    if bool(policy.get("allow_live_submit")):
        blockers.append("Policy invariant failed: autonomous live submission must remain disabled.")
    normalized_action = str(action or "").strip().lower()
    if normalized_action.endswith("execute_paper") and not bool(policy.get("allow_paper_execution")):
        blockers.append("Policy blocks autonomous paper execution.")
    if normalized_action.endswith("create_live_intent") and not bool(policy.get("allow_live_intents")):
        blockers.append("Policy blocks autonomous live intent creation.")
    if normalized_action.endswith("submit_live_order"):
        blockers.append("Autonomous live order submission is not supported.")
    return {
        "action": normalized_action or "ai_desk.unknown",
        "allowed": not blockers,
        "policy_version": str(policy.get("version") or "v1"),
        "policy_digest": _policy_digest(policy),
        "blockers": list(dict.fromkeys(blockers)),
        "evidence": dict(evidence or {}),
    }


def _record_policy_decision(
    db: Session,
    *,
    tenant: Tenant,
    current_user: Any,
    decision: dict[str, Any],
) -> None:
    actor = _resolve_user_for_current_user(db, current_user)
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="ai_desk.policy_decision",
        aggregate_type="ai_desk_policy",
        aggregate_id=decision.get("policy_digest"),
        payload=dict(decision),
    )
    record_audit_event(
        db,
        event_type="ai_desk.policy_decision",
        tenant=tenant,
        user=actor,
        payload=dict(decision),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, "", "nan"):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _target_symbol(target: dict[str, Any] | None) -> str:
    return str((target or {}).get("symbol") or "").strip().upper()


def _target_desk_keys(target: dict[str, Any] | None) -> set[str]:
    keys: set[str] = set()
    for contribution in list((target or {}).get("desk_contributions") or []):
        desk_key = str(contribution.get("desk_key") or "").strip()
        if desk_key:
            keys.add(desk_key)
    fallback = str((target or {}).get("strategy_desk_key") or "").strip()
    if fallback:
        keys.add(fallback)
    return keys


def _target_is_short(target: dict[str, Any] | None) -> bool:
    if target is None:
        return False
    direction_values = {
        str(value or "").strip().lower()
        for value in list(target.get("directions") or [])
        if str(value or "").strip()
    }
    for contribution in list(target.get("desk_contributions") or []):
        direction = str(contribution.get("direction") or "").strip().lower()
        if direction:
            direction_values.add(direction)
    side = str((target.get("order_plan") or {}).get("side") or "").strip().lower()
    if side:
        direction_values.add(side)
    return _safe_float(target.get("target_weight"), 0.0) < 0 or bool(
        direction_values & {"short", "sell", "sell_short"}
    )


def _sorted_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(actions, key=lambda item: (int(item.get("priority") or 50), str(item.get("key") or "")))


def _classify_desk(item: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    latest_run = item.get("latest_run") or {}
    latest_publication = item.get("latest_publication") or {}
    blockers: list[str] = []
    warnings: list[str] = []

    if not bool(item.get("enabled")):
        blockers.append("Desk is disabled.")
    run_status = str(latest_run.get("status") or "").strip().lower()
    if run_status in {"rejected", "blocked", "failed", "error"}:
        blockers.append(str(latest_run.get("error_message") or latest_run.get("validation", {}).get("detail") or "Latest run is blocked."))
    validation = latest_run.get("validation") if isinstance(latest_run.get("validation"), dict) else {}
    if validation and not bool(validation.get("allowed", True)):
        blockers.append(str(validation.get("detail") or "Latest desk validation rejected the run."))

    latest_at = _parse_datetime(latest_run.get("completed_at") or latest_run.get("created_at"))
    target_count = len(list(latest_publication.get("targets") or []))
    if not latest_run:
        warnings.append("No completed desk run has been recorded.")
    elif latest_at and now - latest_at > STALE_RUN_AFTER:
        warnings.append("Latest desk run is stale.")

    if blockers:
        state = "blocked"
    elif not latest_run or (latest_at and now - latest_at > STALE_RUN_AFTER):
        state = "stale"
    elif bool(item.get("paper_trading_enabled")) and str(item.get("trading_mode") or "").lower() == "paper" and target_count:
        state = "ready"
    else:
        state = "watch"

    return {
        "desk_key": item.get("desk_key"),
        "name": item.get("name"),
        "state": state,
        "enabled": bool(item.get("enabled")),
        "paper_trading_enabled": bool(item.get("paper_trading_enabled")),
        "trading_mode": item.get("trading_mode"),
        "lifecycle_stage": item.get("lifecycle_stage"),
        "latest_run_id": latest_run.get("id"),
        "latest_run_status": latest_run.get("status"),
        "latest_run_at": latest_run.get("completed_at") or latest_run.get("created_at"),
        "target_count": target_count,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
    }


def _build_conflicts(
    *,
    desk_states: list[dict[str, Any]],
    latest_targets: dict[str, Any],
    risk_snapshot: dict[str, Any],
    latest_execution: dict[str, Any],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    target_symbols = [_target_symbol(target) for target in list(latest_targets.get("targets") or [])]
    duplicate_symbols = sorted(symbol for symbol, count in Counter(target_symbols).items() if symbol and count > 1)
    if duplicate_symbols:
        conflicts.append(
            {
                "key": "duplicate_target_symbols",
                "severity": "warning",
                "detail": "Multiple target rows reference the same symbol.",
                "symbols": duplicate_symbols,
            }
        )
    if not bool(risk_snapshot.get("allowed", True)):
        conflicts.append(
            {
                "key": "risk_snapshot_blocked",
                "severity": "negative",
                "detail": "Portfolio risk snapshot is currently blocking allocation.",
            }
        )
    blocked_desks = [item for item in desk_states if item.get("state") == "blocked"]
    if blocked_desks:
        conflicts.append(
            {
                "key": "blocked_desks",
                "severity": "negative",
                "detail": "One or more desks cannot contribute executable targets.",
                "desk_keys": [item.get("desk_key") for item in blocked_desks],
            }
        )
    if int(latest_execution.get("rejected_count") or 0) > 0:
        conflicts.append(
            {
                "key": "paper_rejections",
                "severity": "warning",
                "detail": "The latest paper execution lifecycle contains rejected orders.",
            }
        )
    return conflicts


def _has_recent_paper_evidence(latest_execution: dict[str, Any]) -> tuple[bool, str]:
    if not latest_execution or not latest_execution.get("latest_execution_run_id"):
        return False, "No paper execution lifecycle has been recorded."
    filled_count = int(latest_execution.get("filled_count") or 0)
    rejected_count = int(latest_execution.get("rejected_count") or 0)
    status = str(latest_execution.get("status") or "").strip().lower()
    if filled_count <= 0:
        return False, "Paper execution needs at least one filled order before live intent creation."
    if rejected_count > 0 or status in {"blocked", "rejected", "completed_with_errors", "reconciliation_warning"}:
        return False, "Paper execution lifecycle is not clean enough for live intent creation."
    timestamp = _parse_datetime(
        latest_execution.get("last_sync_at")
        or latest_execution.get("completed_at")
        or latest_execution.get("created_at")
    )
    if timestamp and _utc_now() - timestamp > PAPER_EVIDENCE_MAX_AGE:
        return False, "Paper execution evidence is stale."
    return True, "Recent clean paper execution evidence is available."


def _build_live_gate(
    *,
    linked_accounts: dict[str, Any],
    latest_execution: dict[str, Any],
    risk_snapshot: dict[str, Any],
    linked_account_id: str | None = None,
    frontend_confirmation: bool = False,
) -> dict[str, Any]:
    items = list(linked_accounts.get("items") or [])
    live_accounts = [
        item
        for item in items
        if str(item.get("account_environment") or "").strip().lower() == "live"
    ]
    connected_live_accounts = [
        item
        for item in live_accounts
        if str(item.get("connection_status") or "").strip().lower() == "connected"
        and str(item.get("token_health") or "").strip().lower() in {"healthy", "unknown"}
        and bool(item.get("token_present"))
    ]
    selected_account = None
    if linked_account_id:
        selected_account = next((item for item in connected_live_accounts if item.get("id") == linked_account_id), None)
    if selected_account is None and connected_live_accounts:
        selected_account = connected_live_accounts[0]

    paper_ok, paper_detail = _has_recent_paper_evidence(latest_execution)
    checks = [
        {
            "key": "live_adapter_configured",
            "passed": str(settings.execution_adapter or "").strip().lower() == "alpaca_live",
            "detail": "EXECUTION_ADAPTER is set to alpaca_live.",
        },
        {
            "key": "explicit_live_flag",
            "passed": bool(settings.alpaca_live_trading_enabled),
            "detail": "ALPACA_LIVE_TRADING_ENABLED is true.",
        },
        {
            "key": "connected_live_account",
            "passed": selected_account is not None,
            "detail": "A connected live brokerage account with an available token is selected.",
        },
        {
            "key": "risk_snapshot",
            "passed": bool(risk_snapshot.get("allowed", True)),
            "detail": "Current server-side risk snapshot allows target execution.",
        },
        {
            "key": "paper_evidence",
            "passed": paper_ok,
            "detail": paper_detail,
        },
        {
            "key": "frontend_confirmation",
            "passed": bool(frontend_confirmation),
            "detail": "The operator confirmed this is a supervised live intent.",
        },
    ]
    blockers = [check["detail"] for check in checks if not check["passed"]]
    return {
        "allowed": not blockers,
        "status": "ready" if not blockers else "blocked",
        "default_linked_account_id": selected_account.get("id") if selected_account else None,
        "connected_live_account_count": len(connected_live_accounts),
        "live_account_count": len(live_accounts),
        "checks": checks,
        "blockers": blockers,
    }


def _build_next_actions(
    *,
    desk_states: list[dict[str, Any]],
    latest_targets: dict[str, Any],
    risk_snapshot: dict[str, Any],
    latest_execution: dict[str, Any],
    live_gate: dict[str, Any],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    stale_desk = next((item for item in desk_states if item.get("state") == "stale"), None)
    if stale_desk:
        actions.append(
            {
                "key": "run_stale_desk",
                "label": f"Run {stale_desk.get('name') or stale_desk.get('desk_key')}",
                "detail": "Refresh the stale desk before staging new trades.",
                "priority": 10,
                "stage": "command_center",
                "tone": "warning",
                "desk_key": stale_desk.get("desk_key"),
                "payload": {"desk_key": stale_desk.get("desk_key")},
            }
        )
    if latest_targets.get("targets") and bool(risk_snapshot.get("allowed", True)):
        first_target = list(latest_targets.get("targets") or [])[0]
        actions.append(
            {
                "key": "preview_trade_plan",
                "label": f"Preview {first_target.get('symbol')} trade plan",
                "detail": "Build a long-only equity ticket from the latest target.",
                "priority": 20,
                "stage": "trade_planner",
                "tone": "positive",
                "desk_key": next(iter(_target_desk_keys(first_target)), None),
                "payload": {"target_symbol": first_target.get("symbol")},
            }
        )
        actions.append(
            {
                "key": "execute_paper_basket",
                "label": "Execute paper basket",
                "detail": "Route accepted portfolio targets through broker-paper execution.",
                "priority": 30,
                "stage": "paper_execution",
                "tone": "warning",
                "payload": {"portfolio_target_run_id": latest_targets.get("latest_run_id")},
            }
        )
    if latest_execution.get("latest_execution_run_id"):
        actions.append(
            {
                "key": "sync_paper_lifecycle",
                "label": "Sync paper lifecycle",
                "detail": "Refresh broker-paper order status and reconciliation.",
                "priority": 40,
                "stage": "paper_execution",
                "tone": "neutral",
                "payload": {"execution_run_id": latest_execution.get("latest_execution_run_id")},
            }
        )
    if live_gate.get("allowed"):
        actions.append(
            {
                "key": "create_supervised_live_intent",
                "label": "Create supervised live intent",
                "detail": "Create an approval-gated live intent without submitting an order.",
                "priority": 50,
                "stage": "supervised_live",
                "tone": "warning",
                "payload": {"linked_account_id": live_gate.get("default_linked_account_id")},
            }
        )
    else:
        actions.append(
            {
                "key": "review_live_gate",
                "label": "Review live gate blockers",
                "detail": "Live intent creation remains blocked until every gate passes.",
                "priority": 60,
                "stage": "supervised_live",
                "tone": "negative",
                "payload": {"blockers": list(live_gate.get("blockers") or [])},
            }
        )
    return _sorted_actions(actions)


def get_ai_desk_policy(db: Session, *, current_user: Any) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    policy = _tenant_policy(tenant)
    return {
        "manifest": policy,
        "policy_digest": _policy_digest(policy),
        "enabled": bool(policy.get("enabled")),
        "armed": bool(policy.get("armed")),
        "kill_switch": bool(policy.get("kill_switch")),
        "autonomy_boundary": policy.get("autonomy_boundary"),
    }


def update_ai_desk_policy(db: Session, *, current_user: Any, request: AiDeskPolicyUpdateRequest) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    updates = request.model_dump(exclude_unset=True)
    if updates.get("allow_live_submit"):
        raise ValidationError("Autonomous live order submission is not supported.")
    policy = _tenant_policy(tenant)
    policy.update(dict(updates))
    policy["allow_live_submit"] = False
    policy["updated_at"] = _iso(_utc_now())
    policy["updated_by"] = getattr(current_user, "email", None) or getattr(current_user, "auth_subject", None)
    normalized = _normalize_ai_desk_policy(policy)
    _write_tenant_policy(tenant, normalized)
    digest = _policy_digest(normalized)
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="ai_desk.policy_updated",
        aggregate_type="ai_desk_policy",
        aggregate_id=digest,
        payload={"updates": updates, "policy_digest": digest, "policy": normalized},
    )
    record_audit_event(
        db,
        event_type="ai_desk.policy_updated",
        tenant=tenant,
        user=actor,
        payload={"updates": updates, "policy_digest": digest},
    )
    db.commit()
    return get_ai_desk_policy(db, current_user=current_user)


def run_ai_desk_control(db: Session, *, current_user: Any, request: AiDeskControlRequest) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    policy = _tenant_policy(tenant)
    action = str(request.action or "").strip().lower()
    if action == "enable":
        policy["enabled"] = True
    elif action == "disable":
        policy["enabled"] = False
        policy["armed"] = False
    elif action == "arm":
        policy["enabled"] = True
        policy["armed"] = True
        policy["kill_switch"] = False
    elif action == "disarm":
        policy["armed"] = False
    elif action == "kill_switch":
        policy["kill_switch"] = True
        policy["armed"] = False
    elif action == "clear_kill_switch":
        policy["kill_switch"] = False
        policy["armed"] = False
    elif action == "queue_cycle":
        return queue_ai_autonomous_cycle(
            db,
            current_user=current_user,
            request=AiAutonomousCycleRequest(trigger="manual", enqueue=True),
        )
    else:  # pragma: no cover - schema prevents this
        raise ValidationError("Unknown AI desk control action.")

    policy["updated_at"] = _iso(_utc_now())
    policy["updated_by"] = getattr(current_user, "email", None) or getattr(current_user, "auth_subject", None)
    normalized = _normalize_ai_desk_policy(policy)
    _write_tenant_policy(tenant, normalized)
    digest = _policy_digest(normalized)
    payload = {
        "action": action,
        "reason": request.reason,
        "policy_digest": digest,
        "enabled": normalized["enabled"],
        "armed": normalized["armed"],
        "kill_switch": normalized["kill_switch"],
    }
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="ai_desk.control_updated",
        aggregate_type="ai_desk_policy",
        aggregate_id=digest,
        payload=payload,
    )
    record_audit_event(
        db,
        event_type="ai_desk.control_updated",
        tenant=tenant,
        user=actor,
        payload=payload,
    )
    db.commit()
    return {"control": payload, "policy": get_ai_desk_policy(db, current_user=current_user)}


def _latest_ai_cycle_event(db: Session, *, tenant_id: str) -> dict[str, Any]:
    row = db.execute(
        select(DomainEventLog)
        .where(DomainEventLog.tenant_id == tenant_id)
        .where(DomainEventLog.event_type.in_(AI_DESK_LATEST_CYCLE_EVENTS))
        .order_by(DomainEventLog.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return serialize_domain_event(row) if row is not None else {}


def _ai_cycle_job_snapshot(db: Session, *, tenant_id: str) -> dict[str, Any]:
    pending = list(
        db.execute(
            select(AsyncJob)
            .where(AsyncJob.tenant_id == tenant_id)
            .where(AsyncJob.job_type == AI_DESK_JOB_TYPE)
            .where(AsyncJob.status.in_(("queued", "retrying", "running")))
            .order_by(AsyncJob.available_at.asc(), AsyncJob.created_at.asc())
        ).scalars()
    )
    latest = db.execute(
        select(AsyncJob)
        .where(AsyncJob.tenant_id == tenant_id)
        .where(AsyncJob.job_type == AI_DESK_JOB_TYPE)
        .order_by(AsyncJob.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return {
        "pending_count": len(pending),
        "next_scheduled_run": _iso(pending[0].available_at) if pending else None,
        "pending_job_id": pending[0].id if pending else None,
        "latest_job": {
            "id": latest.id,
            "status": latest.status,
            "available_at": _iso(latest.available_at),
            "started_at": _iso(latest.started_at),
            "finished_at": _iso(latest.finished_at),
            "error_message": latest.error_message,
            "result": dict(latest.result_json or {}),
        }
        if latest is not None
        else {},
    }


def build_ai_desk_manager_snapshot(db: Session, *, current_user: Any) -> dict[str, Any]:
    now = _utc_now()
    tenant = _resolve_tenant_for_current_user(db, current_user)
    policy = _tenant_policy(tenant)
    policy_digest = _policy_digest(policy)
    desks_payload = list_strategy_desks(db, current_user=current_user)
    allocator_snapshot = build_allocator_snapshot(db, current_user=current_user)
    latest_targets = get_latest_portfolio_targets(db, current_user=current_user)
    risk_snapshot = get_risk_snapshot(db, current_user=current_user)
    latest_execution = get_latest_portfolio_target_execution(db, current_user=current_user)
    pending_intents = list_trade_intents(db=db, current_user=current_user, status_filter="pending_approval")
    linked_accounts = list_linked_brokerage_accounts(db=db, current_user=current_user)

    desk_states = [_classify_desk(item, now=now) for item in list(desks_payload.get("items") or [])]
    conflicts = _build_conflicts(
        desk_states=desk_states,
        latest_targets=latest_targets,
        risk_snapshot=risk_snapshot,
        latest_execution=latest_execution,
    )
    live_gate = _build_live_gate(
        linked_accounts=linked_accounts,
        latest_execution=latest_execution,
        risk_snapshot=risk_snapshot,
    )
    next_actions = _build_next_actions(
        desk_states=desk_states,
        latest_targets=latest_targets,
        risk_snapshot=risk_snapshot,
        latest_execution=latest_execution,
        live_gate=live_gate,
    )
    state_counts = Counter(str(item.get("state") or "watch") for item in desk_states)
    if conflicts and any(item.get("severity") == "negative" for item in conflicts):
        status = "blocked"
    elif state_counts.get("ready"):
        status = "ready"
    elif state_counts.get("stale"):
        status = "stale"
    else:
        status = "watch"
    policy_decision = _make_policy_decision(policy, "ai_desk.autonomous_cycle", require_armed=True)
    job_snapshot = _ai_cycle_job_snapshot(db, tenant_id=tenant.id)
    latest_cycle = _latest_ai_cycle_event(db, tenant_id=tenant.id)
    latest_agents = list((latest_cycle.get("payload") or {}).get("agents") or [])
    active_blockers = list(policy_decision.get("blockers") or [])
    active_blockers.extend(item.get("detail") for item in conflicts if item.get("severity") == "negative" and item.get("detail"))
    active_blockers.extend(list(live_gate.get("blockers") or []))

    return {
        "stage": "ai_desk_manager",
        "status": status,
        "generated_at": _iso(now),
        "command_center": {
            "desk_count": len(desk_states),
            "ready_count": int(state_counts.get("ready") or 0),
            "watch_count": int(state_counts.get("watch") or 0),
            "blocked_count": int(state_counts.get("blocked") or 0),
            "stale_count": int(state_counts.get("stale") or 0),
            "pending_intent_count": int(pending_intents.get("count") or 0),
            "linked_account_count": int(linked_accounts.get("count") or 0),
        },
        "desk_states": desk_states,
        "next_actions": next_actions,
        "trade_planner": {
            "default_instrument_type": "equity",
            "default_side": "buy",
            "default_order_type": "limit",
            "default_risk_percent": 0.5,
            "candidate_count": len(list(latest_targets.get("targets") or [])),
            "latest_targets": latest_targets,
        },
        "paper_execution": {
            "allocator": allocator_snapshot,
            "risk": risk_snapshot,
            "latest_execution": latest_execution,
            "can_execute": bool(latest_targets.get("targets")) and bool(risk_snapshot.get("allowed", True)),
        },
        "live_gate": live_gate,
        "conflicts": conflicts,
        "policy": {
            "manifest": policy,
            "policy_digest": policy_digest,
            "enabled": bool(policy.get("enabled")),
            "armed": bool(policy.get("armed")),
            "kill_switch": bool(policy.get("kill_switch")),
            "autonomy_boundary": policy.get("autonomy_boundary"),
        },
        "policy_digest": policy_digest,
        "autonomy": {
            "enabled": bool(policy.get("enabled")),
            "armed": bool(policy.get("armed")),
            "kill_switch": bool(policy.get("kill_switch")),
            "boundary": policy.get("autonomy_boundary"),
            "job_type": AI_DESK_JOB_TYPE,
            "pending_cycle_count": int(job_snapshot.get("pending_count") or 0),
        },
        "agents": latest_agents or _base_agent_statuses(
            status="blocked" if policy_decision.get("blockers") else "idle",
            detail="Waiting for policy enablement and arming." if policy_decision.get("blockers") else "Ready for the next autonomous cycle.",
        ),
        "latest_cycle": latest_cycle,
        "active_blockers": list(dict.fromkeys(str(item) for item in active_blockers if str(item or "").strip())),
        "next_scheduled_run": job_snapshot.get("next_scheduled_run"),
    }


def _load_portfolio_target_run_payload(
    db: Session,
    *,
    current_user: Any,
    portfolio_target_run_id: str | None,
) -> dict[str, Any]:
    if not portfolio_target_run_id:
        return get_latest_portfolio_targets(db, current_user=current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    row = db.execute(
        select(PortfolioTargetRun).where(
            PortfolioTargetRun.id == portfolio_target_run_id,
            PortfolioTargetRun.tenant_id == tenant.id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise ValidationError("The requested portfolio target run could not be found.")
    return {
        "latest_run_id": row.id,
        "status": row.status,
        "targets": list((row.portfolio_targets_json or {}).get("targets") or []),
        "metrics": dict(row.metrics_json or {}),
        "risk": {
            "allowed": bool((row.metrics_json or {}).get("allowed", row.status == "accepted")),
            "gross_ok": bool((row.metrics_json or {}).get("gross_ok", True)),
            "net_ok": bool((row.metrics_json or {}).get("net_ok", True)),
        },
        "order_plan": dict(row.order_plan_json or {}),
        "created_at": _iso(row.created_at),
    }


def _resolve_trade_target(
    targets_payload: dict[str, Any],
    *,
    ticker: str | None = None,
    target_symbol: str | None = None,
    desk_key: str | None = None,
) -> dict[str, Any] | None:
    symbol = str(target_symbol or ticker or "").strip().upper()
    normalized_desk = str(desk_key or "").strip()
    candidates = list(targets_payload.get("targets") or [])
    if symbol:
        candidates = [target for target in candidates if _target_symbol(target) == symbol]
    if normalized_desk:
        desk_filtered = [target for target in candidates if normalized_desk in _target_desk_keys(target)]
        if desk_filtered:
            candidates = desk_filtered
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: abs(_safe_float(item.get("target_notional"), 0.0)), reverse=True)[0]


def _build_open_trade_request(
    request: AiTradePlanRequest | AiLiveIntentRequest,
    *,
    targets_payload: dict[str, Any],
    target: dict[str, Any] | None,
    execution_intent: str,
    linked_account_id: str | None = None,
) -> OpenTradeRequest:
    ticker = str(getattr(request, "ticker", None) or getattr(request, "target_symbol", None) or _target_symbol(target)).strip().upper()
    if not ticker:
        raise ValidationError("Choose a ticker or an existing target before building an AI trade plan.")
    target_notional = abs(_safe_float((target or {}).get("target_notional"), 0.0))
    max_notional = getattr(request, "max_notional_per_trade", None) if hasattr(request, "max_notional_per_trade") else None
    if max_notional is None and target_notional > 0:
        max_notional = target_notional
    limit_price = getattr(request, "limit_price", None) or getattr(request, "live_price", None)
    desk_keys = sorted(_target_desk_keys(target))
    desk_key = str(getattr(request, "desk_key", None) or (desk_keys[0] if desk_keys else "")).strip() or None
    linked_account = str(linked_account_id or getattr(request, "linked_account_id", None) or "").strip() or None
    return OpenTradeRequest(
        ticker=ticker,
        interval=getattr(request, "interval", "5m"),
        horizon=getattr(request, "horizon", 5),
        account_target_type="linked_client" if linked_account else "personal",
        linked_account_id=linked_account,
        execution_mode="manual_approval",
        live_price=getattr(request, "live_price", None),
        account_size=float(getattr(request, "account_size", 100000.0)),
        risk_percent=float(getattr(request, "risk_percent", 0.5)),
        instrument_type="equity",
        broker_side="buy",
        execution_intent=execution_intent,
        order_type="limit",
        time_in_force="day",
        limit_price=limit_price,
        extended_hours=False,
        capital_preservation_mode=True,
        regular_hours_only=True,
        max_notional_per_trade=max_notional,
        equities_only=True,
        limit_orders_only=True,
        long_only=True,
        route_family="ai_desk_manager",
        route_version="v1",
        automation_entry_reason="ai_desk_plan",
        thesis_direction="long",
        source="ai_desk_manager",
        portfolio_target_run_id=str(targets_payload.get("latest_run_id") or "").strip() or None,
        strategy_desk_key=desk_key,
        desk_contributions=list((target or {}).get("desk_contributions") or []),
    )


def build_ai_trade_plan(db: Session, *, current_user: Any, request: AiTradePlanRequest) -> dict[str, Any]:
    targets_payload = _load_portfolio_target_run_payload(
        db,
        current_user=current_user,
        portfolio_target_run_id=request.portfolio_target_run_id,
    )
    target = _resolve_trade_target(
        targets_payload,
        ticker=request.ticker,
        target_symbol=request.target_symbol,
        desk_key=request.desk_key,
    )
    blockers: list[str] = []
    warnings: list[str] = []
    if target is not None and _target_is_short(target):
        blockers.append("Short or sell-side targets require manual review; the AI trade planner is long-only in v1.")
    if target is None and not (request.ticker or request.target_symbol):
        blockers.append("No portfolio target or ticker was available for trade planning.")

    open_request = None
    preview: dict[str, Any] = {}
    if not blockers:
        open_request = _build_open_trade_request(
            request,
            targets_payload=targets_payload,
            target=target,
            execution_intent=request.execution_intent,
        )
        preview = preview_trade_from_request(open_request, db=db, current_user=current_user)
        if bool(preview.get("blocked")):
            route = preview.get("route_eligibility") if isinstance(preview.get("route_eligibility"), dict) else {}
            blockers.extend(list(route.get("block_reasons") or []))
            if not blockers:
                blockers.append("Trade preview is blocked by existing route eligibility checks.")
        route = preview.get("route_eligibility") if isinstance(preview.get("route_eligibility"), dict) else {}
        warnings.extend(list(route.get("warnings") or []))

    trade_intent = None
    if request.create_intent and not blockers:
        if not request.linked_account_id:
            blockers.append("Creating a trade intent requires a linked brokerage account.")
        else:
            intent_request = OpenTradeRequest.model_validate(
                {
                    **(open_request.model_dump() if open_request else {}),
                    "account_target_type": "linked_client",
                    "linked_account_id": request.linked_account_id,
                    "execution_intent": request.execution_intent,
                }
            )
            intent_payload = create_trade_intent_from_request(intent_request, db=db, current_user=current_user)
            trade_intent = intent_payload.get("trade_intent")

    allowed = not blockers
    return {
        "allowed": allowed,
        "blocked": not allowed,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
        "open_trade_request": open_request.model_dump() if open_request is not None else {},
        "preview": preview,
        "target": target,
        "trade_intent": trade_intent,
    }


def _paper_execution_blockers(
    *,
    desks_payload: dict[str, Any],
    targets_payload: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    targets = list(targets_payload.get("targets") or [])
    if not targets:
        blockers.append("No portfolio targets are available for paper execution.")
    if str(targets_payload.get("status") or "").strip().lower() != "accepted":
        blockers.append("Latest portfolio targets are not accepted by the allocator risk gate.")
    desks_by_key = {str(item.get("desk_key") or ""): item for item in list(desks_payload.get("items") or [])}
    for target in targets:
        for desk_key in sorted(_target_desk_keys(target)):
            desk = desks_by_key.get(desk_key)
            if desk is None:
                blockers.append(f"{desk_key} is not registered as an executable strategy desk.")
                continue
            if not bool(desk.get("enabled")):
                blockers.append(f"{desk_key} is disabled.")
            if not bool(desk.get("paper_trading_enabled")):
                blockers.append(f"{desk_key} is not enabled for paper trading.")
            if str(desk.get("trading_mode") or "").strip().lower() != "paper":
                blockers.append(f"{desk_key} is not in paper trading mode.")
    return list(dict.fromkeys(blockers))


def execute_ai_paper_execution(db: Session, *, current_user: Any, request: AiPaperExecutionRequest) -> dict[str, Any]:
    targets_payload = _load_portfolio_target_run_payload(
        db,
        current_user=current_user,
        portfolio_target_run_id=request.portfolio_target_run_id,
    )
    desks_payload = list_strategy_desks(db, current_user=current_user)
    blockers = _paper_execution_blockers(desks_payload=desks_payload, targets_payload=targets_payload)
    if blockers:
        raise ValidationError("AI paper execution is blocked.", details={"blockers": blockers})
    return execute_portfolio_targets(
        db,
        current_user=current_user,
        request=PortfolioTargetExecutionRequest(
            portfolio_target_run_id=targets_payload.get("latest_run_id"),
            execution_intent="broker_paper",
            dry_run=bool(request.dry_run),
        ),
    )


def create_ai_live_intent(db: Session, *, current_user: Any, request: AiLiveIntentRequest) -> dict[str, Any]:
    linked_accounts = list_linked_brokerage_accounts(db=db, current_user=current_user)
    latest_execution = get_latest_portfolio_target_execution(db, current_user=current_user)
    risk_snapshot = get_risk_snapshot(db, current_user=current_user)
    live_gate = _build_live_gate(
        linked_accounts=linked_accounts,
        latest_execution=latest_execution,
        risk_snapshot=risk_snapshot,
        linked_account_id=request.linked_account_id,
        frontend_confirmation=request.frontend_confirmation,
    )
    if not live_gate.get("allowed"):
        raise ValidationError(
            "Supervised live intent is blocked.",
            details={"blockers": list(live_gate.get("blockers") or []), "live_gate": live_gate},
        )

    linked_account_id = str(request.linked_account_id or live_gate.get("default_linked_account_id") or "").strip()
    trade_plan = build_ai_trade_plan(
        db,
        current_user=current_user,
        request=AiTradePlanRequest(
            ticker=request.ticker,
            target_symbol=request.target_symbol,
            desk_key=request.desk_key,
            linked_account_id=linked_account_id,
            execution_intent="broker_live",
            account_size=request.account_size,
            risk_percent=request.risk_percent,
            live_price=request.live_price,
            limit_price=request.limit_price,
            create_intent=False,
        ),
    )
    if trade_plan.get("blocked"):
        raise ValidationError(
            "Supervised live intent preview is blocked.",
            details={"blockers": list(trade_plan.get("blockers") or []), "trade_plan": trade_plan},
        )

    open_request = OpenTradeRequest.model_validate(
        {
            **dict(trade_plan.get("open_trade_request") or {}),
            "account_target_type": "linked_client",
            "linked_account_id": linked_account_id,
            "execution_intent": "broker_live",
            "execution_mode": "manual_approval",
        }
    )
    intent_payload = create_trade_intent_from_request(open_request, db=db, current_user=current_user)
    return {
        "created": True,
        "live_gate": live_gate,
        "trade_plan": trade_plan,
        "trade_intent": intent_payload.get("trade_intent"),
    }


def _policy_ticket_blockers(policy: dict[str, Any], open_trade_request: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if str(open_trade_request.get("instrument_type") or "").strip().lower() not in {"equity"}:
        blockers.append("Policy allows equity tickets only.")
    if str(open_trade_request.get("broker_side") or "").strip().lower() != "buy":
        blockers.append("Policy allows long/buy tickets only.")
    if str(open_trade_request.get("order_type") or "").strip().lower() != "limit":
        blockers.append("Policy allows limit orders only.")
    if not bool(open_trade_request.get("equities_only")):
        blockers.append("Ticket is missing the equities-only guard.")
    if not bool(open_trade_request.get("long_only")):
        blockers.append("Ticket is missing the long-only guard.")
    if not bool(open_trade_request.get("limit_orders_only")):
        blockers.append("Ticket is missing the limit-orders-only guard.")
    risk_percent = _safe_float(open_trade_request.get("risk_percent"), 0.0)
    if risk_percent > float(policy.get("max_risk_percent") or 0.5):
        blockers.append("Ticket risk percent exceeds the AI desk policy cap.")
    max_notional_cap = policy.get("max_notional_per_trade")
    ticket_notional = _safe_float(open_trade_request.get("max_notional_per_trade"), 0.0)
    if max_notional_cap is not None and ticket_notional > float(max_notional_cap):
        blockers.append("Ticket notional exceeds the AI desk policy cap.")
    return list(dict.fromkeys(blockers))


def _attach_policy_metadata_to_execution(
    db: Session,
    *,
    execution_run_id: str | None,
    policy_digest: str,
    cycle_id: str,
) -> None:
    if not execution_run_id:
        return
    row = db.get(PortfolioTargetExecutionRun, execution_run_id)
    if row is None:
        return
    metadata = dict(row.metadata_json or {})
    metadata["ai_desk_policy_digest"] = policy_digest
    metadata["ai_desk_cycle_id"] = cycle_id
    row.metadata_json = metadata
    flag_modified(row, "metadata_json")
    db.flush()


def _attach_policy_metadata_to_intent(
    db: Session,
    *,
    intent_id: str | None,
    policy_digest: str,
    cycle_id: str,
) -> None:
    if not intent_id:
        return
    row = db.get(TradeApprovalIntent, intent_id)
    if row is None:
        return
    metadata = dict(row.metadata_json or {})
    metadata["ai_desk_policy_digest"] = policy_digest
    metadata["ai_desk_cycle_id"] = cycle_id
    metadata["ai_desk_live_submit_allowed"] = False
    row.metadata_json = metadata
    flag_modified(row, "metadata_json")
    db.flush()


def _target_symbols(targets_payload: dict[str, Any]) -> list[str]:
    symbols: list[str] = []
    for target in list(targets_payload.get("targets") or []):
        symbol = _target_symbol(target)
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _has_duplicate_ai_cycle_job(db: Session, *, tenant_id: str, exclude_job_id: str | None = None) -> AsyncJob | None:
    rows = list(
        db.execute(
            select(AsyncJob)
            .where(AsyncJob.tenant_id == tenant_id)
            .where(AsyncJob.job_type == AI_DESK_JOB_TYPE)
            .where(AsyncJob.status.in_(("queued", "retrying", "running")))
            .order_by(AsyncJob.available_at.asc(), AsyncJob.created_at.asc())
        ).scalars()
    )
    for row in rows:
        if exclude_job_id and row.id == exclude_job_id:
            continue
        return row
    return None


def queue_ai_autonomous_cycle(
    db: Session,
    *,
    current_user: Any,
    request: AiAutonomousCycleRequest,
    exclude_job_id: str | None = None,
) -> dict[str, Any]:
    from backend.services.job_queue_service import enqueue_job

    tenant = _resolve_tenant_for_current_user(db, current_user)
    policy = _tenant_policy(tenant)
    decision = _make_policy_decision(
        policy,
        "ai_desk.queue_autonomous_cycle",
        require_enabled=True,
        require_armed=True,
    )
    _record_policy_decision(db, tenant=tenant, current_user=current_user, decision=decision)
    if not decision["allowed"]:
        db.commit()
        return {
            "queued": False,
            "status": "blocked",
            "decision": decision,
            "blockers": list(decision["blockers"]),
            "policy_digest": decision["policy_digest"],
        }

    duplicate = _has_duplicate_ai_cycle_job(db, tenant_id=tenant.id, exclude_job_id=exclude_job_id)
    if duplicate is not None:
        db.commit()
        return {
            "queued": False,
            "duplicate": True,
            "status": "duplicate_pending",
            "job_id": duplicate.id,
            "available_at": _iso(duplicate.available_at),
            "policy_digest": decision["policy_digest"],
        }

    available_at = _utc_now() + timedelta(minutes=int(policy.get("cycle_interval_minutes") or 15))
    job = enqueue_job(
        db,
        tenant_id=tenant.id,
        job_type=AI_DESK_JOB_TYPE,
        payload={
            "trigger": "scheduled",
            "dry_run": bool(request.dry_run),
            "policy_digest": decision["policy_digest"],
        },
        available_at=available_at,
    )
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="ai_desk.cycle_queued",
        aggregate_type="async_job",
        aggregate_id=job.id,
        payload={"job_id": job.id, "available_at": _iso(job.available_at), "policy_digest": decision["policy_digest"]},
    )
    db.commit()
    return {
        "queued": True,
        "status": "queued",
        "job_id": job.id,
        "available_at": _iso(job.available_at),
        "policy_digest": decision["policy_digest"],
        "decision": decision,
    }


def _record_cycle_event(
    db: Session,
    *,
    tenant: Tenant,
    current_user: Any,
    result: dict[str, Any],
) -> None:
    actor = _resolve_user_for_current_user(db, current_user)
    event_type = f"ai_desk.cycle.{result.get('status') or 'completed'}"
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type=event_type,
        aggregate_type="ai_desk_cycle",
        aggregate_id=result.get("cycle_id"),
        payload=result,
    )
    record_audit_event(
        db,
        event_type=event_type,
        tenant=tenant,
        user=actor,
        payload={
            "cycle_id": result.get("cycle_id"),
            "status": result.get("status"),
            "policy_digest": result.get("policy_digest"),
            "blockers": list(result.get("blockers") or []),
        },
    )


def _cycle_result(
    *,
    cycle_id: str,
    request: AiAutonomousCycleRequest,
    policy_digest: str,
    status: str,
    agents: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    actions: list[dict[str, Any]] | None = None,
    blockers: list[str] | None = None,
    next_scheduled_run: str | None = None,
) -> dict[str, Any]:
    return {
        "cycle_id": cycle_id,
        "status": status,
        "trigger": request.trigger,
        "dry_run": bool(request.dry_run),
        "policy_digest": policy_digest,
        "decisions": decisions,
        "agents": agents,
        "actions": list(actions or []),
        "blockers": list(dict.fromkeys(blockers or [])),
        "next_scheduled_run": next_scheduled_run,
    }


def run_ai_autonomous_cycle(
    db: Session,
    *,
    current_user: Any,
    request: AiAutonomousCycleRequest,
    current_job_id: str | None = None,
) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    policy = _tenant_policy(tenant)
    policy_digest = _policy_digest(policy)
    cycle_id = f"ai-cycle-{uuid4().hex[:12]}"
    agents = _base_agent_statuses(status="idle")
    decisions: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    blockers: list[str] = []

    start_decision = _make_policy_decision(
        policy,
        "ai_desk.autonomous_cycle",
        evidence={"trigger": request.trigger, "dry_run": bool(request.dry_run)},
        require_enabled=True,
        require_armed=True,
    )
    decisions.append(start_decision)
    _record_policy_decision(db, tenant=tenant, current_user=current_user, decision=start_decision)
    if not start_decision["allowed"]:
        _upsert_agent_status(agents, _agent_status("audit_supervisor", "blocked", "Cycle blocked by policy.", blockers=start_decision["blockers"]))
        result = _cycle_result(
            cycle_id=cycle_id,
            request=request,
            policy_digest=policy_digest,
            status="blocked",
            agents=agents,
            decisions=decisions,
            blockers=list(start_decision["blockers"]),
        )
        _record_cycle_event(db, tenant=tenant, current_user=current_user, result=result)
        db.commit()
        return result

    try:
        desks_payload = list_strategy_desks(db, current_user=current_user)
        allowed_desks = set(_normalize_unique_text_list(policy.get("allowed_desks")))
        executable_desks = [
            item
            for item in list(desks_payload.get("items") or [])
            if str(item.get("desk_key") or "").strip().lower() in allowed_desks
            and bool(item.get("enabled"))
        ]
        if not executable_desks:
            blockers.append("No enabled strategy desks are allowed by policy.")
            _upsert_agent_status(agents, _agent_status("market_data_sentinel", "blocked", "No allowed desk can be refreshed.", blockers=blockers))
        else:
            _upsert_agent_status(
                agents,
                _agent_status(
                    "market_data_sentinel",
                    "ready",
                    "Allowed desks are identified for deterministic refresh.",
                    evidence={"allowed_desks": sorted(allowed_desks), "executable_desks": [item.get("desk_key") for item in executable_desks]},
                ),
            )

        run_results: list[dict[str, Any]] = []
        if not blockers:
            for desk in executable_desks:
                desk_key = str(desk.get("desk_key") or "").strip().lower()
                decision = _make_policy_decision(
                    policy,
                    "ai_desk.run_strategy_desk",
                    evidence={"desk_key": desk_key},
                    require_enabled=True,
                    require_armed=True,
                    additional_blockers=[] if desk_key in allowed_desks else [f"{desk_key} is not allowed by policy."],
                )
                decisions.append(decision)
                _record_policy_decision(db, tenant=tenant, current_user=current_user, decision=decision)
                if not decision["allowed"]:
                    blockers.extend(decision["blockers"])
                    continue
                if request.dry_run:
                    run_results.append({"desk_key": desk_key, "status": "dry_run"})
                    continue
                run_results.append(run_strategy_desk(db, current_user=current_user, desk_key=desk_key, run_type=request.trigger))
            _upsert_agent_status(
                agents,
                _agent_status(
                    "strategy_desk_runner",
                    "completed" if not blockers else "blocked",
                    "Allowed desks were refreshed." if not request.dry_run else "Allowed desks would be refreshed.",
                    blockers=blockers,
                    evidence={"runs": run_results},
                ),
            )

        desk_states: list[dict[str, Any]] = []
        if not blockers:
            refreshed_desks = list_strategy_desks(db, current_user=current_user)
            desk_states = [_classify_desk(item, now=_utc_now()) for item in list(refreshed_desks.get("items") or [])]
            allowed_states = [item for item in desk_states if str(item.get("desk_key") or "").strip().lower() in allowed_desks]
            state_blockers = [
                f"{item.get('desk_key')} is {item.get('state')}."
                for item in allowed_states
                if item.get("state") in {"blocked", "stale"}
            ]
            blockers.extend(state_blockers)
            _upsert_agent_status(
                agents,
                _agent_status(
                    "signal_validator",
                    "ready" if not state_blockers else "blocked",
                    "Allowed desk outputs are fresh enough for allocation." if not state_blockers else "One or more allowed desk outputs are not usable.",
                    blockers=state_blockers,
                    evidence={"states": allowed_states},
                ),
            )

        latest_targets: dict[str, Any] = {}
        risk_snapshot: dict[str, Any] = {}
        if not blockers:
            decision = _make_policy_decision(policy, "ai_desk.allocate_risk", require_enabled=True, require_armed=True)
            decisions.append(decision)
            _record_policy_decision(db, tenant=tenant, current_user=current_user, decision=decision)
            if decision["allowed"]:
                allocator = {"dry_run": True} if request.dry_run else build_allocator_snapshot(db, current_user=current_user)
                latest_targets = get_latest_portfolio_targets(db, current_user=current_user)
                risk_snapshot = get_risk_snapshot(db, current_user=current_user)
                if not bool(risk_snapshot.get("allowed", True)):
                    blockers.append("Risk allocator blocked the latest target set.")
                _upsert_agent_status(
                    agents,
                    _agent_status(
                        "risk_allocator",
                        "ready" if not blockers else "blocked",
                        "Allocator and risk snapshots are inside policy." if not blockers else "Risk allocator blocked downstream automation.",
                        blockers=blockers,
                        evidence={"allocator": allocator, "risk": risk_snapshot, "target_run_id": latest_targets.get("latest_run_id")},
                    ),
                )
            else:
                blockers.extend(decision["blockers"])

        trade_plan: dict[str, Any] = {}
        if not blockers and latest_targets.get("targets"):
            symbol = _target_symbols(latest_targets)[0]
            decision = _make_policy_decision(policy, "ai_desk.preview_trade_plan", evidence={"symbol": symbol}, require_enabled=True, require_armed=True)
            decisions.append(decision)
            _record_policy_decision(db, tenant=tenant, current_user=current_user, decision=decision)
            if decision["allowed"]:
                trade_plan = build_ai_trade_plan(
                    db,
                    current_user=current_user,
                    request=AiTradePlanRequest(
                        target_symbol=symbol,
                        execution_intent="broker_paper",
                        risk_percent=float(policy.get("max_risk_percent") or 0.5),
                        max_notional_per_trade=policy.get("max_notional_per_trade"),
                    ),
                )
                ticket_blockers = _policy_ticket_blockers(policy, dict(trade_plan.get("open_trade_request") or {}))
                blockers.extend(list(trade_plan.get("blockers") or []))
                blockers.extend(ticket_blockers)
                _upsert_agent_status(
                    agents,
                    _agent_status(
                        "trade_planner",
                        "ready" if not (trade_plan.get("blocked") or ticket_blockers) else "blocked",
                        "A policy-bounded paper ticket was previewed." if not (trade_plan.get("blocked") or ticket_blockers) else "Trade planning is blocked.",
                        blockers=list(trade_plan.get("blockers") or []) + ticket_blockers,
                        evidence={"symbol": symbol, "open_trade_request": trade_plan.get("open_trade_request")},
                    ),
                )
        elif not blockers:
            _upsert_agent_status(agents, _agent_status("trade_planner", "skipped", "No accepted target was available for planning."))

        paper_result: dict[str, Any] = {}
        if not blockers and latest_targets.get("targets"):
            latest_execution = get_latest_portfolio_target_execution(db, current_user=current_user)
            already_executed = (
                latest_execution.get("portfolio_target_run_id") == latest_targets.get("latest_run_id")
                and latest_execution.get("status") not in {"idle", "blocked", "rejected"}
            )
            decision = _make_policy_decision(
                policy,
                "ai_desk.execute_paper",
                evidence={"target_run_id": latest_targets.get("latest_run_id"), "already_executed": already_executed},
                require_enabled=True,
                require_armed=True,
            )
            decisions.append(decision)
            _record_policy_decision(db, tenant=tenant, current_user=current_user, decision=decision)
            if already_executed:
                _upsert_agent_status(
                    agents,
                    _agent_status(
                        "paper_execution_manager",
                        "skipped",
                        "The latest target run already has a paper execution lifecycle.",
                        evidence={"latest_execution": latest_execution},
                    ),
                )
            elif decision["allowed"]:
                if request.dry_run:
                    paper_result = {"status": "dry_run", "portfolio_target_run_id": latest_targets.get("latest_run_id")}
                else:
                    paper_result = execute_ai_paper_execution(
                        db,
                        current_user=current_user,
                        request=AiPaperExecutionRequest(portfolio_target_run_id=latest_targets.get("latest_run_id")),
                    )
                    _attach_policy_metadata_to_execution(
                        db,
                        execution_run_id=paper_result.get("latest_execution_run_id"),
                        policy_digest=policy_digest,
                        cycle_id=cycle_id,
                    )
                actions.append({"key": "paper_execution", "status": paper_result.get("status"), "result": paper_result})
                _upsert_agent_status(
                    agents,
                    _agent_status(
                        "paper_execution_manager",
                        "completed",
                        "Paper target execution was routed through the existing executor.",
                        evidence={"execution": paper_result},
                    ),
                )
            else:
                blockers.extend(decision["blockers"])
                _upsert_agent_status(agents, _agent_status("paper_execution_manager", "blocked", "Policy blocked paper execution.", blockers=decision["blockers"]))

        latest_execution = get_latest_portfolio_target_execution(db, current_user=current_user)
        if latest_execution.get("latest_execution_run_id"):
            if request.dry_run:
                sync_result = {"status": "dry_run", "execution_run_id": latest_execution.get("latest_execution_run_id")}
            else:
                sync_result = sync_portfolio_target_execution(
                    db,
                    current_user=current_user,
                    execution_run_id=latest_execution.get("latest_execution_run_id"),
                )
            _upsert_agent_status(
                agents,
                _agent_status(
                    "reconciliation_monitor",
                    "completed",
                    "Paper execution lifecycle was checked for fills, rejections, and orphan events.",
                    evidence={"latest_execution": sync_result},
                ),
            )
        else:
            _upsert_agent_status(agents, _agent_status("reconciliation_monitor", "skipped", "No paper execution lifecycle exists yet."))

        live_gate = _build_live_gate(
            linked_accounts=list_linked_brokerage_accounts(db=db, current_user=current_user),
            latest_execution=get_latest_portfolio_target_execution(db, current_user=current_user),
            risk_snapshot=get_risk_snapshot(db, current_user=current_user),
            frontend_confirmation=True,
        )
        if policy.get("allow_live_intents") and live_gate.get("allowed") and latest_targets.get("targets") and not blockers:
            pending = list_trade_intents(db=db, current_user=current_user, status_filter="pending_approval")
            if int(pending.get("count") or 0) > 0:
                _upsert_agent_status(
                    agents,
                    _agent_status("live_approval_assistant", "skipped", "A pending approval intent already exists.", evidence={"pending_intent_count": pending.get("count")}),
                )
            elif request.dry_run:
                _upsert_agent_status(agents, _agent_status("live_approval_assistant", "skipped", "A live approval intent would be created."))
            else:
                symbol = _target_symbols(latest_targets)[0]
                live_payload = create_ai_live_intent(
                    db,
                    current_user=current_user,
                    request=AiLiveIntentRequest(
                        target_symbol=symbol,
                        linked_account_id=live_gate.get("default_linked_account_id"),
                        frontend_confirmation=True,
                    ),
                )
                _attach_policy_metadata_to_intent(
                    db,
                    intent_id=(live_payload.get("trade_intent") or {}).get("id"),
                    policy_digest=policy_digest,
                    cycle_id=cycle_id,
                )
                actions.append({"key": "live_intent", "status": "created", "result": live_payload})
                _upsert_agent_status(
                    agents,
                    _agent_status("live_approval_assistant", "completed", "A supervised live approval intent was created without order submission.", evidence={"trade_intent": live_payload.get("trade_intent")}),
                )
        else:
            _upsert_agent_status(
                agents,
                _agent_status(
                    "live_approval_assistant",
                    "blocked" if live_gate.get("blockers") else "skipped",
                    "Live approval intent creation remains gated.",
                    blockers=list(live_gate.get("blockers") or []),
                    evidence={"live_gate": live_gate, "allow_live_intents": bool(policy.get("allow_live_intents"))},
                ),
            )

        _upsert_agent_status(
            agents,
            _agent_status(
                "audit_supervisor",
                "completed" if not blockers else "blocked",
                "Policy decisions and cycle outcome were recorded.",
                blockers=blockers,
                evidence={"decision_count": len(decisions), "action_count": len(actions)},
            ),
        )
        status = "blocked" if blockers else "completed"
        next_schedule = None
        if status == "completed" and not request.dry_run:
            queued = queue_ai_autonomous_cycle(
                db,
                current_user=current_user,
                request=AiAutonomousCycleRequest(trigger="scheduled", enqueue=True),
                exclude_job_id=current_job_id,
            )
            next_schedule = queued.get("available_at")
        result = _cycle_result(
            cycle_id=cycle_id,
            request=request,
            policy_digest=policy_digest,
            status=status,
            agents=agents,
            decisions=decisions,
            actions=actions,
            blockers=blockers,
            next_scheduled_run=next_schedule,
        )
        _record_cycle_event(db, tenant=tenant, current_user=current_user, result=result)
        db.commit()
        return result
    except Exception as exc:
        blockers.append(str(exc))
        _upsert_agent_status(
            agents,
            _agent_status("audit_supervisor", "failed", "Autonomous cycle failed and stopped downstream work.", blockers=blockers),
        )
        result = _cycle_result(
            cycle_id=cycle_id,
            request=request,
            policy_digest=policy_digest,
            status="failed",
            agents=agents,
            decisions=decisions,
            actions=actions,
            blockers=blockers,
        )
        _record_cycle_event(db, tenant=tenant, current_user=current_user, result=result)
        db.commit()
        return result


def run_or_queue_ai_autonomous_cycle(
    db: Session,
    *,
    current_user: Any,
    request: AiAutonomousCycleRequest,
) -> dict[str, Any]:
    if request.enqueue:
        return queue_ai_autonomous_cycle(db, current_user=current_user, request=request)
    return run_ai_autonomous_cycle(db, current_user=current_user, request=request)


def process_ai_autonomous_cycle_job(db: Session, job: AsyncJob) -> dict[str, Any]:
    tenant = db.get(Tenant, job.tenant_id) if job.tenant_id else None
    if tenant is None:
        return {
            "ok": False,
            "retryable": False,
            "error": "Tenant for queued AI desk cycle no longer exists.",
            "result": {"job_id": job.id},
        }
    current_user = _system_current_user_for_tenant(tenant)
    payload = dict(job.payload_json or {})
    result = run_ai_autonomous_cycle(
        db,
        current_user=current_user,
        request=AiAutonomousCycleRequest(
            trigger="scheduled",
            dry_run=bool(payload.get("dry_run")),
            enqueue=False,
        ),
        current_job_id=job.id,
    )
    return {
        "ok": result.get("status") in {"completed", "blocked"},
        "retryable": result.get("status") == "failed",
        "error": "; ".join(result.get("blockers") or []) if result.get("status") == "failed" else None,
        "result": result,
    }
