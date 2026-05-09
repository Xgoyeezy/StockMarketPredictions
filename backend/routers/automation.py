from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope, AutomationStrategyActionRequest
from backend.services.productized_control_plane_service import (
    automation_status,
    kill_all_strategies,
    kill_strategy,
    list_automation_events,
    start_strategy,
    stop_strategy,
    strategy_automation_status,
)
from backend.services.live_trading_session_service import request_live_strategy

router = APIRouter(prefix="/automation", tags=["automation"])


@router.get("/status", response_model=ApiEnvelope)
def get_automation_status(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(automation_status(db, current_user=current_user))


@router.get("/strategies/{strategy_id}/status", response_model=ApiEnvelope)
def get_strategy_automation_status(strategy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(strategy_automation_status(db, current_user=current_user, strategy_id=strategy_id))


@router.post("/strategies/{strategy_id}/paper/start", response_model=ApiEnvelope)
def post_strategy_paper_start(strategy_id: str, request: AutomationStrategyActionRequest | None = None, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(start_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request or AutomationStrategyActionRequest(), mode="paper"))


@router.post("/strategies/{strategy_id}/paper/stop", response_model=ApiEnvelope)
def post_strategy_paper_stop(strategy_id: str, request: AutomationStrategyActionRequest | None = None, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(stop_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request or AutomationStrategyActionRequest()))


@router.post("/strategies/{strategy_id}/live/request", response_model=ApiEnvelope)
def post_strategy_live_request(strategy_id: str, request: AutomationStrategyActionRequest | None = None, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(request_live_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request or AutomationStrategyActionRequest()))


@router.post("/strategies/{strategy_id}/kill", response_model=ApiEnvelope)
def post_strategy_kill(strategy_id: str, request: AutomationStrategyActionRequest | None = None, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    payload = request or AutomationStrategyActionRequest()
    return envelope(kill_strategy(db, current_user=current_user, strategy_id=strategy_id, reason=payload.reason))


@router.post("/kill-all", response_model=ApiEnvelope)
def post_kill_all(request: AutomationStrategyActionRequest | None = None, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    payload = request or AutomationStrategyActionRequest()
    return envelope(kill_all_strategies(db, current_user=current_user, reason=payload.reason))


@router.get("/events", response_model=ApiEnvelope)
def get_automation_events(limit: int = Query(default=100, ge=1, le=500), current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(list_automation_events(db, current_user=current_user, limit=limit))
