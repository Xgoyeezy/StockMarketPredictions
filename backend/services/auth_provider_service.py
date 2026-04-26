from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import time
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.saas import User
from backend.services.exceptions import NotFoundError, ServiceError, UnauthorizedError, ValidationError
from backend.services.tenant_service import (
    build_identity_payload,
    claim_tenant_invitation_by_token,
    create_tenant,
    ensure_user,
    get_tenant_auth_routing,
    list_user_memberships,
    resolve_tenant_invitation_by_token,
    resolve_tenant_by_slug,
)

_PROVIDER_LABELS = {
    "local-demo": "Demo auth",
    "local-session": "Local session",
    "clerk": "Clerk",
    "auth0": "Auth0",
    "oidc": "Enterprise OIDC",
}
_REDIRECT_PROVIDER_KEYS = ("auth0", "oidc")
_SUPPORTED_SESSION_PROVIDERS = {"local-session", "auth0", "oidc"}


def get_auth_mode() -> str:
    if settings.auth_enabled:
        return "configured"
    if settings.allow_demo_auth:
        return "demo"
    return "disabled"


def _normalize_auth0_domain(raw_domain: str) -> str:
    cleaned = str(raw_domain or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if cleaned.startswith("https://") or cleaned.startswith("http://"):
        return cleaned
    return f"https://{cleaned}"


def _normalize_url(raw_value: str | None) -> str:
    cleaned = str(raw_value or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if cleaned.startswith("https://") or cleaned.startswith("http://"):
        return cleaned
    return f"https://{cleaned}"


def _normalized_provider_key(value: str | None) -> str | None:
    cleaned = str(value or "").strip().lower()
    return cleaned or None


def _normalize_provider_keys(values: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        cleaned = _normalized_provider_key(value)
        if cleaned in {"local-session", * _REDIRECT_PROVIDER_KEYS} and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized


def _normalize_redirect_path(raw_path: str | None) -> str:
    cleaned = str(raw_path or "").strip()
    if not cleaned:
        return "/"
    parsed = urlparse.urlsplit(cleaned)
    if parsed.scheme or parsed.netloc:
        return "/"
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return urlparse.urlunsplit(("", "", path, parsed.query, parsed.fragment))


def _mask_email(email: str | None) -> str | None:
    cleaned = str(email or "").strip().lower()
    if not cleaned or "@" not in cleaned:
        return None
    mailbox, domain = cleaned.split("@", 1)
    if len(mailbox) <= 2:
        masked_mailbox = f"{mailbox[:1]}***"
    else:
        masked_mailbox = f"{mailbox[:2]}***"
    return f"{masked_mailbox}@{domain}"


def _extract_email_domain(email: str | None) -> str | None:
    cleaned = str(email or "").strip().lower()
    if not cleaned or "@" not in cleaned:
        return None
    return cleaned.split("@", 1)[1]


def _build_frontend_redirect_url(
    *,
    redirect_path: str | None = None,
    requested_tenant_slug: str | None = None,
    invite_token: str | None = None,
    auth_error: str | None = None,
    auth_error_description: str | None = None,
) -> str:
    base_url = settings.auth_post_login_redirect_url
    target = urlparse.urljoin(base_url, _normalize_redirect_path(redirect_path))
    parsed = urlparse.urlsplit(target)
    query = dict(urlparse.parse_qsl(parsed.query, keep_blank_values=False))
    if requested_tenant_slug:
        query["tenant"] = requested_tenant_slug
    if invite_token:
        query["invite"] = invite_token
    if auth_error:
        query["auth_error"] = auth_error
    if auth_error_description:
        query["auth_error_description"] = auth_error_description
    return urlparse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlparse.urlencode(query), parsed.fragment))


def build_auth_entry_context(
    *,
    db: Session | None = None,
    requested_tenant_slug: str | None = None,
    invite_token: str | None = None,
    redirect_path: str | None = None,
    login_email: str | None = None,
) -> dict:
    config = get_auth_provider_config()
    invitation = None
    target_tenant = None
    requested_slug = str(requested_tenant_slug or "").strip().lower() or None

    if invite_token:
        if db is None:
            from backend.core.database import SessionLocal

            with SessionLocal() as session:
                invitation = resolve_tenant_invitation_by_token(session, invite_token=invite_token)
        else:
            invitation = resolve_tenant_invitation_by_token(db, invite_token=invite_token)
        target_tenant = invitation.tenant
        requested_slug = invitation.tenant.slug
    elif requested_slug:
        if db is None:
            from backend.core.database import SessionLocal

            with SessionLocal() as session:
                target_tenant = resolve_tenant_by_slug(session, tenant_slug=requested_slug)
        else:
            target_tenant = resolve_tenant_by_slug(db, tenant_slug=requested_slug)

    tenant_auth_routing = get_tenant_auth_routing(target_tenant) if target_tenant is not None else {}
    public_tenant_auth_routing = {
        **tenant_auth_routing,
        "provider_records": [
            {
                key: value
                for key, value in record.items()
                if key not in {"client_secret", "pending_client_secret"}
            }
            for record in tenant_auth_routing.get("provider_records") or []
        ],
    }
    provider_selection = _resolve_auth_provider_selection(
        config=config,
        tenant_auth_routing=tenant_auth_routing,
        invitation_email=(invitation.email if invitation is not None else None),
        login_email=login_email,
    )
    public_provider_selection = {
        **provider_selection,
        "matched_provider_record": None,
        "default_provider_record": None,
        "provider_records": [
            {
                key: value
                for key, value in record.items()
                if key not in {"client_secret", "pending_client_secret"}
            }
            for record in provider_selection.get("provider_records") or []
        ],
    }
    resolved_redirect_path = _normalize_redirect_path(
        redirect_path
        or tenant_auth_routing.get("post_login_path")
        or (f"/?tenant={requested_slug}" if requested_slug else "/")
    )
    sso_ready = bool(provider_selection["external_login_available"] and provider_selection.get("launch_ready", True))
    preferred_provider = provider_selection["recommended_provider"] or (
        next(
            (
                provider["key"]
                for provider in config.get("available_providers", [])
                if provider.get("mode") == "redirect"
            ),
            "local-session" if config.get("local_session", {}).get("enabled") else config.get("provider"),
        )
    )
    entry_mode = (
        "tenant-sso"
        if target_tenant is not None and sso_ready and preferred_provider in _REDIRECT_PROVIDER_KEYS
        else ("invite" if invitation is not None else ("tenant" if target_tenant is not None else "generic"))
    )

    return {
        "entry_mode": entry_mode,
        "preferred_provider": preferred_provider,
        "supports_local_session": bool(provider_selection["local_login_available"]),
        "supports_external_provider": bool(provider_selection["external_login_available"]),
        "signup_allowed": bool(config.get("supports_signup")),
        "tenant": (
            {
                "slug": target_tenant.slug,
                "name": target_tenant.name,
                "status": target_tenant.status,
                "logo_url": target_tenant.logo_url,
                "auth_routing": public_tenant_auth_routing,
            }
            if target_tenant is not None
            else None
        ),
        "invite": (
            {
                "token_present": True,
                "email_masked": _mask_email(invitation.email),
                "role": invitation.role,
                "status": invitation.status,
            }
            if invitation is not None
            else {
                "token_present": False,
                "email_masked": None,
                "role": None,
                "status": None,
            }
        ),
        "routing": {
            "entry_path": tenant_auth_routing.get("entry_path") or (f"/login/{requested_slug}" if requested_slug else "/login"),
            "post_login_path": tenant_auth_routing.get("post_login_path") or (f"/?tenant={requested_slug}" if requested_slug else "/"),
            "redirect_path": resolved_redirect_path,
            "requires_sso": bool(provider_selection["requires_sso"]),
            "organization_hint": tenant_auth_routing.get("organization_hint"),
            "connection_hint": tenant_auth_routing.get("connection_hint"),
            "email_domain_hint": tenant_auth_routing.get("email_domain_hint"),
            "auth_policy": tenant_auth_routing.get("auth_policy") or "default",
            "preferred_provider": tenant_auth_routing.get("preferred_provider") or "default",
        },
        "provider_selection": public_provider_selection,
    }


def is_local_session_enabled() -> bool:
    provider_key = str(settings.auth_provider or "").strip().lower()
    return settings.auth_enabled and provider_key in {"local-session", "auth0", "oidc"}


def is_auth0_enabled() -> bool:
    return settings.auth_enabled and bool(
        str(getattr(settings, "auth0_domain", "") or "").strip()
        or str(getattr(settings, "auth0_client_id", "") or "").strip()
    )


def is_auth0_primary() -> bool:
    return settings.auth_enabled and str(settings.auth_provider or "").strip().lower() == "auth0"


def is_oidc_enabled() -> bool:
    return settings.auth_enabled and bool(
        str(getattr(settings, "oidc_issuer", "") or "").strip()
        or str(getattr(settings, "oidc_authorize_url", "") or "").strip()
    )


def is_oidc_primary() -> bool:
    return settings.auth_enabled and str(settings.auth_provider or "").strip().lower() == "oidc"


def is_auth0_ready() -> bool:
    return bool(
        is_auth0_enabled()
        and str(getattr(settings, "auth0_domain", "") or "").strip()
        and str(getattr(settings, "auth0_client_id", "") or "").strip()
        and str(getattr(settings, "auth0_client_secret", "") or "").strip()
        and settings.public_api_base_url
    )


def is_oidc_ready() -> bool:
    issuer = str(getattr(settings, "oidc_issuer", "") or "").strip()
    authorize_url = str(getattr(settings, "oidc_authorize_url", "") or "").strip()
    token_url = str(getattr(settings, "oidc_token_url", "") or "").strip()
    userinfo_url = str(getattr(settings, "oidc_userinfo_url", "") or "").strip()
    return bool(
        is_oidc_enabled()
        and str(getattr(settings, "oidc_client_id", "") or "").strip()
        and str(getattr(settings, "oidc_client_secret", "") or "").strip()
        and settings.public_api_base_url
        and (issuer or (authorize_url and token_url and userinfo_url))
    )


def build_auth0_callback_url() -> str:
    return f"{settings.public_api_base_url}/auth/callback"


def build_oidc_callback_url() -> str:
    return f"{settings.public_api_base_url}/auth/callback/oidc"


def _resolve_oidc_provider_metadata() -> dict[str, str | None]:
    issuer = _normalize_url(getattr(settings, "oidc_issuer", ""))
    authorize_url = _normalize_url(getattr(settings, "oidc_authorize_url", ""))
    token_url = _normalize_url(getattr(settings, "oidc_token_url", ""))
    userinfo_url = _normalize_url(getattr(settings, "oidc_userinfo_url", ""))
    logout_url = _normalize_url(getattr(settings, "oidc_logout_url", ""))

    if issuer and (not authorize_url or not token_url or not userinfo_url or not logout_url):
        discovery = _fetch_json(f"{issuer}/.well-known/openid-configuration")
        authorize_url = authorize_url or _normalize_url(discovery.get("authorization_endpoint"))
        token_url = token_url or _normalize_url(discovery.get("token_endpoint"))
        userinfo_url = userinfo_url or _normalize_url(discovery.get("userinfo_endpoint"))
        logout_url = logout_url or _normalize_url(
            discovery.get("end_session_endpoint") or discovery.get("logout_endpoint")
        )

    return {
        "issuer": issuer or None,
        "authorize_url": authorize_url or None,
        "token_url": token_url or None,
        "userinfo_url": userinfo_url or None,
        "logout_url": logout_url or None,
    }


def _resolve_provider_runtime_config(provider_key: str, provider_record: dict[str, object] | None = None) -> dict[str, object]:
    normalized_provider = _normalized_provider_key(provider_key)
    record = provider_record or {}
    if normalized_provider == "auth0":
        auth0_domain = _normalize_auth0_domain(record.get("auth0_domain") or getattr(settings, "auth0_domain", ""))
        return {
            "provider": "auth0",
            "domain": auth0_domain or None,
            "client_id": str(record.get("client_id") or getattr(settings, "auth0_client_id", "") or "").strip() or None,
            "client_secret": str(record.get("client_secret") or getattr(settings, "auth0_client_secret", "") or "").strip() or None,
            "audience": str(record.get("audience") or getattr(settings, "auth0_audience", "") or "").strip() or None,
            "scope": str(record.get("scope") or getattr(settings, "auth0_scope", "openid profile email") or "").strip() or "openid profile email",
            "organization_hint": str(record.get("organization_hint") or "").strip() or None,
            "connection_hint": str(record.get("connection_hint") or "").strip() or None,
            "allow_signup": bool(record.get("allow_signup")) if record.get("allow_signup") is not None else bool(getattr(settings, "auth0_allow_signup", True)),
        }
    if normalized_provider == "oidc":
        issuer = _normalize_url(record.get("issuer") or getattr(settings, "oidc_issuer", ""))
        authorize_url = _normalize_url(record.get("authorize_url") or getattr(settings, "oidc_authorize_url", ""))
        token_url = _normalize_url(record.get("token_url") or getattr(settings, "oidc_token_url", ""))
        userinfo_url = _normalize_url(record.get("userinfo_url") or getattr(settings, "oidc_userinfo_url", ""))
        logout_url = _normalize_url(record.get("logout_url") or getattr(settings, "oidc_logout_url", ""))
        if issuer and (not authorize_url or not token_url or not userinfo_url or not logout_url):
            discovery = _fetch_json(f"{issuer}/.well-known/openid-configuration")
            authorize_url = authorize_url or _normalize_url(discovery.get("authorization_endpoint"))
            token_url = token_url or _normalize_url(discovery.get("token_endpoint"))
            userinfo_url = userinfo_url or _normalize_url(discovery.get("userinfo_endpoint"))
            logout_url = logout_url or _normalize_url(
                discovery.get("end_session_endpoint") or discovery.get("logout_endpoint")
            )
        return {
            "provider": "oidc",
            "issuer": issuer or None,
            "authorize_url": authorize_url or None,
            "token_url": token_url or None,
            "userinfo_url": userinfo_url or None,
            "logout_url": logout_url or None,
            "client_id": str(record.get("client_id") or getattr(settings, "oidc_client_id", "") or "").strip() or None,
            "client_secret": str(record.get("client_secret") or getattr(settings, "oidc_client_secret", "") or "").strip() or None,
            "audience": str(record.get("audience") or getattr(settings, "oidc_audience", "") or "").strip() or None,
            "scope": str(record.get("scope") or getattr(settings, "oidc_scope", "openid profile email") or "").strip() or "openid profile email",
            "allow_signup": bool(record.get("allow_signup")) if record.get("allow_signup") is not None else bool(getattr(settings, "oidc_allow_signup", True)),
        }
    return {}


def _is_runtime_provider_ready(provider_key: str, provider_record: dict[str, object] | None = None) -> bool:
    runtime = _resolve_provider_runtime_config(provider_key, provider_record)
    if provider_key == "auth0":
        return bool(runtime.get("domain") and runtime.get("client_id") and runtime.get("client_secret") and settings.public_api_base_url)
    if provider_key == "oidc":
        return bool(
            runtime.get("client_id")
            and runtime.get("client_secret")
            and settings.public_api_base_url
            and (runtime.get("issuer") or (runtime.get("authorize_url") and runtime.get("token_url") and runtime.get("userinfo_url")))
        )
    return False


def inspect_provider_runtime_config(
    provider_key: str,
    *,
    provider_record: dict[str, object] | None = None,
) -> dict[str, object]:
    normalized_provider = _normalized_provider_key(provider_key)
    checked_at = datetime.now(timezone.utc).isoformat()
    if normalized_provider not in _REDIRECT_PROVIDER_KEYS:
        return {
            "health_status": "error",
            "health_message": "Unsupported auth provider.",
            "last_checked_at": checked_at,
            "discovery_source": None,
            "resolved_authorize_url": None,
            "resolved_token_url": None,
            "resolved_userinfo_url": None,
            "resolved_logout_url": None,
        }

    provider_record = provider_record or {}
    try:
        runtime = _resolve_provider_runtime_config(normalized_provider, provider_record)
    except Exception as exc:
        return {
            "health_status": "error",
            "health_message": str(exc) or "Provider discovery failed.",
            "last_checked_at": checked_at,
            "discovery_source": None,
            "resolved_authorize_url": None,
            "resolved_token_url": None,
            "resolved_userinfo_url": None,
            "resolved_logout_url": None,
        }

    issues: list[str] = []
    discovery_source: str | None = None
    resolved_authorize_url = None
    resolved_token_url = None
    resolved_userinfo_url = None
    resolved_logout_url = None
    message = None

    if normalized_provider == "auth0":
        if not runtime.get("domain"):
            issues.append("Auth0 domain is missing.")
        if not runtime.get("client_id"):
            issues.append("Client ID is missing.")
        if not runtime.get("client_secret"):
            issues.append("Client secret is missing.")
        resolved_authorize_url = f"{str(runtime.get('domain') or '').rstrip('/')}/authorize" if runtime.get("domain") else None
        resolved_token_url = _auth0_token_endpoint(provider_record) if runtime.get("domain") else None
        resolved_userinfo_url = _auth0_userinfo_endpoint(provider_record) if runtime.get("domain") else None
        resolved_logout_url = build_auth0_logout_url(provider_record=provider_record)
        if not issues and runtime.get("domain"):
            discovery_source = "auth0-well-known"
            try:
                _fetch_json(f"{str(runtime.get('domain') or '').rstrip('/')}/.well-known/openid-configuration")
                message = "Auth0 tenant metadata resolved successfully."
            except Exception as exc:
                return {
                    "health_status": "error",
                    "health_message": str(exc) or "Auth0 discovery failed.",
                    "last_checked_at": checked_at,
                    "discovery_source": discovery_source,
                    "resolved_authorize_url": resolved_authorize_url,
                    "resolved_token_url": resolved_token_url,
                    "resolved_userinfo_url": resolved_userinfo_url,
                    "resolved_logout_url": resolved_logout_url,
                }
    elif normalized_provider == "oidc":
        if not runtime.get("client_id"):
            issues.append("Client ID is missing.")
        if not runtime.get("client_secret"):
            issues.append("Client secret is missing.")
        if not runtime.get("issuer") and not (
            runtime.get("authorize_url") and runtime.get("token_url") and runtime.get("userinfo_url")
        ):
            issues.append("Add an issuer or provide authorize, token, and userinfo endpoints.")
        resolved_authorize_url = runtime.get("authorize_url")
        resolved_token_url = runtime.get("token_url")
        resolved_userinfo_url = runtime.get("userinfo_url")
        resolved_logout_url = runtime.get("logout_url")
        if not issues:
            if runtime.get("issuer"):
                discovery_source = "issuer-discovery"
                try:
                    discovery = _fetch_json(f"{str(runtime.get('issuer') or '').rstrip('/')}/.well-known/openid-configuration")
                    resolved_authorize_url = _normalize_url(discovery.get("authorization_endpoint")) or resolved_authorize_url
                    resolved_token_url = _normalize_url(discovery.get("token_endpoint")) or resolved_token_url
                    resolved_userinfo_url = _normalize_url(discovery.get("userinfo_endpoint")) or resolved_userinfo_url
                    resolved_logout_url = (
                        _normalize_url(discovery.get("end_session_endpoint") or discovery.get("logout_endpoint"))
                        or resolved_logout_url
                    )
                    message = "OIDC discovery metadata resolved successfully."
                except Exception as exc:
                    if resolved_authorize_url and resolved_token_url and resolved_userinfo_url:
                        discovery_source = "manual-fallback"
                        message = f"Issuer discovery failed; manual endpoints remain configured. {str(exc) or ''}".strip()
                    else:
                        return {
                            "health_status": "error",
                            "health_message": str(exc) or "OIDC discovery failed.",
                            "last_checked_at": checked_at,
                            "discovery_source": discovery_source,
                            "resolved_authorize_url": resolved_authorize_url,
                            "resolved_token_url": resolved_token_url,
                            "resolved_userinfo_url": resolved_userinfo_url,
                            "resolved_logout_url": resolved_logout_url,
                        }
            else:
                discovery_source = "manual"
                message = "Manual OIDC endpoints are configured."

    if issues:
        return {
            "health_status": "incomplete",
            "health_message": " ".join(issues),
            "last_checked_at": checked_at,
            "discovery_source": discovery_source,
            "resolved_authorize_url": resolved_authorize_url,
            "resolved_token_url": resolved_token_url,
            "resolved_userinfo_url": resolved_userinfo_url,
            "resolved_logout_url": resolved_logout_url,
        }

    return {
        "health_status": "ready",
        "health_message": message or "Provider configuration is ready.",
        "last_checked_at": checked_at,
        "discovery_source": discovery_source,
        "resolved_authorize_url": resolved_authorize_url,
        "resolved_token_url": resolved_token_url,
        "resolved_userinfo_url": resolved_userinfo_url,
        "resolved_logout_url": resolved_logout_url,
    }


def build_auth0_logout_url(return_to: str | None = None, provider_record: dict[str, object] | None = None) -> str | None:
    runtime = _resolve_provider_runtime_config("auth0", provider_record)
    if not _is_runtime_provider_ready("auth0", provider_record):
        return None
    base_domain = str(runtime.get("domain") or "")
    query = {
        "client_id": str(runtime.get("client_id") or "").strip(),
        "returnTo": return_to or settings.auth_post_logout_redirect_url,
    }
    return f"{base_domain}/v2/logout?{urlparse.urlencode(query)}"


def build_oidc_logout_url(return_to: str | None = None, provider_record: dict[str, object] | None = None) -> str | None:
    if not _is_runtime_provider_ready("oidc", provider_record):
        return None
    runtime = _resolve_provider_runtime_config("oidc", provider_record)
    logout_url = runtime.get("logout_url")
    if not logout_url:
        return None
    parsed = urlparse.urlsplit(logout_url)
    query = dict(urlparse.parse_qsl(parsed.query, keep_blank_values=False))
    query["post_logout_redirect_uri"] = return_to or settings.auth_post_logout_redirect_url
    if str(runtime.get("client_id") or "").strip():
        query["client_id"] = str(runtime.get("client_id") or "").strip()
    return urlparse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlparse.urlencode(query), parsed.fragment))


