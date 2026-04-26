from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request, WebSocket
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from backend.core.config import settings
from backend.core.database import SessionLocal, get_db
from backend.services.audit_service import record_audit_event
from backend.services.auth_provider_service import resolve_configured_auth_identity
from backend.services.exceptions import ForbiddenError, TooManyRequestsError, UnauthorizedError
from backend.services.permissions import permission_map_from_permissions, resolve_user_permissions
from backend.services.rate_limit_service import enforce_actor_request_rate_limits, should_emit_throttle_audit
from backend.models.saas import User
from backend.services.tenant_service import (
    authenticate_tenant_api_token,
    build_tenant_payload,
    ensure_demo_tenant_for_user,
    list_user_memberships,
    resolve_tenant_by_slug,
)


def _clean_header_value(value: str | None, fallback: str, *, upper: bool = False) -> str:
    cleaned = str(value or fallback).strip()
    if upper:
        cleaned = cleaned.upper()
    return cleaned or fallback


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    auth_subject: str
    email: str
    name: str
    role: str
    platform_role: str
    provider: str
    environment: str
    mode: str
    tenant_id: str | None = None
    tenant_slug: str | None = None
    tenant_name: str | None = None
    tenant_status: str | None = None
    tenant_plan: str | None = None
    tenant_logo_url: str | None = None
    tenant_brand_settings: dict[str, object] | None = None
    tenant_delivery_settings: dict[str, object] | None = None
    tenant_billing_email: str | None = None
    memberships: tuple[dict[str, object], ...] = ()
    api_token_id: str | None = None
    api_token_name: str | None = None
    api_token_scopes: tuple[str, ...] = ()
    provider_record_id: str | None = None
    permissions: tuple[str, ...] = ()
    authenticated: bool = True

    def to_payload(self) -> dict[str, str | bool]:
        return {
            "id": self.user_id,
            "auth_subject": self.auth_subject,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "platform_role": self.platform_role,
            "permissions": list(self.permissions),
            "permission_map": permission_map_from_permissions(self.permissions),
        }


def build_current_user_from_identity(identity: dict, *, provider: str, mode: str, provider_record_id: str | None = None) -> CurrentUser:
    active_membership = identity["active_membership"]
    active_tenant = identity["active_tenant"]
    user = identity["user"]
    tenant_payload = build_tenant_payload(active_tenant)
    permissions = resolve_user_permissions(
        membership_role=active_membership.role,
        platform_role=user.platform_role,
        mode=mode,
    )
    return CurrentUser(
        user_id=user.auth_subject,
        auth_subject=user.auth_subject,
        email=user.email,
        name=user.name,
        role=active_membership.role,
        platform_role=user.platform_role,
        provider=provider,
        provider_record_id=provider_record_id,
        environment=settings.environment,
        mode=mode,
        tenant_id=active_tenant.id,
        tenant_slug=active_tenant.slug,
        tenant_name=active_tenant.name,
        tenant_status=active_tenant.status,
        tenant_plan=active_tenant.plan_key,
        tenant_logo_url=active_tenant.logo_url,
        tenant_brand_settings=active_tenant.brand_settings or {},
        tenant_delivery_settings=tenant_payload.get("delivery_settings") or {},
        tenant_billing_email=active_tenant.billing_email,
        memberships=identity["memberships_payload"],
        permissions=permissions,
        authenticated=True,
    )


def _build_stateless_demo_user(
    *,
    user_id: str,
    email: str,
    name: str,
    requested_tenant_slug: str,
) -> CurrentUser:
    permissions = resolve_user_permissions(
        membership_role="owner",
        platform_role="admin",
        mode="demo",
    )
    return CurrentUser(
        user_id=user_id,
        auth_subject=user_id,
        email=email,
        name=name,
        role="admin",
        platform_role="admin",
        provider="local-demo",
        environment=settings.environment,
        mode="demo",
        tenant_slug=requested_tenant_slug,
        tenant_name=settings.demo_tenant_name,
        tenant_status="active",
        tenant_plan=settings.demo_tenant_plan,
        tenant_brand_settings={"theme": "dark-trader"},
        tenant_delivery_settings={},
        permissions=permissions,
        authenticated=True,
    )


