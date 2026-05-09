from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.saas import DecisionReplayEvent, LiveOrderIntent, TradeDecision
from backend.services.billing_service import increment_entitlement_usage, require_entitlement
from backend.services.broker_execution_receipt_service import record_not_submitted_receipt
from backend.services.exceptions import ValidationError
from backend.services.live_pretrade_risk_service import run_live_pretrade_risk_check
from backend.services.live_trading_common import (
    LIVE_SESSION_ACTIVE_STATUSES,
    as_float,
    assert_live_approver,
    assert_live_manager,
    assert_live_reader,
    current_readiness,
    latest_live_session,
    load_live_order_or_raise,
    load_live_session_or_raise,
    record_live_evidence,
    resolve_tenant_and_user,
    serialize_order_intent,
    utc_now,
)
from backend.services.strategy_readiness_score_service import load_strategy_or_raise


def _payload(request: Any) -> dict[str, Any]:
    return request.model_dump() if hasattr(request, "model_dump") else dict(request or {})


def _create_trade_decision(
    db: Session,
    *,
    tenant_id: str,
    strategy_id: str,
    data: dict[str, Any],
) -> TradeDecision:
    decision = TradeDecision(
        tenant_id=tenant_id,
        strategy_desk_id=strategy_id,
        symbol=str(data.get("symbol") or "").strip().upper(),
        instrument_type=str(data.get("instrument_type") or "equity"),
        side=str(data.get("side") or "buy"),
        quantity=float(data.get("quantity") or 0),
        confidence_score=float(data.get("confidence_score") or 0),
        decision_status="recorded",
        decision_reason=data.get("decision_reason") or "Live order intent captured for supervised approval.",
        signal_snapshot_json=dict(data.get("signal_snapshot") or {}),
        risk_snapshot_json=dict(data.get("risk_snapshot") or {}),
        readiness_snapshot_json=dict(data.get("readiness_snapshot") or {}),
        market_snapshot_json=dict(data.get("market_snapshot") or {}),
        broker_snapshot_json=dict(data.get("broker_snapshot") or {}),
        decision_hash=data.get("duplicate_key"),
    )
    db.add(decision)
    db.flush()
    now = utc_now()
    replay_events = [
        ("signal_generated", decision.signal_snapshot_json),
        ("trade_decision_stored", {"trade_decision_id": decision.id, "status": decision.decision_status}),
        ("live_order_intent_requested", {"symbol": decision.symbol, "side": decision.side, "quantity": decision.quantity}),
    ]
    for index, (event_type, payload) in enumerate(replay_events, start=1):
        db.add(
            DecisionReplayEvent(
                tenant_id=tenant_id,
                trade_decision_id=decision.id,
                sequence_number=index,
                event_type=event_type,
                event_time=now,
                payload_json=dict(payload or {}),
            )
        )
    return decision


def create_live_order_intent(db: Session, *, current_user: Any, strategy_id: str, request: Any) -> dict[str, Any]:
    assert_live_manager(current_user)
    require_entitlement(db, current_user, "live_order_approvals", message="Live order intents require live approval entitlement.")
    tenant, user = resolve_tenant_and_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    data = _payload(request)
    session = (
        load_live_session_or_raise(db, tenant_id=tenant.id, session_id=str(data.get("live_trading_session_id")))
        if data.get("live_trading_session_id")
        else latest_live_session(db, tenant_id=tenant.id, strategy_id=strategy.id, statuses=LIVE_SESSION_ACTIVE_STATUSES)
    )
    if session is None:
        raise ValidationError("A live session must be armed before creating live order intents.")
    trade_decision = None
    if data.get("trade_decision_id"):
        trade_decision = db.execute(
            select(TradeDecision).where(TradeDecision.tenant_id == tenant.id, TradeDecision.id == str(data.get("trade_decision_id")))
        ).scalar_one_or_none()
    if trade_decision is None:
        readiness = current_readiness(db, tenant_id=tenant.id, strategy_id=strategy.id) or {}
        data["readiness_snapshot"] = data.get("readiness_snapshot") or readiness
        trade_decision = _create_trade_decision(db, tenant_id=tenant.id, strategy_id=strategy.id, data=data)
    notional = float(data.get("notional_value") or 0)
    if notional <= 0 and data.get("limit_price"):
        notional = float(data.get("limit_price") or 0) * float(data.get("quantity") or 0)
    order_intent = LiveOrderIntent(
        tenant_id=tenant.id,
        live_trading_session_id=session.id,
        strategy_desk_id=strategy.id,
        trade_decision_id=trade_decision.id,
        symbol=str(data.get("symbol") or trade_decision.symbol).strip().upper(),
        instrument_type=str(data.get("instrument_type") or trade_decision.instrument_type),
        side=str(data.get("side") or trade_decision.side),
        quantity=float(data.get("quantity") or trade_decision.quantity or 0),
        order_type=str(data.get("order_type") or "market"),
        limit_price=data.get("limit_price"),
        stop_price=data.get("stop_price"),
        time_in_force=data.get("time_in_force") or "day",
        notional_value=notional,
        status="pending_risk_check",
        requires_user_approval=True,
        duplicate_key=data.get("duplicate_key"),
    )
    db.add(order_intent)
    session.order_count = int(session.order_count or 0) + 1
    db.flush()
    record_live_evidence(
        db,
        tenant=tenant,
        user=user,
        event_type="live.order_intent_created",
        aggregate_type="live_order_intent",
        aggregate_id=order_intent.id,
        payload={"strategy_id": strategy.id, "symbol": order_intent.symbol, "approval_required": True},
    )
    db.commit()
    risk = run_live_pretrade_risk_check(db, current_user=current_user, order_intent_id=order_intent.id, force_refresh=True)
    order_intent = load_live_order_or_raise(db, tenant_id=tenant.id, order_intent_id=order_intent.id)
    return {"order_intent": serialize_order_intent(order_intent), "risk": risk, "next_action": risk.get("next_action")}


