from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import parse as urlparse
from urllib import request as urlrequest
from urllib import error as urlerror

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.config import settings
from backend.models.saas import BrokerageLinkedAccount, Tenant, TradeApprovalIntent, User
from backend.services.audit_service import record_audit_event
from backend.services.execution.alpaca_client import AlpacaApiError, build_alpaca_oauth_client
from backend.services.exceptions import NotFoundError, ValidationError
from backend.services.serialization import serialize_value
from backend.services.tenant_service import _resolve_tenant_for_current_user, _resolve_user_for_current_user


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_AUTOMATION_METADATA_KEY = "automation"
_DEFAULT_AUTOMATION_ACCOUNT_SIZE = 10_000.0
_DEFAULT_AUTOMATION_RISK_PERCENT = 0.5
_DEFAULT_AUTOMATION_MAX_OPEN_POSITIONS = 3


def _normalize_redirect_path(raw_path: str | None) -> str:
    cleaned = str(raw_path or "").strip()
    if not cleaned:
        return "/settings"
    parsed = urlparse.urlsplit(cleaned)
    if parsed.scheme or parsed.netloc:
        return "/settings"
    path = parsed.path or "/settings"
    if not path.startswith("/"):
        path = f"/{path}"
    return urlparse.urlunsplit(("", "", path, parsed.query, parsed.fragment))


def _build_frontend_redirect_url(path: str, *, status: str, account_id: str | None = None, provider: str = "alpaca") -> str:
    target = urlparse.urljoin(settings.auth_post_login_redirect_url, _normalize_redirect_path(path))
    parsed = urlparse.urlsplit(target)
    query = dict(urlparse.parse_qsl(parsed.query, keep_blank_values=False))
    query["brokerage_provider"] = provider
    query["brokerage_status"] = status
    if account_id:
        query["brokerage_account_id"] = account_id
    return urlparse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlparse.urlencode(query), parsed.fragment))


