from __future__ import annotations

from datetime import datetime, timezone
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, Query, WebSocket
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, build_demo_user, get_current_user
from backend.core.database import SessionLocal, get_db
from backend.core.responses import envelope
from backend.schemas import AnalyzeRequest, ApiEnvelope, ChartRequest, CompareRequest, LivePricesRequest, ScanRequest, WatchlistRequest
from backend.services.audit_service import record_audit_event
from backend.services.billing_service import has_entitlement
from backend.services.frontend_service import get_dashboard_snapshot
from backend.services.market_service import (
    apply_analysis_entitlements,
    apply_chart_entitlements,
    apply_compare_entitlements,
    apply_dashboard_entitlements,
    apply_scan_entitlements,
    apply_watchlist_entitlements,
    analyze_market,
    build_watchlist,
    get_chart_payload,
    get_history,
    get_live_price_snapshot,
    get_live_prices_snapshot,
    run_scan,
    search_tickers,
    compare_tickers,
)
from backend.services.realtime_market_service import (
    get_realtime_capabilities,
    parse_stream_channels,
    parse_stream_tickers,
    stream_market_data,
)
from backend.services.serialization import serialize_value
from backend.services.tenant_service import _dispatch_partner_webhook_event, _resolve_tenant_for_current_user, _resolve_user_for_current_user

router = APIRouter(prefix="/market", tags=["market"])
logger = logging.getLogger(__name__)


def _json_envelope(payload: dict) -> JSONResponse:
    return JSONResponse(content={"ok": True, "data": serialize_value(payload), "meta": {}})
_MARKET_SIGNAL_SQLITE_LOCK_RETRY_ATTEMPTS = 3
_MARKET_SIGNAL_SQLITE_LOCK_RETRY_DELAY_SECONDS = 0.2