def build_provider_logout_url(
    provider_key: str | None,
    return_to: str | None = None,
    provider_record: dict[str, object] | None = None,
) -> str | None:
    normalized_provider = _normalized_provider_key(provider_key)
    if normalized_provider == "auth0":
        return build_auth0_logout_url(return_to=return_to, provider_record=provider_record)
    if normalized_provider == "oidc":
        return build_oidc_logout_url(return_to=return_to, provider_record=provider_record)
    return None


def get_auth_provider_config() -> dict:
    mode = get_auth_mode()
    provider_key = str(settings.auth_provider or "local-demo").strip().lower() or "local-demo"
    local_session_enabled = is_local_session_enabled()
    auth0_enabled = is_auth0_enabled()
    auth0_ready = is_auth0_ready()
    oidc_enabled = is_oidc_enabled()
    oidc_ready = is_oidc_ready()
    oidc_issuer = _normalize_url(getattr(settings, "oidc_issuer", ""))
    oidc_authorize_url = _normalize_url(getattr(settings, "oidc_authorize_url", ""))
    oidc_userinfo_url = _normalize_url(getattr(settings, "oidc_userinfo_url", ""))
    auth_disabled_for_ui = bool(settings.allow_demo_auth)
    return {
        "enabled": settings.auth_enabled,
        "demo_allowed": settings.allow_demo_auth,
        "provider": "local-demo" if auth_disabled_for_ui else provider_key,
        "provider_label": "Local Demo" if auth_disabled_for_ui else _PROVIDER_LABELS.get(provider_key, provider_key.replace("-", " ").title()),
        "environment": settings.environment,
        "mode": "demo" if auth_disabled_for_ui else mode,
        "supports_login": False if auth_disabled_for_ui else settings.auth_enabled or local_session_enabled or auth0_ready or oidc_ready,
        "supports_logout": False if auth_disabled_for_ui else settings.auth_enabled or local_session_enabled or auth0_enabled or oidc_enabled,
        "supports_signup": False if auth_disabled_for_ui else (
            (local_session_enabled and bool(getattr(settings, "local_auth_allow_signup", True)))
            or (auth0_ready and bool(getattr(settings, "auth0_allow_signup", True)))
            or (oidc_ready and bool(getattr(settings, "oidc_allow_signup", True)))
        ),
        "supports_org_switch": False if auth_disabled_for_ui else settings.auth_enabled or local_session_enabled or auth0_enabled or oidc_enabled,
        "supports_invite_claim": False if auth_disabled_for_ui else settings.auth_enabled or local_session_enabled or auth0_enabled or oidc_enabled,
        "available_providers": [] if auth_disabled_for_ui else [
            provider
            for provider in [
                {
                    "key": "local-session",
                    "label": _PROVIDER_LABELS["local-session"],
                    "enabled": local_session_enabled,
                    "mode": "form",
                    "primary": provider_key == "local-session",
                },
                {
                    "key": "auth0",
                    "label": _PROVIDER_LABELS["auth0"],
                    "enabled": auth0_ready,
                    "mode": "redirect",
                    "primary": provider_key == "auth0",
                },
                {
                    "key": "oidc",
                    "label": _PROVIDER_LABELS["oidc"],
                    "enabled": oidc_ready,
                    "mode": "redirect",
                    "primary": provider_key == "oidc",
                },
            ]
            if provider["enabled"]
        ],
        "local_session": {
            "enabled": local_session_enabled,
            "cookie_name": settings.auth_session_cookie_name,
            "max_age_seconds": settings.auth_session_max_age_seconds,
            "allow_signup": bool(getattr(settings, "local_auth_allow_signup", True)),
            "default_plan": getattr(settings, "local_auth_default_plan", "starter"),
        },
        "auth0": {
            "enabled": auth0_enabled,
            "ready": auth0_ready,
            "primary": is_auth0_primary(),
            "domain": _normalize_auth0_domain(getattr(settings, "auth0_domain", "")),
            "scope": str(getattr(settings, "auth0_scope", "openid profile email") or "").strip() or "openid profile email",
            "audience_configured": bool(str(getattr(settings, "auth0_audience", "") or "").strip()),
            "organization_hint": str(getattr(settings, "auth0_organization", "") or "").strip() or None,
            "callback_url": build_auth0_callback_url() if auth0_enabled else None,
            "post_login_redirect_url": settings.auth_post_login_redirect_url if auth0_enabled else None,
            "post_logout_redirect_url": settings.auth_post_logout_redirect_url if auth0_enabled else None,
        },
        "oidc": {
            "enabled": oidc_enabled,
            "ready": oidc_ready,
            "primary": is_oidc_primary(),
            "issuer": oidc_issuer or None,
            "scope": str(getattr(settings, "oidc_scope", "openid profile email") or "").strip() or "openid profile email",
            "audience_configured": bool(str(getattr(settings, "oidc_audience", "") or "").strip()),
            "callback_url": build_oidc_callback_url() if oidc_enabled else None,
            "authorize_url": oidc_authorize_url or None,
            "userinfo_url": oidc_userinfo_url or None,
            "post_login_redirect_url": settings.auth_post_login_redirect_url if oidc_enabled else None,
            "post_logout_redirect_url": settings.auth_post_logout_redirect_url if oidc_enabled else None,
        },
    }


