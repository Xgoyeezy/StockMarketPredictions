from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope, RiskCheckRequest, RiskKillSwitchRequest, RiskPolicyCreateRequest, RiskPolicyUpdateRequest
from backend.services.productized_control_plane_service import (
    create_risk_policy,
    get_risk_policy,
    list_risk_events,
    list_risk_policies,
    run_risk_check,
    set_risk_kill_switch,
    update_risk_policy,
)

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/policies", response_model=ApiEnvelope)
def get_policies(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(list_risk_policies(db, current_user=current_user))


@router.post("/policies", response_model=ApiEnvelope)
def post_policy(request: RiskPolicyCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(create_risk_policy(db, current_user=current_user, request=request))


@router.get("/policies/{policy_id}", response_model=ApiEnvelope)
def get_policy(policy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(get_risk_policy(db, current_user=current_user, policy_id=policy_id))


@router.patch("/policies/{policy_id}", response_model=ApiEnvelope)
def patch_policy(policy_id: str, request: RiskPolicyUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(update_risk_policy(db, current_user=current_user, policy_id=policy_id, request=request))


@router.post("/check", response_model=ApiEnvelope)
def post_risk_check(request: RiskCheckRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(run_risk_check(db, current_user=current_user, request=request))


@router.get("/events", response_model=ApiEnvelope)
def get_events(limit: int = Query(default=100, ge=1, le=500), current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(list_risk_events(db, current_user=current_user, limit=limit))


@router.post("/kill-switch", response_model=ApiEnvelope)
def post_kill_switch(request: RiskKillSwitchRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(set_risk_kill_switch(db, current_user=current_user, request=request, active=True))


@router.post("/kill-switch/clear", response_model=ApiEnvelope)
def post_kill_switch_clear(request: RiskKillSwitchRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(set_risk_kill_switch(db, current_user=current_user, request=request, active=False))
