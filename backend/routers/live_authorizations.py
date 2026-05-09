from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope, LiveAuthorizationCreateRequest, LiveAuthorizationRevokeRequest
from backend.services.live_trading_authorization_service import (
    create_live_authorization,
    get_live_authorization,
    list_live_authorizations,
    revoke_live_authorization,
)

router = APIRouter(prefix="/live/authorizations", tags=["live-authorizations"])


@router.post("", response_model=ApiEnvelope)
def post_live_authorization(
    request: LiveAuthorizationCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(create_live_authorization(db, current_user=current_user, request=request))


@router.get("", response_model=ApiEnvelope)
def get_live_authorizations(
    strategy_id: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(list_live_authorizations(db, current_user=current_user, strategy_id=strategy_id))


@router.get("/{authorization_id}", response_model=ApiEnvelope)
def get_live_authorization_detail(
    authorization_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_live_authorization(db, current_user=current_user, authorization_id=authorization_id))


@router.post("/{authorization_id}/revoke", response_model=ApiEnvelope)
def post_live_authorization_revoke(
    authorization_id: str,
    request: LiveAuthorizationRevokeRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(revoke_live_authorization(db, current_user=current_user, authorization_id=authorization_id, request=request or LiveAuthorizationRevokeRequest()))