def _resolve_auth_provider_selection(
    *,
    config: dict,
    tenant_auth_routing: dict[str, object] | None,
    invitation_email: str | None = None,
    login_email: str | None = None,
) -> dict[str, object]:
    routing = tenant_auth_routing or {}
    auth_policy = str(routing.get("auth_policy") or "default").strip().lower() or "default"
    preferred_provider = str(routing.get("preferred_provider") or "default").strip().lower() or "default"
    email_domain_hint = str(routing.get("email_domain_hint") or "").strip().lower() or None
    effective_email = str(login_email or invitation_email or "").strip().lower() or None
    email_domain = _extract_email_domain(effective_email)
    configured_providers = {
        provider["key"]: provider
        for provider in config.get("available_providers", [])
        if provider.get("enabled")
    }
    raw_provider_records = routing.get("provider_records") or []
    provider_records: list[dict[str, object]] = []
    seen_record_ids: set[str] = set()
    for raw_record in raw_provider_records:
        if not isinstance(raw_record, dict):
            continue
        provider_key = _normalized_provider_key(raw_record.get("provider_key"))
        if provider_key not in _REDIRECT_PROVIDER_KEYS:
            continue
        record_id = str(raw_record.get("id") or raw_record.get("provider_id") or "").strip().lower()
        if not record_id or record_id in seen_record_ids:
            continue
        seen_record_ids.add(record_id)
        email_domains: list[str] = []
        seen_domains: set[str] = set()
        for raw_domain in raw_record.get("email_domains") or []:
            cleaned_domain = str(raw_domain or "").strip().lower()
            if not cleaned_domain or cleaned_domain in seen_domains:
                continue
            seen_domains.add(cleaned_domain)
            email_domains.append(cleaned_domain)
        provider_records.append(
            {
                "id": record_id,
                "provider_key": provider_key,
                "label": str(raw_record.get("label") or _PROVIDER_LABELS.get(provider_key, provider_key.title())).strip(),
                "enabled": bool(raw_record.get("enabled", True)),
                "email_domains": email_domains,
                "organization_hint": str(raw_record.get("organization_hint") or "").strip() or None,
                "connection_hint": str(raw_record.get("connection_hint") or "").strip() or None,
                "auth0_domain": str(raw_record.get("auth0_domain") or "").strip() or None,
                "issuer": str(raw_record.get("issuer") or "").strip() or None,
                "authorize_url": str(raw_record.get("authorize_url") or "").strip() or None,
                "token_url": str(raw_record.get("token_url") or "").strip() or None,
                "userinfo_url": str(raw_record.get("userinfo_url") or "").strip() or None,
                "logout_url": str(raw_record.get("logout_url") or "").strip() or None,
                "client_id": str(raw_record.get("client_id") or "").strip() or None,
                "client_secret": str(raw_record.get("client_secret") or "").strip() or None,
                "has_client_secret": bool(raw_record.get("client_secret")),
                "audience": str(raw_record.get("audience") or "").strip() or None,
                "scope": str(raw_record.get("scope") or "").strip() or None,
                "allow_signup": raw_record.get("allow_signup"),
                "ready": _is_runtime_provider_ready(provider_key, raw_record),
                "health_status": str(raw_record.get("health_status") or "").strip().lower() or "unchecked",
                "health_message": str(raw_record.get("health_message") or "").strip() or None,
                "has_pending_client_secret": bool(raw_record.get("pending_client_secret")),
                "is_default": bool(raw_record.get("is_default")),
            }
        )
    provider_records = [record for record in provider_records if record["enabled"]]
    tenant_record_keys = {record["provider_key"] for record in provider_records}
    ready_tenant_record_keys = {
        record["provider_key"]
        for record in provider_records
        if record["ready"] and record["health_status"] == "ready" and record["has_client_secret"]
    }
    for provider_key in _REDIRECT_PROVIDER_KEYS:
        if provider_key not in configured_providers and provider_key in ready_tenant_record_keys:
            configured_providers[provider_key] = {
                "key": provider_key,
                "label": _PROVIDER_LABELS.get(provider_key, provider_key.title()),
                "enabled": True,
                "mode": "redirect",
                "primary": False,
            }
    requested_provider_keys = _normalize_provider_keys(routing.get("enabled_providers"))
    enabled_provider_keys = requested_provider_keys or list(configured_providers)
    enabled_provider_keys = [key for key in enabled_provider_keys if key in configured_providers]
    launch_ready_external_provider_keys = [
        key
        for key in enabled_provider_keys
        if key in _REDIRECT_PROVIDER_KEYS
        and (
            (key in tenant_record_keys and key in ready_tenant_record_keys)
            or (key not in tenant_record_keys and key in configured_providers)
        )
    ]
    default_provider_record = next((record for record in provider_records if record["is_default"]), None)
    matching_provider_records = [
        record for record in provider_records if email_domain and email_domain in record["email_domains"]
    ]
    matched_provider_record = matching_provider_records[0] if matching_provider_records else None
    legacy_domain_match = bool(email_domain and email_domain_hint and email_domain == email_domain_hint)
    domain_match = bool(matched_provider_record or legacy_domain_match)

    local_available = "local-session" in enabled_provider_keys and (
        auth_policy != "require_sso" or not launch_ready_external_provider_keys
    )
    external_provider_keys = [
        key
        for key in enabled_provider_keys
        if configured_providers.get(key, {}).get("mode") == "redirect"
        and auth_policy != "local_only"
        and key in launch_ready_external_provider_keys
    ]
    external_available = bool(external_provider_keys)
    launch_blockers: list[str] = []
    if auth_policy == "require_sso" and not external_available:
        if any(key in tenant_record_keys for key in enabled_provider_keys if key in _REDIRECT_PROVIDER_KEYS):
            launch_blockers.append("Tenant SSO is required, but no enabled provider record is validated and ready.")
        else:
            launch_blockers.append("Tenant SSO is required, but no launch-ready external provider is enabled.")
    blocked_external_provider_keys = [
        key for key in enabled_provider_keys if key in _REDIRECT_PROVIDER_KEYS and key not in launch_ready_external_provider_keys
    ]
    requires_sso = bool(external_provider_keys and auth_policy == "require_sso")
    prefers_sso = bool(
        external_provider_keys
        and (
            auth_policy in {"prefer_sso", "require_sso"}
            or domain_match
            or bool(invitation_email)
            or bool(routing.get("organization_hint"))
            or bool(routing.get("connection_hint"))
            or bool(default_provider_record)
        )
    )

    external_preference: list[str] = []
    if matched_provider_record and matched_provider_record["provider_key"] in external_provider_keys:
        external_preference.append(str(matched_provider_record["provider_key"]))
    elif default_provider_record and default_provider_record["provider_key"] in external_provider_keys:
        external_preference.append(str(default_provider_record["provider_key"]))
    if preferred_provider in external_provider_keys:
        external_preference.append(preferred_provider)
    configured_default_provider = _normalized_provider_key(config.get("provider"))
    if configured_default_provider in external_provider_keys and configured_default_provider not in external_preference:
        external_preference.append(configured_default_provider)
    for key in _REDIRECT_PROVIDER_KEYS:
        if key in external_provider_keys and key not in external_preference:
            external_preference.append(key)

    recommended_provider = None
    recommended_provider_record_id = None
    if preferred_provider == "local-session" and local_available:
        recommended_provider = "local-session"
    elif matched_provider_record and str(matched_provider_record["provider_key"]) in external_provider_keys:
        recommended_provider = str(matched_provider_record["provider_key"])
        recommended_provider_record_id = str(matched_provider_record["id"])
    elif preferred_provider in external_provider_keys:
        recommended_provider = preferred_provider
        preferred_record = next(
            (record for record in provider_records if record["provider_key"] == preferred_provider and record["is_default"]),
            None,
        )
        if preferred_record:
            recommended_provider_record_id = str(preferred_record["id"])
    elif default_provider_record and str(default_provider_record["provider_key"]) in external_provider_keys:
        recommended_provider = str(default_provider_record["provider_key"])
        recommended_provider_record_id = str(default_provider_record["id"])
    elif prefers_sso and external_preference:
        recommended_provider = external_preference[0]
    elif local_available:
        recommended_provider = "local-session"
    elif external_preference:
        recommended_provider = external_preference[0]
    elif launch_blockers and "local-session" in enabled_provider_keys:
        recommended_provider = "local-session"

    provider_options: list[dict[str, object]] = []
    ordered_provider_keys: list[str] = []
    if local_available:
        ordered_provider_keys.append("local-session")
    for key in external_preference:
        if key not in ordered_provider_keys:
            ordered_provider_keys.append(key)
    for key in enabled_provider_keys:
        if key not in ordered_provider_keys:
            ordered_provider_keys.append(key)

    emitted_record_ids: set[str] = set()
    for key in ordered_provider_keys:
        provider_meta = configured_providers.get(key)
        if not provider_meta:
            continue
        mode = provider_meta.get("mode", "redirect")
        if mode == "form" and not local_available:
            continue
        if mode == "redirect" and key not in external_provider_keys:
            continue
        if mode == "redirect":
            provider_records_for_key = [
                record
                for record in provider_records
                if record["provider_key"] == key and record.get("ready") and record.get("health_status") == "ready"
            ]
            if provider_records_for_key:
                for record in provider_records_for_key:
                    if record["id"] in emitted_record_ids:
                        continue
                    emitted_record_ids.add(str(record["id"]))
                    reason = (
                        "Matched your organization email domain."
                        if matched_provider_record and record["id"] == matched_provider_record["id"]
                        else "Default tenant SSO route."
                        if record["is_default"]
                        else "Available for organization sign-in."
                    )
                    if requires_sso:
                        reason = "Tenant policy requires SSO."
                    provider_options.append(
                        {
                            "key": key,
                            "label": record["label"],
                            "mode": mode,
                            "recommended": recommended_provider == key and recommended_provider_record_id == record["id"],
                            "reason": reason,
                            "provider_record_id": record["id"],
                            "provider_key": key,
                            "organization_hint": record.get("organization_hint"),
                            "connection_hint": record.get("connection_hint"),
                            "email_domains": list(record.get("email_domains") or []),
                            "is_default": bool(record.get("is_default")),
                            "health_status": record.get("health_status"),
                            "has_pending_client_secret": bool(record.get("has_pending_client_secret")),
                        }
                    )
                continue
        reason = (
            "Tenant policy requires SSO."
            if mode == "redirect" and requires_sso
            else "Matched your organization email domain."
            if mode == "redirect" and domain_match
            else "Preferred for tenant sign-in."
            if key == preferred_provider or (mode == "redirect" and prefers_sso)
            else "Fallback tenant access."
            if key == "local-session" and external_available
            else "Primary tenant sign-in path."
            if key == "local-session"
            else "Available for organization sign-in."
        )
        provider_options.append(
            {
                "key": key,
                "label": provider_meta.get("label", _PROVIDER_LABELS.get(key, key.title())),
                "mode": mode,
                "recommended": recommended_provider == key and recommended_provider_record_id is None,
                "reason": reason,
                "provider_record_id": None,
                "provider_key": key,
            }
        )

    return {
        "auth_policy": auth_policy,
        "preferred_provider": preferred_provider,
        "enabled_provider_keys": enabled_provider_keys,
        "effective_email": effective_email,
        "email_domain": email_domain,
        "domain_match": domain_match,
        "local_login_available": local_available,
        "external_login_available": external_available,
        "external_provider_keys": external_provider_keys,
        "launch_ready_external_provider_keys": launch_ready_external_provider_keys,
        "blocked_external_provider_keys": blocked_external_provider_keys,
        "block_local_login": requires_sso,
        "requires_sso": requires_sso,
        "launch_ready": not launch_blockers,
        "launch_blockers": launch_blockers,
        "recommended_provider": recommended_provider,
        "recommended_provider_record_id": recommended_provider_record_id,
        "matched_provider_record": matched_provider_record,
        "default_provider_record": default_provider_record,
        "provider_records": provider_records,
        "providers": provider_options,
    }


