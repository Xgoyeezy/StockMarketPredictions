from __future__ import annotations

from typing import Any

from backend.services.exceptions import ForbiddenError


PRODUCT_CONTROL_PERMISSIONS = {
    "strategy.manage",
    "automation.manage",
    "readiness.evaluate",
    "risk.manage",
    "audit.export",
    "execution_analytics.read",
    "live.read",
    "live.manage",
    "live.approve",
}

MEMBERSHIP_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "viewer": {
        "tenant.read",
        "market.read",
    },
    "analyst": {
        "tenant.read",
        "market.read",
        "workspace.write",
    },
    "trader": {
        "tenant.read",
        "market.read",
        "workspace.write",
        "trade.execute",
        "readiness.evaluate",
        "execution_analytics.read",
    },
    "admin": {
        "tenant.read",
        "market.read",
        "workspace.write",
        "trade.execute",
        "tenant.manage_branding",
        "tenant.manage_delivery",
        "tenant.manage_onboarding",
        "tenant.manage_flags",
        "tenant.manage_api_tokens",
        "tenant.manage_webhooks",
        "tenant.manage_support",
        "tenant.manage_billing",
        "tenant.manage_members",
        *PRODUCT_CONTROL_PERMISSIONS,
    },
    "owner": {
        "tenant.read",
        "market.read",
        "workspace.write",
        "trade.execute",
        "tenant.manage_branding",
        "tenant.manage_delivery",
        "tenant.manage_onboarding",
        "tenant.manage_flags",
        "tenant.manage_api_tokens",
        "tenant.manage_webhooks",
        "tenant.manage_support",
        "tenant.manage_billing",
        "tenant.manage_members",
        *PRODUCT_CONTROL_PERMISSIONS,
    },
}

PLATFORM_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "member": {
        "tenant.create",
    },
    "admin": {
        "tenant.create",
        "tenant.change_status",
        "platform.admin",
        "tenant.manage_branding",
        "tenant.manage_delivery",
        "tenant.manage_onboarding",
        "tenant.manage_flags",
        "tenant.manage_api_tokens",
        "tenant.manage_webhooks",
        "tenant.manage_support",
        "tenant.manage_billing",
        "tenant.manage_members",
        *PRODUCT_CONTROL_PERMISSIONS,
    },
}

API_SCOPE_PERMISSIONS: dict[str, set[str]] = {
    "tenant.read": {
        "tenant.read",
    },
    "market.read": {
        "market.read",
    },
    "workspace.write": {
        "workspace.write",
    },
    "tenant.admin": {
        "tenant.read",
        "tenant.manage_branding",
        "tenant.manage_delivery",
        "tenant.manage_onboarding",
        "tenant.manage_flags",
        "tenant.manage_api_tokens",
        "tenant.manage_webhooks",
        "tenant.manage_billing",
        *PRODUCT_CONTROL_PERMISSIONS,
    },
    "strategy.manage": {
        "tenant.read",
        "strategy.manage",
        "readiness.evaluate",
        "execution_analytics.read",
    },
    "risk.manage": {
        "tenant.read",
        "risk.manage",
    },
    "audit.export": {
        "tenant.read",
        "audit.export",
    },
    "execution_analytics.read": {
        "tenant.read",
        "execution_analytics.read",
    },
    "live.read": {
        "tenant.read",
        "live.read",
    },
    "live.manage": {
        "tenant.read",
        "live.read",
        "live.manage",
        "readiness.evaluate",
        "risk.manage",
        "audit.export",
    },
    "live.approve": {
        "tenant.read",
        "live.read",
        "live.approve",
    },
}


def normalize_membership_role(value: str | None) -> str:
    role = str(value or "viewer").strip().lower()
    return role if role in MEMBERSHIP_ROLE_PERMISSIONS else "viewer"


def normalize_platform_role(value: str | None) -> str:
    role = str(value or "member").strip().lower()
    return role if role in PLATFORM_ROLE_PERMISSIONS else "member"


def resolve_user_permissions(
    *,
    membership_role: str | None,
    platform_role: str | None,
    api_token_scopes: tuple[str, ...] | list[str] | None = None,
    mode: str = "demo",
) -> tuple[str, ...]:
    permissions: set[str] = set()
    normalized_mode = str(mode or "demo").strip().lower()

    if normalized_mode == "token":
        for scope in tuple(api_token_scopes or ()):
            permissions.update(API_SCOPE_PERMISSIONS.get(str(scope).strip(), set()))
    else:
        permissions.update(MEMBERSHIP_ROLE_PERMISSIONS.get(normalize_membership_role(membership_role), set()))
        permissions.update(PLATFORM_ROLE_PERMISSIONS.get(normalize_platform_role(platform_role), set()))

    return tuple(sorted(permissions))


def permission_map_from_permissions(permissions: tuple[str, ...] | list[str] | None) -> dict[str, bool]:
    normalized = {str(permission).strip() for permission in tuple(permissions or ()) if str(permission).strip()}
    return {permission: permission in normalized for permission in sorted(normalized)}


def current_user_has_permission(current_user: Any, permission: str) -> bool:
    normalized_permission = str(permission or "").strip()
    if not normalized_permission:
        return False
    explicit_permissions = tuple(getattr(current_user, "permissions", ()) or ())
    if explicit_permissions:
        return normalized_permission in set(explicit_permissions)
    derived_permissions = resolve_user_permissions(
        membership_role=getattr(current_user, "role", None),
        platform_role=getattr(current_user, "platform_role", None),
        api_token_scopes=tuple(getattr(current_user, "api_token_scopes", ()) or ()),
        mode=getattr(current_user, "mode", "demo"),
    )
    return normalized_permission in set(derived_permissions)


def require_current_user_permission(current_user: Any, permission: str, message: str) -> None:
    if current_user_has_permission(current_user, permission):
        return
    raise ForbiddenError(message)
