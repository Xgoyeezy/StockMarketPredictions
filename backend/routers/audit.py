from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope, AuditExportRequest
from backend.services.productized_control_plane_service import create_audit_export, get_audit_export, get_strategy_audit, get_trade_audit, list_audit_events

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events", response_model=ApiEnvelope)
def get_events(limit: int = Query(default=100, ge=1, le=500), current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(list_audit_events(db, current_user=current_user, limit=limit))


@router.get("/trades/{trade_id}", response_model=ApiEnvelope)
def get_trade(trade_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(get_trade_audit(db, current_user=current_user, trade_id=trade_id))


@router.get("/trades/{trade_id}/decision", response_model=ApiEnvelope)
def get_trade_decision(trade_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope({"trade_id": trade_id, "decision": get_trade_audit(db, current_user=current_user, trade_id=trade_id).get("decision")})


@router.get("/trades/{trade_id}/replay", response_model=ApiEnvelope)
def get_trade_replay(trade_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(get_trade_audit(db, current_user=current_user, trade_id=trade_id))


@router.get("/strategies/{strategy_id}", response_model=ApiEnvelope)
def get_strategy(strategy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(get_strategy_audit(db, current_user=current_user, strategy_id=strategy_id))


@router.post("/export", response_model=ApiEnvelope)
def post_export(request: AuditExportRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(create_audit_export(db, current_user=current_user, request=request))


@router.get("/exports/{export_id}", response_model=ApiEnvelope)
def get_export(export_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(get_audit_export(db, current_user=current_user, export_id=export_id))