def _build_readonly_demo_user(
    *,
    db: Session | None,
    user_id: str,
    email: str,
    name: str,
    requested_tenant_slug: str,
) -> CurrentUser:
    if db is None:
        return _build_stateless_demo_user(
            user_id=user_id,
            email=email,
            name=name,
            requested_tenant_slug=requested_tenant_slug,
        )

    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except SQLAlchemyError:
            pass

    lookup_session = SessionLocal()
    try:
        tenant = resolve_tenant_by_slug(lookup_session, tenant_slug=requested_tenant_slug)
        tenant_payload = build_tenant_payload(tenant)
        platform_role = "admin"
        membership_role = "owner"
        memberships_payload: tuple[dict[str, object], ...] = ()

        try:
            user = lookup_session.execute(select(User).where(User.auth_subject == user_id)).scalar_one_or_none()
            if user is not None:
                memberships = list_user_memberships(lookup_session, user)
                active_membership = next(
                    (membership for membership in memberships if membership.tenant_id == tenant.id),
                    memberships[0] if memberships else None,
                )
                if active_membership is not None:
                    membership_role = str(active_membership.role or membership_role)
                memberships_payload = tuple(
                    {
                        "tenant": build_tenant_payload(membership.tenant),
                        "role": membership.role,
                        "status": membership.status,
                        "is_default": membership.is_default,
                        "permissions": list(
                            resolve_user_permissions(
                                membership_role=membership.role,
                                platform_role=platform_role,
                                mode="demo",
                            )
                        ),
                        "permission_map": permission_map_from_permissions(
                            resolve_user_permissions(
                                membership_role=membership.role,
                                platform_role=platform_role,
                                mode="demo",
                            )
                        ),
                    }
                    for membership in memberships
                )
        except SQLAlchemyError:
            inner_rollback = getattr(lookup_session, "rollback", None)
            if callable(inner_rollback):
                try:
                    inner_rollback()
                except SQLAlchemyError:
                    pass

        permissions = resolve_user_permissions(
            membership_role=membership_role,
            platform_role=platform_role,
            mode="demo",
        )
        return CurrentUser(
            user_id=user_id,
            auth_subject=user_id,
            email=email,
            name=name,
            role=membership_role,
            platform_role=platform_role,
            provider="local-demo",
            environment=settings.environment,
            mode="demo",
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            tenant_name=tenant.name,
            tenant_status=tenant.status,
            tenant_plan=tenant.plan_key,
            tenant_logo_url=tenant.logo_url,
            tenant_brand_settings=tenant.brand_settings or {},
            tenant_delivery_settings=tenant_payload.get("delivery_settings") or {},
            tenant_billing_email=tenant.billing_email,
            memberships=memberships_payload,
            permissions=permissions,
            authenticated=True,
        )
    finally:
        lookup_session.close()


def build_demo_user(request: Request | WebSocket | None = None, db: Session | None = None) -> CurrentUser:
    header_lookup = request.headers.get if request is not None else lambda _name, default=None: default
    user_id = _clean_header_value(header_lookup("x-demo-user-id"), settings.demo_user_id)
    email = _clean_header_value(header_lookup("x-demo-user-email"), settings.demo_user_email)
    name = _clean_header_value(header_lookup("x-demo-user-name"), settings.demo_user_name)
    requested_tenant_slug = _clean_header_value(
        header_lookup("x-demo-tenant-slug"),
        settings.demo_tenant_slug,
    )
    if db is not None:
        try:
            identity = ensure_demo_tenant_for_user(
                db,
                auth_subject=user_id,
                email=email,
                name=name,
                provider=settings.auth_provider,
                requested_tenant_slug=requested_tenant_slug,
            )
            return build_current_user_from_identity(identity, provider="local-demo", mode="demo")
        except SQLAlchemyError:
            try:
                return _build_readonly_demo_user(
                    db=db,
                    user_id=user_id,
                    email=email,
                    name=name,
                    requested_tenant_slug=requested_tenant_slug,
                )
            except Exception:
                pass

    return _build_stateless_demo_user(
        user_id=user_id,
        email=email,
        name=name,
        requested_tenant_slug=requested_tenant_slug,
    )


