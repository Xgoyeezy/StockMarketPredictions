from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.models.saas import BrokerExecutionReceipt, ExecutionQualitySnapshot, LiveOrderIntent
from backend.services.live_trading_common import (
    as_float,
    provider_live_gate,
    record_live_evidence,
    resolve_tenant_and_user,
    serialize_receipt,
    utc_now,
)


def record_broker_execution_receipt(
    db: Session,
    *,
    current_user: Any,
    order_intent: LiveOrderIntent,
    status: str,
    submitted_payload: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tenant, user = resolve_tenant_and_user(db, current_user)
    linked_account = order_intent.live_trading_session.linked_account if order_intent.live_trading_session else None
    provider_gate = provider_live_gate(linked_account)
    receipt = BrokerExecutionReceipt(
        tenant_id=tenant.id,
        live_order_intent_id=order_intent.id,
        broker=provider_gate["provider"],
        broker_account_id=getattr(linked_account, "external_account_id", None),
        broker_order_id=(response_payload or {}).get("broker_order_id"),
        status=status,
        submitted_payload_json=dict(submitted_payload or {}),
        response_payload_json=dict(response_payload or {}),
    )
    db.add(receipt)
    db.flush()
    db.add(
        ExecutionQualitySnapshot(
            tenant_id=tenant.id,
            strategy_desk_id=order_intent.strategy_desk_id,
            trade_id=order_intent.trade_decision_id,
            symbol=order_intent.symbol,
            broker=provider_gate["provider"],
            route_state=status,
            expected_price=order_intent.limit_price,
            submitted_price=order_intent.limit_price,
            filled_price=None,
            latency_ms=None,
            execution_score=0.0 if status.startswith("not_submitted") else None,
            payload_json={
                "live_order_intent_id": order_intent.id,
                "receipt_id": receipt.id,
                "status": status,
                "provider_gate": provider_gate,
            },
        )
    )
    record_live_evidence(
        db,
        tenant=tenant,
        user=user,
        event_type="live.broker_receipt_recorded",
        aggregate_type="broker_execution_receipt",
        aggregate_id=receipt.id,
        payload={"order_intent_id": order_intent.id, "status": status, "provider": provider_gate["provider"]},
    )
    return {"receipt": serialize_receipt(receipt)}


def record_not_submitted_receipt(
    db: Session,
    *,
    current_user: Any,
    order_intent: LiveOrderIntent,
    reason: str,
) -> dict[str, Any]:
    return record_broker_execution_receipt(
        db,
        current_user=current_user,
        order_intent=order_intent,
        status="not_submitted",
        submitted_payload={
            "order_intent_id": order_intent.id,
            "symbol": order_intent.symbol,
            "side": order_intent.side,
            "quantity": as_float(order_intent.quantity),
            "reason": reason,
            "captured_at": utc_now().isoformat(),
        },
        response_payload={"blocked_reason": reason},
    )