def _is_sqlite_lock_error(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


def _broker_execution_enabled(db: Session, current_user: CurrentUser) -> bool:
    if getattr(current_user, "provider", None) == "local-demo":
        return True
    return has_entitlement(db, current_user, "broker_execution")


def _query_default(value, fallback):
    default = getattr(value, "default", None)
    if default is not None:
        return default
    return fallback if value is None else value


def _build_market_signal_ready_event(
    current_user: CurrentUser,
    *,
    surface: str,
    payload: dict,
    tickers: list[str] | None = None,
) -> dict | None:
    normalized_surface = str(surface or "").strip().lower()
    if normalized_surface not in {"analyze", "chart", "scan", "watchlist", "dashboard", "compare"}:
        return None

    rows = list(payload.get("results") or payload.get("rows") or [])
    report = dict(payload.get("report") or {})
    leader = rows[0] if rows else {}
    point_count = int(payload.get("point_count") or 0)
    chart_ticker = str(payload.get("ticker") or report.get("ticker") or leader.get("ticker") or "").strip().upper()
    requested_tickers = [str(item or "").strip().upper() for item in (tickers or []) if str(item or "").strip()]
    event_tickers = [ticker for ticker in [*requested_tickers, chart_ticker] if ticker][:10]
    if not event_tickers and leader.get("ticker"):
        event_tickers = [str(leader.get("ticker")).strip().upper()]

    ready = bool(report) or bool(rows) or point_count > 0
    if not ready:
        return None

    return {
        "event": "market.signal_ready",
        "surface": normalized_surface,
        "tenant": {
            "slug": current_user.tenant_slug,
            "name": current_user.tenant_name,
            "plan_key": current_user.tenant_plan,
            "status": current_user.tenant_status,
        },
        "tickers": event_tickers,
        "summary": {
            "point_count": point_count,
            "row_count": len(rows),
            "trade_status": payload.get("trade_status") or leader.get("trade_status"),
            "execution_decision": payload.get("execution_decision") or leader.get("execution_action"),
            "verdict": report.get("verdict") or leader.get("trade_decision"),
            "direction": report.get("direction") or leader.get("direction"),
            "strategy_available": bool((payload.get("strategy") or {}).get("available", False)) if isinstance(payload.get("strategy"), dict) else None,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _dispatch_market_signal_ready_event_with_db(db: Session, current_user: CurrentUser, event_payload: dict) -> None:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    dispatch_result = _dispatch_partner_webhook_event(
        db,
        tenant=tenant,
        event_key="market.signal_ready",
        payload=event_payload,
    )
    record_audit_event(
        db,
        event_type="market.signal_ready",
        tenant=tenant,
        user=actor,
        payload={
            **event_payload,
            "webhook_attempts": dispatch_result["attempted"],
            "webhook_jobs_queued": dispatch_result.get("queued", 0),
            "webhook_deliveries": dispatch_result["delivered"],
        },
    )
    db.commit()


def _dispatch_market_signal_ready_event(current_user: CurrentUser, event_payload: dict) -> None:
    for attempt in range(1, _MARKET_SIGNAL_SQLITE_LOCK_RETRY_ATTEMPTS + 1):
        try:
            with SessionLocal() as db:
                _dispatch_market_signal_ready_event_with_db(db, current_user, event_payload)
            return
        except OperationalError as exc:
            if not _is_sqlite_lock_error(exc) or attempt >= _MARKET_SIGNAL_SQLITE_LOCK_RETRY_ATTEMPTS:
                logger.exception("Failed to dispatch market.signal_ready event in background.")
                return
            logger.warning(
                "Retrying market.signal_ready dispatch after SQLite lock (attempt %s/%s).",
                attempt,
                _MARKET_SIGNAL_SQLITE_LOCK_RETRY_ATTEMPTS,
            )
            time.sleep(_MARKET_SIGNAL_SQLITE_LOCK_RETRY_DELAY_SECONDS * attempt)
        except Exception:  # pragma: no cover - defensive logging for background delivery
            logger.exception("Failed to dispatch market.signal_ready event in background.")
            return


def _queue_market_signal_ready_event(
    background_tasks: BackgroundTasks | None,
    db: Session,
    current_user: CurrentUser,
    *,
    surface: str,
    payload: dict,
    tickers: list[str] | None = None,
) -> None:
    if background_tasks is not None and surface in {"chart", "dashboard"}:
        return
    event_payload = _build_market_signal_ready_event(
        current_user,
        surface=surface,
        payload=payload,
        tickers=tickers,
    )
    if event_payload:
        if background_tasks is None:
            _dispatch_market_signal_ready_event_with_db(db, current_user, event_payload)
        else:
            background_tasks.add_task(_dispatch_market_signal_ready_event, current_user, event_payload)


@router.post("/analyze", response_model=ApiEnvelope)
def analyze(
    request: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    broker_execution_enabled = _broker_execution_enabled(db, current_user)
    payload = apply_analysis_entitlements(
        analyze_market(request, current_user=current_user),
        advanced_indicators_enabled=has_entitlement(db, current_user, "advanced_indicators"),
        broker_execution_enabled=broker_execution_enabled,
    )
    _queue_market_signal_ready_event(background_tasks, db, current_user, surface="analyze", payload=payload, tickers=[request.ticker])
    return envelope(payload)


@router.get("/history/{ticker}", response_model=ApiEnvelope)
def history(ticker: str, interval: str = Query(default="5m")) -> ApiEnvelope:
    return envelope(get_history(ticker.strip().upper(), interval))


@router.get("/chart/{ticker}", response_model=ApiEnvelope)
def chart_payload(
    ticker: str,
    background_tasks: BackgroundTasks,
    interval: str = Query(default="5m"),
    points_limit: int = Query(default=300, ge=50, le=5000),
    regular_hours_only: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope | JSONResponse:
    resolved_points_limit = int(_query_default(points_limit, 300))
    resolved_regular_hours_only = bool(_query_default(regular_hours_only, False))
    request = ChartRequest(
        ticker=ticker.strip().upper(),
        interval=interval,
        points_limit=resolved_points_limit,
        regular_hours_only=resolved_regular_hours_only,
    )
    payload = get_chart_payload(request)
    payload = apply_chart_entitlements(payload, advanced_indicators_enabled=has_entitlement(db, current_user, "advanced_indicators"))
    _queue_market_signal_ready_event(background_tasks, db, current_user, surface="chart", payload=payload, tickers=[request.ticker])
    if background_tasks is not None:
        return _json_envelope(payload)
    return envelope(payload)


@router.get("/live/{ticker}", response_model=ApiEnvelope)
def live_price(ticker: str) -> ApiEnvelope:
    return envelope(get_live_price_snapshot(ticker.strip().upper()))


@router.post("/live/batch", response_model=ApiEnvelope)
def live_prices(request: LivePricesRequest) -> ApiEnvelope:
    return envelope(get_live_prices_snapshot(request.tickers))


@router.post("/scan", response_model=ApiEnvelope)
def scan(
    request: ScanRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    broker_execution_enabled = _broker_execution_enabled(db, current_user)
    payload = apply_scan_entitlements(
        run_scan(request),
        advanced_indicators_enabled=has_entitlement(db, current_user, "advanced_indicators"),
        broker_execution_enabled=broker_execution_enabled,
    )
    _queue_market_signal_ready_event(background_tasks, db, current_user, surface="scan", payload=payload, tickers=request.tickers)
    return envelope(payload)


@router.post("/watchlist", response_model=ApiEnvelope)
def watchlist(
    request: WatchlistRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    broker_execution_enabled = _broker_execution_enabled(db, current_user)
    payload = apply_watchlist_entitlements(
        build_watchlist(request),
        advanced_indicators_enabled=has_entitlement(db, current_user, "advanced_indicators"),
        broker_execution_enabled=broker_execution_enabled,
    )
    _queue_market_signal_ready_event(background_tasks, db, current_user, surface="watchlist", payload=payload, tickers=request.tickers)
    return envelope(payload)


@router.get("/dashboard", response_model=ApiEnvelope)
def dashboard(
    background_tasks: BackgroundTasks,
    consumer: str = Query(default="desk"),
    account_profile: str | None = Query(default=None),
    linked_account_id: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope | JSONResponse:
    broker_execution_enabled = _broker_execution_enabled(db, current_user)
    payload = apply_dashboard_entitlements(
        get_dashboard_snapshot(
            current_user=current_user,
            db=db,
            consumer=consumer,
            account_profile=account_profile,
            linked_account_id=linked_account_id,
        ),
        advanced_indicators_enabled=has_entitlement(db, current_user, "advanced_indicators"),
        broker_execution_enabled=broker_execution_enabled,
    )
    _queue_market_signal_ready_event(background_tasks, db, current_user, surface="dashboard", payload=payload)
    if background_tasks is not None:
        return _json_envelope(payload)
    return envelope(payload)


@router.get("/tickers", response_model=ApiEnvelope)
def ticker_search(query: str = Query(default=""), limit: int = Query(default=10, ge=1, le=50)) -> ApiEnvelope:
    return envelope(search_tickers(query=query, limit=limit))


@router.post("/compare", response_model=ApiEnvelope)
def compare(
    request: CompareRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    broker_execution_enabled = _broker_execution_enabled(db, current_user)
    payload = apply_compare_entitlements(
        compare_tickers(request),
        advanced_indicators_enabled=has_entitlement(db, current_user, "advanced_indicators"),
        broker_execution_enabled=broker_execution_enabled,
    )
    _queue_market_signal_ready_event(background_tasks, db, current_user, surface="compare", payload=payload, tickers=request.tickers)
    return envelope(payload)


@router.get("/stream/capabilities", response_model=ApiEnvelope)
def stream_capabilities(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_realtime_capabilities(realtime_entitled=has_entitlement(db, current_user, "realtime_streaming")))


@router.websocket("/stream")
async def stream(
    websocket: WebSocket,
    tickers: str = Query(default=""),
    channels: str = Query(default="trades,quotes"),
) -> None:
    with SessionLocal() as db:
        current_user = build_demo_user(websocket, db)
        realtime_entitled = has_entitlement(db, current_user, "realtime_streaming")
    await stream_market_data(
        websocket,
        tickers=parse_stream_tickers(tickers),
        channels=parse_stream_channels(channels),
        realtime_entitled=realtime_entitled,
        entitlement_reason="Streaming is not enabled for the active tenant.",
    )
