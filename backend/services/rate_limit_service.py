from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from backend.core.config import settings
from backend.services.exceptions import TooManyRequestsError


@dataclass(frozen=True)
class RateLimitPolicy:
    key: str
    label: str
    limit: int
    window_seconds: int


_REQUEST_WINDOWS: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_AUTH_FAILURE_WINDOWS: dict[str, deque[float]] = defaultdict(deque)
_AUTH_LOCKOUTS: dict[str, dict[str, Any]] = {}
_RECENT_THROTTLES: deque[dict[str, Any]] = deque(maxlen=200)
_RECENT_ABUSE_EVENTS: deque[dict[str, Any]] = deque(maxlen=200)
_AUDIT_DEDUPE: dict[str, float] = {}
_LOCK = threading.Lock()

_IP_ROUTE_POLICIES: dict[str, RateLimitPolicy] = {
    "health": RateLimitPolicy("health", "Health endpoint", 300, 60),
    "public": RateLimitPolicy("public", "Public API", 180, 60),
    "market.read": RateLimitPolicy("market.read", "Market API IP", 180, 60),
    "admin.read": RateLimitPolicy("admin.read", "Admin read IP", 120, 60),
    "admin.write": RateLimitPolicy("admin.write", "Admin write IP", 45, 60),
    "billing.write": RateLimitPolicy("billing.write", "Billing write IP", 20, 300),
    "workspace.write": RateLimitPolicy("workspace.write", "Workspace write IP", 60, 60),
}

_ACTOR_ROUTE_POLICIES: dict[str, dict[str, RateLimitPolicy]] = {
    "default": {
        "tenant": RateLimitPolicy("tenant.default", "Tenant default traffic", 600, 60),
        "user": RateLimitPolicy("user.default", "User default traffic", 180, 60),
        "token": RateLimitPolicy("token.default", "Token default traffic", 120, 60),
    },
    "market.read": {
        "tenant": RateLimitPolicy("tenant.market.read", "Tenant market traffic", 900, 60),
        "user": RateLimitPolicy("user.market.read", "User market traffic", 240, 60),
        "token": RateLimitPolicy("token.market.read", "Token market traffic", 360, 60),
    },
    "admin.read": {
        "tenant": RateLimitPolicy("tenant.admin.read", "Tenant admin reads", 240, 60),
        "user": RateLimitPolicy("user.admin.read", "User admin reads", 120, 60),
        "token": RateLimitPolicy("token.admin.read", "Token admin reads", 90, 60),
    },
    "admin.write": {
        "tenant": RateLimitPolicy("tenant.admin.write", "Tenant admin writes", 180, 60),
        "user": RateLimitPolicy("user.admin.write", "User admin writes", 45, 60),
        "token": RateLimitPolicy("token.admin.write", "Token admin writes", 30, 60),
    },
    "billing.write": {
        "tenant": RateLimitPolicy("tenant.billing.write", "Tenant billing writes", 60, 300),
        "user": RateLimitPolicy("user.billing.write", "User billing writes", 18, 300),
        "token": RateLimitPolicy("token.billing.write", "Token billing writes", 12, 300),
    },
    "workspace.write": {
        "tenant": RateLimitPolicy("tenant.workspace.write", "Tenant workspace writes", 180, 60),
        "user": RateLimitPolicy("user.workspace.write", "User workspace writes", 60, 60),
        "token": RateLimitPolicy("token.workspace.write", "Token workspace writes", 40, 60),
    },
}

_AUTH_ROUTE_POLICIES: dict[str, tuple[RateLimitPolicy, RateLimitPolicy]] = {
    "entry": (
        RateLimitPolicy("auth.entry.ip", "Auth entry by IP", 40, 300),
        RateLimitPolicy("auth.entry.email", "Auth entry by email", 12, 300),
    ),
    "start": (
        RateLimitPolicy("auth.start.ip", "Auth start by IP", 20, 300),
        RateLimitPolicy("auth.start.email", "Auth start by email", 8, 300),
    ),
    "login": (
        RateLimitPolicy("auth.login.ip", "Auth login by IP", 12, 600),
        RateLimitPolicy("auth.login.email", "Auth login by email", 6, 600),
    ),
    "callback": (
        RateLimitPolicy("auth.callback.ip", "Auth callback by IP", 30, 300),
        RateLimitPolicy("auth.callback.email", "Auth callback by email", 10, 300),
    ),
}


