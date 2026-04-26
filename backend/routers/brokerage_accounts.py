from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.config import settings
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope, LinkedBrokerageAccountAutomationUpdateRequest, LinkedBrokerageAccountStartRequest
from backend.services.brokerage_account_service import (
    complete_alpaca_account_link,
    list_linked_brokerage_accounts,
    refresh_linked_brokerage_account_status,
    start_alpaca_account_link,
    unlink_linked_brokerage_account,
    update_linked_brokerage_account_automation_policy,
)

router = APIRouter(prefix="/me/brokerage-accounts", tags=["brokerage-accounts"])


@router.get("", response_model=ApiEnvelope)
def list_accounts(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(list_linked_brokerage_accounts(db=db, current_user=current_user))


@router.post("/alpaca/start", response_model=ApiEnvelope)
def start_alpaca_link(
    payload: LinkedBrokerageAccountStartRequest,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    result = start_alpaca_account_link(
        db=db,
        current_user=current_user,
        environment=payload.environment,
        redirect_path=payload.redirect_path,
        linked_account_id=payload.linked_account_id,
    )
    response.set_cookie(
        key=settings.alpaca_link_state_cookie_name,
        value=result["state_cookie_value"],
        max_age=settings.alpaca_link_state_max_age_seconds,
        httponly=True,
        secure=settings.auth_session_secure,
        samesite="lax",
        path="/",
    )
    return envelope(
        {
            "provider": result["provider"],
            "environment": result["environment"],
            "authorize_url": result["authorize_url"],
            "state": result["state"],
            "redirect_path": result["redirect_path"],
        }
    )


@router.get("/alpaca/callback")
def alpaca_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if error:
        redirect = RedirectResponse(
            url=f"{settings.auth_post_login_redirect_url.rstrip('/')}/settings?brokerage_provider=alpaca&brokerage_status=error&brokerage_error={error}",
            status_code=302,
        )
        redirect.delete_cookie(settings.alpaca_link_state_cookie_name, path="/", samesite="lax")
        return redirect
    if not code or not state:
        redirect = RedirectResponse(
            url=f"{settings.auth_post_login_redirect_url.rstrip('/')}/settings?brokerage_provider=alpaca&brokerage_status=error&brokerage_error=missing_callback_params",
            status_code=302,
        )
        redirect.delete_cookie(settings.alpaca_link_state_cookie_name, path="/", samesite="lax")
        return redirect

    result = complete_alpaca_account_link(db=db, request=request, code=code, state=state)
    redirect = RedirectResponse(url=result["redirect_url"], status_code=302)
    redirect.delete_cookie(settings.alpaca_link_state_cookie_name, path="/", samesite="lax")
    return redirect


@router.post("/{linked_account_id}/refresh", response_model=ApiEnvelope)
def refresh_account(
    linked_account_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        refresh_linked_brokerage_account_status(
            db=db,
            current_user=current_user,
            linked_account_id=linked_account_id,
        )
    )


@router.patch("/{linked_account_id}", response_model=ApiEnvelope)
def update_account(
    linked_account_id: str,
    payload: LinkedBrokerageAccountAutomationUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        update_linked_brokerage_account_automation_policy(
            db=db,
            current_user=current_user,
            linked_account_id=linked_account_id,
            updates=payload.model_dump(exclude_unset=True),
        )
    )


@router.post("/{linked_account_id}/unlink", response_model=ApiEnvelope)
def unlink_account(
    linked_account_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        unlink_linked_brokerage_account(
            db=db,
            current_user=current_user,
            linked_account_id=linked_account_id,
        )
    )
