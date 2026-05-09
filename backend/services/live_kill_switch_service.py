from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.saas import LiveKillSwitchEvent, LiveTradingSession, RiskEvent
from backend.services.live_trading_common import (
    active_live_kill_switches,
    assert_live_manager,
    assert_live_reader,
    record_live_evidence,
    resolve_tenant_and_user,
    serialize_kill_switch,
    utc_now,
)
from backend.services.strategy_readiness_score_service import load_strategy_or_raise


def _payload(request: Any) -> dict[str, Any]:
    return request.model_dump() if hasattr(request, "model_dump") else dict(request or {})


def trigger_live_kill_switch(db: Session, *, current_user: Any, request: Any) -> dict[str, Any]:
    assert_live_manager(current_user)
    tenant, user = resolve_tenant_and_user(db, current_user)
    data = _payload(request)
    strategy_id = data.get("strategy_id")
    session_id = data.get("live_trading_session_id")
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id) if strategy_id else None
    session = None
    if session_id:
        session = db.execute(
            select(LiveTradingSession).where(LiveTradingSession.tenant_id == tenant.id, LiveTradingSession.id == session_id)
        ).scalar_one_or_none()
    now = utc_now()
    event = LiveKillSwitchEvent(
        tenant_id=tenant.id,
        strategy_desk_id=strategy.id if strategy else (session.strategy_desk_id if session else None),
        live_trading_session_id=session.id if session else None,
        scope=str(data.get("scope") or ("session" if session else "strategy" if strategy else "tenant")),
        reason=str(data.get("reason") or "Operator requested live kill switch."),
        triggered_by="user",
        triggered_by_user_id=user.id if user else None,
        triggered_at=now,
        status="active",
    )
    db.add(event)
    if strategy:
        strategy.runtime_json = {**dict(strategy.runtime_json or {}), "kill_switch_active": True, "live_state": "killed"}
    if session:
        session.status = "killed"
        session.killed_at = now
        session.updated_at = now
    db.add(
        RiskEvent(
            tenant_id=tenant.id,
            strategy_desk_id=event.strategy_desk_id,
            event_type="live.kill_switch_triggered",
            severity="critical",
            breached_rule="operator_kill_switch",
            action_taken="live_trading_blocked",
            payload_json={"scope": event.scope, "reason": event.reason, "session_id": event.live_trading_session_id},
        )
    )
    db.flush()
    record_live_evidence(
        db,
        tenant=tenant,
        user=user,
        event_type="live.kill_switch_triggered",
        aggregate_type="live_kill_switch",
        aggregate_id=event.id,
        payload={"scope": event.scope, "strategy_id": event.strategy_desk_id, "session_id": event.live_trading_session_id},
    )
    db.commit()
    return {"kill_switch": serialize_kill_switch(event), "next_action": "Clear the kill switch only after reviewing the risk event trail."}


def clear_live_kill_switch(db: Session, *, current_user: Any, request: Any | None = None) -> dict[str, Any]:
    assert_live_manager(current_user)
    tenant, user = resolve_tenant_and_user(db, current_user)
    data = _payload(request)
    strategy_id = data.get("strategy_id")
    session_id = data.get("live_trading_session_id")
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id) if strategy_id else None
    rows = active_live_kill_switches(
        db,
        tenant_id=tenant.id,
        strategy_id=strategy.id if strategy else None,
        session_id=session_id,
    )
    now = utc_now()
    for row in rows:
        row.status = "cleared"
        row.cleared_by_user_id = user.id if user else None
        row.cleared_at = now
    if strategy:
        strategy.runtime_json = {**dict(strategy.runtime_json or {}), "kill_switch_active": False}
    record_live_evidence(
        db,
        tenant=tenant,
        user=user,
        event_type="live.kill_switch_cleared",
        aggregate_type="live_kill_switch",
        aggregate_id=strategy.id if strategy else tenant.id,
        payload={"cleared_count": len(rows), "strategy_id": strategy.id if strategy else None, "session_id": session_id},
    )
    db.commit()
    return {"cleared_count": len(rows), "active": False, "items": [serialize_kill_switch(row) for row in rows]}


def get_live_kill_switch_state(db: Session, *, current_user: Any, strategy_id: str | None = None) -> dict[str, Any]:
    assert_live_reader(current_user)
    tenant, _user = resolve_tenant_and_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id) if strategy_id else None
    rows = active_live_kill_switches(db, tenant_id=tenant.id, strategy_id=strategy.id if strategy else None)
    return {"active": bool(rows), "items": [serialize_kill_switch(row) for row in rows], "count": len(rows)}


def list_live_risk_events(db: Session, *, current_user: Any, limit: int = 100) -> dict[str, Any]:
    assert_live_reader(current_user)
    tenant, _user = resolve_tenant_and_user(db, current_user)
    rows = db.execute(
        select(RiskEvent)
        .where(RiskEvent.tenant_id == tenant.id, RiskEvent.event_type.like("live.%"))
        .order_by(RiskEvent.created_at.desc())
        .limit(max(1, min(int(limit or 100), 500)))
    ).scalars().all()
    return {
        "items": [
            {
                "id": row.id,
                "strategy_id": row.strategy_desk_id,
                "event_type": row.event_type,
                "severity": row.severity,
                "breached_rule": row.breached_rule,
                "action_taken": row.action_taken,
                "payload": dict(row.payload_json or {}),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "count": len(rows),
    }
