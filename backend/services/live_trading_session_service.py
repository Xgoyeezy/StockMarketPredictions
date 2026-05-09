from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.saas import LiveTradingSession, RiskEvent
from backend.services.billing_service import enforce_entitlement_limit, increment_entitlement_usage, require_entitlement
from backend.services.exceptions import NotFoundError, ValidationError
from backend.services.live_kill_switch_service import trigger_live_kill_switch
from backend.services.live_trading_common import (
    LIVE_SESSION_ACTIVE_STATUSES,
    active_live_kill_switches,
    assert_live_manager,
    assert_live_reader,
    current_readiness,
    ensure_strategy_version,
    latest_active_risk_policy,
    latest_live_session,
    load_authorization_or_raise,
    provider_live_gate,
    record_live_evidence,
    resolve_tenant_and_user,
    serialize_authorization,
    serialize_session,
    utc_now,
)
from backend.services.strategy_readiness_score_service import evaluate_strategy_readiness, load_strategy_or_raise


def _payload(request: Any) -> dict[str, Any]:
    return request.model_dump() if hasattr(request, "model_dump") else dict(request or {})


def request_live_strategy(db: Session, *, current_user: Any, strategy_id: str, request: Any | None = None) -> dict[str, Any]:
    assert_live_manager(current_user)
    require_entitlement(db, current_user, "live_canary", message="Live requests require live canary entitlement.")
    tenant, user = resolve_tenant_and_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    data = _payload(request)
    readiness = evaluate_strategy_readiness(db, current_user=current_user, strategy_id=strategy.id, force_refresh=True)
    blockers: list[dict[str, str]] = []
    if not getattr(settings, "feature_live_trading", False):
        blockers.append({"key": "live_feature_disabled", "message": "FEATURE_LIVE_TRADING is false; request can be tracked but cannot start live."})
    if not data.get("authorization_id"):
        blockers.append({"key": "authorization_required", "message": "Create and sign a live authorization before arming."})
    record_live_evidence(
        db,
        tenant=tenant,
        user=user,
        event_type="live.strategy_requested",
        aggregate_type="strategy",
        aggregate_id=strategy.id,
        payload={"readiness_score": readiness.get("score"), "blockers": blockers, "reason": data.get("reason")},
    )
    db.commit()
    return {
        "strategy_id": strategy.id,
        "status": "requested",
        "readiness": readiness,
        "blockers": blockers,
        "next_action": "Sign a live authorization, resolve blockers, then arm the strategy.",
    }


def _validate_arm_gates(
    db: Session,
    *,
    tenant_id: str,
    strategy_id: str,
    authorization_id: str,
    expected_min_readiness_score: int,
) -> tuple[list[dict[str, str]], Any]:
    blockers: list[dict[str, str]] = []
    strategy = load_strategy_or_raise(db, tenant_id=tenant_id, strategy_id=strategy_id)
    authorization = load_authorization_or_raise(db, tenant_id=tenant_id, authorization_id=authorization_id)
    if authorization.strategy_desk_id != strategy.id:
        blockers.append({"key": "authorization_strategy_mismatch", "message": "Authorization does not belong to this strategy."})
    if authorization.status != "signed" or authorization.revoked_at is not None:
        blockers.append({"key": "authorization_not_signed", "message": "Authorization must be signed and active."})
    if str(strategy.lifecycle_stage or "").lower() not in {"live_candidate", "live_ready", "scale_ready"}:
        blockers.append({"key": "strategy_not_live_candidate", "message": "Strategy must be promoted to live candidate before arming."})
    readiness = current_readiness(db, tenant_id=tenant_id, strategy_id=strategy.id)
    if readiness is None:
        blockers.append({"key": "readiness_missing", "message": "Readiness must be evaluated before arming."})
    elif int(readiness.get("score") or 0) < expected_min_readiness_score:
        blockers.append({"key": "readiness_below_threshold", "message": f"Readiness score must be at least {expected_min_readiness_score}."})
    critical_blockers = [item for item in list((readiness or {}).get("blockers") or []) if str(item.get("severity") or "").lower() == "critical"]
    if critical_blockers:
        blockers.append({"key": "critical_readiness_blockers", "message": "Critical readiness blockers must be resolved."})
    policy = latest_active_risk_policy(db, tenant_id=tenant_id, strategy_id=strategy.id)
    if policy is None:
        blockers.append({"key": "risk_policy_missing", "message": "An active risk policy is required before arming."})
    active_kills = active_live_kill_switches(db, tenant_id=tenant_id, strategy_id=strategy.id)
    if active_kills:
        blockers.append({"key": "kill_switch_active", "message": "A live kill switch is active."})
    runtime = dict(strategy.runtime_json or {})
    if not bool(runtime.get("audit_replay_active", True)):
        blockers.append({"key": "audit_replay_unavailable", "message": "Audit replay must be active before arming."})
    if bool(runtime.get("stale_data")):
        blockers.append({"key": "stale_market_data", "message": "Market data must be fresh before arming."})
    if strategy.category == "options" and not bool(runtime.get("options_quotes_and_liquidity_ok", True)):
        blockers.append({"key": "options_liquidity_failure", "message": "Options quote and liquidity checks must pass."})
    return blockers, authorization


