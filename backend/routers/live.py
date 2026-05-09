from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope, LiveKillSwitchRequest, LiveStrategyActionRequest
from backend.services.live_kill_switch_service import clear_live_kill_switch, get_live_kill_switch_state, list_live_risk_events, trigger_live_kill_switch
from backend.services.live_trading_session_service import (
    arm_live_strategy,
    get_live_status,
    kill_live_strategy,
    pause_live_strategy,
    request_live_strategy,
    resume_live_strategy,
    start_live_strategy,
    stop_live_strategy,
)

router = APIRouter(tags=["live"])


@router.post("/strategies/{strategy_id}/live/request", response_model=ApiEnvelope)
def post_strategy_live_request(
    strategy_id: str,
    request: LiveStrategyActionRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(request_live_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request or LiveStrategyActionRequest()))


@router.post("/strategies/{strategy_id}/live/arm", response_model=ApiEnvelope)
def post_strategy_live_arm(
    strategy_id: str,
    request: LiveStrategyActionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(arm_live_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request))


@router.post("/strategies/{strategy_id}/live/start", response_model=ApiEnvelope)
def post_strategy_live_start(
    strategy_id: str,
    request: LiveStrategyActionRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(start_live_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request or LiveStrategyActionRequest()))


@router.post("/strategies/{strategy_id}/live/pause", response_model=ApiEnvelope)
def post_strategy_live_pause(
    strategy_id: str,
    request: LiveStrategyActionRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(pause_live_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request or LiveStrategyActionRequest()))


@router.post("/strategies/{strategy_id}/live/resume", response_model=ApiEnvelope)
def post_strategy_live_resume(
    strategy_id: str,
    request: LiveStrategyActionRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(resume_live_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request or LiveStrategyActionRequest()))


@router.post("/strategies/{strategy_id}/live/stop", response_model=ApiEnvelope)
def post_strategy_live_stop(
    strategy_id: str,
    request: LiveStrategyActionRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(stop_live_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request or LiveStrategyActionRequest()))


@router.post("/strategies/{strategy_id}/live/kill", response_model=ApiEnvelope)
def post_strategy_live_kill(
    strategy_id: str,
    request: LiveStrategyActionRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(kill_live_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request or LiveStrategyActionRequest()))


@router.get("/live/status", response_model=ApiEnvelope)
def get_status(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(get_live_status(db, current_user=current_user))


@router.get("/live/risk/events", response_model=ApiEnvelope)
def get_live_risk_events(
    limit: int = Query(default=100, ge=1, le=500),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(list_live_risk_events(db, current_user=current_user, limit=limit))


@router.get("/live/kill-switch", response_model=ApiEnvelope)
def get_kill_switch(
    strategy_id: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_live_kill_switch_state(db, current_user=current_user, strategy_id=strategy_id))


@router.post("/live/kill-all", response_model=ApiEnvelope)
def post_kill_all(
    request: LiveKillSwitchRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    payload = request or LiveKillSwitchRequest(scope="tenant", reason="Operator requested global live kill switch.")
    payload.scope = "tenant"
    return envelope(trigger_live_kill_switch(db, current_user=current_user, request=payload))


@router.post("/live/kill-switch/clear", response_model=ApiEnvelope)
def post_clear_kill_switch(
    request: LiveKillSwitchRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(clear_live_kill_switch(db, current_user=current_user, request=request or LiveKillSwitchRequest(reason="Operator cleared live kill switch.")))
