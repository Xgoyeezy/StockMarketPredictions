from __future__ import annotations

from backend.core.auth import CurrentUser
from backend.core.config import settings
from backend.services.auth_provider_service import get_auth_provider_config
from backend.services.exceptions import UnauthorizedError
from backend.services.permissions import permission_map_from_permissions, resolve_user_permissions


def get_auth_config() -> dict:
    return get_auth_provider_config()


def get_session_payload() -> dict:
    if settings.auth_enabled:
        raise UnauthorizedError("Authentication is enabled, but no production auth adapter is configured.")
    if not settings.allow_demo_auth:
        raise UnauthorizedError("Demo authentication is disabled for this environment.")
    permissions = resolve_user_permissions(
        membership_role="owner",
        platform_role="admin",
        mode="demo",
    )
    current_user = CurrentUser(
        user_id=settings.demo_user_id,
        auth_subject=settings.demo_user_id,
        email=settings.demo_user_email,
        name=settings.demo_user_name,
        role="admin",
        platform_role="admin",
        provider=settings.auth_provider,
        environment=settings.environment,
        mode="demo",
        tenant_slug=getattr(settings, "demo_tenant_slug", None),
        tenant_name=getattr(settings, "demo_tenant_name", None),
        tenant_plan=getattr(settings, "demo_tenant_plan", None),
        permissions=permissions,
        authenticated=True,
    )
    return build_session_payload(current_user)


def get_unauthenticated_session_payload() -> dict:
    return {
        "authenticated": False,
        "mode": "unauthenticated" if settings.auth_enabled else "disabled",
        "user": None,
        "api_token": None,
        "provider": settings.auth_provider,
        "environment": settings.environment,
        "active_tenant": None,
        "memberships": [],
    }


def build_session_payload(current_user: CurrentUser) -> dict:
    return {
        "authenticated": current_user.authenticated,
        "mode": current_user.mode,
        "user": current_user.to_payload(),
        "api_token": (
            {
                "id": current_user.api_token_id,
                "name": current_user.api_token_name,
                "scopes": list(current_user.api_token_scopes),
            }
            if current_user.api_token_id
            else None
        ),
        "provider": current_user.provider,
        "provider_record_id": current_user.provider_record_id,
        "environment": current_user.environment,
        "active_tenant": (
            {
                "id": current_user.tenant_id,
                "slug": current_user.tenant_slug,
                "name": current_user.tenant_name,
                "status": current_user.tenant_status,
                "plan_key": current_user.tenant_plan,
                "role": current_user.role,
                "logo_url": current_user.tenant_logo_url,
                "billing_email": current_user.tenant_billing_email,
                "brand_settings": current_user.tenant_brand_settings or {},
                "delivery_settings": current_user.tenant_delivery_settings or {},
                "permissions": list(current_user.permissions),
                "permission_map": permission_map_from_permissions(current_user.permissions),
            }
            if current_user.tenant_slug
            else None
        ),
        "memberships": list(current_user.memberships),
    }
