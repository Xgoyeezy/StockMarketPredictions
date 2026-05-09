from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope, LiveOrderApprovalRequest, LiveOrderRejectRequest, LiveRiskCheckRequest
from backend.services.live_order_intent_service import approve_live_order, get_live_order, list_live_orders, reject_live_order
from backend.services.live_pretrade_risk_service import run_live_pretrade_risk_check

router = APIRouter(prefix="/live/orders", tags=["live-orders"])


@router.get("", response_model=ApiEnvelope)
def get_live_orders(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(list_live_orders(db, current_user=current_user, status=status, limit=limit))


@router.get("/{order_intent_id}", response_model=ApiEnvelope)
def get_live_order_detail(
    order_intent_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_live_order(db, current_user=current_user, order_intent_id=order_intent_id))


@router.post("/{order_intent_id}/risk-check", response_model=ApiEnvelope)
def post_live_order_risk_check(
    order_intent_id: str,
    request: LiveRiskCheckRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    payload = request or LiveRiskCheckRequest()
    return envelope(run_live_pretrade_risk_check(db, current_user=current_user, order_intent_id=order_intent_id, force_refresh=payload.force_refresh))


@router.post("/{order_intent_id}/approve", response_model=ApiEnvelope)
def post_live_order_approve(
    order_intent_id: str,
    request: LiveOrderApprovalRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(approve_live_order(db, current_user=current_user, order_intent_id=order_intent_id, request=request or LiveOrderApprovalRequest()))


@router.post("/{order_intent_id}/reject", response_model=ApiEnvelope)
def post_live_order_reject(
    order_intent_id: str,
    request: LiveOrderRejectRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(reject_live_order(db, current_user=current_user, order_intent_id=order_intent_id, request=request))
