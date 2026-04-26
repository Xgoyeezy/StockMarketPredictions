from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
import re
import secrets
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from backend.core.config import settings
from backend.core.database import SessionLocal
from backend.models.saas import Tenant, TenantInvitation, TenantMembership, User
from backend.services.audit_service import list_audit_events_for_tenant, record_audit_event
from backend.services.billing_service import (
    ENTITLEMENT_META,
    enforce_entitlement_limit,
    ensure_subscription_record,
    get_billing_summary,
    get_plan_definition,
    require_entitlement,
    resolve_tenant_entitlements,
    sync_tenant_plan_entitlements,
)
from backend.services.desk_service import SYSTEMATIC_DESK_SLUG, default_desk_definitions, normalize_desk_slug, resolve_desk_name
from backend.services.exceptions import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError, ValidationError
from backend.services.permissions import current_user_has_permission, require_current_user_permission
from backend.services.workspace_service import list_workspaces, save_workspace

_SLUG_PATTERN = re.compile(r"[^a-z0-9-]+")
_DOMAIN_PATTERN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
_ONBOARDING_STEP_KEYS = {"branding", "billing", "starter_workspace", "support_channels", "launch_review"}
_DEFAULT_ONBOARDING_TICKERS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD"]
_ONBOARDING_TEMPLATE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "key": "pilot_launchpad",
        "name": "Personal Launchpad",
        "description": "Chart-first opening workspace with watchlist, scanner, and personal trading defaults.",
        "page": "dashboard",
        "lane": "stable",
        "tags": ["onboarding", "template", "launchpad"],
        "notes": "Seeded from the onboarding template library for personal trading setup.",
        "payload": {
            "defaultTicker": "SPY",
            "defaultInterval": "5m",
            "watchlistTickers": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD"],
            "source": "onboarding-template",
            "templateKey": "pilot_launchpad",
        },
    },
    {
        "key": "ops_command_center",
        "name": "Ops Command Center",
        "description": "Support-friendly tenant workspace focused on alerts, activity, and operational readiness checks.",
        "page": "activity",
        "lane": "stable",
        "tags": ["onboarding", "template", "ops"],
        "notes": "Use this for launch-day monitoring and support triage.",
        "payload": {
            "defaultTicker": "SPY",
            "defaultInterval": "15m",
            "watchlistTickers": ["SPY", "QQQ", "IWM", "DIA"],
            "source": "onboarding-template",
            "templateKey": "ops_command_center",
        },
    },
    {
        "key": "risk_review_board",
        "name": "Risk Review Board",
        "description": "Focused layout for portfolio, journal, and trade review before using a strategy live.",
        "page": "portfolio",
        "lane": "stable",
        "tags": ["onboarding", "template", "risk"],
        "notes": "Use this before increasing real-money exposure.",
        "payload": {
            "defaultTicker": "SPY",
            "defaultInterval": "1h",
            "watchlistTickers": ["SPY", "QQQ", "AAPL", "MSFT"],
            "source": "onboarding-template",
            "templateKey": "risk_review_board",
        },
    },
    {
        "key": "partner_beta_lab",
        "name": "Personal Research Lab",
        "description": "Sandbox workspace for compare, release-lane review, and pre-live experimentation.",
        "page": "compare",
        "lane": "beta",
        "tags": ["onboarding", "template", "beta"],
        "notes": "Reserved for personal research experiments with release-channel access.",
        "payload": {
            "defaultTicker": "QQQ",
            "defaultInterval": "5m",
            "watchlistTickers": ["QQQ", "SPY", "NVDA", "AMD", "TSLA"],
            "source": "onboarding-template",
            "templateKey": "partner_beta_lab",
        },
    },
)
_ADDITIONAL_FEATURE_FLAG_KEYS = (
    "custom_domains",
    "branded_email",
    "onboarding_templates",
    "release_channels",
    "partner_webhooks",
)
_AUTH_PROVIDER_KEYS = ("local-session", "auth0", "oidc")
_EXTERNAL_AUTH_PROVIDER_KEYS = ("auth0", "oidc")
_AUTH_PROVIDER_RECORD_LABELS = {
    "auth0": "Auth0 organization",
    "oidc": "Enterprise OIDC",
}
_AUTH_PROVIDER_CONFIG_KEYS = (
    "provider_key",
    "label",
    "enabled",
    "email_domains",
    "organization_hint",
    "connection_hint",
    "auth0_domain",
    "issuer",
    "authorize_url",
    "token_url",
    "userinfo_url",
    "logout_url",
    "client_id",
    "client_secret",
    "audience",
    "scope",
    "allow_signup",
    "is_default",
)
_AUTH_PROVIDER_HEALTH_KEYS = (
    "health_status",
    "health_message",
    "last_checked_at",
    "discovery_source",
    "resolved_authorize_url",
    "resolved_token_url",
    "resolved_userinfo_url",
    "resolved_logout_url",
)
_AUTH_PROVIDER_PENDING_HEALTH_KEYS = (
    "pending_health_status",
    "pending_health_message",
    "pending_last_checked_at",
    "pending_discovery_source",
    "pending_resolved_authorize_url",
    "pending_resolved_token_url",
    "pending_resolved_userinfo_url",
    "pending_resolved_logout_url",
)
_AUTH_PROVIDER_HISTORY_LIMIT = 8
_EMAIL_PROVIDER_LABELS = {
    "none": "Not configured",
    "resend": "Resend",
    "postmark": "Postmark",
    "sendgrid": "SendGrid",
    "ses": "Amazon SES",
    "custom-smtp": "Custom SMTP",
}
_API_TOKEN_SCOPE_META: dict[str, dict[str, str]] = {
    "tenant.read": {"label": "Tenant read", "description": "Read tenant settings, onboarding, and support state."},
    "market.read": {"label": "Market read", "description": "Call dashboard, chart, scan, compare, and watchlist APIs."},
    "workspace.write": {"label": "Workspace write", "description": "Create, import, duplicate, and update saved workspaces."},
    "tenant.admin": {"label": "Tenant admin", "description": "Manage branding, rollout, onboarding, and delivery controls."},
}
_DEFAULT_API_TOKEN_SCOPES = ("tenant.read", "market.read")
_INVITATION_EXPIRY_DAYS = 14
_PARTNER_WEBHOOK_EVENT_CATALOG: tuple[dict[str, str], ...] = (
    {"key": "tenant.launch_ready", "label": "Tenant launch ready", "description": "Onboarding and rollout checklist crossed the launch-ready threshold."},
    {"key": "workspace.saved", "label": "Workspace saved", "description": "A tenant-scoped workspace was created, duplicated, or updated."},
    {"key": "market.signal_ready", "label": "Market signal ready", "description": "A chart, dashboard, or scan surface produced a ready-to-review signal payload."},
    {"key": "billing.plan_changed", "label": "Billing plan changed", "description": "The tenant plan or subscription state changed."},
)
_SECURITY_AUDIT_EVENT_PREFIXES = (
    "security.",
    "tenant.api_token_",
    "tenant.partner_webhook_",
    "tenant.delivery_",
    "tenant.launch_",
)
_SECURITY_AUDIT_EVENT_TYPES = {
    "tenant.status_updated",
}
_MANAGED_FEATURE_FLAG_KEYS = {
    "workspace_count",
    "saved_layouts",
    "organization_members",
    "realtime_streaming",
    "advanced_indicators",
    "tenant_branding",
    "api_access",
    "broker_execution",
    "priority_support",
    *_ADDITIONAL_FEATURE_FLAG_KEYS,
}


