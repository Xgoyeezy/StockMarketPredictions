from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.productized_control_plane_service import execution_quality_summary

router = APIRouter(prefix="/execution-analytics", tags=["execution-analytics"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(execution_quality_summary(db, current_user=current_user))


@router.get("/slippage", response_model=ApiEnvelope)
def get_slippage(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    result = execution_quality_summary(db, current_user=current_user)
    return envelope({"summary": result["summary"], "rows": [row for row in result["rows"] if row.get("slippage_bps") is not None]})


@router.get("/fills", response_model=ApiEnvelope)
def get_fills(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    result = execution_quality_summary(db, current_user=current_user)
    return envelope({"summary": result["summary"], "rows": [row for row in result["rows"] if row.get("filled_price") is not None]})


@router.get("/routes", response_model=ApiEnvelope)
def get_routes(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    result = execution_quality_summary(db, current_user=current_user)
    route_counts: dict[str, int] = {}
    for row in result["rows"]:
        key = str(row.get("route_state") or "unknown")
        route_counts[key] = route_counts.get(key, 0) + 1
    return envelope({"summary": result["summary"], "route_counts": route_counts, "rows": result["rows"]})


@router.get("/costs", response_model=ApiEnvelope)
def get_costs(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    result = execution_quality_summary(db, current_user=current_user)
    return envelope({"summary": result["summary"], "rows": [row for row in result["rows"] if row.get("estimated_cost_bps") is not None]})


@router.get("/strategies/{strategy_id}", response_model=ApiEnvelope)
def get_strategy_execution(strategy_id: str, limit: int = Query(default=100, ge=1, le=500), current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(execution_quality_summary(db, current_user=current_user, strategy_id=strategy_id, limit=limit))
