from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.core.config import settings
from backend.core.database import SessionLocal, init_database
from backend.routers.auth import router as auth_router
from backend.routers.audit import router as audit_router
from backend.routers.ai_agents import router as ai_agents_router
from backend.routers.automation import router as automation_router
from backend.routers.billing import router as billing_router
from backend.routers.brokerage_accounts import router as brokerage_accounts_router
from backend.routers.data_completeness import router as data_completeness_router
from backend.routers.execution import router as execution_router
from backend.routers.execution_analytics import router as execution_analytics_router
from backend.routers.execution_quality import router as execution_quality_router
from backend.routers.evidence_edge import router as evidence_edge_router
from backend.routers.evidence_outcomes import router as evidence_outcomes_router
from backend.routers.evidence_reward import router as evidence_reward_router
from backend.routers.forecast_validation import router as forecast_validation_router
from backend.routers.live import router as live_router
from backend.routers.live_authorizations import router as live_authorizations_router
from backend.routers.live_orders import router as live_orders_router
from backend.routers.market import router as market_router
from backend.routers.me import router as me_router
from backend.routers.orgs import router as orgs_router
from backend.routers.portfolio import router as portfolio_router
from backend.routers.portfolio_risk import router as portfolio_risk_router
from backend.routers.professional_benchmark import router as professional_benchmark_router
from backend.routers.readiness import router as readiness_router
from backend.routers.research_promotion import router as research_promotion_router
from backend.routers.risk import router as risk_router
from backend.routers.score_calibration import router as score_calibration_router
from backend.routers.shadow_mode import router as shadow_mode_router
from backend.routers.strategies import router as strategies_router
from backend.routers.system import router as system_router
from backend.routers.trades import router as trades_router
from backend.routers.walk_forward import router as walk_forward_router
from backend.services.exceptions import NotFoundError, ServiceError, TooManyRequestsError, ValidationServiceError
from backend.services.job_queue_service import start_job_worker, stop_job_worker
from backend.services.ops_service import record_request
from backend.services.rate_limit_service import enforce_request_ip_rate_limit
from backend.stock_direction_model import warm_model_runtime
from backend.services.tenant_service import authenticate_tenant_api_token, record_tenant_api_request

logger = logging.getLogger("stock_signals.api")


@asynccontextmanager
async def app_lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_database()
    warm_model_runtime()
    start_job_worker()
    try:
        yield
    finally:
        stop_job_worker()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=app_lifespan)
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

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        message = "The request data is invalid."
        payload = {
            "detail": message,
            "error": "validation_error",
            "message": message,
            "details": {"errors": exc.errors()},
        }
        return JSONResponse(status_code=400, content=payload)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        status_code = int(getattr(exc, "status_code", 500) or 500)
        if status_code == 401:
            error_code = "unauthorized"
        elif status_code == 403:
            error_code = "forbidden"
        elif status_code == 404:
            error_code = "not_found"
        elif status_code == 409:
            error_code = "conflict"
        elif status_code == 429:
            error_code = "rate_limited"
        else:
            error_code = "service_error"

        detail = getattr(exc, "detail", None)
        message = detail if isinstance(detail, str) and detail.strip() else "Request failed."
        payload = {
            "detail": message,
            "error": error_code,
            "message": message,
            "details": {"status_code": status_code},
        }
        return JSONResponse(status_code=status_code, content=payload)

    app.include_router(system_router, prefix=settings.api_prefix)
    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(billing_router, prefix=settings.api_prefix)
    app.include_router(brokerage_accounts_router, prefix=settings.api_prefix)
    app.include_router(me_router, prefix=settings.api_prefix)
    app.include_router(orgs_router, prefix=settings.api_prefix)
    app.include_router(market_router, prefix=settings.api_prefix)
    app.include_router(portfolio_router, prefix=settings.api_prefix)
    app.include_router(portfolio_risk_router, prefix=settings.api_prefix)
    app.include_router(trades_router, prefix=settings.api_prefix)
    app.include_router(strategies_router, prefix=settings.api_prefix)
    app.include_router(automation_router, prefix=settings.api_prefix)
    app.include_router(readiness_router, prefix=settings.api_prefix)
    app.include_router(risk_router, prefix=settings.api_prefix)
    app.include_router(audit_router, prefix=settings.api_prefix)
    app.include_router(ai_agents_router, prefix=settings.api_prefix)
    app.include_router(execution_router, prefix=settings.api_prefix)
    app.include_router(execution_analytics_router, prefix=settings.api_prefix)
    app.include_router(execution_quality_router, prefix=settings.api_prefix)
    app.include_router(evidence_edge_router, prefix=settings.api_prefix)
    app.include_router(evidence_outcomes_router, prefix=settings.api_prefix)
    app.include_router(evidence_reward_router, prefix=settings.api_prefix)
    app.include_router(forecast_validation_router, prefix=settings.api_prefix)
    app.include_router(professional_benchmark_router, prefix=settings.api_prefix)
    app.include_router(data_completeness_router, prefix=settings.api_prefix)
    app.include_router(walk_forward_router, prefix=settings.api_prefix)
    app.include_router(research_promotion_router, prefix=settings.api_prefix)
    app.include_router(score_calibration_router, prefix=settings.api_prefix)
    app.include_router(shadow_mode_router, prefix=settings.api_prefix)
    app.include_router(live_router, prefix=settings.api_prefix)
    app.include_router(live_authorizations_router, prefix=settings.api_prefix)
    app.include_router(live_orders_router, prefix=settings.api_prefix)

    return app


app = create_app()