def _encode_state_cookie(payload: dict[str, Any]) -> str:
    payload_with_issue_time = dict(payload)
    payload_with_issue_time.setdefault("iat", int(time.time()))
    raw = json.dumps(payload_with_issue_time, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    signature = hmac.new(
        settings.auth_state_secret.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded_payload}.{signature}"


def _decode_state_cookie(value: str | None) -> dict[str, Any] | None:
    cleaned = str(value or "").strip()
    if not cleaned or "." not in cleaned:
        return None
    try:
        encoded_payload, provided_signature = cleaned.rsplit(".", 1)
        expected_signature = hmac.new(
            settings.auth_state_secret.encode("utf-8"),
            encoded_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(provided_signature, expected_signature):
            return None
        padded = encoded_payload + ("=" * (-len(encoded_payload) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("utf-8"))
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        issued_at = int(payload.get("iat") or 0)
        if issued_at and (int(time.time()) - issued_at) > settings.alpaca_link_state_max_age_seconds:
            return None
        return payload
    except Exception:
        return None


def _serialize_linked_account(row: BrokerageLinkedAccount) -> dict[str, Any]:
    metadata = dict(row.metadata_json or {})
    account = dict(metadata.get("account") or {})
    automation = get_linked_account_automation_profile(row)
    return {
        "id": row.id,
        "provider": row.provider,
        "label": row.label or row.linked_identity_label or account.get("name") or f"Alpaca {row.account_environment}",
        "account_environment": row.account_environment,
        "connection_status": row.connection_status,
        "token_health": row.token_health,
        "approval_policy": row.approval_policy,
        "external_account_id": row.external_account_id,
        "external_account_number_masked": row.external_account_number_masked,
        "linked_identity_label": row.linked_identity_label,
        "linked_at": row.linked_at.isoformat() if row.linked_at else None,
        "last_refreshed_at": row.last_refreshed_at.isoformat() if row.last_refreshed_at else None,
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
        "disconnected_at": row.disconnected_at.isoformat() if row.disconnected_at else None,
        "owner_user_id": row.owner_user_id,
        "owner_email": metadata.get("owner_email"),
        "account_summary": {
            "status": account.get("status"),
            "currency": account.get("currency"),
            "equity": account.get("equity"),
            "buying_power": account.get("buying_power"),
            "cash": account.get("cash"),
            "portfolio_value": account.get("portfolio_value"),
        },
        "automation": serialize_value(automation),
        "client_auto_trading_opt_in": bool(automation.get("client_auto_trading_opt_in")),
        "operator_auto_trading_enabled": bool(automation.get("operator_auto_trading_enabled")),
        "automation_paused": bool(automation.get("automation_paused")),
        "automation_status": automation.get("automation_status"),
        "automation_status_label": automation.get("automation_status_label"),
        "automation_eligible": bool(automation.get("automation_eligible")),
        "automation_block_reason": automation.get("automation_block_reason"),
        "automation_block_label": automation.get("automation_block_label"),
        "account_size": automation.get("account_size"),
        "risk_percent": automation.get("risk_percent"),
        "max_notional_per_trade": automation.get("max_notional_per_trade"),
        "max_open_positions": automation.get("max_open_positions"),
        "entries_only": bool(automation.get("entries_only")),
        "strategy_binding": automation.get("strategy_binding"),
        "last_automated_submission_at": automation.get("last_automated_submission_at"),
        "last_automated_order": serialize_value(automation.get("last_automated_order")),
        "token_present": bool(row.oauth_access_token),
        "relink_required": row.connection_status in {"relink_required", "error"} or row.token_health in {"relink_required", "expired"},
    }


def _coerce_positive_float(value: Any, *, default: float, minimum: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = float(default)
    if numeric < minimum:
        numeric = float(default)
    return float(max(numeric, minimum))


def _coerce_positive_int(value: Any, *, default: int, minimum: int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        numeric = int(default)
    if numeric < minimum:
        numeric = int(default)
    return int(max(numeric, minimum))


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return bool(value)


def _default_automation_account_size(row: BrokerageLinkedAccount) -> float:
    metadata = dict(row.metadata_json or {})
    account = dict(metadata.get("account") or {})
    for key in ("equity", "portfolio_value", "cash", "buying_power"):
        try:
            numeric = float(account.get(key))
        except (TypeError, ValueError):
            numeric = 0.0
        if numeric > 0:
            return max(float(numeric), 100.0)
    return _DEFAULT_AUTOMATION_ACCOUNT_SIZE


def _normalize_linked_account_automation_settings(row: BrokerageLinkedAccount) -> dict[str, Any]:
    metadata = dict(row.metadata_json or {})
    automation = dict(metadata.get(_AUTOMATION_METADATA_KEY) or {})
    account_size = _coerce_positive_float(
        automation.get("account_size"),
        default=_default_automation_account_size(row),
        minimum=100.0,
    )
    risk_percent = _coerce_positive_float(
        automation.get("risk_percent"),
        default=_DEFAULT_AUTOMATION_RISK_PERCENT,
        minimum=0.05,
    )
    max_notional_default = max(account_size * 0.12, 100.0)
    return {
        "client_auto_trading_opt_in": _coerce_bool(automation.get("client_auto_trading_opt_in"), default=False),
        "operator_auto_trading_enabled": _coerce_bool(automation.get("operator_auto_trading_enabled"), default=False),
        "automation_paused": _coerce_bool(automation.get("automation_paused"), default=False),
        "account_size": account_size,
        "risk_percent": min(risk_percent, 100.0),
        "max_notional_per_trade": _coerce_positive_float(
            automation.get("max_notional_per_trade"),
            default=max_notional_default,
            minimum=100.0,
        ),
        "max_open_positions": _coerce_positive_int(
            automation.get("max_open_positions"),
            default=_DEFAULT_AUTOMATION_MAX_OPEN_POSITIONS,
            minimum=1,
        ),
        "entries_only": True,
        "strategy_binding": "main_desk",
        "last_automated_submission_at": automation.get("last_automated_submission_at"),
        "last_automated_order": serialize_value(automation.get("last_automated_order")),
        "last_automation_error": serialize_value(automation.get("last_automation_error")),
    }


def _automation_block_label(reason: str | None) -> str | None:
    mapping = {
        "client_opt_in_required": "Client opt-in required",
        "operator_activation_required": "Operator activation required",
        "paused": "Paused",
        "paper_only": "Paper accounts only",
        "relink_required": "Reconnect account",
        "connection_unavailable": "Connection unavailable",
        "token_unhealthy": "Token refresh required",
    }
    return mapping.get(str(reason or "").strip().lower()) or None


def get_linked_account_automation_profile(row: BrokerageLinkedAccount) -> dict[str, Any]:
    settings_state = _normalize_linked_account_automation_settings(row)
    if settings_state["automation_paused"]:
        status = "paused"
        block_reason = "paused"
    elif row.connection_status != "connected":
        status = "blocked"
        block_reason = "relink_required" if row.connection_status == "relink_required" else "connection_unavailable"
    elif row.token_health not in {"healthy", "unknown"}:
        status = "blocked"
        block_reason = "token_unhealthy"
    elif row.account_environment != "paper":
        status = "blocked"
        block_reason = "paper_only"
    elif not settings_state["client_auto_trading_opt_in"]:
        status = "disabled"
        block_reason = "client_opt_in_required"
    elif not settings_state["operator_auto_trading_enabled"]:
        status = "disabled"
        block_reason = "operator_activation_required"
    else:
        status = "paper_ready"
        block_reason = None
    return {
        **settings_state,
        "automation_status": status,
        "automation_status_label": (
            "Paper ready"
            if status == "paper_ready"
            else "Paused"
            if status == "paused"
            else "Blocked"
            if status == "blocked"
            else "Disabled"
        ),
        "automation_eligible": status == "paper_ready",
        "automation_block_reason": block_reason,
        "automation_block_label": _automation_block_label(block_reason),
    }


def _write_linked_account_automation_settings(
    linked_account: BrokerageLinkedAccount,
    *,
    client_auto_trading_opt_in: bool | None = None,
    operator_auto_trading_enabled: bool | None = None,
    automation_paused: bool | None = None,
    account_size: float | None = None,
    risk_percent: float | None = None,
    max_notional_per_trade: float | None = None,
    max_open_positions: int | None = None,
    last_automated_submission_at: str | None = None,
    last_automated_order: dict[str, Any] | None = None,
    last_automation_error: dict[str, Any] | None = None,
) -> None:
    metadata = dict(linked_account.metadata_json or {})
    automation = dict(metadata.get(_AUTOMATION_METADATA_KEY) or {})
    if client_auto_trading_opt_in is not None:
        automation["client_auto_trading_opt_in"] = bool(client_auto_trading_opt_in)
    if operator_auto_trading_enabled is not None:
        automation["operator_auto_trading_enabled"] = bool(operator_auto_trading_enabled)
    if automation_paused is not None:
        automation["automation_paused"] = bool(automation_paused)
    if account_size is not None:
        automation["account_size"] = float(account_size)
    if risk_percent is not None:
        automation["risk_percent"] = float(risk_percent)
    if max_notional_per_trade is not None:
        automation["max_notional_per_trade"] = float(max_notional_per_trade)
    if max_open_positions is not None:
        automation["max_open_positions"] = int(max_open_positions)
    if last_automated_submission_at is not None:
        automation["last_automated_submission_at"] = str(last_automated_submission_at or "").strip() or None
    if last_automated_order is not None:
        automation["last_automated_order"] = serialize_value(last_automated_order)
    if last_automation_error is not None:
        automation["last_automation_error"] = serialize_value(last_automation_error)
    automation["entries_only"] = True
    automation["strategy_binding"] = "main_desk"
    metadata[_AUTOMATION_METADATA_KEY] = automation
    linked_account.metadata_json = metadata
    flag_modified(linked_account, "metadata_json")


def read_alpaca_link_state_cookie(request: Request) -> dict[str, Any] | None:
    return _decode_state_cookie(request.cookies.get(settings.alpaca_link_state_cookie_name))


def _assert_alpaca_oauth_configured() -> None:
    if not settings.alpaca_oauth_client_id or not settings.alpaca_oauth_client_secret:
        raise ValidationError("Alpaca OAuth client credentials are not configured.")


def _exchange_alpaca_oauth_code(code: str) -> dict[str, Any]:
    _assert_alpaca_oauth_configured()
    body = urlparse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": str(code or "").strip(),
            "client_id": settings.alpaca_oauth_client_id,
            "client_secret": settings.alpaca_oauth_client_secret,
            "redirect_uri": settings.alpaca_oauth_redirect_uri,
        }
    ).encode("utf-8")
    req = urlrequest.Request(
        settings.alpaca_oauth_token_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=settings.alpaca_trading_request_timeout_seconds) as response:
            raw = response.read().decode("utf-8").strip()
            payload = json.loads(raw or "{}")
            return payload if isinstance(payload, dict) else {}
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8").strip()
        raise ValidationError(raw or "Alpaca OAuth token exchange failed.") from exc
    except urlerror.URLError as exc:
        raise ValidationError(f"Could not reach Alpaca OAuth token endpoint: {exc.reason}") from exc


def _mask_account_number(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    if len(cleaned) <= 4:
        return cleaned
    return f"****{cleaned[-4:]}"


def _resolve_linked_account(db: Session, *, current_user: Any, linked_account_id: str) -> tuple[Tenant, User, BrokerageLinkedAccount]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    if actor is None:
        raise ValidationError("An authenticated user is required for linked brokerage account actions.")
    row = db.execute(
        select(BrokerageLinkedAccount).where(
            BrokerageLinkedAccount.id == str(linked_account_id or "").strip(),
            BrokerageLinkedAccount.tenant_id == tenant.id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Linked brokerage account was not found.")
    return tenant, actor, row


def list_linked_brokerage_accounts(*, db: Session, current_user: Any) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    rows = db.execute(
        select(BrokerageLinkedAccount)
        .where(BrokerageLinkedAccount.tenant_id == tenant.id)
        .order_by(BrokerageLinkedAccount.created_at.desc())
    ).scalars().all()
    items = [_serialize_linked_account(row) for row in rows]
    return {
        "items": items,
        "count": len(items),
        "oauth_configured": bool(settings.alpaca_oauth_client_id and settings.alpaca_oauth_client_secret),
        "provider": "alpaca",
        "automation_summary": _build_linked_client_automation_summary_from_items(items),
    }


def _build_linked_client_automation_summary_from_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    eligible_count = sum(1 for item in items if item.get("automation_eligible"))
    automated_count = sum(
        1
        for item in items
        if bool(item.get("client_auto_trading_opt_in")) and bool(item.get("operator_auto_trading_enabled"))
    )
    blocked_items = [item for item in items if str(item.get("automation_status") or "").strip().lower() == "blocked"]
    last_order_candidates = [
        {
            "linked_account_id": item.get("id"),
            "account_label": item.get("label"),
            "submitted_at": item.get("last_automated_submission_at"),
            "order": serialize_value(item.get("last_automated_order")),
        }
        for item in items
        if item.get("last_automated_submission_at")
    ]
    last_order_candidates.sort(key=lambda item: str(item.get("submitted_at") or ""), reverse=True)
    return {
        "eligible_linked_account_count": eligible_count,
        "automated_linked_account_count": automated_count,
        "blocked_linked_account_count": len(blocked_items),
        "last_automated_client_order": last_order_candidates[0] if last_order_candidates else None,
        "block_reasons_by_account": {
            str(item.get("id") or ""): item.get("automation_block_reason")
            for item in items
            if item.get("automation_block_reason")
        },
        "items": [
            {
                "linked_account_id": item.get("id"),
                "label": item.get("label"),
                "automation_status": item.get("automation_status"),
                "automation_eligible": bool(item.get("automation_eligible")),
                "automation_block_reason": item.get("automation_block_reason"),
                "automation_block_label": item.get("automation_block_label"),
                "last_automated_submission_at": item.get("last_automated_submission_at"),
                "last_automated_order": serialize_value(item.get("last_automated_order")),
            }
            for item in items
        ],
    }


def build_linked_client_automation_summary(*, db: Session, current_user: Any) -> dict[str, Any]:
    return dict(list_linked_brokerage_accounts(db=db, current_user=current_user).get("automation_summary") or {})


def list_eligible_linked_accounts_for_automation(*, db: Session, current_user: Any) -> list[BrokerageLinkedAccount]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    rows = db.execute(
        select(BrokerageLinkedAccount)
        .where(BrokerageLinkedAccount.tenant_id == tenant.id, BrokerageLinkedAccount.provider == "alpaca")
        .order_by(BrokerageLinkedAccount.created_at.asc())
    ).scalars().all()
    return [row for row in rows if get_linked_account_automation_profile(row).get("automation_eligible")]


def update_linked_brokerage_account_automation_policy(
    *,
    db: Session,
    current_user: Any,
    linked_account_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    tenant, actor, row = _resolve_linked_account(db, current_user=current_user, linked_account_id=linked_account_id)
    payload = dict(updates or {})
    _write_linked_account_automation_settings(
        row,
        client_auto_trading_opt_in=payload.get("client_auto_trading_opt_in"),
        operator_auto_trading_enabled=payload.get("operator_auto_trading_enabled"),
        automation_paused=payload.get("automation_paused"),
        account_size=payload.get("account_size"),
        risk_percent=payload.get("risk_percent"),
        max_notional_per_trade=payload.get("max_notional_per_trade"),
        max_open_positions=payload.get("max_open_positions"),
    )
    row.last_refreshed_at = _utc_now()
    record_audit_event(
        db,
        event_type="brokerage_account.automation_policy_updated",
        tenant=tenant,
        user=actor,
        payload={
            "linked_account_id": row.id,
            "provider": row.provider,
            "client_auto_trading_opt_in": payload.get("client_auto_trading_opt_in"),
            "operator_auto_trading_enabled": payload.get("operator_auto_trading_enabled"),
            "automation_paused": payload.get("automation_paused"),
            "account_size": payload.get("account_size"),
            "risk_percent": payload.get("risk_percent"),
            "max_notional_per_trade": payload.get("max_notional_per_trade"),
            "max_open_positions": payload.get("max_open_positions"),
        },
    )
    db.commit()
    db.refresh(row)
    return _serialize_linked_account(row)


def start_alpaca_account_link(
    *,
    db: Session,
    current_user: Any,
    environment: str = "paper",
    redirect_path: str | None = None,
    linked_account_id: str | None = None,
) -> dict[str, Any]:
    _assert_alpaca_oauth_configured()
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    if actor is None:
        raise ValidationError("An authenticated user is required to start Alpaca account linking.")

    normalized_environment = str(environment or "paper").strip().lower() or "paper"
    if normalized_environment not in {"paper", "live"}:
        raise ValidationError("Linked brokerage accounts must target either the paper or live Alpaca environment.")

    normalized_redirect_path = _normalize_redirect_path(redirect_path)
    state = secrets.token_urlsafe(24)
    cookie_payload = {
        "state": state,
        "tenant_id": tenant.id,
        "user_id": actor.id,
        "environment": normalized_environment,
        "redirect_path": normalized_redirect_path,
        "linked_account_id": str(linked_account_id or "").strip() or None,
        "issued_at": _utc_now().isoformat(),
    }
    authorize_query = {
        "response_type": "code",
        "client_id": settings.alpaca_oauth_client_id,
        "redirect_uri": settings.alpaca_oauth_redirect_uri,
        "state": state,
        "scope": settings.alpaca_oauth_scope,
        "env": normalized_environment,
    }
    authorize_url = f"{settings.alpaca_oauth_authorize_url}?{urlparse.urlencode(authorize_query)}"
    return {
        "provider": "alpaca",
        "environment": normalized_environment,
        "state": state,
        "authorize_url": authorize_url,
        "state_cookie_value": _encode_state_cookie(cookie_payload),
        "redirect_path": normalized_redirect_path,
    }


def refresh_linked_brokerage_account_status(*, db: Session, current_user: Any, linked_account_id: str) -> dict[str, Any]:
    tenant, actor, row = _resolve_linked_account(db, current_user=current_user, linked_account_id=linked_account_id)
    if not row.oauth_access_token:
        row.connection_status = "relink_required"
        row.token_health = "missing"
        row.last_refreshed_at = _utc_now()
        db.commit()
        return _serialize_linked_account(row)

    client = build_alpaca_oauth_client(
        access_token=row.oauth_access_token,
        account_environment=row.account_environment,
    )
    try:
        account_payload = client.get_account()
        row.connection_status = "connected"
        row.token_health = "healthy"
        row.external_account_id = str(account_payload.get("id") or row.external_account_id or "").strip() or row.external_account_id
        row.external_account_number_masked = _mask_account_number(account_payload.get("account_number")) or row.external_account_number_masked
        row.linked_identity_label = str(account_payload.get("account_number") or row.linked_identity_label or "").strip() or row.linked_identity_label
        metadata = dict(row.metadata_json or {})
        metadata["account"] = {
            "id": account_payload.get("id"),
            "status": account_payload.get("status"),
            "currency": account_payload.get("currency"),
            "equity": account_payload.get("equity"),
            "buying_power": account_payload.get("buying_power"),
            "cash": account_payload.get("cash"),
            "portfolio_value": account_payload.get("portfolio_value"),
            "name": str(account_payload.get("account_number") or "").strip() or metadata.get("account", {}).get("name"),
        }
        metadata["owner_email"] = actor.email
        row.metadata_json = metadata
        _write_linked_account_automation_settings(row)
        row.last_refreshed_at = _utc_now()
        row.last_synced_at = row.last_refreshed_at
        db.commit()
    except AlpacaApiError as exc:
        row.last_refreshed_at = _utc_now()
        if exc.status_code in {401, 403}:
            row.connection_status = "relink_required"
            row.token_health = "relink_required"
        else:
            row.connection_status = "error"
            row.token_health = "degraded"
        metadata = dict(row.metadata_json or {})
        metadata["last_error"] = {"message": str(exc), "status_code": exc.status_code}
        row.metadata_json = metadata
        db.commit()

    record_audit_event(
        db,
        event_type="brokerage_account.refreshed",
        tenant=tenant,
        user=actor,
        payload={"linked_account_id": row.id, "provider": row.provider, "status": row.connection_status},
    )
    db.commit()
    return _serialize_linked_account(row)


def unlink_linked_brokerage_account(*, db: Session, current_user: Any, linked_account_id: str) -> dict[str, Any]:
    tenant, actor, row = _resolve_linked_account(db, current_user=current_user, linked_account_id=linked_account_id)
    row.connection_status = "disconnected"
    row.token_health = "revoked"
    row.oauth_access_token = None
    row.oauth_refresh_token = None
    row.oauth_token_type = None
    row.oauth_scope = None
    row.oauth_expires_at = None
    row.disconnected_at = _utc_now()
    row.last_refreshed_at = row.disconnected_at
    record_audit_event(
        db,
        event_type="brokerage_account.unlinked",
        tenant=tenant,
        user=actor,
        payload={"linked_account_id": row.id, "provider": row.provider},
    )
    db.commit()
    return _serialize_linked_account(row)


def complete_alpaca_account_link(
    *,
    db: Session,
    request: Request,
    code: str,
    state: str,
) -> dict[str, Any]:
    state_payload = read_alpaca_link_state_cookie(request)
    if not state_payload:
        raise ValidationError("Alpaca account link state is missing or expired.")
    if str(state_payload.get("state") or "").strip() != str(state or "").strip():
        raise ValidationError("Alpaca account link state did not match the callback.")

    tenant_id = str(state_payload.get("tenant_id") or "").strip()
    user_id = str(state_payload.get("user_id") or "").strip()
    normalized_environment = str(state_payload.get("environment") or "paper").strip().lower() or "paper"
    redirect_path = _normalize_redirect_path(state_payload.get("redirect_path"))
    relink_account_id = str(state_payload.get("linked_account_id") or "").strip() or None

    tenant = db.execute(select(Tenant).where(Tenant.id == tenant_id)).scalar_one_or_none()
    actor = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if tenant is None or actor is None:
        raise ValidationError("The account-link callback could not resolve the active tenant or user.")

    token_payload = _exchange_alpaca_oauth_code(code)
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        raise ValidationError("Alpaca did not return an access token for this account link.")
    client = build_alpaca_oauth_client(access_token=access_token, account_environment=normalized_environment)
    account_payload = client.get_account()

    if relink_account_id:
        row = db.execute(
            select(BrokerageLinkedAccount).where(
                BrokerageLinkedAccount.id == relink_account_id,
                BrokerageLinkedAccount.tenant_id == tenant.id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Linked brokerage account was not found for relinking.")
    else:
        external_account_id = str(account_payload.get("id") or "").strip() or None
        row = None
        if external_account_id:
            row = db.execute(
                select(BrokerageLinkedAccount).where(
                    BrokerageLinkedAccount.tenant_id == tenant.id,
                    BrokerageLinkedAccount.provider == "alpaca",
                    BrokerageLinkedAccount.external_account_id == external_account_id,
                    BrokerageLinkedAccount.account_environment == normalized_environment,
                )
            ).scalar_one_or_none()
        if row is None:
            row = BrokerageLinkedAccount(
                tenant=tenant,
                owner_user=actor,
                provider="alpaca",
                account_environment=normalized_environment,
                approval_policy="approval_required",
            )
            db.add(row)

    metadata = dict(row.metadata_json or {})
    metadata["account"] = {
        "id": account_payload.get("id"),
        "status": account_payload.get("status"),
        "currency": account_payload.get("currency"),
        "equity": account_payload.get("equity"),
        "buying_power": account_payload.get("buying_power"),
        "cash": account_payload.get("cash"),
        "portfolio_value": account_payload.get("portfolio_value"),
        "name": str(account_payload.get("account_number") or "").strip() or None,
    }
    metadata["owner_email"] = actor.email
    row.metadata_json = metadata
    _write_linked_account_automation_settings(row)
    row.owner_user = actor
    row.provider = "alpaca"
    row.account_environment = normalized_environment
    row.connection_status = "connected"
    row.token_health = "healthy"
    row.approval_policy = "approval_required"
    row.oauth_access_token = access_token
    row.oauth_refresh_token = str(token_payload.get("refresh_token") or "").strip() or None
    row.oauth_token_type = str(token_payload.get("token_type") or "bearer").strip().lower() or None
    row.oauth_scope = str(token_payload.get("scope") or settings.alpaca_oauth_scope).strip() or None
    expires_in = token_payload.get("expires_in")
    row.oauth_expires_at = (
        _utc_now() + timedelta(seconds=int(expires_in))
        if expires_in not in (None, "", "nan")
        else None
    )
    row.external_account_id = str(account_payload.get("id") or row.external_account_id or "").strip() or row.external_account_id
    row.external_account_number_masked = _mask_account_number(account_payload.get("account_number")) or row.external_account_number_masked
    row.linked_identity_label = str(account_payload.get("account_number") or row.linked_identity_label or "").strip() or row.linked_identity_label
    row.label = row.label or f"Alpaca {normalized_environment.title()} {row.external_account_number_masked or ''}".strip()
    row.linked_at = row.linked_at or _utc_now()
    row.last_refreshed_at = _utc_now()
    row.last_synced_at = row.last_refreshed_at
    row.disconnected_at = None

    record_audit_event(
        db,
        event_type="brokerage_account.linked",
        tenant=tenant,
        user=actor,
        payload={
            "linked_account_id": row.id,
            "provider": row.provider,
            "environment": row.account_environment,
            "external_account_id": row.external_account_id,
        },
    )
    db.commit()
    db.refresh(row)
    return {
        "account": _serialize_linked_account(row),
        "redirect_url": _build_frontend_redirect_url(
            redirect_path,
            status="connected",
            account_id=row.id,
        ),
    }


def build_linked_account_execution_client(linked_account: BrokerageLinkedAccount):
    access_token = str(linked_account.oauth_access_token or "").strip()
    if not access_token:
        raise ValidationError("Linked brokerage account is missing an OAuth access token.")
    return build_alpaca_oauth_client(
        access_token=access_token,
        account_environment=linked_account.account_environment,
    )


def record_linked_account_automation_submission(
    *,
    linked_account: BrokerageLinkedAccount,
    submitted_at: datetime,
    order_payload: dict[str, Any],
) -> None:
    _write_linked_account_automation_settings(
        linked_account,
        last_automated_submission_at=submitted_at.astimezone(timezone.utc).isoformat(),
        last_automated_order=order_payload,
        last_automation_error={"message": None},
    )


def record_linked_account_automation_error(
    *,
    linked_account: BrokerageLinkedAccount,
    message: str,
    status_code: int | None = None,
) -> None:
    _write_linked_account_automation_settings(
        linked_account,
        last_automation_error={"message": str(message or "").strip(), "status_code": status_code},
    )


def mark_linked_account_execution_failure(
    *,
    db: Session,
    linked_account: BrokerageLinkedAccount,
    error: Exception,
) -> None:
    message = str(error)
    if isinstance(error, AlpacaApiError) and error.status_code in {401, 403}:
        linked_account.connection_status = "relink_required"
        linked_account.token_health = "relink_required"
    else:
        linked_account.connection_status = "error"
        linked_account.token_health = "degraded"
    linked_account.last_refreshed_at = _utc_now()
    metadata = dict(linked_account.metadata_json or {})
    metadata["last_error"] = {
        "message": message,
        "status_code": getattr(error, "status_code", None),
    }
    linked_account.metadata_json = metadata
    record_linked_account_automation_error(
        linked_account=linked_account,
        message=message,
        status_code=getattr(error, "status_code", None),
    )
    db.flush()


def list_trade_intent_audit_events(
    *,
    db: Session,
    tenant_id: str,
    intent_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    normalized_ids = [str(item).strip() for item in intent_ids if str(item).strip()]
    if not normalized_ids:
        return {}
    from backend.models.saas import AuditEvent

    audit_rows = db.execute(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id, AuditEvent.event_type.in_(
            [
                "client_trade_intent.created",
                "client_trade_intent.approved",
                "client_trade_intent.conditionally_approved",
                "client_trade_intent.rejected",
                "client_trade_intent.expired",
                "client_trade_intent.submitted",
                "client_trade_intent.submit_failed",
                "client_trade_intent.automated_submitted",
                "client_trade_intent.automated_submit_failed",
            ]
        ))
        .order_by(AuditEvent.created_at.asc())
    ).scalars().all()
    grouped: dict[str, list[dict[str, Any]]] = {intent_id: [] for intent_id in normalized_ids}
    for row in audit_rows:
        payload = dict(row.payload_json or {})
        intent_id = str(payload.get("intent_id") or "").strip()
        if intent_id and intent_id in grouped:
            grouped[intent_id].append(
                {
                    "event_type": row.event_type,
                    "actor_email": row.actor_email,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "payload": payload,
                }
            )
    return grouped
