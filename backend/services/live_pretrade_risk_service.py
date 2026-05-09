from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.saas import LiveOrderIntent, LiveRiskCheck, RiskEvent
from backend.services.billing_service import require_entitlement
from backend.services.live_trading_common import (
    LIVE_SESSION_ORDERABLE_STATUSES,
    active_live_kill_switches,
    as_float,
    as_list,
    assert_live_approver,
    assert_live_manager,
    current_readiness,
    latest_active_risk_policy,
    load_live_order_or_raise,
    provider_live_gate,
    record_live_evidence,
    resolve_tenant_and_user,
    serialize_risk_check,
)


def _add_check(checks: list[dict[str, Any]], blockers: list[dict[str, str]], rule: str, passed: bool, message: str) -> None:
    checks.append({"rule": rule, "status": "pass" if passed else "block", "message": message})
    if not passed:
        blockers.append({"key": rule, "message": message})


def _allowed_list(value: Any) -> set[str]:
    return {str(item or "").strip().upper() for item in as_list(value) if str(item or "").strip()}


def _instrument_matches(value: str, allowed: set[str]) -> bool:
    if not allowed:
        return True
    normalized = str(value or "").strip().upper()
    return normalized in allowed or normalized.lower() in {item.lower() for item in allowed}


def run_live_pretrade_risk_check(
    db: Session,
    *,
    current_user: Any,
    order_intent_id: str,
    force_refresh: bool = False,
) -> dict[str, Any]:
    if force_refresh:
        assert_live_manager(current_user)
    else:
        assert_live_approver(current_user)
    require_entitlement(db, current_user, "live_order_approvals", message="Live order risk checks require live approval entitlement.")
    tenant, user = resolve_tenant_and_user(db, current_user)
    order_intent = load_live_order_or_raise(db, tenant_id=tenant.id, order_intent_id=order_intent_id)
    session = order_intent.live_trading_session
    strategy = order_intent.strategy_desk
    authorization = session.authorization if session else None
    linked_account = session.linked_account if session else None
    policy = latest_active_risk_policy(db, tenant_id=tenant.id, strategy_id=order_intent.strategy_desk_id)
    readiness = current_readiness(db, tenant_id=tenant.id, strategy_id=order_intent.strategy_desk_id)
    provider_gate = provider_live_gate(linked_account)
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []
    min_score = int(getattr(settings, "readiness_min_live_score", 85) or 85)

    _add_check(checks, blockers, "signed_authorization", bool(authorization and authorization.status == "signed" and authorization.signed_at and not authorization.revoked_at), "A signed, active live authorization is required.")
    _add_check(checks, blockers, "session_orderable", bool(session and session.status in LIVE_SESSION_ORDERABLE_STATUSES), "The strategy must be armed or live before live order approval.")
    _add_check(checks, blockers, "live_feature_enabled", provider_gate["enabled"], "; ".join(item["message"] for item in provider_gate["blockers"]) or "Live broker provider is enabled.")
    active_kills = active_live_kill_switches(db, tenant_id=tenant.id, strategy_id=order_intent.strategy_desk_id, session_id=session.id if session else None)
    _add_check(checks, blockers, "kill_switch_clear", not active_kills, "No live kill switch may be active.")
    _add_check(checks, blockers, "risk_policy_active", bool(policy and policy.status == "active"), "An active risk policy is required.")
    readiness_score = int((readiness or {}).get("score") or 0)
    readiness_blockers = [item for item in list((readiness or {}).get("blockers") or []) if str(item.get("severity") or "").lower() == "critical"]
    _add_check(checks, blockers, "readiness_score", readiness_score >= min_score, f"Readiness score must be at least {min_score}.")
    _add_check(checks, blockers, "readiness_blockers_clear", not readiness_blockers, "Critical readiness blockers must be resolved.")
    runtime = dict(strategy.runtime_json or {}) if strategy else {}
    _add_check(checks, blockers, "fresh_market_data", not bool(runtime.get("stale_data")), "Market data must be fresh.")
    _add_check(checks, blockers, "audit_logging_available", bool(runtime.get("audit_logging_available", True)), "Audit logging must be available.")
    if str(getattr(strategy, "category", "") or "").lower() == "options":
        _add_check(checks, blockers, "options_liquidity", bool(runtime.get("options_quotes_and_liquidity_ok", True)), "Options quote and liquidity checks must pass.")

    if policy:
        max_notional = as_float(policy.max_order_notional)
        if max_notional > 0:
            _add_check(checks, blockers, "max_order_notional", as_float(order_intent.notional_value) <= max_notional, "Order notional exceeds the active policy.")
        blocked_symbols = _allowed_list(policy.blocked_symbols_json)
        allowed_symbols = _allowed_list(policy.allowed_symbols_json)
        allowed_instruments = _allowed_list(policy.allowed_instruments_json)
        symbol = str(order_intent.symbol or "").strip().upper()
        _add_check(checks, blockers, "symbol_not_blocked", symbol not in blocked_symbols, "Symbol is blocked by policy.")
        _add_check(checks, blockers, "symbol_allowed", not allowed_symbols or symbol in allowed_symbols, "Symbol is not in the allowed policy universe.")
        _add_check(checks, blockers, "instrument_allowed", _instrument_matches(order_intent.instrument_type, allowed_instruments), "Instrument is not allowed by policy.")

    if order_intent.duplicate_key:
        duplicate = db.execute(
            select(LiveOrderIntent).where(
                LiveOrderIntent.tenant_id == tenant.id,
                LiveOrderIntent.duplicate_key == order_intent.duplicate_key,
                LiveOrderIntent.id != order_intent.id,
                LiveOrderIntent.status.notin_(("rejected", "blocked")),
            )
        ).scalars().first()
        _add_check(checks, blockers, "duplicate_order", duplicate is None, "Duplicate live order intent detected.")

    if policy and bool((policy.config_json or {}).get("allow_policy_auto_approval")):
        try:
            require_entitlement(db, current_user, "live_canary")
            require_entitlement(db, current_user, "automation_advanced")
            if not blockers:
                order_intent.requires_user_approval = False
                warnings.append({"key": "policy_auto_approval", "message": "Policy auto-approval is configured; broker submission still requires every live gate."})
        except Exception:
            warnings.append({"key": "policy_auto_approval_unavailable", "message": "Policy auto-approval requires Professional+ live automation entitlements."})

    status = "pass" if not blockers else "blocked"
    score = max(0, 100 - len(blockers) * 12)
    row = LiveRiskCheck(
        tenant_id=tenant.id,
        live_order_intent_id=order_intent.id,
        strategy_desk_id=order_intent.strategy_desk_id,
        risk_policy_id=policy.id if policy else None,
        status=status,
        score=score,
        checks_json={"items": checks},
        blockers_json=blockers,
        warnings_json=warnings,
    )
    db.add(row)
    if blockers:
        order_intent.status = "blocked"
        if session:
            session.blocked_order_count = int(session.blocked_order_count or 0) + 1
        db.add(
            RiskEvent(
                tenant_id=tenant.id,
                strategy_desk_id=order_intent.strategy_desk_id,
                trade_decision_id=order_intent.trade_decision_id,
                event_type="live.pretrade_blocked",
                severity="critical",
                breached_rule=";".join(item["key"] for item in blockers[:3]),
                action_taken="order_intent_blocked",
                payload_json={"order_intent_id": order_intent.id, "blockers": blockers, "checks": checks},
            )
        )
    elif order_intent.requires_user_approval:
        order_intent.status = "pending_approval"
    else:
        order_intent.status = "approved"
    db.flush()
    record_live_evidence(
        db,
        tenant=tenant,
        user=user,
        event_type="live.pretrade_risk_checked",
        aggregate_type="live_order_intent",
        aggregate_id=order_intent.id,
        payload={"status": status, "score": score, "blocker_count": len(blockers)},
    )
    db.commit()
    return {
        "risk_check": serialize_risk_check(row),
        "allowed": status == "pass",
        "requires_user_approval": bool(order_intent.requires_user_approval),
        "order_status": order_intent.status,
        "next_action": "Approve or reject the order intent." if status == "pass" and order_intent.requires_user_approval else "Resolve blockers before approval.",
    }