def _extract_api_token(request: Request) -> str | None:
    authorization = str(request.headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token
    for header_name in ("x-api-token", "x-tenant-token"):
        value = str(request.headers.get(header_name) or "").strip()
        if value:
            return value
    return None


def _required_scopes_for_request(request: Request) -> tuple[str, ...]:
    path = request.url.path
    method = request.method.upper()
    if path.startswith("/api/market") or path.startswith("/api/portfolio") or path.startswith("/api/trades"):
        return ("market.read",)
    if path.startswith("/api/frontend/workspaces"):
        if method in {"POST", "PUT", "DELETE"} or path.endswith("/duplicate") or path.endswith("/import"):
            return ("workspace.write",)
        return ("tenant.read",)
    if path.startswith("/api/orgs") or path.startswith("/api/billing"):
        if method == "GET" and path not in {"/api/orgs/tokens", "/api/orgs/api-usage"}:
            return ("tenant.read",)
        return ("tenant.admin",)
    if path.startswith("/api/auth") or path == "/api/me":
        return ("tenant.read",)
    return ("tenant.read",)


def _authorize_api_token_request(request: Request, scopes: tuple[str, ...]) -> None:
    required = _required_scopes_for_request(request)
    missing = [scope for scope in required if scope not in scopes]
    if missing:
        raise ForbiddenError(f"API token is missing required scope: {', '.join(missing)}")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    api_token = _extract_api_token(request)
    if api_token:
        identity = authenticate_tenant_api_token(db, raw_token=api_token)
        tenant = identity["tenant"]
        tenant_payload = identity["tenant_payload"]
        current_user = CurrentUser(
            user_id=identity["token_id"],
            auth_subject=identity["token_id"],
            email=f"{tenant.slug}+api-token@stocksignals.local",
            name=identity["token_name"],
            role=identity["role"],
            platform_role="member",
            provider="tenant-api-token",
            environment=settings.environment,
            mode="token",
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            tenant_name=tenant.name,
            tenant_status=tenant.status,
            tenant_plan=tenant.plan_key,
            tenant_logo_url=tenant.logo_url,
            tenant_brand_settings=tenant.brand_settings or {},
            tenant_delivery_settings=tenant_payload.get("delivery_settings") or {},
            tenant_billing_email=tenant.billing_email,
            memberships=tuple(identity.get("memberships") or ()),
            api_token_id=identity["token_id"],
            api_token_name=identity["token_name"],
            api_token_scopes=tuple(identity.get("scopes") or ()),
            permissions=resolve_user_permissions(
                membership_role=identity.get("role"),
                platform_role="member",
                api_token_scopes=tuple(identity.get("scopes") or ()),
                mode="token",
            ),
            authenticated=True,
        )
        _authorize_api_token_request(request, current_user.api_token_scopes)
        try:
            request.state.actor_rate_limits = enforce_actor_request_rate_limits(request, current_user)
        except TooManyRequestsError as exc:
            event_key = f"{tenant.slug}:{identity['token_id']}:{exc.details.get('policy_key')}"
            if should_emit_throttle_audit(event_key):
                record_audit_event(
                    db,
                    event_type="security.rate_limited",
                    tenant=tenant,
                    payload={
                        "actor_kind": "token",
                        "actor_id": identity["token_id"],
                        "token_name": identity["token_name"],
                        "policy_key": exc.details.get("policy_key"),
                        "path": exc.details.get("path"),
                        "method": exc.details.get("method"),
                        "retry_after_seconds": exc.details.get("retry_after_seconds"),
                    },
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                )
                db.commit()
            raise
        request.state.current_user = current_user
        return current_user
    # Authentication is intentionally bypassed while demo auth is allowed so the
    # full app boots into a local operator session without any sign-in gate.
    if settings.allow_demo_auth:
        try:
            current_user = build_demo_user(request, db)
        except SQLAlchemyError:
            current_user = build_demo_user(request, None)
        request.state.actor_rate_limits = enforce_actor_request_rate_limits(request, current_user)
        request.state.current_user = current_user
        return current_user
    if settings.auth_enabled:
        identity = resolve_configured_auth_identity(request, db)
        current_user = build_current_user_from_identity(
            identity["identity"],
            provider=identity["provider"],
            provider_record_id=identity.get("provider_record_id"),
            mode=identity["mode"],
        )
        try:
            request.state.actor_rate_limits = enforce_actor_request_rate_limits(request, current_user)
        except TooManyRequestsError as exc:
            active_tenant = identity["identity"]["active_tenant"]
            user = identity["identity"]["user"]
            event_key = f"{active_tenant.slug}:{user.auth_subject}:{exc.details.get('policy_key')}"
            if should_emit_throttle_audit(event_key):
                record_audit_event(
                    db,
                    event_type="security.rate_limited",
                    tenant=active_tenant,
                    user=user,
                    payload={
                        "actor_kind": "user",
                        "actor_id": user.auth_subject,
                        "policy_key": exc.details.get("policy_key"),
                        "path": exc.details.get("path"),
                        "method": exc.details.get("method"),
                        "retry_after_seconds": exc.details.get("retry_after_seconds"),
                    },
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                )
                db.commit()
            raise
        request.state.current_user = current_user
        return current_user
    raise UnauthorizedError("Authentication is disabled and demo auth is unavailable.")


def get_optional_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser | None:
    try:
        return get_current_user(request, db)
    except UnauthorizedError:
        return None
    except SQLAlchemyError:
        if settings.auth_enabled or not settings.allow_demo_auth:
            return None
        return build_demo_user(request, None)
