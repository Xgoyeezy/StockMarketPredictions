from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.core.config import settings
from backend.core.database import SessionLocal, init_database
from backend.routers.auth import router as auth_router
from backend.routers.billing import router as billing_router
from backend.routers.brokerage_accounts import router as brokerage_accounts_router
from backend.routers.market import router as market_router
from backend.routers.me import router as me_router
from backend.routers.orgs import router as orgs_router
from backend.routers.portfolio import router as portfolio_router
from backend.routers.system import router as system_router
from backend.routers.trades import router as trades_router
from backend.services.exceptions import NotFoundError, ServiceError, TooManyRequestsError, ValidationServiceError
from backend.services.job_queue_service import start_job_worker, stop_job_worker
from backend.services.ops_service import record_request
from backend.services.rate_limit_service import enforce_request_ip_rate_limit
from backend.stock_direction_model import warm_model_runtime
from backend.services.tenant_service import authenticate_tenant_api_token, record_tenant_api_request

logger = logging.getLogger("stock_signals.api")


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allow_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        request.state.request_id = request_id
        started = time.perf_counter()
        response = None
        rate_limit_headers: dict[str, str] = {}
        try:
            try:
                rate_limit_decision = enforce_request_ip_rate_limit(request)
                if rate_limit_decision:
                    request.state.ip_rate_limit = rate_limit_decision
                    rate_limit_headers = {
                        "X-RateLimit-Policy": str(rate_limit_decision.get("policy_key") or ""),
                        "X-RateLimit-Remaining": str(rate_limit_decision.get("remaining") or 0),
                    }
            except TooManyRequestsError as exc:
                response = JSONResponse(status_code=exc.status_code, content={"detail": exc.message, **exc.to_dict()})
                retry_after = exc.details.get("retry_after_seconds")
                if retry_after:
                    response.headers["Retry-After"] = str(retry_after)
                return response
            response = await call_next(request)
            return response
        finally:
            duration = time.perf_counter() - started
            status_code = response.status_code if response is not None else 500
            record_request(
                path=request.url.path,
                method=request.method,
                status_code=status_code,
                duration_seconds=duration,
                request_id=request_id,
            )
            authorization = str(request.headers.get("authorization") or "").strip()
            api_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else str(request.headers.get("x-api-token") or request.headers.get("x-tenant-token") or "").strip()
            if api_token:
                with SessionLocal() as db:
                    try:
                        identity = authenticate_tenant_api_token(db, raw_token=api_token)
                        record_tenant_api_request(
                            db,
                            tenant_id=identity["tenant"].id,
                            token_id=identity["token_id"],
                            token_name=identity["token_name"],
                            scopes=list(identity.get("scopes") or ()),
                            request_path=request.url.path,
                            method=request.method,
                            status_code=status_code,
                        )
                    except Exception:
                        logger.debug("Skipping API usage record for token request that could not be re-authenticated.", exc_info=True)
            if response is not None:
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Process-Time"] = f"{duration:.4f}"
                for header_key, header_value in rate_limit_headers.items():
                    if header_value:
                        response.headers[header_key] = header_value
            if settings.request_logging:
                logger.info("%s %s -> %s %.4fs [%s]", request.method, request.url.path, status_code, duration, request_id)

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, **exc.to_dict()})

    @app.exception_handler(ValidationServiceError)
    async def validation_handler(_: Request, exc: ValidationServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, **exc.to_dict()})

    @app.exception_handler(ServiceError)
    async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
        response = JSONResponse(status_code=exc.status_code, content={"detail": exc.message, **exc.to_dict()})
        retry_after = exc.details.get("retry_after_seconds") if isinstance(exc.details, dict) else None
        if retry_after:
            response.headers["Retry-After"] = str(retry_after)
        return response

    @app.on_event("startup")
    async def startup_event() -> None:
        init_database()
        warm_model_runtime()
        start_job_worker()

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        stop_job_worker()

    app.include_router(system_router, prefix=settings.api_prefix)
    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(billing_router, prefix=settings.api_prefix)
    app.include_router(brokerage_accounts_router, prefix=settings.api_prefix)
    app.include_router(me_router, prefix=settings.api_prefix)
    app.include_router(orgs_router, prefix=settings.api_prefix)
    app.include_router(market_router, prefix=settings.api_prefix)
    app.include_router(portfolio_router, prefix=settings.api_prefix)
    app.include_router(trades_router, prefix=settings.api_prefix)

    return app


app = create_app()
