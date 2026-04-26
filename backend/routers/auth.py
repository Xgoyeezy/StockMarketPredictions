from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from urllib import parse as urlparse

from backend.core.auth import CurrentUser, build_current_user_from_identity, get_optional_current_user
from backend.core.config import settings
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope, AuthLoginRequest
from backend.services.auth_provider_service import (
    build_auth_entry_context,
    build_provider_logout_url,
    complete_provider_callback,
    login_with_local_session,
    read_auth_state_cookie,
    start_provider_login as begin_provider_login,
    _build_frontend_redirect_url,
)
from backend.services.auth_service import build_session_payload, get_auth_config, get_unauthenticated_session_payload
from backend.services.exceptions import ServiceError, UnauthorizedError, ValidationError
from backend.services.rate_limit_service import check_auth_flow_allowed, get_client_ip, record_auth_failure, record_auth_success

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/session", response_model=ApiEnvelope)
def session(current_user: CurrentUser | None = Depends(get_optional_current_user)) -> ApiEnvelope:
    if current_user is None:
        return envelope(get_unauthenticated_session_payload())
    return envelope(build_session_payload(current_user))


@router.get("/config", response_model=ApiEnvelope)
def config() -> ApiEnvelope:
    return envelope(get_auth_config())


@router.get("/entry", response_model=ApiEnvelope)
def auth_entry(
    request: Request,
    tenant_slug: str | None = Query(default=None),
    invite_token: str | None = Query(default=None),
    redirect_path: str | None = Query(default=None),
    email: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    check_auth_flow_allowed(
        action="entry",
        ip_address=get_client_ip(request),
        email=email,
        tenant_slug=tenant_slug,
    )
    return envelope(
        build_auth_entry_context(
            db=db,
            requested_tenant_slug=tenant_slug,
            invite_token=invite_token,
            redirect_path=redirect_path,
            login_email=email,
        )
    )


@router.post("/login", response_model=ApiEnvelope)
def login(
    request: Request,
    payload: AuthLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    ip_address = get_client_ip(request)
    check_auth_flow_allowed(
        action="login",
        ip_address=ip_address,
        email=payload.email,
        tenant_slug=payload.tenant_slug,
    )
    try:
        result = login_with_local_session(
            db,
            email=payload.email,
            name=payload.name,
            requested_tenant_slug=payload.tenant_slug,
            invite_token=payload.invite_token,
            organization_name=payload.organization_name,
            create_organization_if_missing=payload.create_organization_if_missing,
        )
    except (ServiceError, UnauthorizedError, ValidationError) as exc:
        record_auth_failure(
            action="login",
            ip_address=ip_address,
            email=payload.email,
            tenant_slug=payload.tenant_slug,
            reason=str(exc.message if hasattr(exc, "message") else exc),
        )
        raise
    record_auth_success(action="login", ip_address=ip_address, email=payload.email)
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=result["cookie_value"],
        max_age=settings.auth_session_max_age_seconds,
        httponly=True,
        secure=settings.auth_session_secure,
        samesite="lax",
        path="/",
    )
    current_user = build_current_user_from_identity(
        result["identity"],
        provider="local-session",
        mode="authenticated",
    )
    session_payload = build_session_payload(current_user)
    session_payload["login"] = {
        "created_organization": result["created_organization"],
    }
    return envelope(session_payload)


@router.post("/start", response_model=ApiEnvelope)
def start_provider_login(
    request: Request,
    response: Response,
    provider: str | None = Query(default=None),
    provider_record_id: str | None = Query(default=None),
    tenant_slug: str | None = Query(default=None),
    invite_token: str | None = Query(default=None),
    redirect_path: str | None = Query(default=None),
    email: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    check_auth_flow_allowed(
        action="start",
        ip_address=get_client_ip(request),
        email=email,
        tenant_slug=tenant_slug,
    )
    result = begin_provider_login(
        provider=provider,
        provider_record_id=provider_record_id,
        db=db,
        requested_tenant_slug=tenant_slug,
        invite_token=invite_token,
        redirect_path=redirect_path,
        login_email=email,
    )
    response.set_cookie(
        key=settings.auth_state_cookie_name,
        value=result["state_cookie_value"],
        max_age=settings.auth_state_max_age_seconds,
        httponly=True,
        secure=settings.auth_session_secure,
        samesite="lax",
        path="/",
    )
    return envelope(
        {
            "provider": result["provider"],
            "provider_record_id": result.get("provider_record_id"),
            "authorize_url": result["authorize_url"],
            "state": result["state"],
        }
    )


def _handle_auth_callback(
    *,
    provider: str | None,
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    state_payload = read_auth_state_cookie(request)
    requested_tenant_slug = (state_payload or {}).get("requested_tenant_slug")
    callback_email = (state_payload or {}).get("login_email")
    ip_address = get_client_ip(request)
    check_auth_flow_allowed(
        action="callback",
        ip_address=ip_address,
        email=callback_email,
        tenant_slug=requested_tenant_slug,
    )
    if error:
        record_auth_failure(
            action="callback",
            ip_address=ip_address,
            email=callback_email,
            tenant_slug=requested_tenant_slug,
            reason=error_description or error,
        )
        redirect_url = _build_frontend_redirect_url(
            redirect_path=(state_payload or {}).get("redirect_path"),
            requested_tenant_slug=(state_payload or {}).get("requested_tenant_slug"),
            invite_token=(state_payload or {}).get("invite_token"),
            auth_error=error,
            auth_error_description=error_description,
        )
        response = RedirectResponse(url=redirect_url, status_code=302)
        response.delete_cookie(settings.auth_state_cookie_name, path="/", samesite="lax")
        return response

    if not code or not state:
        record_auth_failure(
            action="callback",
            ip_address=ip_address,
            email=callback_email,
            tenant_slug=requested_tenant_slug,
            reason="missing_callback_params",
        )
        redirect_url = _build_frontend_redirect_url(
            redirect_path=(state_payload or {}).get("redirect_path"),
            requested_tenant_slug=(state_payload or {}).get("requested_tenant_slug"),
            invite_token=(state_payload or {}).get("invite_token"),
            auth_error="missing_callback_params",
        )
        response = RedirectResponse(url=redirect_url, status_code=302)
        response.delete_cookie(settings.auth_state_cookie_name, path="/", samesite="lax")
        return response

    try:
        result = complete_provider_callback(db, provider=provider, code=code, state=state, request=request)
    except (ServiceError, UnauthorizedError, ValidationError) as exc:
        record_auth_failure(
            action="callback",
            ip_address=ip_address,
            email=callback_email,
            tenant_slug=requested_tenant_slug,
            reason=str(exc.message if hasattr(exc, "message") else exc),
        )
        redirect_url = _build_frontend_redirect_url(
            redirect_path=(state_payload or {}).get("redirect_path"),
            requested_tenant_slug=(state_payload or {}).get("requested_tenant_slug"),
            invite_token=(state_payload or {}).get("invite_token"),
            auth_error="callback_failed",
            auth_error_description=str(exc.message if hasattr(exc, "message") else exc),
        )
        response = RedirectResponse(url=redirect_url, status_code=302)
        response.delete_cookie(settings.auth_state_cookie_name, path="/", samesite="lax")
        return response
    record_auth_success(action="callback", ip_address=ip_address, email=callback_email)
    response = RedirectResponse(url=result["redirect_url"], status_code=302)
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=result["cookie_value"],
        max_age=settings.auth_session_max_age_seconds,
        httponly=True,
        secure=settings.auth_session_secure,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(settings.auth_state_cookie_name, path="/", samesite="lax")
    return response


@router.get("/callback")
def auth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return _handle_auth_callback(
        provider=None,
        request=request,
        code=code,
        state=state,
        error=error,
        error_description=error_description,
        db=db,
    )


@router.get("/callback/{provider}")
def provider_auth_callback(
    provider: str,
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return _handle_auth_callback(
        provider=provider,
        request=request,
        code=code,
        state=state,
        error=error,
        error_description=error_description,
        db=db,
    )


@router.post("/logout", response_model=ApiEnvelope)
def logout(response: Response, current_user: CurrentUser | None = Depends(get_optional_current_user)) -> ApiEnvelope:
    response.delete_cookie(
        key=settings.auth_session_cookie_name,
        path="/",
        samesite="lax",
    )
    response.delete_cookie(
        key=settings.auth_state_cookie_name,
        path="/",
        samesite="lax",
    )
    provider_record = None
    if current_user and current_user.provider_record_id:
        provider_records = (
            (((current_user.tenant_delivery_settings or {}).get("auth_routing") or {}).get("provider_records"))
            or []
        )
        provider_record = next(
            (
                record
                for record in provider_records
                if str(record.get("id") or "").strip().lower() == str(current_user.provider_record_id or "").strip().lower()
            ),
            None,
        )
    logout_url = build_provider_logout_url(current_user.provider, provider_record=provider_record) if current_user else None
    return envelope({"signed_out": True, "logout_url": logout_url})
