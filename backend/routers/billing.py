from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope, BillingCheckoutRequest, BillingPlanChangeRequest, BillingRecoveryRequest
from backend.services.billing_service import (
    change_tenant_plan,
    create_billing_checkout_session,
    create_billing_portal_preview,
    get_billing_entitlements,
    get_billing_summary,
    handle_billing_webhook_request,
    list_billing_plans,
    queue_billing_recovery_action,
)

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans", response_model=ApiEnvelope)
def billing_plans() -> ApiEnvelope:
    return envelope(list_billing_plans())


@router.get("/summary", response_model=ApiEnvelope)
def billing_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_billing_summary(db, current_user))


@router.get("/entitlements", response_model=ApiEnvelope)
def billing_entitlements(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_billing_entitlements(db, current_user))


@router.post("/change-plan", response_model=ApiEnvelope)
def billing_change_plan(
    payload: BillingPlanChangeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(change_tenant_plan(db, current_user, payload.plan_key))


@router.post("/checkout", response_model=ApiEnvelope)
def billing_checkout(
    payload: BillingCheckoutRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        create_billing_checkout_session(
            db,
            current_user,
            plan_key=payload.plan_key,
            billing_cycle=payload.billing_cycle,
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
        )
    )


@router.post("/portal", response_model=ApiEnvelope)
def billing_portal(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(create_billing_portal_preview(db, current_user))


@router.post("/recover", response_model=ApiEnvelope)
def billing_recover(
    payload: BillingRecoveryRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(queue_billing_recovery_action(db, current_user, action=payload.action))


@router.post("/webhook", response_model=ApiEnvelope)
async def billing_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    body = await request.body()
    return envelope(handle_billing_webhook_request(db, body, stripe_signature))