def arm_live_strategy(db: Session, *, current_user: Any, strategy_id: str, request: Any) -> dict[str, Any]:
    assert_live_manager(current_user)
    require_entitlement(db, current_user, "live_canary", message="Live arming requires live canary entitlement.")
    require_entitlement(db, current_user, "live_sessions", message="This plan does not allow live sessions.")
    tenant, user = resolve_tenant_and_user(db, current_user)
    data = _payload(request)
    authorization_id = str(data.get("authorization_id") or "")
    if not authorization_id:
        raise ValidationError("authorization_id is required to arm live trading.")
    expected_score = int(data.get("expected_min_readiness_score") or getattr(settings, "readiness_min_live_score", 85) or 85)
    blockers, authorization = _validate_arm_gates(
        db,
        tenant_id=tenant.id,
        strategy_id=strategy_id,
        authorization_id=authorization_id,
        expected_min_readiness_score=expected_score,
    )
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    if blockers:
        db.add(
            RiskEvent(
                tenant_id=tenant.id,
                strategy_desk_id=strategy.id,
                event_type="live.arm_blocked",
                severity="critical",
                breached_rule=";".join(item["key"] for item in blockers[:3]),
                action_taken="live_arm_blocked",
                payload_json={"blockers": blockers},
            )
        )
        record_live_evidence(db, tenant=tenant, user=user, event_type="live.arm_blocked", aggregate_type="strategy", aggregate_id=strategy.id, payload={"blockers": blockers})
        db.commit()
        return {"strategy_id": strategy.id, "live_state": "blocked", "blockers": blockers, "next_action": "Resolve live arming blockers."}
    current_count = db.scalar(
        select(func.count(LiveTradingSession.id)).where(
            LiveTradingSession.tenant_id == tenant.id,
            LiveTradingSession.status.in_(LIVE_SESSION_ACTIVE_STATUSES),
        )
    ) or 0
    enforce_entitlement_limit(db, current_user, "live_sessions", requested_total=int(current_count) + 1, resource_label="live sessions")
    session = LiveTradingSession(
        tenant_id=tenant.id,
        strategy_desk_id=strategy.id,
        strategy_version_id=authorization.strategy_version_id,
        linked_account_id=authorization.linked_account_id,
        authorization_id=authorization.id,
        status="armed",
        last_heartbeat_at=utc_now(),
    )
    db.add(session)
    strategy.runtime_json = {**dict(strategy.runtime_json or {}), "live_state": "armed", "live_session_id": session.id}
    db.flush()
    increment_entitlement_usage(db, current_user, "live_sessions")
    record_live_evidence(db, tenant=tenant, user=user, event_type="live.strategy_armed", aggregate_type="live_session", aggregate_id=session.id, payload={"strategy_id": strategy.id})
    db.commit()
    return {
        "strategy_id": strategy.id,
        "live_state": "armed",
        "session": serialize_session(session),
        "authorization": serialize_authorization(authorization),
        "next_action": "Explicit start required before any live order can be submitted.",
    }