def list_live_orders(db: Session, *, current_user: Any, status: str | None = None, limit: int = 100) -> dict[str, Any]:
    assert_live_reader(current_user)
    tenant, _user = resolve_tenant_and_user(db, current_user)
    statement = select(LiveOrderIntent).where(LiveOrderIntent.tenant_id == tenant.id)
    if status:
        statement = statement.where(LiveOrderIntent.status == status)
    rows = db.execute(statement.order_by(LiveOrderIntent.created_at.desc()).limit(max(1, min(int(limit or 100), 500)))).scalars().all()
    return {"items": [serialize_order_intent(row) for row in rows], "count": len(rows)}


def get_live_order(db: Session, *, current_user: Any, order_intent_id: str) -> dict[str, Any]:
    assert_live_reader(current_user)
    tenant, _user = resolve_tenant_and_user(db, current_user)
    row = load_live_order_or_raise(db, tenant_id=tenant.id, order_intent_id=order_intent_id)
    return {"order_intent": serialize_order_intent(row)}


def approve_live_order(db: Session, *, current_user: Any, order_intent_id: str, request: Any | None = None) -> dict[str, Any]:
    assert_live_approver(current_user)
    require_entitlement(db, current_user, "live_order_approvals", message="This plan does not allow live order approvals.")
    tenant, user = resolve_tenant_and_user(db, current_user)
    order_intent = load_live_order_or_raise(db, tenant_id=tenant.id, order_intent_id=order_intent_id)
    latest_risk = sorted(list(order_intent.risk_checks or []), key=lambda item: item.created_at or utc_now(), reverse=True)
    if not latest_risk or latest_risk[0].status != "pass":
        risk = run_live_pretrade_risk_check(db, current_user=current_user, order_intent_id=order_intent.id, force_refresh=False)
        order_intent = load_live_order_or_raise(db, tenant_id=tenant.id, order_intent_id=order_intent.id)
        if not risk.get("allowed"):
            return {"order_intent": serialize_order_intent(order_intent), "risk": risk, "broker_submission_status": "blocked_by_risk", "next_action": "Resolve blockers before approval."}
    data = _payload(request)
    now = utc_now()
    order_intent.status = "approved"
    order_intent.approved_by_user_id = user.id if user else None
    order_intent.approved_at = now
    order_intent.updated_at = now
    increment_entitlement_usage(db, current_user, "live_order_approvals")
    receipt = record_not_submitted_receipt(
        db,
        current_user=current_user,
        order_intent=order_intent,
        reason="Broker submission is queued behind live-provider execution integration and feature gates.",
    )
    record_live_evidence(
        db,
        tenant=tenant,
        user=user,
        event_type="live.order_intent_approved",
        aggregate_type="live_order_intent",
        aggregate_id=order_intent.id,
        payload={"note": data.get("note"), "broker_submission_status": "not_submitted"},
    )
    db.commit()
    return {
        "order_intent": serialize_order_intent(order_intent),
        "receipt": receipt.get("receipt"),
        "broker_submission_status": "not_submitted",
        "next_action": "Broker submission remains disabled until live provider execution is explicitly enabled and integrated.",
    }


def reject_live_order(db: Session, *, current_user: Any, order_intent_id: str, request: Any) -> dict[str, Any]:
    assert_live_approver(current_user)
    tenant, user = resolve_tenant_and_user(db, current_user)
    order_intent = load_live_order_or_raise(db, tenant_id=tenant.id, order_intent_id=order_intent_id)
    data = _payload(request)
    now = utc_now()
    order_intent.status = "rejected"
    order_intent.rejected_at = now
    order_intent.rejection_reason = str(data.get("reason") or "Rejected by operator.")
    order_intent.updated_at = now
    record_live_evidence(
        db,
        tenant=tenant,
        user=user,
        event_type="live.order_intent_rejected",
        aggregate_type="live_order_intent",
        aggregate_id=order_intent.id,
        payload={"reason": order_intent.rejection_reason},
    )
    db.commit()
    return {"order_intent": serialize_order_intent(order_intent), "next_action": "No broker submission will occur for this intent."}