def slugify_tenant_name(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    cleaned = _SLUG_PATTERN.sub("-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned[:80] or "tenant"


def _membership_payload(membership: TenantMembership) -> dict[str, Any]:
    tenant = membership.tenant
    return {
        "membership_id": membership.id,
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
            "logo_url": tenant.logo_url,
            "brand_settings": tenant.brand_settings or {},
        },
        "role": membership.role,
        "status": membership.status,
        "is_default": membership.is_default,
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _refresh_tenant_metadata(tenant: Tenant) -> None:
    try:
        tenant_state = sa_inspect(tenant)
    except Exception:
        tenant_state = None
    if tenant_state is not None and tenant_state.persistent and tenant_state.session is not None:
        metadata_attr = tenant_state.attrs.metadata_json
        if not metadata_attr.history.has_changes():
            tenant_state.session.refresh(tenant, attribute_names=["metadata_json"])


def _copy_tenant_metadata(tenant: Tenant) -> dict[str, Any]:
    _refresh_tenant_metadata(tenant)
    return dict(tenant.metadata_json or {})


def _normalize_invitation_email(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    if not cleaned or "@" not in cleaned:
        raise ValidationError("Invitation email must be a valid email address.")
    return cleaned


def _normalize_member_role(value: str | None) -> str:
    role = str(value or "").strip().lower()
    if role not in {"viewer", "analyst", "trader", "admin", "owner"}:
        raise ValidationError("Unsupported membership role.")
    return role


def _generate_invitation_token(tenant: Tenant) -> str:
    return f"invite_{tenant.slug}_{secrets.token_urlsafe(18)}"


def _is_invitation_expired(invitation: TenantInvitation) -> bool:
    if not invitation.expires_at:
        return False
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= _utc_now()


def _sync_invitation_status(invitation: TenantInvitation) -> None:
    if invitation.status == "pending" and _is_invitation_expired(invitation):
        invitation.status = "expired"


def _serialize_invitation(invitation: TenantInvitation) -> dict[str, Any]:
    _sync_invitation_status(invitation)
    return {
        "id": invitation.id,
        "email": invitation.email,
        "name": invitation.name,
        "role": invitation.role,
        "status": invitation.status,
        "invite_token": invitation.invite_token,
        "message": invitation.message,
        "created_at": invitation.created_at.isoformat() if invitation.created_at else None,
        "updated_at": invitation.updated_at.isoformat() if invitation.updated_at else None,
        "expires_at": invitation.expires_at.isoformat() if invitation.expires_at else None,
        "accepted_at": invitation.accepted_at.isoformat() if invitation.accepted_at else None,
        "cancelled_at": invitation.cancelled_at.isoformat() if invitation.cancelled_at else None,
        "inviter_name": invitation.inviter_user.name if invitation.inviter_user else None,
        "inviter_email": invitation.inviter_user.email if invitation.inviter_user else None,
        "is_expired": invitation.status == "expired",
        "can_cancel": invitation.status == "pending",
        "can_resend": invitation.status in {"pending", "expired", "cancelled"},
    }


def resolve_tenant_invitation_by_token(db: Session, *, invite_token: str) -> TenantInvitation:
    normalized_token = str(invite_token or "").strip()
    if not normalized_token:
        raise ValidationError("Invitation token is required.")
    invitation = db.execute(
        select(TenantInvitation)
        .where(TenantInvitation.invite_token == normalized_token)
        .options(
            selectinload(TenantInvitation.tenant),
            selectinload(TenantInvitation.inviter_user),
        )
    ).scalar_one_or_none()
    if invitation is None:
        raise NotFoundError("The invitation could not be found.")
    _sync_invitation_status(invitation)
    return invitation


def _accept_tenant_invitation(db: Session, *, invitation: TenantInvitation, user: User, accepted_via: str) -> bool:
    _sync_invitation_status(invitation)
    if invitation.status != "pending":
        return False

    normalized_email = _normalize_invitation_email(user.email)
    if normalized_email != invitation.email:
        raise UnauthorizedError("This invitation does not match the signed-in email address.")

    existing_memberships = {
        membership.tenant_id: membership
        for membership in list(
            db.execute(select(TenantMembership).where(TenantMembership.user_id == user.id)).scalars()
        )
    }
    membership = existing_memberships.get(invitation.tenant_id)
    if membership is None:
        membership = TenantMembership(
            tenant_id=invitation.tenant_id,
            user_id=user.id,
            role=invitation.role,
            status="active",
            is_default=not bool(existing_memberships),
        )
        db.add(membership)
        db.flush()
        existing_memberships[invitation.tenant_id] = membership
        if not membership.is_default:
            _normalize_default_memberships(list(existing_memberships.values()))

    invitation.status = "accepted"
    invitation.accepted_at = _utc_now()
    invitation.cancelled_at = None
    invitation.metadata_json = {
        **dict(invitation.metadata_json or {}),
        "accepted_by_auth_subject": user.auth_subject,
        "accepted_via": accepted_via,
    }
    record_audit_event(
        db,
        event_type="tenant.invitation_accepted",
        tenant=invitation.tenant,
        user=user,
        payload={
            "invitation_id": invitation.id,
            "email": invitation.email,
            "role": invitation.role,
            "accepted_via": accepted_via,
        },
    )
    return True


def build_tenant_payload(tenant: Tenant) -> dict[str, Any]:
    subscription = next(iter(tenant.subscriptions), None)
    entitlements = {
        entitlement["key"]: {
            "enabled": entitlement["enabled"],
            "limit": entitlement["limit"],
            "source": entitlement["source"],
        }
        for entitlement in resolve_tenant_entitlements(tenant)["items"]
    }
    return {
        "id": tenant.id,
        "slug": tenant.slug,
        "name": tenant.name,
        "status": tenant.status,
        "plan_key": tenant.plan_key,
        "billing_email": tenant.billing_email,
        "logo_url": tenant.logo_url,
        "brand_settings": tenant.brand_settings or {},
        "delivery_settings": _build_delivery_snapshot_from_tenant(tenant),
        "feature_overrides": tenant.feature_overrides or {},
        "subscription": (
            {
                "provider": subscription.provider,
                "status": subscription.status,
                "plan_key": subscription.plan_key,
                "current_period_end": subscription.current_period_end.isoformat()
                if subscription.current_period_end
                else None,
            }
            if subscription
            else None
        ),
        "entitlements": entitlements,
    }


def _seed_default_entitlements(db: Session, tenant: Tenant) -> None:
    sync_tenant_plan_entitlements(db, tenant)


def _ensure_subscription(db: Session, tenant: Tenant) -> None:
    ensure_subscription_record(db, tenant)


def ensure_user(
    db: Session,
    *,
    auth_subject: str,
    email: str,
    name: str,
    provider: str,
    platform_role: str = "member",
) -> User:
    statement = select(User).where(User.auth_subject == auth_subject)
    user = db.execute(statement).scalar_one_or_none()
    if user is None:
        user = User(
            auth_subject=auth_subject,
            email=email,
            name=name,
            provider=provider,
            platform_role=platform_role,
        )
        db.add(user)
        db.flush()
        claim_pending_tenant_invitations_for_user(db, user=user)
        return user

    changed = False
    next_platform_role = platform_role or user.platform_role
    if user.email != email:
        user.email = email
        changed = True
    if user.name != name:
        user.name = name
        changed = True
    if user.provider != provider:
        user.provider = provider
        changed = True
    if user.platform_role != next_platform_role:
        user.platform_role = next_platform_role
        changed = True
    if not user.is_active:
        user.is_active = True
        changed = True
    if changed:
        db.flush()
    claim_pending_tenant_invitations_for_user(db, user=user)
    return user


def claim_pending_tenant_invitations_for_user(db: Session, *, user: User) -> int:
    email = _normalize_invitation_email(user.email)
    invitations = list(
        db.execute(
            select(TenantInvitation)
            .where(TenantInvitation.email == email)
            .where(TenantInvitation.status == "pending")
            .options(
                selectinload(TenantInvitation.tenant),
                selectinload(TenantInvitation.inviter_user),
            )
        ).scalars()
    )
    if not invitations:
        return 0

    claimed = 0
    existing_memberships = {
        membership.tenant_id: membership
        for membership in list(
            db.execute(select(TenantMembership).where(TenantMembership.user_id == user.id)).scalars()
        )
    }
    for invitation in invitations:
        if _accept_tenant_invitation(db, invitation=invitation, user=user, accepted_via="email-match"):
            claimed += 1

    if claimed:
        db.flush()
    return claimed


def claim_tenant_invitation_by_token(db: Session, *, user: User, invite_token: str) -> TenantInvitation:
    invitation = resolve_tenant_invitation_by_token(db, invite_token=invite_token)
    if invitation.status == "accepted":
        accepted_by = str((invitation.metadata_json or {}).get("accepted_by_auth_subject") or "").strip()
        if accepted_by == user.auth_subject:
            return invitation
        raise ValidationError("This invitation has already been accepted.")
    if invitation.status != "pending":
        raise ValidationError("This invitation is no longer pending.")
    if _is_invitation_expired(invitation):
        invitation.status = "expired"
        db.flush()
        raise ValidationError("This invitation has expired.")
    _accept_tenant_invitation(db, invitation=invitation, user=user, accepted_via="invite-token")
    db.flush()
    return invitation


def create_tenant(
    db: Session,
    *,
    owner: User,
    name: str,
    slug: str | None = None,
    plan_key: str = "starter",
    billing_email: str | None = None,
) -> dict[str, Any]:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValidationError("Tenant name is required.")

    slug_value = slugify_tenant_name(slug or clean_name)
    if db.execute(select(Tenant).where(Tenant.slug == slug_value)).scalar_one_or_none():
        raise ConflictError("That tenant slug is already in use.")

    tenant = Tenant(
        slug=slug_value,
        name=clean_name[:160],
        status="active",
        plan_key=str(plan_key or "starter").strip().lower() or "starter",
        billing_email=str(billing_email or owner.email).strip() or owner.email,
    )
    membership = TenantMembership(
        tenant=tenant,
        user=owner,
        role="owner",
        status="active",
        is_default=not bool(list_user_memberships(db, owner)),
    )
    db.add_all([tenant, membership])
    db.flush()
    _ensure_subscription(db, tenant)
    _seed_default_entitlements(db, tenant)
    record_audit_event(db, event_type="tenant.created", tenant=tenant, user=owner, payload={"slug": tenant.slug, "plan_key": tenant.plan_key})
    db.commit()
    db.refresh(tenant)
    return build_tenant_payload(tenant)


def resolve_tenant_by_slug(db: Session, *, tenant_slug: str) -> Tenant:
    normalized_slug = normalize_desk_slug(tenant_slug)
    tenant = db.execute(select(Tenant).where(Tenant.slug == normalized_slug)).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("The requested tenant could not be found.")
    return tenant


def list_user_memberships(db: Session, user: User) -> list[TenantMembership]:
    statement = (
        select(TenantMembership)
        .where(TenantMembership.user_id == user.id)
        .order_by(TenantMembership.is_default.desc(), TenantMembership.created_at.asc())
    )
    return list(db.execute(statement).scalars())


def _normalize_default_memberships(memberships: list[TenantMembership]) -> bool:
    if not memberships:
        return False

    default_memberships = [membership for membership in memberships if membership.is_default]
    target_membership = default_memberships[0] if default_memberships else memberships[0]
    changed = False
    for membership in memberships:
        should_be_default = membership.id == target_membership.id
        if membership.is_default != should_be_default:
            membership.is_default = should_be_default
            changed = True
    return changed


def resolve_active_membership(
    db: Session,
    *,
    user: User,
    requested_tenant_slug: str | None = None,
) -> TenantMembership:
    memberships = list_user_memberships(db, user)
    if not memberships:
        raise NotFoundError("No tenant memberships were found for this account.")

    if requested_tenant_slug:
        normalized_slug = normalize_desk_slug(requested_tenant_slug)
        for membership in memberships:
            if membership.tenant.slug == normalized_slug:
                return membership
        raise NotFoundError("Requested tenant was not found for this account.")

    default_membership = next((item for item in memberships if item.is_default), None)
    return default_membership or memberships[0]


def _resolve_tenant_for_current_user(db: Session, current_user: Any) -> Tenant:
    tenant_id = str(getattr(current_user, "tenant_id", "") or "").strip()
    if not tenant_id:
        raise NotFoundError("No active tenant is set for this session.")
    tenant = db.execute(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .execution_options(populate_existing=True)
        .options(
            selectinload(Tenant.memberships).selectinload(TenantMembership.user),
            selectinload(Tenant.invitations).selectinload(TenantInvitation.inviter_user),
            selectinload(Tenant.subscriptions),
            selectinload(Tenant.entitlements),
        )
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("The active tenant could not be found.")
    return tenant


def _assert_tenant_brand_manager(current_user: Any) -> None:
    require_current_user_permission(
        current_user,
        "tenant.manage_branding",
        "Only tenant owners or admins can update branding.",
    )


def _assert_support_operator(current_user: Any) -> None:
    require_current_user_permission(
        current_user,
        "tenant.manage_support",
        "Only tenant owners or admins can use support controls.",
    )


def _assert_platform_admin(current_user: Any) -> None:
    require_current_user_permission(
        current_user,
        "tenant.change_status",
        "Only platform admins can change tenant status.",
    )


def _assert_member_manager(current_user: Any) -> None:
    require_current_user_permission(
        current_user,
        "tenant.manage_members",
        "Only tenant owners or admins can manage members.",
    )


def _resolve_user_for_current_user(db: Session, current_user: Any) -> User | None:
    auth_subject = str(getattr(current_user, "auth_subject", "") or getattr(current_user, "user_id", "") or "").strip()
    if not auth_subject:
        return None
    return db.execute(select(User).where(User.auth_subject == auth_subject)).scalar_one_or_none()


def _can_manage_owner_role(current_user: Any) -> bool:
    return str(getattr(current_user, "role", "") or "").strip().lower() == "owner"


def _assert_role_assignment_allowed(current_user: Any, target_role: str) -> None:
    normalized_role = _normalize_member_role(target_role)
    if normalized_role == "owner" and not _can_manage_owner_role(current_user):
        raise ForbiddenError("Only tenant owners can invite or promote another owner.")


def _assert_membership_target_allowed(current_user: Any, membership: TenantMembership) -> None:
    if membership.role == "owner" and not _can_manage_owner_role(current_user):
        raise ForbiddenError("Only tenant owners can manage another owner.")


def _assert_invitation_target_allowed(current_user: Any, invitation: TenantInvitation) -> None:
    if invitation.role == "owner" and not _can_manage_owner_role(current_user):
        raise ForbiddenError("Only tenant owners can manage owner invitations.")


def _read_onboarding_state(tenant: Tenant) -> dict[str, Any]:
    metadata = _copy_tenant_metadata(tenant)
    onboarding = metadata.get("onboarding") or {}
    if not isinstance(onboarding, dict):
        onboarding = {}
    completed_steps = onboarding.get("completed_steps") or []
    if not isinstance(completed_steps, list):
        completed_steps = []
    onboarding["completed_steps"] = [str(step).strip().lower() for step in completed_steps if str(step).strip()]
    return onboarding


def _write_onboarding_state(tenant: Tenant, onboarding: dict[str, Any]) -> None:
    metadata = _copy_tenant_metadata(tenant)
    metadata["onboarding"] = onboarding
    tenant.metadata_json = metadata
    flag_modified(tenant, "metadata_json")


def _read_template_state(tenant: Tenant) -> list[dict[str, Any]]:
    metadata = _copy_tenant_metadata(tenant)
    rows = metadata.get("template_runs") or []
    if not isinstance(rows, list):
        return []

    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        template_key = str(row.get("template_key") or "").strip().lower()
        workspace_id = str(row.get("workspace_id") or "").strip() or None
        workspace_name = str(row.get("workspace_name") or "").strip() or None
        lane = str(row.get("lane") or "stable").strip().lower()
        if lane not in {"stable", "pilot", "beta"}:
            lane = "stable"
        if not template_key:
            continue
        normalized.append(
            {
                "template_key": template_key,
                "workspace_id": workspace_id,
                "workspace_name": workspace_name,
                "lane": lane,
                "applied_at": str(row.get("applied_at") or "").strip() or None,
                "applied_by": str(row.get("applied_by") or "").strip() or None,
            }
        )
    return normalized


def _write_template_state(tenant: Tenant, template_runs: list[dict[str, Any]]) -> None:
    metadata = _copy_tenant_metadata(tenant)
    metadata["template_runs"] = template_runs
    tenant.metadata_json = metadata
    flag_modified(tenant, "metadata_json")


def _hash_api_token(raw_token: str) -> str:
    normalized = str(raw_token or "").strip()
    payload = f"{settings.api_token_salt}:{normalized}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize_api_token_scopes(scopes: list[str] | tuple[str, ...] | None) -> list[str]:
    values = scopes or _DEFAULT_API_TOKEN_SCOPES
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip().lower()
        if not cleaned or cleaned not in _API_TOKEN_SCOPE_META or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized or list(_DEFAULT_API_TOKEN_SCOPES)


def _serialize_api_token_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    token_id = str(row.get("id") or "").strip()
    name = str(row.get("name") or "").strip()
    token_hash = str(row.get("token_hash") or "").strip()
    if not token_id or not name or not token_hash:
        return None
    scopes = _normalize_api_token_scopes(row.get("scopes"))
    revoked_at = str(row.get("revoked_at") or "").strip() or None
    expires_at = str(row.get("expires_at") or "").strip() or None
    last_used_at = str(row.get("last_used_at") or "").strip() or None
    created_at = str(row.get("created_at") or "").strip() or None
    created_by = str(row.get("created_by") or "").strip() or None
    token_prefix = str(row.get("token_prefix") or "").strip() or None
    return {
        "id": token_id,
        "name": name,
        "token_hash": token_hash,
        "token_prefix": token_prefix,
        "scopes": scopes,
        "created_at": created_at,
        "created_by": created_by,
        "expires_at": expires_at,
        "last_used_at": last_used_at,
        "revoked_at": revoked_at,
    }


def _read_api_token_state(tenant: Tenant) -> list[dict[str, Any]]:
    metadata = _copy_tenant_metadata(tenant)
    rows = metadata.get("api_tokens") or []
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        serialized = _serialize_api_token_row(row)
        if serialized is not None:
            normalized.append(serialized)
    return normalized


def _write_api_token_state(tenant: Tenant, tokens: list[dict[str, Any]]) -> None:
    metadata = _copy_tenant_metadata(tenant)
    metadata["api_tokens"] = tokens
    tenant.metadata_json = metadata
    flag_modified(tenant, "metadata_json")


def _api_token_is_expired(token: dict[str, Any]) -> bool:
    expires_at = token.get("expires_at")
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


def _build_api_token_snapshot(tenant: Tenant) -> dict[str, Any]:
    entitlement_items = {item["key"]: item for item in resolve_tenant_entitlements(tenant)["items"]}
    api_access_item = entitlement_items.get("api_access", {"enabled": False, "limit": None, "source": "missing"})
    rows = _read_api_token_state(tenant)
    items: list[dict[str, Any]] = []
    active_count = 0
    revoked_count = 0
    expired_count = 0
    for row in rows:
        expired = _api_token_is_expired(row)
        revoked = bool(row.get("revoked_at"))
        if revoked:
            status = "revoked"
            revoked_count += 1
        elif expired:
            status = "expired"
            expired_count += 1
        else:
            status = "active"
            active_count += 1
        items.append(
            {
                "id": row["id"],
                "name": row["name"],
                "token_prefix": row.get("token_prefix"),
                "scopes": list(row.get("scopes") or []),
                "scope_labels": [_API_TOKEN_SCOPE_META[scope]["label"] for scope in row.get("scopes") or [] if scope in _API_TOKEN_SCOPE_META],
                "created_at": row.get("created_at"),
                "created_by": row.get("created_by"),
                "expires_at": row.get("expires_at"),
                "last_used_at": row.get("last_used_at"),
                "revoked_at": row.get("revoked_at"),
                "status": status,
                "can_revoke": status == "active",
            }
        )

    limit_value = api_access_item.get("limit")
    remaining = max(int(limit_value) - active_count, 0) if str(limit_value or "").isdigit() else None
    return {
        "enabled": bool(api_access_item.get("enabled", False)),
        "limit": limit_value,
        "source": api_access_item.get("source", "plan"),
        "count": len(items),
        "active_count": active_count,
        "revoked_count": revoked_count,
        "expired_count": expired_count,
        "remaining": remaining,
        "scope_catalog": [
            {
                "key": key,
                "label": meta["label"],
                "description": meta["description"],
            }
            for key, meta in _API_TOKEN_SCOPE_META.items()
        ],
        "items": items,
    }


def _read_api_usage_state(tenant: Tenant) -> dict[str, Any]:
    metadata = _copy_tenant_metadata(tenant)
    usage = metadata.get("api_usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    counters = usage.get("counters") or {}
    if not isinstance(counters, dict):
        counters = {}
    usage["counters"] = {
        "total_requests": int(counters.get("total_requests") or 0),
        "route_groups": dict(counters.get("route_groups") or {}),
        "methods": dict(counters.get("methods") or {}),
        "status_buckets": dict(counters.get("status_buckets") or {}),
        "last_request_at": str(counters.get("last_request_at") or "").strip() or None,
    }
    usage["daily"] = list(usage.get("daily") or [])
    usage["token_usage"] = dict(usage.get("token_usage") or {})
    usage["recent"] = list(usage.get("recent") or [])
    return usage


def _write_api_usage_state(tenant: Tenant, usage: dict[str, Any]) -> None:
    metadata = _copy_tenant_metadata(tenant)
    metadata["api_usage"] = usage
    tenant.metadata_json = metadata
    flag_modified(tenant, "metadata_json")


def _derive_route_group(request_path: str) -> str:
    trimmed = str(request_path or "").strip().strip("/")
    if not trimmed:
        return "root"
    parts = trimmed.split("/")
    if parts and parts[0] == "api":
        parts = parts[1:]
    return parts[0] if parts else "root"


def _read_partner_webhooks_state(tenant: Tenant) -> list[dict[str, Any]]:
    metadata = _copy_tenant_metadata(tenant)
    rows = metadata.get("partner_webhooks") or []
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        webhook_id = str(row.get("id") or "").strip()
        name = str(row.get("name") or "").strip()
        url = str(row.get("url") or "").strip()
        secret = str(row.get("secret") or "").strip()
        if not webhook_id or not name or not url or not secret:
            continue
        events = []
        seen: set[str] = set()
        for event in row.get("events") or []:
            cleaned = str(event or "").strip().lower()
            if cleaned and cleaned in {item["key"] for item in _PARTNER_WEBHOOK_EVENT_CATALOG} and cleaned not in seen:
                events.append(cleaned)
                seen.add(cleaned)
        normalized.append(
            {
                "id": webhook_id,
                "name": name,
                "url": url,
                "events": events or [item["key"] for item in _PARTNER_WEBHOOK_EVENT_CATALOG[:2]],
                "secret": secret,
                "secret_prefix": str(row.get("secret_prefix") or "").strip() or secret[:12],
                "status": str(row.get("status") or "active").strip().lower() if str(row.get("status") or "active").strip().lower() in {"active", "paused"} else "active",
                "created_at": str(row.get("created_at") or "").strip() or None,
                "created_by": str(row.get("created_by") or "").strip() or None,
                "updated_at": str(row.get("updated_at") or "").strip() or None,
                "last_test_at": str(row.get("last_test_at") or "").strip() or None,
                "last_delivery_at": str(row.get("last_delivery_at") or "").strip() or None,
                "last_delivery_status": str(row.get("last_delivery_status") or "").strip() or None,
            }
        )
    return normalized


def _write_partner_webhooks_state(tenant: Tenant, rows: list[dict[str, Any]]) -> None:
    metadata = _copy_tenant_metadata(tenant)
    metadata["partner_webhooks"] = rows
    tenant.metadata_json = metadata
    flag_modified(tenant, "metadata_json")


def _read_webhook_delivery_log(tenant: Tenant) -> list[dict[str, Any]]:
    metadata = _copy_tenant_metadata(tenant)
    rows = metadata.get("webhook_deliveries") or []
    return list(rows) if isinstance(rows, list) else []


def _write_webhook_delivery_log(tenant: Tenant, rows: list[dict[str, Any]]) -> None:
    metadata = _copy_tenant_metadata(tenant)
    metadata["webhook_deliveries"] = rows[:50]
    tenant.metadata_json = metadata
    flag_modified(tenant, "metadata_json")


def _deliver_partner_webhook_request(
    *,
    tenant: Tenant,
    target: dict[str, Any],
    event_key: str,
    payload: dict[str, Any],
    deliveries: list[dict[str, Any]],
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    delivered_at = datetime.now(timezone.utc).isoformat()
    raw_body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(str(target["secret"]).encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    delivery_status = "success"
    status_code = 200
    error_detail = None
    try:
        req = urlrequest.Request(
            target["url"],
            data=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-StockSignals-Event": event_key,
                "X-StockSignals-Webhook": target["id"],
                "X-StockSignals-Signature": signature,
            },
            method="POST",
        )
        default_timeout = max(1, int(getattr(settings, "partner_webhook_timeout_seconds", 5) or 5))
        with urlrequest.urlopen(req, timeout=max(1, int(timeout_seconds or default_timeout))) as response:
            status_code = getattr(response, "status", 200) or 200
            delivery_status = "success" if int(status_code) < 400 else "failed"
    except urlerror.HTTPError as exc:
        status_code = int(exc.code or 500)
        delivery_status = "failed"
        error_detail = str(exc)
    except urlerror.URLError as exc:
        status_code = 0
        delivery_status = "failed"
        error_detail = str(exc.reason)

    target["last_delivery_at"] = delivered_at
    target["last_delivery_status"] = delivery_status
    target["updated_at"] = delivered_at
    if event_key == "tenant.test":
        target["last_test_at"] = delivered_at
    deliveries.insert(
        0,
        {
            "id": f"wd_{secrets.token_hex(8)}",
            "webhook_id": target["id"],
            "webhook_name": target["name"],
            "event_key": event_key,
            "status": delivery_status,
            "status_code": status_code,
            "delivered_at": delivered_at,
            "error": error_detail,
        },
    )
    return deliveries[0]


def _dispatch_partner_webhook_event(
    db: Session,
    *,
    tenant: Tenant,
    event_key: str,
    payload: dict[str, Any],
) -> dict[str, int]:
    from backend.services.job_queue_service import enqueue_partner_webhook_delivery

    snapshot = _build_partner_webhook_snapshot(tenant)
    if not snapshot["enabled"]:
        return {"attempted": 0, "queued": 0, "delivered": 0}

    rows = _read_partner_webhooks_state(tenant)
    attempted = 0
    for target in rows:
        if target.get("status") != "active":
            continue
        if event_key not in (target.get("events") or []):
            continue
        attempted += 1
        enqueue_partner_webhook_delivery(
            db,
            tenant=tenant,
            webhook_id=str(target.get("id") or ""),
            event_key=event_key,
            payload=payload,
        )
    if attempted:
        db.flush()
    return {"attempted": attempted, "queued": attempted, "delivered": 0}


def _build_partner_webhook_snapshot(tenant: Tenant, db: Session | None = None) -> dict[str, Any]:
    if db is not None:
        from backend.services.job_queue_service import get_job_metrics_snapshot

    entitlement_items = {item["key"]: item for item in resolve_tenant_entitlements(tenant)["items"]}
    webhook_item = entitlement_items.get("partner_webhooks", {"enabled": False, "limit": None, "source": "missing"})
    api_access_item = entitlement_items.get("api_access", {"enabled": False, "limit": None, "source": "missing"})
    rows = _read_partner_webhooks_state(tenant)
    deliveries = _read_webhook_delivery_log(tenant)
    active_count = sum(1 for row in rows if row.get("status") == "active")
    limit_value = webhook_item.get("limit")
    remaining = max(int(limit_value) - active_count, 0) if str(limit_value or "").isdigit() else None
    items = []
    for row in rows:
        items.append(
            {
                "id": row["id"],
                "name": row["name"],
                "url": row["url"],
                "events": list(row["events"]),
                "status": row["status"],
                "secret_prefix": row.get("secret_prefix"),
                "created_at": row.get("created_at"),
                "created_by": row.get("created_by"),
                "updated_at": row.get("updated_at"),
                "last_test_at": row.get("last_test_at"),
                "last_delivery_at": row.get("last_delivery_at"),
                "last_delivery_status": row.get("last_delivery_status"),
            }
        )
    return {
        "enabled": bool(api_access_item.get("enabled", False)) and bool(webhook_item.get("enabled", False)),
        "api_access_enabled": bool(api_access_item.get("enabled", False)),
        "limit": limit_value,
        "source": webhook_item.get("source", "plan"),
        "count": len(items),
        "active_count": active_count,
        "remaining": remaining,
        "event_catalog": [dict(item) for item in _PARTNER_WEBHOOK_EVENT_CATALOG],
        "items": items,
        "deliveries": deliveries[:20],
        "jobs": (
            get_job_metrics_snapshot(db, tenant_id=tenant.id, job_type="partner_webhook_delivery")
            if db is not None
            else {
                "summary": {
                    "count": 0,
                    "queued": 0,
                    "retrying": 0,
                    "running": 0,
                    "succeeded": 0,
                    "dead_letter": 0,
                    "pending": 0,
                    "oldest_pending_at": None,
                    "recent_failure_count": 0,
                    "last_finished_at": None,
                },
                "job_types": [],
                "recent_jobs": [],
                "recent_failures": [],
                "dead_letters": [],
            }
        ),
    }


def _get_onboarding_template(template_key: str) -> dict[str, Any]:
    normalized_key = str(template_key or "").strip().lower()
    for template in _ONBOARDING_TEMPLATE_CATALOG:
        if template["key"] == normalized_key:
            return dict(template)
    raise ValidationError("Unknown onboarding template.")


def _build_onboarding_templates_snapshot(tenant: Tenant) -> dict[str, Any]:
    entitlements = {
        item["key"]: item
        for item in resolve_tenant_entitlements(tenant)["items"]
    }
    template_item = entitlements.get("onboarding_templates", {"enabled": False, "limit": None, "source": "missing"})
    release_item = entitlements.get("release_channels", {"enabled": False, "limit": None, "source": "missing"})
    applied_rows = _read_template_state(tenant)
    applied_by_key = {row["template_key"]: row for row in applied_rows}
    items: list[dict[str, Any]] = []
    for template in _ONBOARDING_TEMPLATE_CATALOG:
        applied = applied_by_key.get(template["key"])
        lane = template["lane"]
        lane_available = lane == "stable" or bool(release_item.get("enabled", False))
        items.append(
            {
                "key": template["key"],
                "name": template["name"],
                "description": template["description"],
                "page": template["page"],
                "lane": lane,
                "tags": list(template.get("tags") or []),
                "available": bool(template_item.get("enabled", False)) and lane_available,
                "release_lane_required": lane != "stable",
                "release_lane_available": lane_available,
                "is_applied": applied is not None,
                "applied_at": applied["applied_at"] if applied else None,
                "workspace_id": applied["workspace_id"] if applied else None,
                "workspace_name": applied["workspace_name"] if applied else None,
            }
        )
    limit_value = template_item.get("limit")
    applied_count = len(applied_rows)
    remaining = max(int(limit_value) - applied_count, 0) if str(limit_value or "").isdigit() else None
    return {
        "enabled": bool(template_item.get("enabled", False)),
        "limit": limit_value,
        "source": template_item.get("source", "plan"),
        "count": len(items),
        "applied_count": applied_count,
        "remaining": remaining,
        "release_channels_enabled": bool(release_item.get("enabled", False)),
        "items": items,
    }


def _generate_auth_provider_record_id(provider_key: str) -> str:
    return f"provider_{provider_key}_{secrets.token_hex(4)}"


def _normalize_auth_provider_url(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("https://") or cleaned.startswith("http://"):
        return cleaned.rstrip("/")
    return f"https://{cleaned.rstrip('/')}"


def _auth_provider_record_is_ready(record: dict[str, Any]) -> bool:
    return not _auth_provider_record_config_issues(record)


def _auth_provider_record_config_issues(record: dict[str, Any]) -> list[str]:
    provider_key = str(record.get("provider_key") or "").strip().lower()
    client_id = str(record.get("client_id") or "").strip()
    client_secret = str(record.get("client_secret") or "").strip()
    issues: list[str] = []
    if provider_key == "auth0":
        auth0_domain = str(record.get("auth0_domain") or "").strip()
        if not auth0_domain:
            issues.append("Auth0 domain is missing.")
        if not client_id:
            issues.append("Client ID is missing.")
        if not client_secret:
            issues.append("Client secret is missing.")
        return issues
    if provider_key == "oidc":
        issuer = str(record.get("issuer") or "").strip()
        authorize_url = str(record.get("authorize_url") or "").strip()
        token_url = str(record.get("token_url") or "").strip()
        userinfo_url = str(record.get("userinfo_url") or "").strip()
        if not client_id:
            issues.append("Client ID is missing.")
        if not client_secret:
            issues.append("Client secret is missing.")
        if not issuer and not (authorize_url and token_url and userinfo_url):
            issues.append("Add an issuer or provide authorize, token, and userinfo endpoints.")
        return issues
    issues.append("Unsupported provider.")
    return issues


def _normalize_auth_provider_health(record: dict[str, Any], existing_record: dict[str, Any]) -> dict[str, Any]:
    def _comparable(key: str, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(value)
        if key in {"auth0_domain", "issuer", "authorize_url", "token_url", "userinfo_url", "logout_url"}:
            return _normalize_auth_provider_url(value)
        return value

    config_changed = any(
        _comparable(key, record.get(key)) != _comparable(key, existing_record.get(key))
        for key in _AUTH_PROVIDER_CONFIG_KEYS
    )
    if config_changed:
        cleared = {key: None for key in _AUTH_PROVIDER_HEALTH_KEYS}
        cleared.update({key: None for key in _AUTH_PROVIDER_PENDING_HEALTH_KEYS})
        return cleared
    preserved = {key: existing_record.get(key) for key in _AUTH_PROVIDER_HEALTH_KEYS}
    pending_secret_changed = _comparable("pending_client_secret", record.get("pending_client_secret")) != _comparable(
        "pending_client_secret", existing_record.get("pending_client_secret")
    )
    if pending_secret_changed:
        preserved.update({key: None for key in _AUTH_PROVIDER_PENDING_HEALTH_KEYS})
    else:
        preserved.update({key: existing_record.get(key) for key in _AUTH_PROVIDER_PENDING_HEALTH_KEYS})
    return preserved


def _read_auth_provider_pending_health(record: dict[str, Any]) -> tuple[str | None, str | None]:
    pending_secret = str(record.get("pending_client_secret") or "").strip()
    if not pending_secret:
        return None, None

    health_status = str(record.get("pending_health_status") or "").strip().lower() or None
    health_message = str(record.get("pending_health_message") or "").strip() or None
    if health_status in {"ready", "error", "unchecked", "incomplete"}:
        return health_status, health_message
    return "unchecked", "Validate the staged secret before promoting it live."


def _append_auth_provider_history(
    record: dict[str, Any],
    *,
    target: str,
    status: str,
    message: str | None,
    event: str = "validation",
) -> None:
    previous_history = list(record.get("health_history") or [])
    history_item = {
        "event": str(event or "validation").strip().lower() or "validation",
        "target": target,
        "status": str(status or "").strip().lower() or "unchecked",
        "message": str(message or "").strip() or None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    record["health_history"] = [history_item, *previous_history][: _AUTH_PROVIDER_HISTORY_LIMIT]


def _parse_datetime_for_sort(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        normalized = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _latest_timestamp(*values: Any) -> str | None:
    latest_raw: str | None = None
    latest_dt: datetime | None = None
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        parsed = _parse_datetime_for_sort(raw)
        if parsed is None:
            continue
        if latest_dt is None or parsed > latest_dt:
            latest_dt = parsed
            latest_raw = raw
    return latest_raw


def _summarize_auth_provider_history(record: dict[str, Any]) -> dict[str, Any]:
    history_items = list(record.get("health_history") or [])
    normalized_items: list[dict[str, Any]] = []
    for item in history_items:
        if not isinstance(item, dict):
            continue
        normalized_items.append(
            {
                "event": str(item.get("event") or "validation").strip().lower() or "validation",
                "target": str(item.get("target") or "live").strip().lower() or "live",
                "status": str(item.get("status") or "unchecked").strip().lower() or "unchecked",
                "message": str(item.get("message") or "").strip() or None,
                "checked_at": str(item.get("checked_at") or "").strip() or None,
            }
        )
    normalized_items.sort(
        key=lambda item: _parse_datetime_for_sort(item.get("checked_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    last_ready = next((item.get("checked_at") for item in normalized_items if item.get("status") == "ready"), None)
    last_failed = next(
        (item.get("checked_at") for item in normalized_items if item.get("status") in {"error", "incomplete"}),
        None,
    )
    return {
        "items": normalized_items,
        "count": len(normalized_items),
        "last_checked_at": normalized_items[0].get("checked_at") if normalized_items else None,
        "last_ready_at": last_ready,
        "last_failed_at": last_failed,
    }


def _read_auth_provider_health(record: dict[str, Any]) -> tuple[str, str | None]:
    config_issues = _auth_provider_record_config_issues(record)
    health_status = str(record.get("health_status") or "").strip().lower() or None
    health_message = str(record.get("health_message") or "").strip() or None
    if config_issues:
        if health_status == "incomplete" and health_message:
            return "incomplete", health_message
        return "incomplete", " ".join(config_issues)
    if health_status in {"ready", "error", "unchecked"}:
        return health_status, health_message
    return "unchecked", "Validate this provider to confirm metadata and routing readiness."


def _sanitize_auth_provider_record(record: dict[str, Any]) -> dict[str, Any]:
    config_issues = _auth_provider_record_config_issues(record)
    health_status, health_message = _read_auth_provider_health(record)
    pending_health_status, pending_health_message = _read_auth_provider_pending_health(record)
    history_summary = _summarize_auth_provider_history(record)
    sanitized = {
        "id": record["id"],
        "provider_key": record["provider_key"],
        "label": record["label"],
        "enabled": bool(record.get("enabled", True)),
        "email_domains": list(record.get("email_domains") or []),
        "organization_hint": record.get("organization_hint"),
        "connection_hint": record.get("connection_hint"),
        "auth0_domain": record.get("auth0_domain"),
        "issuer": record.get("issuer"),
        "authorize_url": record.get("authorize_url"),
        "token_url": record.get("token_url"),
        "userinfo_url": record.get("userinfo_url"),
        "logout_url": record.get("logout_url"),
        "client_id": record.get("client_id"),
        "audience": record.get("audience"),
        "scope": record.get("scope"),
        "allow_signup": record.get("allow_signup"),
        "has_client_secret": bool(record.get("client_secret")),
        "has_pending_client_secret": bool(record.get("pending_client_secret")),
        "pending_secret_updated_at": record.get("pending_secret_updated_at"),
        "ready": _auth_provider_record_is_ready(record),
        "config_issues": config_issues,
        "health_status": health_status,
        "health_message": health_message,
        "last_checked_at": record.get("last_checked_at"),
        "discovery_source": record.get("discovery_source"),
        "resolved_authorize_url": record.get("resolved_authorize_url"),
        "resolved_token_url": record.get("resolved_token_url"),
        "resolved_userinfo_url": record.get("resolved_userinfo_url"),
        "resolved_logout_url": record.get("resolved_logout_url"),
        "pending_health_status": pending_health_status,
        "pending_health_message": pending_health_message,
        "pending_last_checked_at": record.get("pending_last_checked_at"),
        "pending_discovery_source": record.get("pending_discovery_source"),
        "pending_resolved_authorize_url": record.get("pending_resolved_authorize_url"),
        "pending_resolved_token_url": record.get("pending_resolved_token_url"),
        "pending_resolved_userinfo_url": record.get("pending_resolved_userinfo_url"),
        "pending_resolved_logout_url": record.get("pending_resolved_logout_url"),
        "health_history": history_summary["items"],
        "last_checked_at_history": history_summary["last_checked_at"],
        "last_ready_at": history_summary["last_ready_at"],
        "last_failed_at": history_summary["last_failed_at"],
        "actions": {
            "validate": bool(record.get("enabled", True)),
            "rotate_secret": bool(record.get("client_secret")),
            "promote_secret": bool(record.get("pending_client_secret")),
            "discard_secret": bool(record.get("pending_client_secret")),
        },
        "is_default": bool(record.get("is_default")),
    }
    return sanitized


def _normalize_auth_provider_records(
    values: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    *,
    existing_records: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    generate_missing_ids: bool = False,
    include_secrets: bool = False,
    preserve_secret_state: bool = False,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    existing_by_id = {
        str(record.get("id") or record.get("provider_id") or "").strip().lower(): dict(record)
        for record in existing_records or []
        if isinstance(record, dict) and str(record.get("id") or record.get("provider_id") or "").strip()
    }
    for index, value in enumerate(values or []):
        if not isinstance(value, dict):
            continue
        provider_key = str(value.get("provider_key") or "").strip().lower()
        if provider_key not in _EXTERNAL_AUTH_PROVIDER_KEYS:
            continue

        raw_id = str(value.get("provider_id") or value.get("id") or "").strip().lower()
        if not raw_id:
            raw_id = _generate_auth_provider_record_id(provider_key) if generate_missing_ids else f"{provider_key}-provider-{index + 1}"
        record_id = re.sub(r"[^a-z0-9_-]+", "-", raw_id).strip("-") or f"{provider_key}-provider-{index + 1}"
        base_record_id = record_id
        suffix = 2
        while record_id in seen_ids:
            record_id = f"{base_record_id}-{suffix}"
            suffix += 1
        seen_ids.add(record_id)
        existing_record = existing_by_id.get(record_id, {})

        label = str(value.get("label") or "").strip() or _AUTH_PROVIDER_RECORD_LABELS.get(provider_key, provider_key.upper())
        organization_hint = str(value.get("organization_hint") or value.get("auth0_organization") or "").strip() or None
        connection_hint = str(value.get("connection_hint") or value.get("auth0_connection") or "").strip() or None

        email_domains: list[str] = []
        seen_domains: set[str] = set()
        for raw_domain in value.get("email_domains") or []:
            cleaned_domain = str(raw_domain or "").strip().lower()
            if not cleaned_domain or cleaned_domain in seen_domains or not _DOMAIN_PATTERN.fullmatch(cleaned_domain):
                continue
            seen_domains.add(cleaned_domain)
            email_domains.append(cleaned_domain)

        active_client_secret = str(existing_record.get("client_secret") or "").strip() or None
        pending_client_secret = str(existing_record.get("pending_client_secret") or "").strip() or None
        pending_secret_updated_at = existing_record.get("pending_secret_updated_at")
        if preserve_secret_state:
            active_client_secret = active_client_secret or (str(value.get("client_secret") or "").strip() or None)
            pending_client_secret = pending_client_secret or (str(value.get("pending_client_secret") or "").strip() or None)
            pending_secret_updated_at = pending_secret_updated_at or value.get("pending_secret_updated_at")
        else:
            incoming_client_secret = str(value.get("client_secret") or "").strip() or None
            if incoming_client_secret:
                if active_client_secret and incoming_client_secret != active_client_secret:
                    pending_client_secret = incoming_client_secret
                    pending_secret_updated_at = datetime.now(timezone.utc).isoformat()
                else:
                    active_client_secret = incoming_client_secret
                    pending_client_secret = None
                    pending_secret_updated_at = None
        client_secret = active_client_secret
        record = {
            "id": record_id,
            "provider_key": provider_key,
            "label": label,
            "enabled": bool(value.get("enabled", True)),
            "email_domains": email_domains,
            "organization_hint": organization_hint,
            "connection_hint": connection_hint,
            "auth0_domain": _normalize_auth_provider_url(value.get("auth0_domain") or existing_record.get("auth0_domain")),
            "issuer": _normalize_auth_provider_url(value.get("issuer") or existing_record.get("issuer")),
            "authorize_url": _normalize_auth_provider_url(value.get("authorize_url") or existing_record.get("authorize_url")),
            "token_url": _normalize_auth_provider_url(value.get("token_url") or existing_record.get("token_url")),
            "userinfo_url": _normalize_auth_provider_url(value.get("userinfo_url") or existing_record.get("userinfo_url")),
            "logout_url": _normalize_auth_provider_url(value.get("logout_url") or existing_record.get("logout_url")),
            "client_id": str(value.get("client_id") or existing_record.get("client_id") or "").strip() or None,
            "client_secret": client_secret,
            "pending_client_secret": pending_client_secret,
            "pending_secret_updated_at": pending_secret_updated_at,
            "audience": str(value.get("audience") or existing_record.get("audience") or "").strip() or None,
            "scope": str(value.get("scope") or existing_record.get("scope") or "").strip() or None,
            "allow_signup": (
                bool(value.get("allow_signup"))
                if value.get("allow_signup") is not None
                else existing_record.get("allow_signup")
            ),
            "is_default": bool(value.get("is_default")),
            "health_history": list(existing_record.get("health_history") or []),
        }
        record.update(_normalize_auth_provider_health(record, existing_record))

        normalized.append(
            record
        )

    default_index = next((idx for idx, item in enumerate(normalized) if item["enabled"] and item["is_default"]), None)
    if default_index is None:
        default_index = next((idx for idx, item in enumerate(normalized) if item["enabled"]), None)
    if default_index is not None:
        for idx, item in enumerate(normalized):
            item["is_default"] = idx == default_index
    if include_secrets:
        return normalized
    return [_sanitize_auth_provider_record(item) for item in normalized]


def _read_delivery_state(tenant: Tenant, *, include_secrets: bool = False) -> dict[str, Any]:
    metadata = dict(tenant.metadata_json or {})
    delivery = metadata.get("delivery") or {}
    if not isinstance(delivery, dict):
        delivery = {}

    primary_domain = str(delivery.get("primary_domain") or "").strip().lower() or None
    secondary_domains: list[str] = []
    seen_domains: set[str] = set()
    for value in delivery.get("secondary_domains") or []:
        cleaned = str(value or "").strip().lower()
        if not cleaned or cleaned == primary_domain or cleaned in seen_domains:
            continue
        secondary_domains.append(cleaned)
        seen_domains.add(cleaned)

    domain_status = str(delivery.get("domain_status") or "draft").strip().lower()
    if domain_status not in {"draft", "pending_verification", "verified", "live"}:
        domain_status = "draft"

    provider_key = str(delivery.get("email_provider") or "none").strip().lower()
    if provider_key not in _EMAIL_PROVIDER_LABELS:
        provider_key = "none"

    provider_status = str(delivery.get("provider_status") or "draft").strip().lower()
    if provider_status not in {"draft", "configured", "ready", "live"}:
        provider_status = "draft"

    release_channel = str(delivery.get("release_channel") or "stable").strip().lower()
    if release_channel not in {"stable", "pilot", "beta"}:
        release_channel = "stable"

    auth_policy = str(delivery.get("auth_policy") or "default").strip().lower()
    if auth_policy not in {"default", "prefer_sso", "require_sso", "local_only"}:
        auth_policy = "default"

    preferred_provider = str(delivery.get("preferred_provider") or "default").strip().lower()
    if preferred_provider not in {"default", "auth0", "oidc", "local-session"}:
        preferred_provider = "default"

    enabled_providers: list[str] = []
    seen_provider_keys: set[str] = set()
    for value in delivery.get("enabled_providers") or []:
        cleaned = str(value or "").strip().lower()
        if cleaned in _AUTH_PROVIDER_KEYS and cleaned not in seen_provider_keys:
            seen_provider_keys.add(cleaned)
            enabled_providers.append(cleaned)
    raw_provider_records = list(delivery.get("auth_provider_records") or [])
    storage_provider_records = _normalize_auth_provider_records(
        raw_provider_records,
        existing_records=raw_provider_records,
        include_secrets=True,
        preserve_secret_state=True,
    )
    provider_records = storage_provider_records if include_secrets else [_sanitize_auth_provider_record(record) for record in storage_provider_records]
    for record in storage_provider_records:
        provider_key = record["provider_key"]
        if provider_key not in seen_provider_keys:
            seen_provider_keys.add(provider_key)
            enabled_providers.append(provider_key)

    return {
        "primary_domain": primary_domain,
        "secondary_domains": secondary_domains,
        "domain_status": domain_status,
        "sender_name": str(delivery.get("sender_name") or "").strip() or None,
        "sender_email": str(delivery.get("sender_email") or "").strip().lower() or None,
        "reply_to_email": str(delivery.get("reply_to_email") or "").strip().lower() or None,
        "mail_from_subdomain": str(delivery.get("mail_from_subdomain") or "").strip().lower() or None,
        "email_signature": str(delivery.get("email_signature") or "").strip() or None,
        "email_provider": provider_key,
        "provider_status": provider_status,
        "template_set_name": str(delivery.get("template_set_name") or "").strip() or None,
        "release_channel": release_channel,
        "auth0_organization": str(delivery.get("auth0_organization") or "").strip() or None,
        "auth0_connection": str(delivery.get("auth0_connection") or "").strip() or None,
        "sso_email_domain": str(delivery.get("sso_email_domain") or "").strip().lower() or None,
        "enabled_providers": enabled_providers,
        "auth_policy": auth_policy,
        "preferred_provider": preferred_provider,
        "auth_provider_records": provider_records,
        "verified_at": str(delivery.get("verified_at") or "").strip() or None,
        "live_at": str(delivery.get("live_at") or "").strip() or None,
        "last_test_at": str(delivery.get("last_test_at") or "").strip() or None,
    }


def _write_delivery_state(tenant: Tenant, delivery: dict[str, Any]) -> None:
    metadata = dict(tenant.metadata_json or {})
    metadata["delivery"] = delivery
    tenant.metadata_json = metadata
    flag_modified(tenant, "metadata_json")


def _build_delivery_dns_records(
    *,
    tenant: Tenant,
    primary_domain: str | None,
    mail_from_subdomain: str | None,
    provider_key: str,
) -> list[dict[str, str]]:
    if not primary_domain:
        return []

    records = [
        {
            "type": "TXT",
            "host": f"_stocksignals.{primary_domain}",
            "value": f"verify-{tenant.slug}",
            "purpose": "Tenant ownership verification",
        }
    ]

    if mail_from_subdomain:
        records.append(
            {
                "type": "CNAME",
                "host": f"{mail_from_subdomain}.{primary_domain}",
                "value": f"{tenant.slug}.mail.stocksignals.local",
                "purpose": "MAIL FROM / bounce routing",
            }
        )

    if provider_key != "none":
        records.append(
            {
                "type": "CNAME",
                "host": f"track.{primary_domain}",
                "value": f"{provider_key}.{tenant.slug}.links.stocksignals.local",
                "purpose": "Open and click tracking",
            }
        )

    return records


def _build_delivery_checklist(
    *,
    primary_domain: str | None,
    domain_status: str,
    provider_key: str,
    provider_status: str,
    sender_name: str | None,
    sender_email: str | None,
    last_test_at: str | None,
) -> dict[str, list[dict[str, Any]]]:
    domain_items = [
        {
            "key": "domain_added",
            "label": "Primary domain configured",
            "complete": bool(primary_domain),
            "detail": primary_domain or "Add a primary domain to begin verification.",
        },
        {
            "key": "verification_requested",
            "label": "Verification requested",
            "complete": domain_status in {"pending_verification", "verified", "live"},
            "detail": "Move the domain into verification once DNS records are published.",
        },
        {
            "key": "domain_verified",
            "label": "Domain verified",
            "complete": domain_status in {"verified", "live"},
            "detail": "Mark the domain verified after TXT/CNAME records resolve.",
        },
        {
            "key": "domain_live",
            "label": "Domain live",
            "complete": domain_status == "live",
            "detail": "Go live when routing and sender identity are ready.",
        },
    ]
    sender_items = [
        {
            "key": "provider_selected",
            "label": "Email provider selected",
            "complete": provider_key != "none",
            "detail": _EMAIL_PROVIDER_LABELS.get(provider_key, "Not configured"),
        },
        {
            "key": "sender_identity",
            "label": "Sender identity configured",
            "complete": bool(sender_name and sender_email),
            "detail": sender_email or "Add sender name and sender email.",
        },
        {
            "key": "provider_ready",
            "label": "Provider ready",
            "complete": provider_status in {"ready", "live"},
            "detail": provider_status.replace("-", " ").title(),
        },
        {
            "key": "test_delivery",
            "label": "Test delivery sent",
            "complete": bool(last_test_at),
            "detail": last_test_at or "Trigger a test email after provider setup.",
        },
    ]
    return {"domain": domain_items, "sender": sender_items}


def _build_auth_routing_summary(tenant: Tenant, delivery: dict[str, Any]) -> dict[str, Any]:
    auth_routing_configured = bool(
        delivery["auth0_organization"]
        or delivery["auth0_connection"]
        or delivery["sso_email_domain"]
        or delivery["enabled_providers"]
        or delivery["auth_provider_records"]
    )
    provider_records = list(delivery["auth_provider_records"])
    default_provider_record = next((record for record in provider_records if record["enabled"] and record["is_default"]), None)
    provider_domain_count = sum(len(record["email_domains"]) for record in provider_records)
    provider_health_counts = {
        "ready": sum(1 for record in provider_records if record.get("health_status") == "ready"),
        "unchecked": sum(1 for record in provider_records if record.get("health_status") == "unchecked"),
        "incomplete": sum(1 for record in provider_records if record.get("health_status") == "incomplete"),
        "error": sum(1 for record in provider_records if record.get("health_status") == "error"),
        "pending": sum(1 for record in provider_records if record.get("pending_client_secret")),
    }

    recent_operations: list[dict[str, Any]] = []
    for record in provider_records:
        for item in _summarize_auth_provider_history(record)["items"]:
            recent_operations.append(
                {
                    **item,
                    "provider_id": record.get("id"),
                    "provider_key": record.get("provider_key"),
                    "provider_label": record.get("label"),
                }
            )
    recent_operations.sort(
        key=lambda item: _parse_datetime_for_sort(item.get("checked_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    recent_operations = recent_operations[:10]
    last_ready_at = next((item.get("checked_at") for item in recent_operations if item.get("status") == "ready"), None)
    last_failed_at = next(
        (item.get("checked_at") for item in recent_operations if item.get("status") in {"error", "incomplete"}),
        None,
    )

    launch_blockers: list[str] = []
    external_provider_records = [
        record for record in provider_records if record.get("enabled") and record.get("provider_key") in _EXTERNAL_AUTH_PROVIDER_KEYS
    ]
    ready_external_records = [record for record in external_provider_records if record.get("health_status") == "ready"]
    if delivery["auth_policy"] == "require_sso" and not ready_external_records:
        launch_blockers.append("SSO is required, but no tenant provider record is validated and ready for launch.")
    auth_next_action = (
        "Add tenant provider records or org hints"
        if not auth_routing_configured
        else "Promote or discard staged auth provider secrets"
        if provider_health_counts["pending"]
        else "Complete provider credentials or endpoints"
        if provider_health_counts["incomplete"]
        else "Validate tenant provider records"
        if provider_health_counts["unchecked"]
        else "Fix provider validation errors"
        if provider_health_counts["error"]
        else "Resolve SSO launch blockers"
        if launch_blockers
        else "Tenant auth routing is configured"
    )
    return {
        "configured": auth_routing_configured,
        "provider": delivery["preferred_provider"]
        if delivery["preferred_provider"] != "default"
        else (
            default_provider_record["provider_key"]
            if default_provider_record is not None
            else (
                str(getattr(settings, "auth_provider", "") or "").strip().lower()
                if str(getattr(settings, "auth_provider", "") or "").strip().lower() in {"auth0", "oidc"}
                else ("auth0" if auth_routing_configured else "none")
            )
        ),
        "organization_hint": delivery["auth0_organization"],
        "connection_hint": delivery["auth0_connection"],
        "email_domain_hint": delivery["sso_email_domain"],
        "enabled_providers": list(delivery["enabled_providers"]),
        "auth_policy": delivery["auth_policy"],
        "preferred_provider": delivery["preferred_provider"],
        "provider_records": provider_records,
        "provider_record_count": len(provider_records),
        "provider_domain_count": provider_domain_count,
        "provider_health": provider_health_counts,
        "launch_ready": not launch_blockers,
        "launch_blockers": launch_blockers,
        "last_ready_at": last_ready_at,
        "last_failed_at": last_failed_at,
        "recent_operations": recent_operations,
        "default_provider_record_id": default_provider_record["id"] if default_provider_record else None,
        "entry_path": f"/login/{tenant.slug}",
        "post_login_path": f"/?tenant={tenant.slug}",
        "next_action": auth_next_action,
    }


def _build_delivery_snapshot_from_tenant(tenant: Tenant) -> dict[str, Any]:
    delivery = _read_delivery_state(tenant)
    entitlement_items = {
        item["key"]: item
        for item in resolve_tenant_entitlements(tenant)["items"]
    }
    custom_domains_item = entitlement_items.get("custom_domains", {"enabled": False, "limit": None, "source": "missing"})
    branded_email_item = entitlement_items.get("branded_email", {"enabled": False, "limit": None, "source": "missing"})

    primary_domain = delivery["primary_domain"]
    domains = [primary_domain, *delivery["secondary_domains"]] if primary_domain else list(delivery["secondary_domains"])
    verification_host = f"_stocksignals.{primary_domain}" if primary_domain else None
    verification_value = f"verify-{tenant.slug}" if primary_domain else None
    domain_records = _build_delivery_dns_records(
        tenant=tenant,
        primary_domain=primary_domain,
        mail_from_subdomain=delivery["mail_from_subdomain"],
        provider_key=delivery["email_provider"],
    )
    mail_from_domain = (
        f"{delivery['mail_from_subdomain']}.{primary_domain}"
        if primary_domain and delivery["mail_from_subdomain"]
        else None
    )
    sender_preview = (
        delivery["sender_email"]
        or (f"trading@{primary_domain}" if primary_domain and branded_email_item.get("enabled") else None)
    )
    checklists = _build_delivery_checklist(
        primary_domain=primary_domain,
        domain_status=delivery["domain_status"],
        provider_key=delivery["email_provider"],
        provider_status=delivery["provider_status"],
        sender_name=delivery["sender_name"],
        sender_email=delivery["sender_email"],
        last_test_at=delivery["last_test_at"],
    )
    total_domains = len(domains)
    domain_limit = custom_domains_item.get("limit")
    provider_label = _EMAIL_PROVIDER_LABELS.get(delivery["email_provider"], delivery["email_provider"].replace("-", " ").title())
    domain_next_action = (
        "Add a primary domain"
        if not primary_domain
        else "Request verification"
        if delivery["domain_status"] == "draft"
        else "Wait for DNS and mark verified"
        if delivery["domain_status"] == "pending_verification"
        else "Activate the domain"
        if delivery["domain_status"] == "verified"
        else "Domain is live"
    )
    sender_next_action = (
        "Select an email provider"
        if delivery["email_provider"] == "none"
        else "Configure sender identity"
        if not delivery["sender_email"]
        else "Send a test delivery"
        if not delivery["last_test_at"]
        else "Promote provider to live"
        if delivery["provider_status"] != "live"
        else "Sender stack is live"
    )
    auth_routing = _build_auth_routing_summary(tenant, delivery)

    return {
        "custom_domains": {
            "enabled": bool(custom_domains_item.get("enabled", False)),
            "limit": custom_domains_item.get("limit"),
            "source": custom_domains_item.get("source", "plan"),
            "configured": bool(primary_domain),
            "count": total_domains,
            "limit_reached": bool(str(domain_limit or "").isdigit() and total_domains >= int(domain_limit)),
            "primary_domain": primary_domain,
            "domains": domains,
            "secondary_domains": list(delivery["secondary_domains"]),
            "domain_status": delivery["domain_status"],
            "verification_host": verification_host,
            "verification_value": verification_value,
            "verified_at": delivery["verified_at"],
            "live_at": delivery["live_at"],
            "dns_records": domain_records,
            "checklist": checklists["domain"],
            "next_action": domain_next_action,
            "actions": {
                "request_verification": bool(primary_domain) and delivery["domain_status"] == "draft",
                "mark_verified": bool(primary_domain) and delivery["domain_status"] in {"draft", "pending_verification"},
                "activate_live": bool(primary_domain) and delivery["domain_status"] == "verified",
                "reset_domain": bool(primary_domain),
            },
        },
        "branded_email": {
            "enabled": bool(branded_email_item.get("enabled", False)),
            "limit": branded_email_item.get("limit"),
            "source": branded_email_item.get("source", "plan"),
            "configured": bool(delivery["sender_name"] or delivery["sender_email"]),
            "provider_key": delivery["email_provider"],
            "provider_label": provider_label,
            "provider_status": delivery["provider_status"],
            "template_set_name": delivery["template_set_name"],
            "release_channel": delivery["release_channel"],
            "sender_name": delivery["sender_name"],
            "sender_email": delivery["sender_email"],
            "reply_to_email": delivery["reply_to_email"],
            "mail_from_subdomain": delivery["mail_from_subdomain"],
            "mail_from_domain": mail_from_domain,
            "email_signature": delivery["email_signature"],
            "preview_from": sender_preview,
            "last_test_at": delivery["last_test_at"],
            "dns_records": domain_records[1:] if len(domain_records) > 1 else [],
            "checklist": checklists["sender"],
            "next_action": sender_next_action,
            "actions": {
                "send_test": bool(primary_domain and delivery["sender_email"] and delivery["email_provider"] != "none"),
                "reset_sender": bool(delivery["sender_name"] or delivery["sender_email"] or delivery["email_provider"] != "none"),
            },
        },
        "auth_routing": auth_routing,
    }


def get_tenant_auth_routing(tenant: Tenant) -> dict[str, Any]:
    delivery = _read_delivery_state(tenant, include_secrets=True)
    return _build_auth_routing_summary(tenant, delivery)


def _build_tenant_launch_ops_snapshot(
    tenant: Tenant,
    *,
    onboarding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    onboarding_snapshot = onboarding or _read_onboarding_state(tenant)
    steps_by_key = {
        str(step.get("key") or "").strip(): step
        for step in onboarding_snapshot.get("steps", [])
        if isinstance(step, dict)
    }
    completed_manual_steps = {
        str(step).strip().lower()
        for step in onboarding_snapshot.get("completed_steps", [])
        if str(step).strip()
    }
    delivery = _build_delivery_snapshot_from_tenant(tenant)
    custom_domains = delivery["custom_domains"]
    branded_email = delivery["branded_email"]
    auth_routing = delivery["auth_routing"]

    release_channel = str(branded_email.get("release_channel") or "stable").strip().lower() or "stable"
    primary_domain = str(custom_domains.get("primary_domain") or "").strip() or None
    sender_email = str(branded_email.get("sender_email") or "").strip() or None
    provider_key = str(branded_email.get("provider_key") or "none").strip().lower() or "none"
    launch_review_complete = bool(steps_by_key.get("launch_review", {}).get("completed")) or "launch_review" in completed_manual_steps

    domain_required = bool(primary_domain)
    sender_required = bool(domain_required or sender_email or provider_key != "none")
    auth_required = bool(auth_routing.get("configured"))
    lane_review_required = release_channel in {"pilot", "beta"}
    white_label_mode = bool(domain_required or sender_required or auth_required or lane_review_required)

    domain_ready = (not domain_required) or str(custom_domains.get("domain_status") or "").strip().lower() == "live"
    sender_ready = (not sender_required) or (
        str(branded_email.get("provider_status") or "").strip().lower() == "live"
        and bool(sender_email)
        and bool(branded_email.get("last_test_at"))
    )
    auth_ready = (not auth_required) or bool(auth_routing.get("launch_ready", False))
    lane_review_ready = (not lane_review_required) or launch_review_complete

    blockers: list[str] = []
    if white_label_mode and not launch_review_complete:
        if lane_review_required:
            blockers.append("Launch review must be completed before activating a pilot or beta delivery lane.")
        else:
            blockers.append("Launch review is still pending for this white-label tenant.")
    if domain_required and not domain_ready:
        blockers.append("Primary domain routing must be live before activating this tenant.")
    if sender_required and not sender_ready:
        blockers.append("Branded sender routing must be live and test-verified before activating this tenant.")
    if auth_required and not auth_ready:
        blockers.extend(auth_routing.get("launch_blockers") or ["Tenant SSO routing is not launch-ready yet."])

    checklist: list[dict[str, Any]] = []
    if white_label_mode:
        checklist.append(
            {
                "key": "launch_review",
                "label": "Launch review approved",
                "complete": launch_review_complete,
                "detail": "Manual approval checkpoint before a tenant can be resumed into a white-label launch path.",
            }
        )
    if domain_required:
        checklist.append(
            {
                "key": "domain_live",
                "label": "Primary domain live",
                "complete": domain_ready,
                "detail": f"Current status: {custom_domains.get('domain_status') or 'draft'}",
            }
        )
    if sender_required:
        checklist.append(
            {
                "key": "sender_live",
                "label": "Branded sender live",
                "complete": sender_ready,
                "detail": (
                    f"Provider {branded_email.get('provider_label') or 'not configured'}"
                    if branded_email.get("provider_key") != "none"
                    else "No branded sender configured yet."
                ),
            }
        )
    if auth_required:
        checklist.append(
            {
                "key": "auth_routing",
                "label": "Tenant SSO launch-ready",
                "complete": auth_ready,
                "detail": auth_routing.get("next_action") or "Validate tenant auth routing.",
            }
        )
    if lane_review_required:
        checklist.append(
            {
                "key": "release_lane",
                "label": "Pilot lane cleared",
                "complete": lane_review_ready,
                "detail": f"Delivery lane: {release_channel}",
            }
        )

    launch_ready = not blockers
    if white_label_mode:
        if launch_ready and tenant.status == "active":
            stage = "White-label live"
        elif launch_ready:
            stage = "White-label ready"
        else:
            stage = "Launch blocked"
    elif tenant.status == "active":
        stage = "Internal live"
    else:
        stage = "Standard tenant"

    return {
        "enabled": white_label_mode,
        "stage": stage,
        "release_channel": release_channel,
        "launch_ready": launch_ready,
        "blockers": blockers,
        "checklist": checklist,
        "domain_required": domain_required,
        "domain_ready": domain_ready,
        "sender_required": sender_required,
        "sender_ready": sender_ready,
        "auth_required": auth_required,
        "auth_ready": auth_ready,
        "last_ready_at": _latest_timestamp(
            custom_domains.get("live_at"),
            branded_email.get("last_test_at") if sender_ready else None,
            auth_routing.get("last_ready_at"),
        ),
        "last_failed_at": _latest_timestamp(auth_routing.get("last_failed_at")),
        "recent_operations": list(auth_routing.get("recent_operations") or []),
        "next_action": blockers[0] if blockers else (auth_routing.get("next_action") or "Tenant launch path is ready."),
    }


def get_tenant_launch_rollup(
    *,
    tenant_slug: str | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        tenant = resolve_tenant_by_slug(session, tenant_slug=tenant_slug or settings.demo_tenant_slug)
        launch_ops = _build_tenant_launch_ops_snapshot(tenant)
        checklist = list(launch_ops.get("checklist") or [])
        blockers = list(launch_ops.get("blockers") or [])
        completed_checks = sum(1 for item in checklist if item.get("complete"))
        total_checks = len(checklist)
        if not launch_ops.get("enabled"):
            status = "inactive"
        elif launch_ops.get("launch_ready"):
            status = "ready"
        else:
            status = "blocked"

        return {
            "tenant": {
                "slug": tenant.slug,
                "name": tenant.name,
                "status": tenant.status,
                "plan_key": tenant.plan_key,
            },
            "summary": {
                "status": status,
                "enabled": bool(launch_ops.get("enabled")),
                "stage": launch_ops.get("stage"),
                "launch_ready": bool(launch_ops.get("launch_ready")),
                "release_channel": launch_ops.get("release_channel"),
                "blocker_count": len(blockers),
                "completed_checks": completed_checks,
                "total_checks": total_checks,
                "last_ready_at": launch_ops.get("last_ready_at"),
                "last_failed_at": launch_ops.get("last_failed_at"),
                "next_action": launch_ops.get("next_action"),
            },
            "checks": {
                "domain_required": bool(launch_ops.get("domain_required")),
                "domain_ready": bool(launch_ops.get("domain_ready")),
                "sender_required": bool(launch_ops.get("sender_required")),
                "sender_ready": bool(launch_ops.get("sender_ready")),
                "auth_required": bool(launch_ops.get("auth_required")),
                "auth_ready": bool(launch_ops.get("auth_ready")),
            },
            "checklist": checklist,
            "blockers": blockers,
            "recent_operations": list(launch_ops.get("recent_operations") or []),
        }
    finally:
        if owns_session:
            session.close()


def _emit_launch_ops_transition(
    db: Session,
    *,
    tenant: Tenant,
    actor: User | None,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    trigger: str,
) -> None:
    previous_snapshot = previous or {}
    if not previous_snapshot.get("enabled") and not current.get("enabled"):
        return

    previous_signature = (
        bool(previous_snapshot.get("enabled")),
        bool(previous_snapshot.get("launch_ready")),
        str(previous_snapshot.get("stage") or ""),
        tuple(str(item.get("key") or "") for item in previous_snapshot.get("checklist") or [] if not item.get("complete")),
    )
    current_signature = (
        bool(current.get("enabled")),
        bool(current.get("launch_ready")),
        str(current.get("stage") or ""),
        tuple(str(item.get("key") or "") for item in current.get("checklist") or [] if not item.get("complete")),
    )
    if previous_signature == current_signature:
        return

    payload = {
        "trigger": trigger,
        "previous": {
            "enabled": bool(previous_snapshot.get("enabled")),
            "stage": previous_snapshot.get("stage"),
            "launch_ready": bool(previous_snapshot.get("launch_ready")),
            "blockers": list(previous_snapshot.get("blockers") or []),
            "release_channel": previous_snapshot.get("release_channel"),
        },
        "current": {
            "enabled": bool(current.get("enabled")),
            "stage": current.get("stage"),
            "launch_ready": bool(current.get("launch_ready")),
            "blockers": list(current.get("blockers") or []),
            "release_channel": current.get("release_channel"),
        },
    }
    record_audit_event(
        db,
        event_type="tenant.launch_state_changed",
        tenant=tenant,
        user=actor,
        payload=payload,
    )

    if current.get("enabled") and current.get("launch_ready") and not previous_snapshot.get("launch_ready"):
        launch_ready_payload = {
            "event": "tenant.launch_ready",
            "trigger": trigger,
            "tenant": {
                "slug": tenant.slug,
                "name": tenant.name,
                "plan_key": tenant.plan_key,
                "status": tenant.status,
            },
            "launch_ops": {
                "stage": current.get("stage"),
                "release_channel": current.get("release_channel"),
                "last_ready_at": current.get("last_ready_at"),
                "last_failed_at": current.get("last_failed_at"),
                "next_action": current.get("next_action"),
                "checklist": list(current.get("checklist") or []),
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        dispatch_result = _dispatch_partner_webhook_event(
            db,
            tenant=tenant,
            event_key="tenant.launch_ready",
            payload=launch_ready_payload,
        )
        record_audit_event(
            db,
            event_type="tenant.launch_ready",
            tenant=tenant,
            user=actor,
            payload={
                **launch_ready_payload,
                "webhook_attempts": dispatch_result["attempted"],
                "webhook_jobs_queued": dispatch_result.get("queued", 0),
                "webhook_deliveries": dispatch_result["delivered"],
            },
        )
    elif previous_snapshot.get("launch_ready") and not current.get("launch_ready"):
        record_audit_event(
            db,
            event_type="tenant.launch_blocked",
            tenant=tenant,
            user=actor,
            payload={
                "trigger": trigger,
                "stage": current.get("stage"),
                "blockers": list(current.get("blockers") or []),
                "release_channel": current.get("release_channel"),
            },
        )


def _serialize_feature_limit(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def _feature_flag_items_for_tenant(tenant: Tenant) -> dict[str, Any]:
    resolved = resolve_tenant_entitlements(tenant)
    plan = get_plan_definition(tenant.plan_key)
    plan_entitlements = plan.get("entitlements", {})
    overrides = dict(tenant.feature_overrides or {})

    merged: dict[str, dict[str, Any]] = {item["key"]: dict(item) for item in resolved["items"]}
    order = [item["key"] for item in resolved["items"]]
    for key in _ADDITIONAL_FEATURE_FLAG_KEYS:
        if key not in merged:
            meta = ENTITLEMENT_META.get(key, {})
            merged[key] = {
                "key": key,
                "label": meta.get("label", key.replace("_", " ").title()),
                "description": meta.get("description", ""),
                "enabled": False,
                "limit": None,
                "source": "plan",
            }
            order.append(key)

    items: list[dict[str, Any]] = []
    for key in order:
        resolved_item = merged[key]
        plan_config = plan_entitlements.get(key, {})
        override = overrides.get(key)
        override_enabled = None
        override_limit = None
        if isinstance(override, dict):
            if "enabled" in override:
                override_enabled = bool(override.get("enabled"))
            if "limit" in override:
                override_limit = _serialize_feature_limit(override.get("limit"))
        elif isinstance(override, bool):
            override_enabled = override
        elif override not in {None, ""}:
            override_limit = _serialize_feature_limit(override)

        items.append(
            {
                "key": key,
                "label": resolved_item["label"],
                "description": resolved_item["description"],
                "effective_enabled": bool(resolved_item.get("enabled", False)),
                "effective_limit": _serialize_feature_limit(resolved_item.get("limit")),
                "source": resolved_item.get("source", "plan"),
                "plan_enabled": bool(plan_config.get("enabled", False)),
                "plan_limit": _serialize_feature_limit(plan_config.get("limit")),
                "override_enabled": override_enabled,
                "override_limit": override_limit,
                "is_overridden": key in overrides,
                "plan_defined": key in plan_entitlements,
            }
        )

    override_count = sum(1 for item in items if item["is_overridden"])
    enabled_count = sum(1 for item in items if item["effective_enabled"])
    return {
        "items": items,
        "count": len(items),
        "enabled_count": enabled_count,
        "override_count": override_count,
        "custom_count": sum(1 for item in items if not item["plan_defined"]),
    }


def get_tenant_feature_flags(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_support_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    snapshot = _feature_flag_items_for_tenant(tenant)
    return {
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
        },
        **snapshot,
    }


def update_tenant_feature_flag(
    db: Session,
    *,
    current_user: Any,
    flag_key: str,
    enabled: Any = ...,
    limit: Any = ...,
    reset: bool = False,
) -> dict[str, Any]:
    _assert_support_operator(current_user)
    normalized_key = str(flag_key or "").strip().lower().replace(" ", "_")
    if not normalized_key or normalized_key not in _MANAGED_FEATURE_FLAG_KEYS:
        raise ValidationError("Unknown feature flag.")

    tenant = _resolve_tenant_for_current_user(db, current_user)
    overrides = dict(tenant.feature_overrides or {})
    previous_override = overrides.get(normalized_key)

    if reset:
        overrides.pop(normalized_key, None)
    else:
        merged_override: dict[str, Any] = {}
        if isinstance(previous_override, dict):
            merged_override.update(previous_override)
        elif isinstance(previous_override, bool):
            merged_override["enabled"] = previous_override
        elif previous_override not in {None, ""}:
            merged_override["limit"] = previous_override

        changed = False
        if enabled is not ...:
            merged_override["enabled"] = bool(enabled)
            changed = True
        if limit is not ...:
            if limit in {None, ""}:
                merged_override.pop("limit", None)
            else:
                merged_override["limit"] = int(limit)
            changed = True
        if not changed:
            raise ValidationError("Provide a feature flag change or reset request.")

        if merged_override:
            overrides[normalized_key] = merged_override
        else:
            overrides.pop(normalized_key, None)

    tenant.feature_overrides = overrides
    db.flush()
    record_audit_event(
        db,
        event_type="tenant.feature_flag_updated",
        tenant=tenant,
        user=_resolve_user_for_current_user(db, current_user),
        payload={
            "flag_key": normalized_key,
            "reset": bool(reset),
            "previous_override": previous_override,
            "next_override": overrides.get(normalized_key),
        },
    )
    db.commit()
    return get_tenant_feature_flags(db, current_user=current_user)


def get_tenant_api_tokens(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_support_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    return {
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
        },
        "tokens": _build_api_token_snapshot(tenant),
    }


def create_tenant_api_token(
    db: Session,
    *,
    current_user: Any,
    name: str,
    scopes: list[str] | None = None,
    expires_in_days: int | None = 90,
) -> dict[str, Any]:
    _assert_support_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    snapshot = _build_api_token_snapshot(tenant)
    if not snapshot["enabled"]:
        raise ForbiddenError("API access is not enabled for the active tenant plan.")

    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValidationError("Token name is required.")

    normalized_scopes = _normalize_api_token_scopes(scopes)
    requested_total = int(snapshot.get("active_count", 0) or 0) + 1
    enforce_entitlement_limit(
        db,
        current_user,
        "api_access",
        requested_total=requested_total,
        resource_label="API tokens",
    )

    raw_secret = f"stk_live_{secrets.token_urlsafe(24)}"
    created_at = datetime.now(timezone.utc)
    expires_at = None
    if expires_in_days:
        expires_at = (created_at + timedelta(days=int(expires_in_days))).isoformat()
    token_row = {
        "id": f"tok_{secrets.token_hex(8)}",
        "name": normalized_name[:120],
        "token_hash": _hash_api_token(raw_secret),
        "token_prefix": raw_secret[:16],
        "scopes": normalized_scopes,
        "created_at": created_at.isoformat(),
        "created_by": str(getattr(current_user, "auth_subject", getattr(current_user, "user_id", "")) or ""),
        "expires_at": expires_at,
        "last_used_at": None,
        "revoked_at": None,
    }
    rows = _read_api_token_state(tenant)
    rows.append(token_row)
    _write_api_token_state(tenant, rows)
    db.flush()
    record_audit_event(
        db,
        event_type="tenant.api_token_created",
        tenant=tenant,
        user=_resolve_user_for_current_user(db, current_user),
        payload={
            "token_id": token_row["id"],
            "name": token_row["name"],
            "scopes": normalized_scopes,
            "expires_at": expires_at,
        },
    )
    db.commit()
    db.refresh(tenant)
    return {
        "token": {
            "id": token_row["id"],
            "name": token_row["name"],
            "secret": raw_secret,
            "token_prefix": token_row["token_prefix"],
            "scopes": normalized_scopes,
            "expires_at": expires_at,
        },
        "tokens": _build_api_token_snapshot(tenant),
    }


def revoke_tenant_api_token(db: Session, *, current_user: Any, token_id: str) -> dict[str, Any]:
    _assert_support_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    normalized_id = str(token_id or "").strip()
    if not normalized_id:
        raise ValidationError("Token id is required.")

    rows = _read_api_token_state(tenant)
    target = next((row for row in rows if row["id"] == normalized_id), None)
    if target is None:
        raise NotFoundError("API token was not found for this tenant.")
    if target.get("revoked_at"):
        raise ConflictError("That API token has already been revoked.")

    target["revoked_at"] = datetime.now(timezone.utc).isoformat()
    _write_api_token_state(tenant, rows)
    db.flush()
    record_audit_event(
        db,
        event_type="tenant.api_token_revoked",
        tenant=tenant,
        user=_resolve_user_for_current_user(db, current_user),
        payload={
            "token_id": target["id"],
            "name": target["name"],
        },
    )
    db.commit()
    db.refresh(tenant)
    return {
        "revoked_token_id": target["id"],
        "tokens": _build_api_token_snapshot(tenant),
    }


def authenticate_tenant_api_token(db: Session, *, raw_token: str) -> dict[str, Any]:
    normalized = str(raw_token or "").strip()
    if not normalized:
        raise UnauthorizedError("API token is required.")

    token_hash = _hash_api_token(normalized)
    tenants = list(
        db.execute(
            select(Tenant).options(
                selectinload(Tenant.memberships).selectinload(TenantMembership.user),
                selectinload(Tenant.subscriptions),
                selectinload(Tenant.entitlements),
            )
        ).scalars()
    )
    now = datetime.now(timezone.utc)
    for tenant in tenants:
        rows = _read_api_token_state(tenant)
        target = next((row for row in rows if row.get("token_hash") == token_hash), None)
        if target is None:
            continue
        if target.get("revoked_at") or _api_token_is_expired(target):
            raise UnauthorizedError("API token is invalid or expired.")

        current_last_used = target.get("last_used_at")
        should_persist_last_used = True
        if current_last_used:
            try:
                parsed = datetime.fromisoformat(str(current_last_used).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                should_persist_last_used = (now - parsed) >= timedelta(minutes=5)
            except ValueError:
                should_persist_last_used = True
        if should_persist_last_used:
            target["last_used_at"] = now.isoformat()
            _write_api_token_state(tenant, rows)
            db.flush()
            db.commit()
            db.refresh(tenant)

        scopes = list(target.get("scopes") or [])
        tenant_payload = build_tenant_payload(tenant)
        role = "admin" if "tenant.admin" in scopes else "viewer"
        return {
            "token_id": target["id"],
            "token_name": target["name"],
            "scopes": scopes,
            "role": role,
            "tenant": tenant,
            "tenant_payload": tenant_payload,
            "memberships": (
                {
                    "membership_id": f"token-{target['id']}",
                    "tenant": {
                        "id": tenant.id,
                        "slug": tenant.slug,
                        "name": tenant.name,
                        "status": tenant.status,
                        "plan_key": tenant.plan_key,
                        "logo_url": tenant.logo_url,
                        "brand_settings": tenant.brand_settings or {},
                    },
                    "role": role,
                    "status": "active",
                    "is_default": True,
                },
            ),
        }

    raise UnauthorizedError("API token is invalid or expired.")


def record_authenticated_tenant_api_request(
    db: Session,
    *,
    tenant: Tenant,
    token_id: str,
    token_name: str,
    scopes: list[str] | tuple[str, ...],
    request_path: str,
    method: str,
    status_code: int,
) -> None:
    usage = _read_api_usage_state(tenant)
    counters = usage["counters"]
    route_group = _derive_route_group(request_path)
    method_key = str(method or "GET").upper()
    status_bucket = f"{int(status_code) // 100}xx"
    now = datetime.now(timezone.utc)
    today_key = now.date().isoformat()

    counters["total_requests"] = int(counters.get("total_requests") or 0) + 1
    route_counts = dict(counters.get("route_groups") or {})
    route_counts[route_group] = int(route_counts.get(route_group) or 0) + 1
    counters["route_groups"] = route_counts
    method_counts = dict(counters.get("methods") or {})
    method_counts[method_key] = int(method_counts.get(method_key) or 0) + 1
    counters["methods"] = method_counts
    status_counts = dict(counters.get("status_buckets") or {})
    status_counts[status_bucket] = int(status_counts.get(status_bucket) or 0) + 1
    counters["status_buckets"] = status_counts
    counters["last_request_at"] = now.isoformat()

    daily = list(usage.get("daily") or [])
    daily_row = next((row for row in daily if row.get("date") == today_key), None)
    if daily_row is None:
        daily_row = {"date": today_key, "count": 0}
        daily.append(daily_row)
    daily_row["count"] = int(daily_row.get("count") or 0) + 1
    usage["daily"] = sorted(daily, key=lambda row: row.get("date", ""), reverse=True)[:14]

    token_usage = dict(usage.get("token_usage") or {})
    token_row = dict(token_usage.get(token_id) or {})
    token_row["token_name"] = token_name
    token_row["count"] = int(token_row.get("count") or 0) + 1
    token_row["last_used_at"] = now.isoformat()
    token_row["last_route_group"] = route_group
    token_row["scopes"] = list(scopes or [])
    token_usage[token_id] = token_row
    usage["token_usage"] = token_usage

    recent = list(usage.get("recent") or [])
    recent.insert(
        0,
        {
            "at": now.isoformat(),
            "token_id": token_id,
            "token_name": token_name,
            "route_group": route_group,
            "method": method_key,
            "status_code": int(status_code),
            "path": request_path,
        },
    )
    usage["recent"] = recent[:25]
    _write_api_usage_state(tenant, usage)
    db.flush()
    db.commit()


def record_tenant_api_request(
    db: Session,
    *,
    tenant_id: str,
    token_id: str,
    token_name: str,
    scopes: list[str] | tuple[str, ...],
    request_path: str,
    method: str,
    status_code: int,
) -> None:
    tenant = db.execute(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .options(selectinload(Tenant.entitlements), selectinload(Tenant.subscriptions))
    ).scalar_one_or_none()
    if tenant is None:
        return
    record_authenticated_tenant_api_request(
        db,
        tenant=tenant,
        token_id=token_id,
        token_name=token_name,
        scopes=scopes,
        request_path=request_path,
        method=method,
        status_code=status_code,
    )


def get_tenant_api_usage_snapshot(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_support_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    usage = _read_api_usage_state(tenant)
    counters = usage["counters"]
    route_groups = [
        {"key": key, "count": int(value or 0)}
        for key, value in sorted((counters.get("route_groups") or {}).items(), key=lambda item: item[1], reverse=True)
    ]
    methods = [
        {"key": key, "count": int(value or 0)}
        for key, value in sorted((counters.get("methods") or {}).items(), key=lambda item: item[1], reverse=True)
    ]
    status_buckets = [
        {"key": key, "count": int(value or 0)}
        for key, value in sorted((counters.get("status_buckets") or {}).items(), key=lambda item: item[1], reverse=True)
    ]
    token_usage_items = [
        {
            "token_id": token_id,
            "token_name": row.get("token_name"),
            "count": int(row.get("count") or 0),
            "last_used_at": row.get("last_used_at"),
            "last_route_group": row.get("last_route_group"),
            "scopes": list(row.get("scopes") or []),
        }
        for token_id, row in sorted((usage.get("token_usage") or {}).items(), key=lambda item: (item[1] or {}).get("count", 0), reverse=True)
    ]
    last_24h = sum(int(row.get("count") or 0) for row in usage.get("daily") or [] if row.get("date"))
    return {
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
        },
        "summary": {
            "total_requests": int(counters.get("total_requests") or 0),
            "last_request_at": counters.get("last_request_at"),
            "route_group_count": len(route_groups),
            "token_count": len(token_usage_items),
            "last_14d_requests": sum(int(row.get("count") or 0) for row in usage.get("daily") or []),
            "last_24h_requests": last_24h,
        },
        "route_groups": route_groups,
        "methods": methods,
        "status_buckets": status_buckets,
        "tokens": token_usage_items,
        "daily": list(usage.get("daily") or []),
        "recent": list(usage.get("recent") or []),
    }


def _is_security_audit_event(event_type: str | None) -> bool:
    normalized = str(event_type or "").strip()
    if not normalized:
        return False
    return normalized in _SECURITY_AUDIT_EVENT_TYPES or normalized.startswith(_SECURITY_AUDIT_EVENT_PREFIXES)


def get_tenant_security_snapshot(db: Session, *, current_user: Any, limit: int = 12) -> dict[str, Any]:
    from backend.services.job_queue_service import run_due_jobs
    from backend.services.rate_limit_service import get_rate_limit_snapshot

    _assert_support_operator(current_user)
    run_due_jobs(db, limit=max(1, int(getattr(settings, "job_worker_batch_size", 12) or 12)))
    tenant = _resolve_tenant_for_current_user(db, current_user)
    token_snapshot = _build_api_token_snapshot(tenant)
    webhook_snapshot = _build_partner_webhook_snapshot(tenant, db)
    auth_snapshot = _build_auth_routing_summary(tenant, _read_delivery_state(tenant))
    rate_limit_snapshot = get_rate_limit_snapshot(tenant_slug=tenant.slug, limit=max(12, int(limit or 12)))
    usage = _read_api_usage_state(tenant)
    recent_audit_items = list_audit_events_for_tenant(db, tenant_id=tenant.id, limit=max(20, int(limit or 12) * 4))
    security_audit_items = [item for item in recent_audit_items if _is_security_audit_event(item.get("event_type"))]

    now = datetime.now(timezone.utc)
    warning_risks: list[dict[str, Any]] = []
    critical_risks: list[dict[str, Any]] = []

    def _record_risk(
        *,
        severity: str,
        category: str,
        key: str,
        title: str,
        message: str,
        count: int | None = None,
    ) -> None:
        item = {
            "severity": severity,
            "category": category,
            "key": key,
            "title": title,
            "message": message,
            "count": count,
        }
        if severity == "critical":
            critical_risks.append(item)
        else:
            warning_risks.append(item)

    active_tokens = [item for item in token_snapshot.get("items", []) if item.get("status") == "active"]
    admin_scope_count = 0
    unused_active_count = 0
    stale_active_count = 0
    expiring_soon_count = 0
    oldest_active_created_at: str | None = None
    oldest_active_created_dt: datetime | None = None
    next_expiring_at: str | None = None
    last_token_use_at: str | None = None

    for token in active_tokens:
        scopes = {str(scope).strip() for scope in token.get("scopes") or [] if str(scope).strip()}
        if "tenant.admin" in scopes:
            admin_scope_count += 1

        created_at = _parse_datetime_for_sort(token.get("created_at"))
        last_used_at = _parse_datetime_for_sort(token.get("last_used_at"))
        expires_at = _parse_datetime_for_sort(token.get("expires_at"))

        if last_used_at is None:
            unused_active_count += 1
        if last_used_at is not None:
            last_token_use_at = _latest_timestamp(last_token_use_at, token.get("last_used_at"))

        if created_at is not None and (oldest_active_created_dt is None or created_at < oldest_active_created_dt):
            oldest_active_created_dt = created_at
            oldest_active_created_at = token.get("created_at")

        if expires_at is not None and expires_at > now:
            if next_expiring_at is None or expires_at < (_parse_datetime_for_sort(next_expiring_at) or expires_at):
                next_expiring_at = token.get("expires_at")
            if expires_at <= now + timedelta(days=14):
                expiring_soon_count += 1

        if last_used_at is not None and last_used_at <= now - timedelta(days=30):
            stale_active_count += 1
        elif last_used_at is None and created_at is not None and created_at <= now - timedelta(days=7):
            stale_active_count += 1

    if admin_scope_count:
        _record_risk(
            severity="warning",
            category="tokens",
            key="admin_tokens",
            title="Admin API tokens are active",
            message=f"{admin_scope_count} active token{'s have' if admin_scope_count != 1 else ' has'} tenant-admin scope. Review whether those elevated credentials are still needed.",
            count=admin_scope_count,
        )
    if stale_active_count:
        _record_risk(
            severity="warning",
            category="tokens",
            key="stale_tokens",
            title="Stale active API tokens",
            message=f"{stale_active_count} active token{'s have' if stale_active_count != 1 else ' has'} not been used recently. Revoke or rotate idle credentials before they drift out of control.",
            count=stale_active_count,
        )
    if expiring_soon_count:
        _record_risk(
            severity="warning",
            category="tokens",
            key="expiring_tokens",
            title="API tokens expiring soon",
            message=f"{expiring_soon_count} active token{'s are' if expiring_soon_count != 1 else ' is'} due to expire within the next 14 days.",
            count=expiring_soon_count,
        )

    webhook_items = list(webhook_snapshot.get("items") or [])
    paused_webhook_count = sum(1 for item in webhook_items if item.get("status") == "paused")
    active_webhook_count = int(webhook_snapshot.get("active_count") or 0)
    failed_deliveries = [item for item in webhook_snapshot.get("deliveries") or [] if item.get("status") != "success"]
    failed_delivery_count = len(failed_deliveries)
    last_failure_at = next((item.get("delivered_at") for item in failed_deliveries if item.get("delivered_at")), None)
    job_summary = webhook_snapshot.get("jobs", {}).get("summary", {})
    retrying_count = int(job_summary.get("retrying") or 0)
    dead_letter_count = int(job_summary.get("dead_letter") or 0)

    if dead_letter_count:
        _record_risk(
            severity="critical",
            category="webhooks",
            key="dead_letter_webhooks",
            title="Partner webhook jobs hit dead-letter state",
            message=f"{dead_letter_count} webhook delivery job{'s have' if dead_letter_count != 1 else ' has'} exhausted retries and need operator attention.",
            count=dead_letter_count,
        )
    elif failed_delivery_count or retrying_count:
        _record_risk(
            severity="warning",
            category="webhooks",
            key="failing_webhooks",
            title="Partner webhook delivery issues",
            message=f"{failed_delivery_count} recent delivery failure{'s' if failed_delivery_count != 1 else ''} and {retrying_count} retrying job{'s' if retrying_count != 1 else ''} are on the queue.",
            count=failed_delivery_count + retrying_count,
        )
    if webhook_items and paused_webhook_count == len(webhook_items):
        _record_risk(
            severity="warning",
            category="webhooks",
            key="all_webhooks_paused",
            title="All partner webhooks are paused",
            message="Every configured partner webhook is paused, so outbound tenant events are currently suppressed.",
            count=paused_webhook_count,
        )

    provider_health = dict(auth_snapshot.get("provider_health") or {})
    launch_blockers = list(auth_snapshot.get("launch_blockers") or [])
    if launch_blockers:
        _record_risk(
            severity="critical",
            category="auth",
            key="auth_launch_blockers",
            title="Tenant SSO launch is blocked",
            message=" ".join(launch_blockers),
            count=len(launch_blockers),
        )
    elif int(provider_health.get("error") or 0) or int(provider_health.get("incomplete") or 0):
        _record_risk(
            severity="warning",
            category="auth",
            key="provider_health_issues",
            title="Identity provider records need attention",
            message="One or more tenant-managed identity providers are incomplete or failing validation.",
            count=int(provider_health.get("error") or 0) + int(provider_health.get("incomplete") or 0),
        )
    elif int(provider_health.get("unchecked") or 0) or int(provider_health.get("pending") or 0):
        _record_risk(
            severity="warning",
            category="auth",
            key="provider_validation_pending",
            title="Identity providers still need validation",
            message="Tenant-managed identity providers have pending secrets or have not been validated since their last change.",
            count=int(provider_health.get("unchecked") or 0) + int(provider_health.get("pending") or 0),
        )

    rate_limit_summary = dict(rate_limit_snapshot.get("summary") or {})
    blocked_actor_count = int(rate_limit_summary.get("blocked_actor_count") or 0)
    throttle_event_count = int(rate_limit_summary.get("throttle_event_count") or 0)
    auth_lockout_count = int(rate_limit_summary.get("auth_lockout_count") or 0)

    if auth_lockout_count:
        _record_risk(
            severity="critical",
            category="rate_limits",
            key="auth_lockouts",
            title="Authentication actors are currently locked out",
            message=f"{auth_lockout_count} actor{'s are' if auth_lockout_count != 1 else ' is'} currently blocked after repeated authentication failures.",
            count=auth_lockout_count,
        )
    elif blocked_actor_count:
        _record_risk(
            severity="warning",
            category="rate_limits",
            key="blocked_actors",
            title="Actors are currently being throttled",
            message=f"{blocked_actor_count} actor{'s are' if blocked_actor_count != 1 else ' is'} currently blocked by active rate-limit rules.",
            count=blocked_actor_count,
        )
    elif throttle_event_count:
        _record_risk(
            severity="warning",
            category="rate_limits",
            key="recent_throttles",
            title="Recent throttle events recorded",
            message=f"{throttle_event_count} recent throttle event{'s were' if throttle_event_count != 1 else ' was'} recorded for this tenant. Review integrations or user behavior if this continues.",
            count=throttle_event_count,
        )

    event_type_counts: dict[str, int] = {}
    for event in security_audit_items:
        event_type = str(event.get("event_type") or "").strip()
        if not event_type:
            continue
        event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1

    top_risks = [*critical_risks, *warning_risks]
    status = "critical" if critical_risks else "warning" if warning_risks else "healthy"

    return {
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
        },
        "summary": {
            "status": status,
            "critical_count": len(critical_risks),
            "warning_count": len(warning_risks),
            "active_admin_tokens": admin_scope_count,
            "stale_tokens": stale_active_count,
            "expiring_tokens": expiring_soon_count,
            "failed_webhooks": failed_delivery_count,
            "dead_letter_jobs": dead_letter_count,
            "auth_launch_blockers": len(launch_blockers),
            "rate_limit_events": throttle_event_count,
            "blocked_actors": blocked_actor_count,
            "last_security_event_at": security_audit_items[0].get("created_at") if security_audit_items else None,
        },
        "tokens": {
            "enabled": bool(token_snapshot.get("enabled", False)),
            "count": token_snapshot.get("count", 0),
            "active_count": token_snapshot.get("active_count", 0),
            "revoked_count": token_snapshot.get("revoked_count", 0),
            "expired_count": token_snapshot.get("expired_count", 0),
            "admin_scope_count": admin_scope_count,
            "unused_active_count": unused_active_count,
            "stale_active_count": stale_active_count,
            "expiring_soon_count": expiring_soon_count,
            "oldest_active_created_at": oldest_active_created_at,
            "next_expiring_at": next_expiring_at,
            "last_token_use_at": last_token_use_at or usage.get("counters", {}).get("last_request_at"),
            "risk_items": [item for item in top_risks if item["category"] == "tokens"],
        },
        "webhooks": {
            "enabled": bool(webhook_snapshot.get("enabled", False)),
            "count": webhook_snapshot.get("count", 0),
            "active_count": active_webhook_count,
            "paused_count": paused_webhook_count,
            "failed_delivery_count": failed_delivery_count,
            "retrying_count": retrying_count,
            "dead_letter_count": dead_letter_count,
            "last_failure_at": last_failure_at,
            "risk_items": [item for item in top_risks if item["category"] == "webhooks"],
        },
        "auth": {
            "configured": bool(auth_snapshot.get("configured", False)),
            "provider": auth_snapshot.get("provider"),
            "auth_policy": auth_snapshot.get("auth_policy"),
            "preferred_provider": auth_snapshot.get("preferred_provider"),
            "provider_record_count": auth_snapshot.get("provider_record_count", 0),
            "provider_domain_count": auth_snapshot.get("provider_domain_count", 0),
            "provider_health": provider_health,
            "launch_ready": bool(auth_snapshot.get("launch_ready", True)),
            "launch_blockers": launch_blockers,
            "last_ready_at": auth_snapshot.get("last_ready_at"),
            "last_failed_at": auth_snapshot.get("last_failed_at"),
            "next_action": auth_snapshot.get("next_action"),
            "risk_items": [item for item in top_risks if item["category"] == "auth"],
        },
        "rate_limits": {
            "enabled": bool(rate_limit_summary.get("enabled", False)),
            "throttle_event_count": throttle_event_count,
            "blocked_actor_count": blocked_actor_count,
            "auth_lockout_count": auth_lockout_count,
            "abuse_failure_count": int(rate_limit_summary.get("abuse_failure_count") or 0),
            "last_throttle_at": rate_limit_summary.get("last_throttle_at"),
            "last_abuse_event_at": rate_limit_summary.get("last_abuse_event_at"),
            "recent_events": rate_limit_snapshot.get("recent_events") or [],
            "recent_abuse": rate_limit_snapshot.get("recent_abuse") or [],
            "blocked_actors": rate_limit_snapshot.get("blocked_actors") or [],
            "risk_items": [item for item in top_risks if item["category"] == "rate_limits"],
        },
        "audit": {
            "count": len(security_audit_items),
            "items": security_audit_items[: max(1, min(int(limit or 12), 20))],
            "event_type_counts": [
                {"event_type": key, "count": value}
                for key, value in sorted(event_type_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
            "last_event_at": security_audit_items[0].get("created_at") if security_audit_items else None,
        },
        "risk_items": top_risks[:12],
    }


def get_tenant_partner_webhooks(db: Session, *, current_user: Any) -> dict[str, Any]:
    from backend.services.job_queue_service import run_due_jobs

    _assert_support_operator(current_user)
    run_due_jobs(db, limit=max(1, int(getattr(settings, "job_worker_batch_size", 12) or 12)))
    tenant = _resolve_tenant_for_current_user(db, current_user)
    return {
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
        },
        "webhooks": _build_partner_webhook_snapshot(tenant, db),
    }


def create_tenant_partner_webhook(
    db: Session,
    *,
    current_user: Any,
    name: str,
    url: str,
    events: list[str] | None = None,
) -> dict[str, Any]:
    _assert_support_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    require_entitlement(db, current_user, "api_access", message="API access is required before enabling partner webhooks.")
    snapshot = _build_partner_webhook_snapshot(tenant, db)
    if not snapshot["enabled"]:
        raise ForbiddenError("Partner webhooks are not enabled for the active tenant plan.")

    normalized_name = str(name or "").strip()
    normalized_url = str(url or "").strip()
    if not normalized_name or not normalized_url:
        raise ValidationError("Webhook name and URL are required.")
    if not normalized_url.startswith(("http://", "https://")):
        raise ValidationError("Webhook URL must start with http:// or https://.")

    valid_event_keys = {item["key"] for item in _PARTNER_WEBHOOK_EVENT_CATALOG}
    normalized_events = []
    seen_events: set[str] = set()
    for event in events or []:
        cleaned = str(event or "").strip().lower()
        if cleaned in valid_event_keys and cleaned not in seen_events:
            seen_events.add(cleaned)
            normalized_events.append(cleaned)
    normalized_events = normalized_events or [item["key"] for item in _PARTNER_WEBHOOK_EVENT_CATALOG[:2]]

    existing = _read_partner_webhooks_state(tenant)
    if any(row["url"] == normalized_url and row["name"].lower() == normalized_name.lower() for row in existing):
        raise ConflictError("A partner webhook with that name and URL already exists for this tenant.")
    enforce_entitlement_limit(
        db,
        current_user,
        "partner_webhooks",
        requested_total=snapshot["active_count"] + 1,
        resource_label="partner webhooks",
    )

    secret = f"whsec_{secrets.token_urlsafe(24)}"
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": f"wh_{secrets.token_hex(8)}",
        "name": normalized_name[:120],
        "url": normalized_url,
        "events": normalized_events,
        "secret": secret,
        "secret_prefix": secret[:14],
        "status": "active",
        "created_at": now,
        "created_by": str(getattr(current_user, "auth_subject", getattr(current_user, "user_id", "")) or ""),
        "updated_at": now,
        "last_test_at": None,
        "last_delivery_at": None,
        "last_delivery_status": None,
    }
    existing.append(row)
    _write_partner_webhooks_state(tenant, existing)
    db.flush()
    record_audit_event(
        db,
        event_type="tenant.partner_webhook_created",
        tenant=tenant,
        user=_resolve_user_for_current_user(db, current_user),
        payload={"webhook_id": row["id"], "name": row["name"], "events": normalized_events},
    )
    db.commit()
    db.refresh(tenant)
    return {
        "webhook": {
            "id": row["id"],
            "name": row["name"],
            "url": row["url"],
            "events": normalized_events,
            "secret": secret,
        },
        "webhooks": _build_partner_webhook_snapshot(tenant, db),
    }


def run_tenant_partner_webhook_action(
    db: Session,
    *,
    current_user: Any,
    webhook_id: str,
    action: str,
) -> dict[str, Any]:
    from backend.services.job_queue_service import enqueue_partner_webhook_delivery

    _assert_support_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    snapshot = _build_partner_webhook_snapshot(tenant, db)
    if not snapshot["enabled"]:
        raise ForbiddenError("Partner webhooks are not enabled for the active tenant plan.")

    normalized_id = str(webhook_id or "").strip()
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"send_test", "rotate_secret", "pause", "resume", "delete"}:
        raise ValidationError("Unsupported webhook action.")

    rows = _read_partner_webhooks_state(tenant)
    target = next((row for row in rows if row["id"] == normalized_id), None)
    if target is None:
        raise NotFoundError("Webhook was not found for this tenant.")

    actor = _resolve_user_for_current_user(db, current_user)
    now = datetime.now(timezone.utc).isoformat()
    secret_payload = None

    if normalized_action == "rotate_secret":
        target["secret"] = f"whsec_{secrets.token_urlsafe(24)}"
        target["secret_prefix"] = target["secret"][:14]
        target["updated_at"] = now
        secret_payload = {"secret": target["secret"]}
    elif normalized_action == "pause":
        target["status"] = "paused"
        target["updated_at"] = now
    elif normalized_action == "resume":
        target["status"] = "active"
        target["updated_at"] = now
    elif normalized_action == "delete":
        rows = [row for row in rows if row["id"] != normalized_id]
    elif normalized_action == "send_test":
        payload = {
            "event": "tenant.test",
            "tenant": {"slug": tenant.slug, "name": tenant.name, "plan_key": tenant.plan_key},
            "generated_at": now,
            "webhook_id": target["id"],
        }
        enqueue_partner_webhook_delivery(
            db,
            tenant=tenant,
            webhook_id=target["id"],
            event_key="tenant.test",
            payload=payload,
        )
        target["updated_at"] = now

    _write_partner_webhooks_state(tenant, rows)
    db.flush()
    record_audit_event(
        db,
        event_type=f"tenant.partner_webhook_{normalized_action}",
        tenant=tenant,
        user=actor,
        payload={"webhook_id": normalized_id, "action": normalized_action},
    )
    db.commit()
    db.refresh(tenant)
    response = {"webhooks": _build_partner_webhook_snapshot(tenant, db)}
    if secret_payload is not None:
        response["secret"] = secret_payload
    return response


def get_tenant_analytics_snapshot(db: Session, *, current_user: Any, activity_limit: int = 8) -> dict[str, Any]:
    _assert_support_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    billing = get_billing_summary(db, current_user)
    onboarding = get_tenant_onboarding_snapshot(db, current_user=current_user)
    launch_ops = _build_tenant_launch_ops_snapshot(tenant, onboarding=onboarding)
    feature_flags = _feature_flag_items_for_tenant(tenant)
    recent_activity = list_audit_events_for_tenant(db, tenant_id=tenant.id, limit=activity_limit)

    active_members = sum(1 for membership in tenant.memberships if membership.status == "active")
    workspace_count = int(onboarding.get("workspace_count", 0) or 0)
    brand_settings = tenant.brand_settings or {}
    support_ready = bool(brand_settings.get("support_email") or brand_settings.get("support_url"))
    branding_ready = bool(
        tenant.logo_url
        or brand_settings.get("app_name")
        or brand_settings.get("app_tagline")
        or brand_settings.get("accent_primary")
    )
    billing_ready = bool(next(iter(tenant.subscriptions), None) and next(iter(tenant.subscriptions)).status == "active")
    launch_review = any(step["key"] == "launch_review" and step["completed"] for step in onboarding.get("steps", []))
    rollout_items = [
        {"key": "branding", "label": "Brand ready", "complete": branding_ready},
        {"key": "billing", "label": "Billing active", "complete": billing_ready},
        {"key": "workspace", "label": "Workspace seeded", "complete": workspace_count > 0},
        {"key": "support", "label": "Support channels set", "complete": support_ready},
        {"key": "launch_review", "label": "Launch review passed", "complete": launch_review},
    ]
    rollout_items.extend(
        {
            "key": f"launch_{item['key']}",
            "label": item["label"],
            "complete": item["complete"],
        }
        for item in launch_ops["checklist"]
    )
    readiness_complete = sum(1 for item in rollout_items if item["complete"])
    rollout_readiness = round((readiness_complete / len(rollout_items)) * 100) if rollout_items else 0
    adoption_score = min(
        100,
        round(
            (onboarding.get("progress_percent", 0) * 0.5)
            + min(workspace_count, 3) * 8
            + min(active_members, 5) * 4
            + min(feature_flags["enabled_count"], 8) * 2
            + (10 if tenant.status == "active" else 0)
        ),
    )
    if launch_ops["enabled"]:
        activation_stage = launch_ops["stage"]
    elif rollout_readiness >= 100:
        activation_stage = "Pilot live"
    elif rollout_readiness >= 80:
        activation_stage = "Launch ready"
    elif rollout_readiness >= 50:
        activation_stage = "Pilot prep"
    elif rollout_readiness >= 25:
        activation_stage = "Configured"
    else:
        activation_stage = "Provisioning"

    return {
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
        },
        "summary": {
            "adoption_score": adoption_score,
            "rollout_readiness": rollout_readiness,
            "activation_stage": activation_stage,
            "member_count": active_members,
            "workspace_count": workspace_count,
            "recent_activity_count": len(recent_activity),
            "enabled_flag_count": feature_flags["enabled_count"],
            "override_count": feature_flags["override_count"],
            "last_activity_at": recent_activity[0]["created_at"] if recent_activity else None,
            "launch_ready": launch_ops["launch_ready"],
            "launch_enabled": launch_ops["enabled"],
            "last_ready_at": launch_ops["last_ready_at"],
            "last_failed_at": launch_ops["last_failed_at"],
            "launch_blockers_count": len(launch_ops["blockers"]),
        },
        "plan": billing.get("plan"),
        "usage": billing.get("usage"),
        "onboarding": {
            "progress_percent": onboarding.get("progress_percent", 0),
            "completed_count": onboarding.get("completed_count", 0),
            "count": onboarding.get("count", 0),
        },
        "flag_summary": {
            "count": feature_flags["count"],
            "enabled_count": feature_flags["enabled_count"],
            "override_count": feature_flags["override_count"],
        },
        "launch_ops": launch_ops,
        "rollout_funnel": rollout_items,
        "recent_activity": {"items": recent_activity, "count": len(recent_activity)},
    }


def get_tenant_delivery_snapshot(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_tenant_brand_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    return {
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
        },
        "delivery": _build_delivery_snapshot_from_tenant(tenant),
    }


def update_tenant_delivery_settings(
    db: Session,
    *,
    current_user: Any,
    updates: dict[str, Any],
) -> dict[str, Any]:
    if not updates:
        raise ValidationError("No delivery fields were provided.")

    _assert_tenant_brand_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    previous_launch_ops = _build_tenant_launch_ops_snapshot(tenant)
    delivery = _read_delivery_state(tenant, include_secrets=True)

    domain_fields = {"primary_domain", "secondary_domains", "domain_status"}
    email_fields = {
        "sender_name",
        "sender_email",
        "reply_to_email",
        "mail_from_subdomain",
        "email_signature",
        "email_provider",
        "provider_status",
        "template_set_name",
        "release_channel",
    }
    auth_fields = {
        "auth0_organization",
        "auth0_connection",
        "sso_email_domain",
        "enabled_providers",
        "auth_policy",
        "preferred_provider",
        "auth_provider_records",
    }
    if any(field in updates for field in domain_fields):
        require_entitlement(db, current_user, "custom_domains", message="Custom domains are not enabled for the active plan.")
    if any(field in updates for field in email_fields):
        require_entitlement(db, current_user, "branded_email", message="Branded email is not enabled for the active plan.")
    if any(field in updates for field in auth_fields):
        require_entitlement(
            db,
            current_user,
            "tenant_branding",
            message="Tenant auth routing is not enabled for the active plan.",
        )
    if "release_channel" in updates and str(updates.get("release_channel") or "stable").strip().lower() != "stable":
        require_entitlement(
            db,
            current_user,
            "release_channels",
            message="Pilot and beta delivery lanes are not enabled for the active plan.",
        )

    changed_fields: list[str] = []
    original_primary_domain = delivery.get("primary_domain")
    original_provider = delivery.get("email_provider")
    existing_provider_records = list(delivery.get("auth_provider_records") or [])
    for field in domain_fields | email_fields | auth_fields:
        if field not in updates:
            continue
        next_value = updates[field]
        delivery[field] = next_value
        changed_fields.append(field)

    primary_domain = str(delivery.get("primary_domain") or "").strip().lower() or None
    secondary_domains: list[str] = []
    seen_domains: set[str] = set()
    for value in delivery.get("secondary_domains") or []:
        cleaned = str(value or "").strip().lower()
        if not cleaned or cleaned == primary_domain or cleaned in seen_domains:
            continue
        secondary_domains.append(cleaned)
        seen_domains.add(cleaned)
    delivery["primary_domain"] = primary_domain
    delivery["secondary_domains"] = secondary_domains

    total_domains = len(([primary_domain] if primary_domain else []) + secondary_domains)
    if total_domains:
        enforce_entitlement_limit(
            db,
            current_user,
            "custom_domains",
            requested_total=total_domains,
            resource_label="custom domains",
        )

    if not primary_domain:
        delivery["domain_status"] = "draft"
        delivery["verified_at"] = None
        delivery["live_at"] = None
    elif primary_domain != original_primary_domain:
        if "domain_status" not in updates:
            delivery["domain_status"] = "draft"
        delivery["verified_at"] = None
        delivery["live_at"] = None

    provider_key = str(delivery.get("email_provider") or "none").strip().lower()
    if provider_key not in _EMAIL_PROVIDER_LABELS:
        provider_key = "none"
    delivery["email_provider"] = provider_key
    if provider_key == "none":
        delivery["provider_status"] = "draft"
        delivery["last_test_at"] = None
    elif provider_key != original_provider and "provider_status" not in updates:
        delivery["provider_status"] = "configured"
        delivery["last_test_at"] = None

    if delivery.get("release_channel") not in {"stable", "pilot", "beta"}:
        delivery["release_channel"] = "stable"

    if "auth_provider_records" in updates:
        delivery["auth_provider_records"] = _normalize_auth_provider_records(
            delivery.get("auth_provider_records") or [],
            existing_records=existing_provider_records,
            generate_missing_ids=True,
            include_secrets=True,
        )
    else:
        delivery["auth_provider_records"] = _normalize_auth_provider_records(
            delivery.get("auth_provider_records") or [],
            existing_records=existing_provider_records,
            include_secrets=True,
        )

    record_provider_keys = [
        record["provider_key"]
        for record in delivery.get("auth_provider_records") or []
        if record.get("enabled")
    ]
    enabled_provider_keys: list[str] = []
    seen_provider_keys: set[str] = set()
    for value in delivery.get("enabled_providers") or []:
        cleaned = str(value or "").strip().lower()
        if cleaned in _AUTH_PROVIDER_KEYS and cleaned not in seen_provider_keys:
            seen_provider_keys.add(cleaned)
            enabled_provider_keys.append(cleaned)
    for provider_key in record_provider_keys:
        if provider_key not in seen_provider_keys:
            seen_provider_keys.add(provider_key)
            enabled_provider_keys.append(provider_key)
    delivery["enabled_providers"] = enabled_provider_keys

    if delivery.get("preferred_provider") not in {"default", "auth0", "oidc", "local-session"}:
        delivery["preferred_provider"] = "default"
    if delivery.get("preferred_provider") == "default":
        default_provider_record = next(
            (record for record in delivery.get("auth_provider_records") or [] if record.get("enabled") and record.get("is_default")),
            None,
        )
        if default_provider_record and default_provider_record["provider_key"] not in delivery["enabled_providers"]:
            delivery["enabled_providers"].append(default_provider_record["provider_key"])

    _write_delivery_state(tenant, delivery)
    db.flush()
    record_audit_event(
        db,
        event_type="tenant.delivery_updated",
        tenant=tenant,
        user=actor,
        payload={"fields": changed_fields},
    )
    _emit_launch_ops_transition(
        db,
        tenant=tenant,
        actor=actor,
        previous=previous_launch_ops,
        current=_build_tenant_launch_ops_snapshot(tenant),
        trigger="delivery_updated",
    )
    db.commit()
    db.refresh(tenant)
    return get_tenant_delivery_snapshot(db, current_user=current_user)


def run_tenant_delivery_action(
    db: Session,
    *,
    current_user: Any,
    action: str,
    provider_id: str | None = None,
) -> dict[str, Any]:
    _assert_tenant_brand_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    previous_launch_ops = _build_tenant_launch_ops_snapshot(tenant)
    delivery = _read_delivery_state(tenant, include_secrets=True)
    normalized_action = str(action or "").strip().lower()
    normalized_provider_id = str(provider_id or "").strip().lower() or None
    now_iso = datetime.now(timezone.utc).isoformat()

    if normalized_action in {"request_verification", "mark_verified", "activate_live", "reset_domain"}:
        require_entitlement(db, current_user, "custom_domains", message="Custom domains are not enabled for the active plan.")
    if normalized_action in {"send_test", "reset_sender"}:
        require_entitlement(db, current_user, "branded_email", message="Branded email is not enabled for the active plan.")
    if normalized_action in {
        "validate_auth_provider",
        "promote_auth_provider_secret",
        "discard_auth_provider_secret",
        "rotate_auth_provider_secret",
    }:
        require_entitlement(
            db,
            current_user,
            "tenant_branding",
            message="Tenant auth routing is not enabled for the active plan.",
        )

    if normalized_action == "request_verification":
        if not delivery.get("primary_domain"):
            raise ValidationError("Add a primary domain before requesting verification.")
        delivery["domain_status"] = "pending_verification"
    elif normalized_action == "mark_verified":
        if not delivery.get("primary_domain"):
            raise ValidationError("Add a primary domain before marking verification complete.")
        delivery["domain_status"] = "verified"
        delivery["verified_at"] = now_iso
    elif normalized_action == "activate_live":
        if not delivery.get("primary_domain"):
            raise ValidationError("Add and verify a primary domain before going live.")
        if delivery.get("domain_status") not in {"verified", "live"}:
            raise ValidationError("Verify the primary domain before activating live routing.")
        if delivery.get("email_provider") == "none":
            raise ValidationError("Select an email provider before activating live routing.")
        if not delivery.get("sender_email"):
            raise ValidationError("Configure a sender email before activating live routing.")
        if delivery.get("provider_status") not in {"ready", "live"}:
            raise ValidationError("Mark the sender provider ready before activating live routing.")
        if not delivery.get("last_test_at"):
            raise ValidationError("Send a branded email test before activating live routing.")
        auth_routing = _build_auth_routing_summary(tenant, delivery)
        if auth_routing.get("configured") and not auth_routing.get("launch_ready", True):
            raise ValidationError(" ".join(auth_routing.get("launch_blockers") or []) or "Tenant auth routing is not launch-ready yet.")
        delivery["domain_status"] = "live"
        delivery["live_at"] = now_iso
        if delivery.get("provider_status") in {"draft", "configured", "ready"}:
            delivery["provider_status"] = "live"
    elif normalized_action == "reset_domain":
        delivery["domain_status"] = "draft"
        delivery["verified_at"] = None
        delivery["live_at"] = None
    elif normalized_action == "send_test":
        if delivery.get("email_provider") == "none":
            raise ValidationError("Select a branded email provider before sending a test delivery.")
        if not delivery.get("primary_domain"):
            raise ValidationError("Add a primary domain before sending a test delivery.")
        if not delivery.get("sender_name") or not delivery.get("sender_email"):
            raise ValidationError("Configure sender name and sender email before sending a test delivery.")
        delivery["last_test_at"] = now_iso
        if delivery.get("provider_status") in {"draft", "configured"}:
            delivery["provider_status"] = "ready"
    elif normalized_action == "reset_sender":
        delivery["provider_status"] = "draft"
        delivery["last_test_at"] = None
    elif normalized_action in {
        "validate_auth_provider",
        "promote_auth_provider_secret",
        "discard_auth_provider_secret",
        "rotate_auth_provider_secret",
    }:
        if not normalized_provider_id:
            raise ValidationError("Select a tenant auth provider before running this action.")
        provider_records = list(delivery.get("auth_provider_records") or [])
        target = next(
            (
                record
                for record in provider_records
                if str(record.get("id") or record.get("provider_id") or "").strip().lower() == normalized_provider_id
            ),
            None,
        )
        if target is None:
            raise ValidationError("The selected tenant auth provider could not be found.")
        if normalized_action == "validate_auth_provider":
            from backend.services import auth_provider_service

            validation_record = dict(target)
            validation_target = "live"
            if str(target.get("pending_client_secret") or "").strip():
                validation_record["client_secret"] = str(target.get("pending_client_secret") or "").strip()
                validation_target = "pending"
            validation_result = auth_provider_service.inspect_provider_runtime_config(
                str(target.get("provider_key") or ""),
                provider_record=validation_record,
            )
            if validation_target == "pending":
                target["pending_health_status"] = validation_result.get("health_status")
                target["pending_health_message"] = validation_result.get("health_message")
                target["pending_last_checked_at"] = validation_result.get("last_checked_at")
                target["pending_discovery_source"] = validation_result.get("discovery_source")
                target["pending_resolved_authorize_url"] = validation_result.get("resolved_authorize_url")
                target["pending_resolved_token_url"] = validation_result.get("resolved_token_url")
                target["pending_resolved_userinfo_url"] = validation_result.get("resolved_userinfo_url")
                target["pending_resolved_logout_url"] = validation_result.get("resolved_logout_url")
            else:
                target.update(validation_result)
            _append_auth_provider_history(
                target,
                target=validation_target,
                status=str(validation_result.get("health_status") or "unchecked"),
                message=str(validation_result.get("health_message") or "").strip() or None,
                event="validation",
            )
        elif normalized_action == "promote_auth_provider_secret":
            pending_secret = str(target.get("pending_client_secret") or "").strip()
            if not pending_secret:
                raise ValidationError("This tenant auth provider does not have a staged secret to promote.")
            if str(target.get("pending_health_status") or "").strip().lower() != "ready":
                raise ValidationError("Validate the staged secret successfully before promoting it live.")
            target["client_secret"] = pending_secret
            target["pending_client_secret"] = None
            target["pending_secret_updated_at"] = None
            target["health_status"] = target.get("pending_health_status")
            target["health_message"] = target.get("pending_health_message")
            target["last_checked_at"] = target.get("pending_last_checked_at")
            target["discovery_source"] = target.get("pending_discovery_source")
            target["resolved_authorize_url"] = target.get("pending_resolved_authorize_url")
            target["resolved_token_url"] = target.get("pending_resolved_token_url")
            target["resolved_userinfo_url"] = target.get("pending_resolved_userinfo_url")
            target["resolved_logout_url"] = target.get("pending_resolved_logout_url")
            for key in _AUTH_PROVIDER_PENDING_HEALTH_KEYS:
                target[key] = None
            _append_auth_provider_history(
                target,
                target="promotion",
                status="ready",
                message="Staged provider secret promoted live.",
                event="promote_secret",
            )
        elif normalized_action == "discard_auth_provider_secret":
            pending_secret = str(target.get("pending_client_secret") or "").strip()
            if not pending_secret:
                raise ValidationError("This tenant auth provider does not have a staged secret to discard.")
            target["pending_client_secret"] = None
            target["pending_secret_updated_at"] = None
            for key in _AUTH_PROVIDER_PENDING_HEALTH_KEYS:
                target[key] = None
            _append_auth_provider_history(
                target,
                target="pending",
                status="unchecked",
                message="Staged provider secret discarded.",
                event="discard_secret",
            )
        else:
            target["client_secret"] = None
            target["health_status"] = "incomplete"
            target["health_message"] = "Client secret cleared. Save a new secret and validate again."
            target["last_checked_at"] = None
            target["discovery_source"] = None
            target["resolved_authorize_url"] = None
            target["resolved_token_url"] = None
            target["resolved_userinfo_url"] = None
            target["resolved_logout_url"] = None
            _append_auth_provider_history(
                target,
                target="live",
                status="incomplete",
                message="Live provider secret cleared.",
                event="clear_live_secret",
            )
        delivery["auth_provider_records"] = provider_records
    else:
        raise ValidationError("Unknown delivery action.")

    _write_delivery_state(tenant, delivery)
    db.flush()
    record_audit_event(
        db,
        event_type="tenant.delivery_action_run",
        tenant=tenant,
        user=actor,
        payload={"action": normalized_action, "provider_id": normalized_provider_id},
    )
    _emit_launch_ops_transition(
        db,
        tenant=tenant,
        actor=actor,
        previous=previous_launch_ops,
        current=_build_tenant_launch_ops_snapshot(tenant),
        trigger=f"delivery_action:{normalized_action}",
    )
    db.commit()
    db.refresh(tenant)
    return get_tenant_delivery_snapshot(db, current_user=current_user)


def get_tenant_onboarding_templates_snapshot(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_support_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    return {
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
        },
        "templates": _build_onboarding_templates_snapshot(tenant),
    }


def apply_tenant_onboarding_template(
    db: Session,
    *,
    current_user: Any,
    template_key: str,
) -> dict[str, Any]:
    _assert_support_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    previous_launch_ops = _build_tenant_launch_ops_snapshot(tenant)
    template = _get_onboarding_template(template_key)
    templates_snapshot = _build_onboarding_templates_snapshot(tenant)
    if not templates_snapshot["enabled"]:
        raise ForbiddenError("Onboarding templates are not enabled for the active plan.")
    if template["lane"] != "stable" and not templates_snapshot["release_channels_enabled"]:
        raise ForbiddenError("Release channels are not enabled for this tenant, so pilot/beta templates are unavailable.")

    already_applied = next((item for item in templates_snapshot["items"] if item["key"] == template["key"] and item["is_applied"]), None)
    if already_applied:
        raise ConflictError("That onboarding template has already been applied to this tenant.")

    enforce_entitlement_limit(
        db,
        current_user,
        "onboarding_templates",
        requested_total=templates_snapshot["applied_count"] + 1,
        resource_label="onboarding templates",
    )

    user_id = getattr(
        current_user,
        "user_id",
        getattr(current_user, "auth_subject", getattr(settings, "demo_user_id", "demo-trader")),
    )
    workspace = save_workspace(
        user_id,
        template["name"],
        template["page"],
        payload=dict(template.get("payload") or {}),
        notes=str(template.get("notes") or ""),
        pinned=True,
        tags=list(template.get("tags") or []),
        tenant_slug=tenant.slug,
    )
    template_runs = _read_template_state(tenant)
    template_runs.append(
        {
            "template_key": template["key"],
            "workspace_id": workspace["id"],
            "workspace_name": workspace["name"],
            "lane": template["lane"],
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "applied_by": str(getattr(current_user, "auth_subject", getattr(current_user, "user_id", "")) or ""),
        }
    )
    _write_template_state(tenant, template_runs)
    db.flush()
    record_audit_event(
        db,
        event_type="tenant.onboarding_template_applied",
        tenant=tenant,
        user=actor,
        payload={
            "template_key": template["key"],
            "workspace_id": workspace["id"],
            "workspace_name": workspace["name"],
            "lane": template["lane"],
        },
    )
    _emit_launch_ops_transition(
        db,
        tenant=tenant,
        actor=actor,
        previous=previous_launch_ops,
        current=_build_tenant_launch_ops_snapshot(tenant),
        trigger="onboarding_template_applied",
    )
    db.commit()
    db.refresh(tenant)
    return {
        "workspace": workspace,
        "templates": _build_onboarding_templates_snapshot(tenant),
    }


def get_tenant_onboarding_snapshot(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_support_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    onboarding = _read_onboarding_state(tenant)
    templates_snapshot = _build_onboarding_templates_snapshot(tenant)
    completed_manual_steps = set(onboarding.get("completed_steps", []))
    workspaces = list_workspaces(
        getattr(
            current_user,
            "user_id",
            getattr(current_user, "auth_subject", getattr(settings, "demo_user_id", "demo-trader")),
        ),
        tenant_slug=tenant.slug,
    )
    brand_settings = tenant.brand_settings or {}
    support_ready = bool(brand_settings.get("support_email") or brand_settings.get("support_url"))
    branding_ready = bool(
        tenant.logo_url
        or brand_settings.get("app_name")
        or brand_settings.get("app_tagline")
        or brand_settings.get("accent_primary")
    )
    billing_ready = bool(next(iter(tenant.subscriptions), None) and next(iter(tenant.subscriptions)).status == "active")
    workspace_ready = bool(workspaces.get("count", 0))
    steps = [
        {
            "key": "branding",
            "title": "Brand the tenant",
            "description": "Set app name, colors, logo, and support links.",
            "completed": branding_ready or "branding" in completed_manual_steps,
            "source": "derived",
            "action_label": "Open branding controls",
        },
        {
            "key": "billing",
            "title": "Verify billing state",
            "description": "Confirm the plan, subscription mode, and entitlements are correct.",
            "completed": billing_ready or "billing" in completed_manual_steps,
            "source": "derived",
            "action_label": "Review billing",
        },
        {
            "key": "starter_workspace",
            "title": "Seed a launch workspace",
            "description": "Create the first saved workspace so pilots land in a usable desk.",
            "completed": workspace_ready or "starter_workspace" in completed_manual_steps,
            "source": "derived",
            "action_label": "Seed starter workspace",
        },
        {
            "key": "support_channels",
            "title": "Set support channels",
            "description": "Add support email or URL so operators know where to go when something breaks.",
            "completed": support_ready or "support_channels" in completed_manual_steps,
            "source": "derived",
            "action_label": "Update support links",
        },
        {
            "key": "launch_review",
            "title": "Mark tenant launch review complete",
            "description": "Manual go-live checkpoint for pilot launch approval.",
            "completed": "launch_review" in completed_manual_steps,
            "source": "manual",
            "action_label": "Toggle completion",
        },
    ]
    completed_count = sum(1 for step in steps if step["completed"])
    launch_ops = _build_tenant_launch_ops_snapshot(tenant, onboarding={"steps": steps})
    return {
        "tenant": {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "status": tenant.status,
            "plan_key": tenant.plan_key,
        },
        "steps": steps,
        "completed_count": completed_count,
        "count": len(steps),
        "progress_percent": round((completed_count / len(steps)) * 100) if steps else 0,
        "workspace_count": workspaces.get("count", 0),
        "template_summary": {
            "enabled": templates_snapshot["enabled"],
            "applied_count": templates_snapshot["applied_count"],
            "limit": templates_snapshot["limit"],
            "remaining": templates_snapshot["remaining"],
        },
        "launch_ops": {
            "enabled": launch_ops["enabled"],
            "stage": launch_ops["stage"],
            "launch_ready": launch_ops["launch_ready"],
            "blockers": launch_ops["blockers"],
        },
    }


def update_tenant_onboarding_step(
    db: Session,
    *,
    current_user: Any,
    step_key: str,
    completed: bool,
) -> dict[str, Any]:
    _assert_support_operator(current_user)
    normalized_step_key = str(step_key or "").strip().lower()
    if normalized_step_key not in _ONBOARDING_STEP_KEYS:
        raise ValidationError("Unknown onboarding step.")

    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    previous_launch_ops = _build_tenant_launch_ops_snapshot(tenant)
    onboarding = _read_onboarding_state(tenant)
    completed_steps = set(onboarding.get("completed_steps", []))
    if completed:
        completed_steps.add(normalized_step_key)
    else:
        completed_steps.discard(normalized_step_key)
    onboarding["completed_steps"] = sorted(completed_steps)
    onboarding["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_onboarding_state(tenant, onboarding)
    db.flush()
    record_audit_event(
        db,
        event_type="tenant.onboarding_step_updated",
        tenant=tenant,
        user=actor,
        payload={"step_key": normalized_step_key, "completed": bool(completed)},
    )
    _emit_launch_ops_transition(
        db,
        tenant=tenant,
        actor=actor,
        previous=previous_launch_ops,
        current=_build_tenant_launch_ops_snapshot(tenant),
        trigger="onboarding_step_updated",
    )
    db.commit()
    return get_tenant_onboarding_snapshot(db, current_user=current_user)


def seed_tenant_onboarding_workspace(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_support_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    previous_launch_ops = _build_tenant_launch_ops_snapshot(tenant)
    workspaces = list_workspaces(
        getattr(
            current_user,
            "user_id",
            getattr(current_user, "auth_subject", getattr(settings, "demo_user_id", "demo-trader")),
        ),
        tenant_slug=tenant.slug,
    )
    existing = next(
        (
            item for item in workspaces.get("items", [])
            if "onboarding" in (item.get("tags") or [])
            or str(item.get("name", "")).strip().lower() == "personal launchpad"
        ),
        None,
    )
    if existing is None:
        existing = save_workspace(
            getattr(
                current_user,
                "user_id",
                getattr(current_user, "auth_subject", getattr(settings, "demo_user_id", "demo-trader")),
            ),
            "Personal Launchpad",
            "dashboard",
            payload={
                "defaultTicker": "SPY",
                "defaultInterval": "5m",
                "watchlistTickers": list(_DEFAULT_ONBOARDING_TICKERS),
                "source": "tenant-onboarding",
            },
            notes="Starter workspace seeded for personal onboarding.",
            pinned=True,
            tags=["onboarding", "launchpad"],
            tenant_slug=tenant.slug,
        )
        record_audit_event(
            db,
            event_type="tenant.onboarding_workspace_seeded",
            tenant=tenant,
            user=actor,
            payload={"workspace_id": existing["id"], "workspace_name": existing["name"]},
        )
        _emit_launch_ops_transition(
            db,
            tenant=tenant,
            actor=actor,
            previous=previous_launch_ops,
            current=_build_tenant_launch_ops_snapshot(tenant),
            trigger="onboarding_workspace_seeded",
        )
        db.commit()
    return {
        "workspace": existing,
        "onboarding": get_tenant_onboarding_snapshot(db, current_user=current_user),
    }


def create_tenant_member_invitation(
    db: Session,
    *,
    current_user: Any,
    email: str,
    role: str = "viewer",
    name: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    _assert_member_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    normalized_email = _normalize_invitation_email(email)
    normalized_role = _normalize_member_role(role)
    _assert_role_assignment_allowed(current_user, normalized_role)

    existing_member = next(
        (
            membership
            for membership in tenant.memberships
            if membership.status == "active" and membership.user and membership.user.email.strip().lower() == normalized_email
        ),
        None,
    )
    if existing_member is not None:
        raise ConflictError("That email is already an active member of this tenant.")

    existing_invitation = next(
        (
            invitation
            for invitation in tenant.invitations
            if invitation.email == normalized_email and invitation.status in {"pending", "expired", "cancelled"}
        ),
        None,
    )
    pending_invitations = [
        invitation
        for invitation in tenant.invitations
        if invitation.status == "pending" and not _is_invitation_expired(invitation) and invitation is not existing_invitation
    ]
    active_members = [membership for membership in tenant.memberships if membership.status == "active"]
    enforce_entitlement_limit(
        db,
        current_user,
        "organization_members",
        requested_total=len(active_members) + len(pending_invitations) + 1,
        resource_label="organization members",
    )

    expires_at = _utc_now() + timedelta(days=_INVITATION_EXPIRY_DAYS)
    if existing_invitation is None:
        existing_invitation = TenantInvitation(
            tenant_id=tenant.id,
            inviter_user_id=actor.id if actor else None,
            email=normalized_email,
            name=str(name or "").strip() or None,
            role=normalized_role,
            status="pending",
            invite_token=_generate_invitation_token(tenant),
            message=str(message or "").strip() or None,
            expires_at=expires_at,
            metadata_json={"last_sent_at": _utc_now().isoformat()},
        )
        db.add(existing_invitation)
    else:
        existing_invitation.inviter_user_id = actor.id if actor else existing_invitation.inviter_user_id
        existing_invitation.name = str(name or "").strip() or None
        existing_invitation.role = normalized_role
        existing_invitation.status = "pending"
        existing_invitation.invite_token = _generate_invitation_token(tenant)
        existing_invitation.message = str(message or "").strip() or None
        existing_invitation.expires_at = expires_at
        existing_invitation.accepted_at = None
        existing_invitation.cancelled_at = None
        existing_invitation.metadata_json = {
            **dict(existing_invitation.metadata_json or {}),
            "last_sent_at": _utc_now().isoformat(),
        }
    db.flush()
    record_audit_event(
        db,
        event_type="tenant.member_invited",
        tenant=tenant,
        user=actor,
        payload={"invitation_id": existing_invitation.id, "email": normalized_email, "role": normalized_role},
    )
    db.commit()
    return {
        "invitation": _serialize_invitation(existing_invitation),
        "support": get_tenant_support_snapshot(db, current_user=current_user),
    }


def update_tenant_membership_role(
    db: Session,
    *,
    current_user: Any,
    membership_id: str,
    role: str,
) -> dict[str, Any]:
    _assert_member_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    membership = next((item for item in tenant.memberships if item.id == str(membership_id or "").strip()), None)
    if membership is None:
        raise NotFoundError("The requested tenant membership could not be found.")
    _assert_membership_target_allowed(current_user, membership)
    normalized_role = _normalize_member_role(role)
    _assert_role_assignment_allowed(current_user, normalized_role)
    actor_subject = str(getattr(current_user, "auth_subject", "") or getattr(current_user, "user_id", "") or "").strip()
    if membership.user and membership.user.auth_subject == actor_subject:
        raise ValidationError("Update your role from another owner account to avoid locking yourself out.")

    if membership.role == "owner" and normalized_role != "owner":
        other_active_owners = [
            item for item in tenant.memberships if item.id != membership.id and item.status == "active" and item.role == "owner"
        ]
        if not other_active_owners:
            raise ValidationError("This tenant must keep at least one active owner.")

    previous_role = membership.role
    membership.role = normalized_role
    db.flush()
    record_audit_event(
        db,
        event_type="tenant.membership_role_updated",
        tenant=tenant,
        user=actor,
        payload={
            "membership_id": membership.id,
            "email": membership.user.email if membership.user else None,
            "from_role": previous_role,
            "to_role": normalized_role,
        },
    )
    db.commit()
    return {
        "membership": {
            "membership_id": membership.id,
            "user_id": membership.user.auth_subject if membership.user else None,
            "name": membership.user.name if membership.user else "Unknown user",
            "email": membership.user.email if membership.user else None,
            "role": membership.role,
            "status": membership.status,
            "is_default": membership.is_default,
        },
        "support": get_tenant_support_snapshot(db, current_user=current_user),
    }


def remove_tenant_membership(
    db: Session,
    *,
    current_user: Any,
    membership_id: str,
) -> dict[str, Any]:
    _assert_member_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    membership = next((item for item in tenant.memberships if item.id == str(membership_id or "").strip()), None)
    if membership is None:
        raise NotFoundError("The requested tenant membership could not be found.")
    _assert_membership_target_allowed(current_user, membership)
    actor_subject = str(getattr(current_user, "auth_subject", "") or getattr(current_user, "user_id", "") or "").strip()
    if membership.user and membership.user.auth_subject == actor_subject:
        raise ValidationError("Remove your membership from a different owner account to avoid locking yourself out.")
    if membership.role == "owner":
        other_active_owners = [
            item for item in tenant.memberships if item.id != membership.id and item.status == "active" and item.role == "owner"
        ]
        if not other_active_owners:
            raise ValidationError("This tenant must keep at least one active owner.")

    target_email = membership.user.email if membership.user else None
    target_user = membership.user
    target_role = membership.role
    db.delete(membership)
    db.flush()
    if target_user is not None:
        _normalize_default_memberships(list_user_memberships(db, target_user))
    record_audit_event(
        db,
        event_type="tenant.membership_removed",
        tenant=tenant,
        user=actor,
        payload={"email": target_email, "role": target_role},
    )
    db.commit()
    return {"support": get_tenant_support_snapshot(db, current_user=current_user)}


def run_tenant_invitation_action(
    db: Session,
    *,
    current_user: Any,
    invitation_id: str,
    action: str,
) -> dict[str, Any]:
    _assert_member_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    invitation = next((item for item in tenant.invitations if item.id == str(invitation_id or "").strip()), None)
    if invitation is None:
        raise NotFoundError("The requested invitation could not be found.")
    _assert_invitation_target_allowed(current_user, invitation)
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"cancel", "resend"}:
        raise ValidationError("Unsupported invitation action.")
    if invitation.status == "accepted":
        raise ValidationError("Accepted invitations cannot be changed.")

    if normalized_action == "cancel":
        invitation.status = "cancelled"
        invitation.cancelled_at = _utc_now()
        event_type = "tenant.invitation_cancelled"
    else:
        invitation.status = "pending"
        invitation.expires_at = _utc_now() + timedelta(days=_INVITATION_EXPIRY_DAYS)
        invitation.cancelled_at = None
        invitation.invite_token = _generate_invitation_token(tenant)
        invitation.metadata_json = {
            **dict(invitation.metadata_json or {}),
            "last_sent_at": _utc_now().isoformat(),
        }
        event_type = "tenant.invitation_resent"
    db.flush()
    record_audit_event(
        db,
        event_type=event_type,
        tenant=tenant,
        user=actor,
        payload={"invitation_id": invitation.id, "email": invitation.email, "role": invitation.role},
    )
    db.commit()
    return {
        "invitation": _serialize_invitation(invitation),
        "support": get_tenant_support_snapshot(db, current_user=current_user),
    }


def get_tenant_support_snapshot(db: Session, *, current_user: Any, limit: int = 20) -> dict[str, Any]:
    from backend.services.job_queue_service import run_due_jobs

    _assert_support_operator(current_user)
    run_due_jobs(db, limit=max(1, int(getattr(settings, "job_worker_batch_size", 12) or 12)))
    tenant = _resolve_tenant_for_current_user(db, current_user)
    billing = get_billing_summary(db, current_user)
    onboarding = get_tenant_onboarding_snapshot(db, current_user=current_user)
    launch_ops = _build_tenant_launch_ops_snapshot(tenant, onboarding=onboarding)
    actor_subject = str(getattr(current_user, "auth_subject", "") or getattr(current_user, "user_id", "") or "").strip()
    memberships = [
        {
            "membership_id": membership.id,
            "user_id": membership.user.auth_subject if membership.user else None,
            "name": membership.user.name if membership.user else "Unknown user",
            "email": membership.user.email if membership.user else None,
            "role": membership.role,
            "status": membership.status,
            "is_default": membership.is_default,
            "is_current_user": bool(membership.user and membership.user.auth_subject == actor_subject),
            "can_edit_role": current_user_has_permission(current_user, "tenant.manage_members")
            and not (membership.user and membership.user.auth_subject == actor_subject)
            and (membership.role != "owner" or _can_manage_owner_role(current_user)),
            "can_remove": current_user_has_permission(current_user, "tenant.manage_members")
            and not (membership.user and membership.user.auth_subject == actor_subject)
            and (membership.role != "owner" or _can_manage_owner_role(current_user)),
        }
        for membership in tenant.memberships
    ]
    invitations = [_serialize_invitation(invitation) for invitation in tenant.invitations]
    timeline = list_audit_events_for_tenant(db, tenant_id=tenant.id, limit=limit)
    role_options = [
        {"key": role_key, "label": role_key.replace("_", " ").title(), "assignable": role_key != "owner" or _can_manage_owner_role(current_user)}
        for role_key in ("viewer", "analyst", "trader", "admin", "owner")
    ]
    return {
        "tenant": build_tenant_payload(tenant),
        "status": tenant.status,
        "billing": billing,
        "onboarding": onboarding,
        "memberships": {"items": memberships, "count": len(memberships)},
        "invitations": {"items": invitations, "count": len(invitations)},
        "timeline": {"items": timeline, "count": len(timeline)},
        "launch_ops": launch_ops,
        "support_actions": {
            "can_pause": current_user_has_permission(current_user, "tenant.change_status") and tenant.status != "paused",
            "can_resume": current_user_has_permission(current_user, "tenant.change_status")
            and tenant.status == "paused"
            and (not launch_ops["enabled"] or launch_ops["launch_ready"]),
            "resume_blockers": launch_ops["blockers"] if tenant.status == "paused" and launch_ops["enabled"] else [],
            "can_manage_members": current_user_has_permission(current_user, "tenant.manage_members"),
            "role_options": role_options,
        },
    }


def update_tenant_status(db: Session, *, current_user: Any, status: str) -> dict[str, Any]:
    _assert_platform_admin(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    previous_launch_ops = _build_tenant_launch_ops_snapshot(tenant)
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"active", "paused"}:
        raise ValidationError("Unsupported tenant status.")
    previous_status = tenant.status
    if previous_status != "active" and normalized_status == "active":
        onboarding = get_tenant_onboarding_snapshot(db, current_user=current_user)
        launch_ops = _build_tenant_launch_ops_snapshot(tenant, onboarding=onboarding)
        if launch_ops["enabled"] and not launch_ops["launch_ready"]:
            raise ValidationError(" ".join(launch_ops["blockers"]) or "Tenant launch path is not ready yet.")
    tenant.status = normalized_status
    db.flush()
    record_audit_event(
        db,
        event_type="tenant.status_updated",
        tenant=tenant,
        user=actor,
        payload={"from_status": previous_status, "to_status": normalized_status},
    )
    _emit_launch_ops_transition(
        db,
        tenant=tenant,
        actor=actor,
        previous=previous_launch_ops,
        current=_build_tenant_launch_ops_snapshot(tenant),
        trigger="status_updated",
    )
    db.commit()
    db.refresh(tenant)
    return {
        "tenant": build_tenant_payload(tenant),
        "status": tenant.status,
        "message": f"Tenant status updated from {previous_status} to {normalized_status}.",
    }


def build_identity_payload(
    db: Session,
    *,
    user: User,
    requested_tenant_slug: str | None = None,
) -> dict[str, Any]:
    memberships = list_user_memberships(db, user)
    active_membership = resolve_active_membership(db, user=user, requested_tenant_slug=requested_tenant_slug)
    active_tenant = active_membership.tenant
    return {
        "user": user,
        "active_membership": active_membership,
        "active_tenant": active_tenant,
        "memberships": memberships,
        "memberships_payload": tuple(_membership_payload(membership) for membership in memberships),
    }


def ensure_demo_tenant_for_user(
    db: Session,
    *,
    auth_subject: str,
    email: str,
    name: str,
    provider: str,
    requested_tenant_slug: str | None = None,
) -> dict[str, Any]:
    requested_tenant_slug = normalize_desk_slug(requested_tenant_slug or settings.demo_tenant_slug)
    user = ensure_user(
        db,
        auth_subject=auth_subject,
        email=email,
        name=name,
        provider=provider,
        platform_role="admin",
    )

    memberships = list_user_memberships(db, user)
    legacy_demo_slug = "alpha-desk"
    canonical_demo_slug = normalize_desk_slug(settings.demo_tenant_slug)
    legacy_tenant = db.execute(select(Tenant).where(Tenant.slug == legacy_demo_slug)).scalar_one_or_none()
    canonical_tenant = db.execute(select(Tenant).where(Tenant.slug == canonical_demo_slug)).scalar_one_or_none()
    changed = False

    if legacy_tenant is not None and canonical_tenant is None and normalize_desk_slug(legacy_tenant.slug) == SYSTEMATIC_DESK_SLUG:
        legacy_tenant.slug = canonical_demo_slug
        legacy_tenant.name = resolve_desk_name(canonical_demo_slug, settings.demo_tenant_name)
        canonical_tenant = legacy_tenant
        changed = True

    memberships_by_slug = {
        normalize_desk_slug(membership.tenant.slug): membership
        for membership in memberships
    }

    for index, desk in enumerate(default_desk_definitions()):
        desk_slug = normalize_desk_slug(desk["slug"])
        desk_name = resolve_desk_name(desk_slug, desk.get("name"))
        membership = memberships_by_slug.get(desk_slug)
        tenant = None
        if membership is not None:
            tenant = membership.tenant
        if tenant is None:
            tenant = db.execute(select(Tenant).where(Tenant.slug == desk_slug)).scalar_one_or_none()
        if tenant is None and desk_slug == SYSTEMATIC_DESK_SLUG and canonical_tenant is not None:
            tenant = canonical_tenant
        if tenant is None:
            tenant = Tenant(
                slug=desk_slug,
                name=desk_name,
                status="active",
                plan_key=settings.demo_tenant_plan,
                billing_email=email,
                brand_settings={"theme": "dark-trader"},
            )
            db.add(tenant)
            db.flush()
            _ensure_subscription(db, tenant)
            _seed_default_entitlements(db, tenant)
            record_audit_event(
                db,
                event_type="tenant.seeded",
                tenant=tenant,
                user=user,
                payload={"seed": "demo", "desk_slug": desk_slug},
            )
            changed = True
        else:
            if tenant.slug != desk_slug:
                tenant.slug = desk_slug
                changed = True
            if tenant.name != desk_name:
                tenant.name = desk_name
                changed = True
            if tenant.status != "active":
                tenant.status = "active"
                changed = True
            if not tenant.billing_email:
                tenant.billing_email = email
                changed = True

        if membership is None:
            membership = TenantMembership(
                tenant=tenant,
                user=user,
                role="owner",
                status="active",
                is_default=index == 0 and not memberships,
            )
            db.add(membership)
            memberships.append(membership)
            memberships_by_slug[desk_slug] = membership
            record_audit_event(
                db,
                event_type="membership.seeded",
                tenant=tenant,
                user=user,
                payload={"role": "owner", "desk_slug": desk_slug},
            )
            changed = True

    if db.new:
        db.flush()
    changed = _normalize_default_memberships(memberships) or changed
    if changed or db.new or db.dirty:
        db.commit()

    payload = build_identity_payload(db, user=user, requested_tenant_slug=requested_tenant_slug)
    return payload


def list_tenants_for_current_user(db: Session, user: User) -> dict[str, Any]:
    memberships = list_user_memberships(db, user)
    items = []
    for membership in memberships:
        tenant_payload = build_tenant_payload(membership.tenant)
        tenant_payload["membership_role"] = membership.role
        tenant_payload["is_default"] = membership.is_default
        items.append(tenant_payload)
    return {"items": items, "count": len(items)}


def activate_tenant_for_user(db: Session, *, user: User, tenant_slug: str) -> dict[str, Any]:
    memberships = list_user_memberships(db, user)
    normalized_slug = normalize_desk_slug(tenant_slug)
    target_membership = next((membership for membership in memberships if membership.tenant.slug == normalized_slug), None)
    if target_membership is None:
        raise NotFoundError("Requested tenant was not found for this account.")

    for membership in memberships:
        membership.is_default = membership.id == target_membership.id

    record_audit_event(
        db,
        event_type="membership.default_changed",
        tenant=target_membership.tenant,
        user=user,
        payload={"tenant_slug": normalized_slug},
    )
    db.commit()

    payload = build_identity_payload(db, user=user, requested_tenant_slug=normalized_slug)
    return {
        "active_tenant": _membership_payload(payload["active_membership"]),
        "memberships": payload["memberships_payload"],
    }


def update_tenant_branding(db: Session, *, current_user: Any, updates: dict[str, Any]) -> dict[str, Any]:
    if not updates:
        raise ValidationError("No branding fields were provided.")

    _assert_tenant_brand_manager(current_user)
    require_entitlement(db, current_user, "tenant_branding", message="Tenant branding is not enabled for the active plan.")
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    previous_launch_ops = _build_tenant_launch_ops_snapshot(tenant)

    changed_fields: list[str] = []
    if "name" in updates and updates["name"]:
        tenant.name = str(updates["name"]).strip()
        changed_fields.append("name")

    if "billing_email" in updates:
        tenant.billing_email = updates["billing_email"]
        changed_fields.append("billing_email")

    if "logo_url" in updates:
        tenant.logo_url = updates["logo_url"]
        changed_fields.append("logo_url")

    brand_settings = dict(tenant.brand_settings or {})
    for key in (
        "app_name",
        "app_tagline",
        "accent_primary",
        "accent_secondary",
        "background_color",
        "surface_color",
        "text_color",
        "support_email",
        "support_url",
    ):
        if key not in updates:
            continue
        if updates[key] is None:
            brand_settings.pop(key, None)
        else:
            brand_settings[key] = updates[key]
        changed_fields.append(key)

    tenant.brand_settings = brand_settings
    db.flush()
    record_audit_event(
        db,
        event_type="tenant.branding_updated",
        tenant=tenant,
        user=actor,
        payload={"fields": changed_fields},
    )
    _emit_launch_ops_transition(
        db,
        tenant=tenant,
        actor=actor,
        previous=previous_launch_ops,
        current=_build_tenant_launch_ops_snapshot(tenant),
        trigger="branding_updated",
    )
    db.commit()
    db.refresh(tenant)
    return build_tenant_payload(tenant)