def start_live_strategy(db: Session, *, current_user: Any, strategy_id: str, request: Any | None = None) -> dict[str, Any]:
    assert_live_manager(current_user)
    require_entitlement(db, current_user, "live_canary", message="Live start requires live canary entitlement.")
    tenant, user = resolve_tenant_and_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    session = latest_live_session(db, tenant_id=tenant.id, strategy_id=strategy.id, statuses={"armed", "paused"})
    if session is None:
        raise NotFoundError("No armed live session exists for this strategy.")
    provider_gate = provider_live_gate(session.linked_account)
    blockers = list(provider_gate["blockers"])
    if active_live_kill_switches(db, tenant_id=tenant.id, strategy_id=strategy.id, session_id=session.id):
        blockers.append({"key": "kill_switch_active", "message": "A live kill switch is active."})
    if blockers:
        db.add(
            RiskEvent(
                tenant_id=tenant.id,
                strategy_desk_id=strategy.id,
                event_type="live.start_blocked",
                severity="critical",
                breached_rule=";".join(item["key"] for item in blockers[:3]),
                action_taken="live_start_blocked",
                payload_json={"blockers": blockers},
            )
        )
        session.status = "blocked"
        strategy.runtime_json = {**dict(strategy.runtime_json or {}), "live_state": "blocked"}
        record_live_evidence(db, tenant=tenant, user=user, event_type="live.start_blocked", aggregate_type="live_session", aggregate_id=session.id, payload={"blockers": blockers})
        db.commit()
        return {"strategy_id": strategy.id, "live_state": "blocked", "session": serialize_session(session), "blockers": blockers, "next_action": "Enable live flags and clear blockers before starting."}
    now = utc_now()
    session.status = "live"
    session.started_at = session.started_at or now
    session.last_heartbeat_at = now
    session.updated_at = now
    strategy.runtime_json = {**dict(strategy.runtime_json or {}), "live_state": "live", "live_session_id": session.id}
    record_live_evidence(db, tenant=tenant, user=user, event_type="live.strategy_started", aggregate_type="live_session", aggregate_id=session.id, payload={"strategy_id": strategy.id})
    db.commit()
    return {"strategy_id": strategy.id, "live_state": "live", "session": serialize_session(session), "next_action": "Monitor approvals, risk events, and broker receipts."}


def pause_live_strategy(db: Session, *, current_user: Any, strategy_id: str, request: Any | None = None) -> dict[str, Any]:
    return _transition_session(db, current_user=current_user, strategy_id=strategy_id, status="paused", event_type="live.strategy_paused")


def resume_live_strategy(db: Session, *, current_user: Any, strategy_id: str, request: Any | None = None) -> dict[str, Any]:
    return _transition_session(db, current_user=current_user, strategy_id=strategy_id, status="live", event_type="live.strategy_resumed")


def stop_live_strategy(db: Session, *, current_user: Any, strategy_id: str, request: Any | None = None) -> dict[str, Any]:
    return _transition_session(db, current_user=current_user, strategy_id=strategy_id, status="stopped", event_type="live.strategy_stopped")


def _transition_session(db: Session, *, current_user: Any, strategy_id: str, status: str, event_type: str) -> dict[str, Any]:
    assert_live_manager(current_user)
    tenant, user = resolve_tenant_and_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    session = latest_live_session(db, tenant_id=tenant.id, strategy_id=strategy.id, statuses=LIVE_SESSION_ACTIVE_STATUSES | {"blocked"})
    if session is None:
        raise NotFoundError("No live session exists for this strategy.")
    now = utc_now()
    session.status = status
    session.updated_at = now
    if status == "paused":
        session.paused_at = now
    elif status == "stopped":
        session.stopped_at = now
    elif status == "live":
        session.last_heartbeat_at = now
    strategy.runtime_json = {**dict(strategy.runtime_json or {}), "live_state": status}
    record_live_evidence(db, tenant=tenant, user=user, event_type=event_type, aggregate_type="live_session", aggregate_id=session.id, payload={"strategy_id": strategy.id, "status": status})
    db.commit()
    return {"strategy_id": strategy.id, "live_state": status, "session": serialize_session(session)}


def kill_live_strategy(db: Session, *, current_user: Any, strategy_id: str, request: Any | None = None) -> dict[str, Any]:
    data = _payload(request)
    data["strategy_id"] = strategy_id
    data["scope"] = "strategy"
    data["reason"] = data.get("reason") or "Operator killed live strategy."
    return trigger_live_kill_switch(db, current_user=current_user, request=data)


def get_live_status(db: Session, *, current_user: Any) -> dict[str, Any]:
    assert_live_reader(current_user)
    tenant, _user = resolve_tenant_and_user(db, current_user)
    sessions = db.execute(
        select(LiveTradingSession)
        .where(LiveTradingSession.tenant_id == tenant.id)
        .order_by(LiveTradingSession.created_at.desc())
        .limit(100)
    ).scalars().all()
    active = [row for row in sessions if row.status in LIVE_SESSION_ACTIVE_STATUSES]
    return {
        "feature_flags": {
            "live_trading": bool(getattr(settings, "feature_live_trading", False)),
            "managed_advisory": bool(getattr(settings, "feature_managed_advisory", False)),
            "alpaca_live": bool(getattr(settings, "alpaca_live_trading_enabled", False)),
        },
        "summary": {
            "active_session_count": len(active),
            "armed_count": len([row for row in sessions if row.status == "armed"]),
            "live_count": len([row for row in sessions if row.status == "live"]),
            "blocked_count": len([row for row in sessions if row.status == "blocked"]),
        },
        "sessions": [serialize_session(row) for row in sessions],
    }