def _now() -> float:
    return time.time()


def reset_rate_limit_state() -> None:
    with _LOCK:
        _REQUEST_WINDOWS.clear()
        _AUTH_FAILURE_WINDOWS.clear()
        _AUTH_LOCKOUTS.clear()
        _RECENT_THROTTLES.clear()
        _RECENT_ABUSE_EVENTS.clear()
        _AUDIT_DEDUPE.clear()


def _clean_actor_value(value: str | None, *, fallback: str = "unknown") -> str:
    cleaned = str(value or "").strip()
    return cleaned or fallback


def _clean_tenant_slug(value: str | None, *, fallback: str = "") -> str:
    cleaned = _clean_actor_value(value, fallback=fallback)
    if not cleaned:
        return cleaned
    from backend.services.desk_service import normalize_desk_slug

    return normalize_desk_slug(cleaned)


def get_client_ip(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    real_ip = str(request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    client = getattr(request, "client", None)
    return _clean_actor_value(getattr(client, "host", None))


def _is_local_dev_request(request: Request) -> bool:
    ip_address = get_client_ip(request)
    if ip_address not in {"127.0.0.1", "::1", "localhost"}:
        return False
    if settings.environment in {"development", "local", "test"}:
        return True
    if settings.enterprise_runtime_profile == "operator-local":
        return True
    if settings.allow_demo_auth and settings.auth_provider == "local-demo":
        return True
    return False


def _route_category(path: str, method: str) -> str:
    normalized = str(path or "").strip().lower()
    method_name = str(method or "GET").strip().upper()
    if normalized.endswith("/health"):
        return "health"
    if normalized.startswith("/api/market") or normalized.startswith("/api/portfolio") or normalized.startswith("/api/trades"):
        return "market.read"
    if normalized.startswith("/api/billing") and method_name != "GET":
        return "billing.write"
    if normalized.startswith("/api/frontend/workspaces") and (
        method_name in {"POST", "PUT", "DELETE"} or normalized.endswith("/duplicate") or normalized.endswith("/import")
    ):
        return "workspace.write"
    if normalized.startswith("/api/orgs") or normalized.startswith("/api/billing"):
        return "admin.read" if method_name == "GET" else "admin.write"
    return "public" if normalized.startswith("/api/auth") or normalized == "/api/me" else "default"


def _bucket_name(scope_kind: str, scope_value: str) -> str:
    return f"{scope_kind}:{_clean_actor_value(scope_value)}"


def _prune_window(window: deque[float], *, now_ts: float, window_seconds: int) -> None:
    boundary = now_ts - window_seconds
    while window and window[0] <= boundary:
        window.popleft()


def _record_recent_throttle(*, policy: RateLimitPolicy, bucket: str, category: str, retry_after_seconds: int, metadata: dict[str, Any] | None = None) -> None:
    event = {
        "policy_key": policy.key,
        "policy_label": policy.label,
        "bucket": bucket,
        "category": category,
        "retry_after_seconds": retry_after_seconds,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if metadata:
        event.update(metadata)
    _RECENT_THROTTLES.appendleft(event)


def _record_recent_abuse(*, event_type: str, actor_key: str, blocked_until: float | None, metadata: dict[str, Any] | None = None) -> None:
    event = {
        "event_type": event_type,
        "actor_key": actor_key,
        "blocked_until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(blocked_until)) if blocked_until else None,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if metadata:
        event.update(metadata)
    _RECENT_ABUSE_EVENTS.appendleft(event)


def _consume_policy(
    *,
    policy: RateLimitPolicy,
    bucket: str,
    category: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not settings.rate_limit_enabled:
        return {
            "policy_key": policy.key,
            "policy_label": policy.label,
            "limit": policy.limit,
            "remaining": policy.limit,
            "window_seconds": policy.window_seconds,
            "bucket": bucket,
        }

    now_ts = _now()
    with _LOCK:
        window = _REQUEST_WINDOWS[(policy.key, bucket)]
        _prune_window(window, now_ts=now_ts, window_seconds=policy.window_seconds)
        if len(window) >= policy.limit:
            retry_after_seconds = max(1, math.ceil(policy.window_seconds - (now_ts - window[0])))
            _record_recent_throttle(
                policy=policy,
                bucket=bucket,
                category=category,
                retry_after_seconds=retry_after_seconds,
                metadata=metadata,
            )
            raise TooManyRequestsError(
                f"{policy.label} rate limit exceeded.",
                details={
                    "policy_key": policy.key,
                    "policy_label": policy.label,
                    "bucket": bucket,
                    "category": category,
                    "retry_after_seconds": retry_after_seconds,
                    "limit": policy.limit,
                    "window_seconds": policy.window_seconds,
                    **(metadata or {}),
                },
            )
        window.append(now_ts)
        remaining = max(0, policy.limit - len(window))
    return {
        "policy_key": policy.key,
        "policy_label": policy.label,
        "limit": policy.limit,
        "remaining": remaining,
        "window_seconds": policy.window_seconds,
        "bucket": bucket,
    }


def enforce_request_ip_rate_limit(request: Request) -> dict[str, Any] | None:
    if _is_local_dev_request(request):
        return None
    path = str(request.url.path or "").strip().lower()
    if path in {
        "/api/auth/entry",
        "/api/auth/start",
        "/api/auth/login",
    } or path.startswith("/api/auth/callback"):
        return None

    category = _route_category(path, request.method)
    policy = _IP_ROUTE_POLICIES.get(category) or _IP_ROUTE_POLICIES["public"]
    bucket = _bucket_name("ip", get_client_ip(request))
    return _consume_policy(
        policy=policy,
        bucket=bucket,
        category=category,
        metadata={
            "path": path,
            "method": request.method.upper(),
            "ip_address": get_client_ip(request),
        },
    )


def enforce_actor_request_rate_limits(request: Request, current_user: Any) -> list[dict[str, Any]]:
    if _is_local_dev_request(request):
        return []
    category = _route_category(request.url.path, request.method)
    actor_policies = _ACTOR_ROUTE_POLICIES.get(category) or _ACTOR_ROUTE_POLICIES["default"]
    decisions: list[dict[str, Any]] = []
    tenant_slug = _clean_tenant_slug(getattr(current_user, "tenant_slug", None), fallback="tenant")
    metadata = {
        "path": str(request.url.path or ""),
        "method": request.method.upper(),
        "tenant_slug": tenant_slug,
        "provider": getattr(current_user, "provider", None),
    }
    decisions.append(
        _consume_policy(
            policy=actor_policies["tenant"],
            bucket=_bucket_name("tenant", tenant_slug),
            category=category,
            metadata=metadata,
        )
    )
    if getattr(current_user, "api_token_id", None):
        decisions.append(
            _consume_policy(
                policy=actor_policies["token"],
                bucket=_bucket_name("token", getattr(current_user, "api_token_id", None)),
                category=category,
                metadata={**metadata, "api_token_id": getattr(current_user, "api_token_id", None)},
            )
        )
    else:
        decisions.append(
            _consume_policy(
                policy=actor_policies["user"],
                bucket=_bucket_name("user", getattr(current_user, "user_id", None)),
                category=category,
                metadata={**metadata, "user_id": getattr(current_user, "user_id", None)},
            )
        )
    return decisions


def _lockout_key(action: str, actor_kind: str, actor_value: str) -> str:
    return f"{action}:{actor_kind}:{_clean_actor_value(actor_value)}"


def _check_lockout(key: str) -> dict[str, Any] | None:
    now_ts = _now()
    with _LOCK:
        state = _AUTH_LOCKOUTS.get(key)
        if not state:
            return None
        blocked_until = float(state.get("blocked_until") or 0)
        if blocked_until <= now_ts:
            _AUTH_LOCKOUTS.pop(key, None)
            return None
        return {
            "actor_key": key,
            "blocked_until": blocked_until,
            "reason": state.get("reason") or "auth_failures",
            "metadata": state.get("metadata") or {},
        }


def check_auth_flow_allowed(
    *,
    action: str,
    ip_address: str,
    email: str | None = None,
    tenant_slug: str | None = None,
) -> list[dict[str, Any]]:
    if not settings.rate_limit_enabled:
        return []
    ip_policy, email_policy = _AUTH_ROUTE_POLICIES.get(action, _AUTH_ROUTE_POLICIES["entry"])
    email_value = _clean_actor_value(email, fallback="")
    for actor_key in [_lockout_key(action, "ip", ip_address)] + ([_lockout_key(action, "email", email_value)] if email_value else []):
        lockout = _check_lockout(actor_key)
        if lockout:
            retry_after_seconds = max(1, math.ceil(lockout["blocked_until"] - _now()))
            raise TooManyRequestsError(
                "Authentication attempts are temporarily locked for this actor.",
                details={
                    "policy_key": f"auth.{action}.lockout",
                    "policy_label": f"Authentication {action} lockout",
                    "bucket": actor_key,
                    "category": "auth",
                    "retry_after_seconds": retry_after_seconds,
                    "tenant_slug": tenant_slug,
                    "ip_address": ip_address,
                    "email": email_value or None,
                    **(lockout["metadata"] or {}),
                },
            )

    decisions = [
        _consume_policy(
            policy=ip_policy,
            bucket=_bucket_name("ip", ip_address),
            category="auth",
            metadata={"tenant_slug": tenant_slug, "ip_address": ip_address, "email": email_value or None, "action": action},
        )
    ]
    if email_value:
        decisions.append(
            _consume_policy(
                policy=email_policy,
                bucket=_bucket_name("email", email_value.lower()),
                category="auth",
                metadata={"tenant_slug": tenant_slug, "ip_address": ip_address, "email": email_value.lower(), "action": action},
            )
        )
    return decisions


def record_auth_failure(
    *,
    action: str,
    ip_address: str,
    email: str | None = None,
    tenant_slug: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    now_ts = _now()
    threshold = max(1, int(settings.rate_limit_auth_failure_threshold or 5))
    window_seconds = max(60, int(settings.rate_limit_auth_window_seconds or 600))
    lockout_seconds = max(60, int(settings.rate_limit_auth_lockout_seconds or 900))
    actors = [("ip", _clean_actor_value(ip_address))]
    email_value = _clean_actor_value(email, fallback="")
    if email_value:
        actors.append(("email", email_value.lower()))

    result = {"locked": False, "blocked_actors": []}
    with _LOCK:
        for actor_kind, actor_value in actors:
            key = _lockout_key(action, actor_kind, actor_value)
            failures = _AUTH_FAILURE_WINDOWS[key]
            _prune_window(failures, now_ts=now_ts, window_seconds=window_seconds)
            failures.append(now_ts)
            if len(failures) >= threshold:
                blocked_until = now_ts + lockout_seconds
                metadata = {
                    "tenant_slug": tenant_slug,
                    "ip_address": ip_address,
                    "email": email_value or None,
                    "failure_count": len(failures),
                }
                _AUTH_LOCKOUTS[key] = {
                    "blocked_until": blocked_until,
                    "reason": reason or "auth_failures",
                    "metadata": metadata,
                }
                _record_recent_abuse(
                    event_type=f"auth.{action}.locked",
                    actor_key=key,
                    blocked_until=blocked_until,
                    metadata=metadata,
                )
                result["locked"] = True
                result["blocked_actors"].append(
                    {
                        "actor_kind": actor_kind,
                        "actor_value": actor_value,
                        "blocked_until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(blocked_until)),
                    }
                )
            else:
                _record_recent_abuse(
                    event_type=f"auth.{action}.failure",
                    actor_key=key,
                    blocked_until=None,
                    metadata={
                        "tenant_slug": tenant_slug,
                        "ip_address": ip_address,
                        "email": email_value or None,
                        "failure_count": len(failures),
                    },
                )
    return result


def record_auth_success(*, action: str, ip_address: str, email: str | None = None) -> None:
    with _LOCK:
        for actor_kind, actor_value in [("ip", _clean_actor_value(ip_address)), ("email", _clean_actor_value(email, fallback=""))]:
            if not actor_value:
                continue
            key = _lockout_key(action, actor_kind, actor_value.lower() if actor_kind == "email" else actor_value)
            _AUTH_FAILURE_WINDOWS.pop(key, None)
            _AUTH_LOCKOUTS.pop(key, None)


def should_emit_throttle_audit(event_key: str, *, dedupe_seconds: int = 60) -> bool:
    now_ts = _now()
    with _LOCK:
        last_emitted_at = _AUDIT_DEDUPE.get(event_key)
        if last_emitted_at and now_ts - last_emitted_at < dedupe_seconds:
            return False
        _AUDIT_DEDUPE[event_key] = now_ts
        return True


def get_rate_limit_snapshot(*, tenant_slug: str | None = None, limit: int = 12) -> dict[str, Any]:
    now_ts = _now()
    tenant_filter = _clean_tenant_slug(tenant_slug, fallback="")

    with _LOCK:
        recent_events = list(_RECENT_THROTTLES)
        recent_abuse = list(_RECENT_ABUSE_EVENTS)
        blocked_actors: list[dict[str, Any]] = []
        for actor_key, state in list(_AUTH_LOCKOUTS.items()):
            blocked_until = float(state.get("blocked_until") or 0)
            if blocked_until <= now_ts:
                _AUTH_LOCKOUTS.pop(actor_key, None)
                continue
            metadata = dict(state.get("metadata") or {})
            if tenant_filter and _clean_tenant_slug(metadata.get("tenant_slug"), fallback="") != tenant_filter:
                continue
            blocked_actors.append(
                {
                    "actor_key": actor_key,
                    "blocked_until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(blocked_until)),
                    "reason": state.get("reason") or "auth_failures",
                    **metadata,
                }
            )

    if tenant_filter:
        recent_events = [
            event for event in recent_events
            if _clean_tenant_slug(event.get("tenant_slug"), fallback="") == tenant_filter
        ]
        recent_abuse = [
            event for event in recent_abuse
            if _clean_tenant_slug(event.get("tenant_slug"), fallback="") == tenant_filter
        ]

    recent_events = recent_events[: max(1, min(int(limit or 12), 50))]
    recent_abuse = recent_abuse[: max(1, min(int(limit or 12), 50))]
    blocked_actors = blocked_actors[: max(1, min(int(limit or 12), 50))]

    throttle_event_count = len(recent_events)
    auth_lockout_count = len(blocked_actors)
    abuse_failure_count = sum(1 for item in recent_abuse if str(item.get("event_type") or "").endswith(".failure"))

    return {
        "summary": {
            "enabled": bool(settings.rate_limit_enabled),
            "throttle_event_count": throttle_event_count,
            "blocked_actor_count": auth_lockout_count,
            "auth_lockout_count": auth_lockout_count,
            "abuse_failure_count": abuse_failure_count,
            "last_throttle_at": recent_events[0].get("at") if recent_events else None,
            "last_abuse_event_at": recent_abuse[0].get("at") if recent_abuse else None,
        },
        "recent_events": recent_events,
        "recent_abuse": recent_abuse,
        "blocked_actors": blocked_actors,
    }
