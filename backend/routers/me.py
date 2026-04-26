from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.desk_service import build_desk_summaries
from backend.services.permissions import permission_map_from_permissions

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=ApiEnvelope)
def me(current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(
        {
            "authenticated": current_user.authenticated,
            "mode": current_user.mode,
            "provider": current_user.provider,
            "environment": current_user.environment,
            "user": current_user.to_payload(),
            "active_tenant": {
                "id": current_user.tenant_id,
                "slug": current_user.tenant_slug,
                "name": current_user.tenant_name,
                "status": current_user.tenant_status,
                "plan_key": current_user.tenant_plan,
                "role": current_user.role,
                "permissions": list(current_user.permissions),
                "permission_map": permission_map_from_permissions(current_user.permissions),
            }
            if current_user.tenant_slug
            else None,
            "api_token": (
                {
                    "id": current_user.api_token_id,
                    "name": current_user.api_token_name,
                    "scopes": list(current_user.api_token_scopes),
                }
                if current_user.api_token_id
                else None
            ),
            "memberships": list(current_user.memberships),
        }
    )


@router.get("/me/desk-summaries", response_model=ApiEnvelope)
def desk_summaries(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(build_desk_summaries(db, current_user=current_user))