def _local_auth_subject(email: str) -> str:
    return f"local:{email.strip().lower()}"


def _normalize_email(email: str) -> str:
    cleaned = str(email or "").strip().lower()
    if not cleaned or "@" not in cleaned:
        raise ValidationError("A valid email address is required.")
    return cleaned


def _normalize_name(name: str) -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        raise ValidationError("A display name is required.")
    return cleaned


def _default_organization_name(name: str, email: str) -> str:
    first_name = _normalize_name(name).split()[0]
    mailbox = email.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
    mailbox_name = " ".join(part.capitalize() for part in mailbox.split() if part)
    seed = first_name or mailbox_name or "Trader"
    return f"{seed} Desk"


def _urlsafe_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _urlsafe_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(f"{raw}{padding}")


def _sign_payload(encoded_payload: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _build_signed_cookie_value(payload: dict, *, secret: str) -> str:
    encoded_payload = _urlsafe_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _sign_payload(encoded_payload, secret)
    return f"{encoded_payload}.{signature}"


def _read_signed_cookie(raw_cookie: str, *, secret: str) -> dict | None:
    if not raw_cookie or "." not in raw_cookie:
        return None
    encoded_payload, provided_signature = raw_cookie.rsplit(".", 1)
    expected_signature = _sign_payload(encoded_payload, secret)
    if not hmac.compare_digest(provided_signature, expected_signature):
        return None
    try:
        payload = json.loads(_urlsafe_decode(encoded_payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    return payload


def build_local_session_cookie_value(
    *,
    auth_subject: str,
    provider: str = "local-session",
    provider_record_id: str | None = None,
) -> str:
    payload = {
        "v": 1,
        "sub": str(auth_subject or "").strip(),
        "provider": str(provider or "local-session").strip().lower() or "local-session",
        "provider_record_id": str(provider_record_id or "").strip().lower() or None,
        "iat": int(time.time()),
    }
    return _build_signed_cookie_value(payload, secret=settings.auth_session_secret)


def read_local_session_cookie(request: Request) -> dict | None:
    raw_cookie = str(request.cookies.get(settings.auth_session_cookie_name) or "").strip()
    payload = _read_signed_cookie(raw_cookie, secret=settings.auth_session_secret)
    if not payload:
        return None
    if str(payload.get("provider") or "").strip().lower() not in _SUPPORTED_SESSION_PROVIDERS:
        return None
    if not str(payload.get("sub") or "").strip():
        return None
    return payload


def build_auth_state_cookie_value(
    *,
    state: str,
    provider: str | None = None,
    provider_record_id: str | None = None,
    requested_tenant_slug: str | None = None,
    invite_token: str | None = None,
    redirect_path: str | None = None,
) -> str:
    payload = {
        "v": 1,
        "state": state,
        "provider": _normalized_provider_key(provider),
        "provider_record_id": str(provider_record_id or "").strip().lower() or None,
        "requested_tenant_slug": str(requested_tenant_slug or "").strip().lower() or None,
        "invite_token": str(invite_token or "").strip() or None,
        "redirect_path": _normalize_redirect_path(redirect_path),
        "iat": int(time.time()),
    }
    return _build_signed_cookie_value(payload, secret=settings.auth_state_secret)


def read_auth_state_cookie(request: Request) -> dict | None:
    raw_cookie = str(request.cookies.get(settings.auth_state_cookie_name) or "").strip()
    payload = _read_signed_cookie(raw_cookie, secret=settings.auth_state_secret)
    if not payload:
        return None
    issued_at = int(payload.get("iat") or 0)
    if issued_at and (int(time.time()) - issued_at) > settings.auth_state_max_age_seconds:
        return None
    if not str(payload.get("state") or "").strip():
        return None
    return payload


def login_with_local_session(
    db: Session,
    *,
    email: str,
    name: str,
    requested_tenant_slug: str | None = None,
    invite_token: str | None = None,
    organization_name: str | None = None,
    create_organization_if_missing: bool = True,
) -> dict:
    if not is_local_session_enabled():
        raise UnauthorizedError("Local session auth is not enabled for this environment.")

    normalized_email = _normalize_email(email)
    normalized_name = _normalize_name(name)
    auth_subject = _local_auth_subject(normalized_email)
    user = ensure_user(
        db,
        auth_subject=auth_subject,
        email=normalized_email,
        name=normalized_name,
        provider="local-session",
        platform_role="member",
    )
    if invite_token:
        invitation = claim_tenant_invitation_by_token(db, user=user, invite_token=invite_token)
        requested_tenant_slug = invitation.tenant.slug
    memberships = list_user_memberships(db, user)
    created_organization = False
    if not memberships:
        if not create_organization_if_missing:
            raise UnauthorizedError("No organizations were found for this account.")
        if not settings.local_auth_allow_signup:
            raise UnauthorizedError("Self-serve signup is disabled for this environment.")
        tenant_name = str(organization_name or "").strip() or _default_organization_name(normalized_name, normalized_email)
        create_tenant(
            db,
            owner=user,
            name=tenant_name,
            plan_key=settings.local_auth_default_plan,
            billing_email=normalized_email,
        )
        created_organization = True

    try:
        identity = build_identity_payload(db, user=user, requested_tenant_slug=requested_tenant_slug)
    except NotFoundError:
        identity = build_identity_payload(db, user=user, requested_tenant_slug=None)
    return {
        "identity": identity,
        "cookie_value": build_local_session_cookie_value(auth_subject=user.auth_subject),
        "created_organization": created_organization,
    }


def _resolve_login_target(
    *,
    db: Session | None = None,
    requested_tenant_slug: str | None = None,
    invite_token: str | None = None,
) -> tuple[object | None, object | None, str | None, str | None]:
    invitation = None
    target_tenant = None
    requested_slug = str(requested_tenant_slug or "").strip().lower() or None
    if invite_token:
        if db is None:
            from backend.core.database import SessionLocal

            with SessionLocal() as session:
                invitation = resolve_tenant_invitation_by_token(session, invite_token=invite_token)
        else:
            invitation = resolve_tenant_invitation_by_token(db, invite_token=invite_token)
        if invitation.status != "pending":
            raise ValidationError("This invitation is no longer pending.")
        target_tenant = invitation.tenant
        if invitation.email:
            query_login_hint = invitation.email
        else:
            query_login_hint = None
        requested_slug = invitation.tenant.slug
    else:
        query_login_hint = None
        if requested_slug:
            try:
                if db is None:
                    from backend.core.database import SessionLocal

                    with SessionLocal() as session:
                        target_tenant = resolve_tenant_by_slug(session, tenant_slug=requested_slug)
                else:
                    target_tenant = resolve_tenant_by_slug(db, tenant_slug=requested_slug)
            except NotFoundError:
                target_tenant = None
        query_login_hint = None

    return invitation, target_tenant, requested_slug, query_login_hint


def _build_provider_callback_url(provider_key: str) -> str:
    normalized_provider = _normalized_provider_key(provider_key)
    if normalized_provider == "auth0":
        return build_auth0_callback_url()
    if normalized_provider == "oidc":
        return build_oidc_callback_url()
    raise ValidationError("Unsupported auth provider.")


def _build_auth0_authorize_url(
    *,
    state: str,
    query_login_hint: str | None = None,
    tenant_auth_routing: dict[str, object] | None = None,
    provider_record: dict[str, object] | None = None,
) -> str:
    tenant_auth_routing = tenant_auth_routing or {}
    provider_record = provider_record or {}
    runtime = _resolve_provider_runtime_config("auth0", provider_record)
    query = {
        "response_type": "code",
        "client_id": str(runtime.get("client_id") or "").strip(),
        "redirect_uri": build_auth0_callback_url(),
        "scope": str(runtime.get("scope") or "").strip() or "openid profile email",
        "state": state,
    }
    auth0_audience = str(runtime.get("audience") or "").strip()
    if auth0_audience:
        query["audience"] = auth0_audience
    auth0_organization = str(getattr(settings, "auth0_organization", "") or "").strip() or None
    organization_hint = runtime.get("organization_hint") or tenant_auth_routing.get("organization_hint") or auth0_organization
    connection_hint = provider_record.get("connection_hint") or tenant_auth_routing.get("connection_hint")
    if organization_hint:
        query["organization"] = organization_hint
    if connection_hint:
        query["connection"] = connection_hint
    if query_login_hint:
        query["login_hint"] = query_login_hint
    return f"{str(runtime.get('domain') or '')}/authorize?{urlparse.urlencode(query)}"


def _build_oidc_authorize_url(
    *,
    state: str,
    query_login_hint: str | None = None,
    provider_record: dict[str, object] | None = None,
) -> str:
    runtime = _resolve_provider_runtime_config("oidc", provider_record)
    authorize_url = runtime.get("authorize_url")
    if not authorize_url:
        raise UnauthorizedError("OIDC is enabled, but the authorize endpoint is not configured.")
    query = {
        "response_type": "code",
        "client_id": str(runtime.get("client_id") or "").strip(),
        "redirect_uri": build_oidc_callback_url(),
        "scope": str(runtime.get("scope") or "").strip() or "openid profile email",
        "state": state,
    }
    oidc_audience = str(runtime.get("audience") or "").strip()
    if oidc_audience:
        query["audience"] = oidc_audience
    if query_login_hint:
        query["login_hint"] = query_login_hint
    return f"{authorize_url}?{urlparse.urlencode(query)}"


def start_provider_login(
    *,
    provider: str | None = None,
    provider_record_id: str | None = None,
    db: Session | None = None,
    requested_tenant_slug: str | None = None,
    invite_token: str | None = None,
    redirect_path: str | None = None,
    login_email: str | None = None,
) -> dict:
    config = get_auth_provider_config()
    invitation, target_tenant, requested_slug, query_login_hint = _resolve_login_target(
        db=db,
        requested_tenant_slug=requested_tenant_slug,
        invite_token=invite_token,
    )
    tenant_auth_routing = get_tenant_auth_routing(target_tenant) if target_tenant is not None else {}
    provider_selection = _resolve_auth_provider_selection(
        config=config,
        tenant_auth_routing=tenant_auth_routing,
        invitation_email=(invitation.email if invitation is not None else None),
        login_email=login_email,
    )
    provider_records = provider_selection.get("provider_records") or []
    chosen_provider_record = None
    requested_provider_record_id = str(provider_record_id or "").strip().lower() or None
    if requested_provider_record_id:
        chosen_provider_record = next(
            (
                record
                for record in provider_records
                if str(record.get("id") or "").strip().lower() == requested_provider_record_id
            ),
            None,
        )
        if chosen_provider_record is None:
            raise UnauthorizedError("The requested tenant auth provider could not be found.")
    elif provider_selection.get("matched_provider_record"):
        chosen_provider_record = provider_selection.get("matched_provider_record")
    elif provider_selection.get("recommended_provider_record_id"):
        chosen_provider_record = next(
            (
                record
                for record in provider_records
                if record.get("id") == provider_selection.get("recommended_provider_record_id")
            ),
            None,
        )
    chosen_provider = _normalized_provider_key(provider) or _normalized_provider_key(provider_selection.get("recommended_provider"))
    if chosen_provider_record is not None and chosen_provider is None:
        chosen_provider = _normalized_provider_key(chosen_provider_record.get("provider_key"))
    if chosen_provider_record is not None and _normalized_provider_key(chosen_provider_record.get("provider_key")) != chosen_provider:
        raise UnauthorizedError("The requested tenant auth provider does not match the chosen login provider.")
    if chosen_provider not in provider_selection.get("external_provider_keys", []):
        if provider_selection.get("launch_blockers"):
            raise UnauthorizedError(" ".join(provider_selection.get("launch_blockers") or []))
        raise UnauthorizedError("The requested identity provider is not available for this tenant.")
    if not _is_runtime_provider_ready(chosen_provider, chosen_provider_record):
        raise UnauthorizedError("The selected tenant identity provider is not fully configured.")

    state = secrets.token_urlsafe(24)
    effective_login_hint = query_login_hint or (str(login_email or "").strip().lower() or None)
    authorize_url = (
        _build_auth0_authorize_url(
            state=state,
            query_login_hint=effective_login_hint,
            tenant_auth_routing=tenant_auth_routing,
            provider_record=chosen_provider_record,
        )
        if chosen_provider == "auth0"
        else _build_oidc_authorize_url(
            state=state,
            query_login_hint=effective_login_hint,
            provider_record=chosen_provider_record,
        )
    )
    resolved_redirect_path = _normalize_redirect_path(
        redirect_path
        or tenant_auth_routing.get("post_login_path")
        or (f"/?tenant={requested_slug}" if requested_slug else "/")
    )
    return {
        "provider": chosen_provider,
        "provider_record_id": chosen_provider_record.get("id") if chosen_provider_record else None,
        "authorize_url": authorize_url,
        "state": state,
        "state_cookie_value": build_auth_state_cookie_value(
            state=state,
            provider=chosen_provider,
            provider_record_id=chosen_provider_record.get("id") if chosen_provider_record else None,
            requested_tenant_slug=requested_slug,
            invite_token=invite_token,
            redirect_path=resolved_redirect_path,
        ),
    }


def start_auth0_login(
    *,
    db: Session | None = None,
    requested_tenant_slug: str | None = None,
    invite_token: str | None = None,
    redirect_path: str | None = None,
) -> dict:
    return start_provider_login(
        provider="auth0",
        db=db,
        requested_tenant_slug=requested_tenant_slug,
        invite_token=invite_token,
        redirect_path=redirect_path,
    )


def _auth0_token_endpoint(provider_record: dict[str, object] | None = None) -> str:
    runtime = _resolve_provider_runtime_config("auth0", provider_record)
    return f"{str(runtime.get('domain') or '')}/oauth/token"


def _auth0_userinfo_endpoint(provider_record: dict[str, object] | None = None) -> str:
    runtime = _resolve_provider_runtime_config("auth0", provider_record)
    return f"{str(runtime.get('domain') or '')}/userinfo"


def _fetch_json(url: str, *, method: str = "GET", payload: dict | None = None, headers: dict | None = None) -> dict:
    request_headers = {"content-type": "application/json", **(headers or {})}
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urlrequest.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlrequest.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise ServiceError(f"Auth provider request failed: {detail or exc.reason}", status_code=502) from exc
    except urlerror.URLError as exc:
        raise ServiceError(f"Auth provider request failed: {exc.reason}", status_code=502) from exc


def _exchange_auth0_code_for_tokens(code: str, provider_record: dict[str, object] | None = None) -> dict:
    runtime = _resolve_provider_runtime_config("auth0", provider_record)
    return _fetch_json(
        _auth0_token_endpoint(provider_record),
        method="POST",
        payload={
            "grant_type": "authorization_code",
            "client_id": str(runtime.get("client_id") or "").strip(),
            "client_secret": str(runtime.get("client_secret") or "").strip(),
            "code": code,
            "redirect_uri": build_auth0_callback_url(),
        },
    )


def _fetch_auth0_user_profile(access_token: str, provider_record: dict[str, object] | None = None) -> dict:
    return _fetch_json(
        _auth0_userinfo_endpoint(provider_record),
        headers={"authorization": f"Bearer {access_token}"},
    )


def _exchange_oidc_code_for_tokens(code: str, provider_record: dict[str, object] | None = None) -> dict:
    runtime = _resolve_provider_runtime_config("oidc", provider_record)
    token_url = runtime.get("token_url")
    if not token_url:
        raise UnauthorizedError("OIDC is enabled, but the token endpoint is not configured.")
    return _fetch_json(
        token_url,
        method="POST",
        payload={
            "grant_type": "authorization_code",
            "client_id": str(runtime.get("client_id") or "").strip(),
            "client_secret": str(runtime.get("client_secret") or "").strip(),
            "code": code,
            "redirect_uri": build_oidc_callback_url(),
        },
    )


def _fetch_oidc_user_profile(access_token: str, provider_record: dict[str, object] | None = None) -> dict:
    runtime = _resolve_provider_runtime_config("oidc", provider_record)
    userinfo_url = runtime.get("userinfo_url")
    if not userinfo_url:
        raise UnauthorizedError("OIDC is enabled, but the userinfo endpoint is not configured.")
    return _fetch_json(
        userinfo_url,
        headers={"authorization": f"Bearer {access_token}"},
    )


def _exchange_provider_code_for_tokens(provider_key: str, code: str, provider_record: dict[str, object] | None = None) -> dict:
    normalized_provider = _normalized_provider_key(provider_key)
    if normalized_provider == "auth0":
        return _exchange_auth0_code_for_tokens(code, provider_record)
    if normalized_provider == "oidc":
        return _exchange_oidc_code_for_tokens(code, provider_record)
    raise UnauthorizedError("Unsupported auth provider.")


def _fetch_provider_user_profile(provider_key: str, access_token: str, provider_record: dict[str, object] | None = None) -> dict:
    normalized_provider = _normalized_provider_key(provider_key)
    if normalized_provider == "auth0":
        return _fetch_auth0_user_profile(access_token, provider_record)
    if normalized_provider == "oidc":
        return _fetch_oidc_user_profile(access_token, provider_record)
    raise UnauthorizedError("Unsupported auth provider.")


def _provider_allows_signup(provider_key: str, provider_record: dict[str, object] | None = None) -> bool:
    runtime = _resolve_provider_runtime_config(provider_key, provider_record)
    normalized_provider = _normalized_provider_key(provider_key)
    if normalized_provider in {"auth0", "oidc"}:
        return bool(runtime.get("allow_signup"))
    return bool(settings.local_auth_allow_signup)


def complete_provider_callback(
    db: Session,
    *,
    provider: str | None = None,
    code: str,
    state: str,
    request: Request,
) -> dict:
    state_payload = read_auth_state_cookie(request)
    if not state_payload or str(state_payload.get("state") or "").strip() != str(state or "").strip():
        raise UnauthorizedError("The sign-in state is invalid or has expired.")
    provider_key = _normalized_provider_key(provider) or _normalized_provider_key(state_payload.get("provider")) or _normalized_provider_key(settings.auth_provider)
    if provider_key not in _REDIRECT_PROVIDER_KEYS:
        raise UnauthorizedError("The auth provider is not supported for this callback.")
    requested_tenant_slug = str(state_payload.get("requested_tenant_slug") or "").strip() or None
    invite_token = str(state_payload.get("invite_token") or "").strip() or None
    redirect_path = str(state_payload.get("redirect_path") or "").strip() or "/"
    try:
        _, target_tenant, requested_tenant_slug, _ = _resolve_login_target(
            db=db,
            requested_tenant_slug=requested_tenant_slug,
            invite_token=invite_token,
        )
    except NotFoundError:
        target_tenant = None
    tenant_auth_routing = get_tenant_auth_routing(target_tenant) if target_tenant is not None else {}
    requested_provider_record_id = str(state_payload.get("provider_record_id") or "").strip().lower() or None
    chosen_provider_record = None
    if requested_provider_record_id:
        chosen_provider_record = next(
            (
                record
                for record in tenant_auth_routing.get("provider_records") or []
                if str(record.get("id") or "").strip().lower() == requested_provider_record_id
            ),
            None,
        )
        if chosen_provider_record is None:
            raise UnauthorizedError("The tenant identity provider could not be resolved for this callback.")
    if not _is_runtime_provider_ready(provider_key, chosen_provider_record):
        raise UnauthorizedError("The selected tenant identity provider is not fully configured.")

    token_payload = _exchange_provider_code_for_tokens(provider_key, code, chosen_provider_record)
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        raise UnauthorizedError("The auth provider did not return an access token.")

    user_profile = _fetch_provider_user_profile(provider_key, access_token, chosen_provider_record)
    auth_subject = str(user_profile.get("sub") or "").strip()
    email = _normalize_email(str(user_profile.get("email") or ""))
    name = _normalize_name(
        str(
            user_profile.get("name")
            or user_profile.get("nickname")
            or user_profile.get("preferred_username")
            or email.split("@", 1)[0]
        )
    )
    if not auth_subject:
        raise UnauthorizedError("The auth provider response did not include a subject identifier.")

    user = ensure_user(
        db,
        auth_subject=auth_subject,
        email=email,
        name=name,
        provider=provider_key,
        platform_role="member",
    )
    memberships = list_user_memberships(db, user)
    created_organization = False
    if invite_token:
        invitation = claim_tenant_invitation_by_token(db, user=user, invite_token=invite_token)
        requested_tenant_slug = invitation.tenant.slug
        memberships = list_user_memberships(db, user)
    if not memberships:
        if not _provider_allows_signup(provider_key, chosen_provider_record):
            raise UnauthorizedError("No organizations were found for this account.")
        create_tenant(
            db,
            owner=user,
            name=_default_organization_name(name, email),
            plan_key=settings.local_auth_default_plan,
            billing_email=email,
        )
        created_organization = True

    try:
        identity = build_identity_payload(db, user=user, requested_tenant_slug=requested_tenant_slug)
    except NotFoundError:
        identity = build_identity_payload(db, user=user, requested_tenant_slug=None)
    return {
        "identity": identity,
        "cookie_value": build_local_session_cookie_value(
            auth_subject=user.auth_subject,
            provider=provider_key,
            provider_record_id=chosen_provider_record.get("id") if chosen_provider_record else None,
        ),
        "created_organization": created_organization,
        "redirect_url": _build_frontend_redirect_url(
            redirect_path=redirect_path,
            requested_tenant_slug=requested_tenant_slug or getattr(identity["active_tenant"], "slug", None),
        ),
    }


def complete_auth0_callback(
    db: Session,
    *,
    code: str,
    state: str,
    request: Request,
) -> dict:
    return complete_provider_callback(
        db,
        provider="auth0",
        code=code,
        state=state,
        request=request,
    )


def resolve_configured_auth_identity(request: Request, db: Session) -> dict:
    provider_key = str(settings.auth_provider or "").strip().lower() or "local-demo"
    session_payload = read_local_session_cookie(request)
    if not session_payload:
        raise UnauthorizedError("Sign in required.")
    session_provider = str(session_payload.get("provider") or provider_key).strip().lower() or provider_key
    if provider_key not in {"local-session", "auth0", "oidc"}:
        raise UnauthorizedError("Authentication is enabled, but the configured provider adapter is not implemented yet.")
    if session_provider not in _SUPPORTED_SESSION_PROVIDERS:
        raise UnauthorizedError("The current session uses an unsupported provider.")
    auth_subject = str(session_payload.get("sub") or "").strip()
    provider_record_id = str(session_payload.get("provider_record_id") or "").strip().lower() or None
    user = db.execute(select(User).where(User.auth_subject == auth_subject)).scalar_one_or_none()
    if user is None or not user.is_active:
        raise UnauthorizedError("The current session is no longer valid.")

    return {
        "provider": session_provider,
        "provider_record_id": provider_record_id,
        "mode": "authenticated",
        "identity": build_identity_payload(db, user=user),
    }
