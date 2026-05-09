from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope, ReadinessEvaluateRequest
from backend.services.productized_control_plane_service import list_strategies
from backend.services.strategy_readiness_score_service import evaluate_strategy_readiness, latest_readiness_snapshot
from backend.services.tenant_service import _resolve_tenant_for_current_user

router = APIRouter(prefix="/readiness", tags=["readiness"])


@router.get("/desk", response_model=ApiEnvelope)
def get_desk_readiness(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    strategies = list_strategies(db, current_user=current_user)["items"]
    scores = [int((item.get("readiness") or {}).get("score") or 0) for item in strategies if item.get("readiness")]
    return envelope({"strategy_count": len(strategies), "average_score": round(sum(scores) / len(scores), 2) if scores else 0, "items": strategies})


@router.get("/strategies/{strategy_id}", response_model=ApiEnvelope)
def get_strategy_readiness(strategy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    snapshot = latest_readiness_snapshot(db, tenant_id=tenant.id, strategy_id=strategy_id)
    return envelope(snapshot or evaluate_strategy_readiness(db, current_user=current_user, strategy_id=strategy_id))


@router.post("/strategies/{strategy_id}/evaluate", response_model=ApiEnvelope)
def post_strategy_readiness(strategy_id: str, request: ReadinessEvaluateRequest | None = None, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    payload = request or ReadinessEvaluateRequest()
    return envelope(evaluate_strategy_readiness(db, current_user=current_user, strategy_id=strategy_id, force_refresh=payload.force_refresh))


@router.get("/strategies/{strategy_id}/blockers", response_model=ApiEnvelope)
def get_strategy_readiness_blockers(strategy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    snapshot = latest_readiness_snapshot(db, tenant_id=tenant.id, strategy_id=strategy_id) or evaluate_strategy_readiness(db, current_user=current_user, strategy_id=strategy_id)
    return envelope({"items": list(snapshot.get("blockers") or []), "count": len(list(snapshot.get("blockers") or []))})


@router.get("/strategies/{strategy_id}/promotion", response_model=ApiEnvelope)
def get_strategy_readiness_promotion(strategy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(evaluate_strategy_readiness(db, current_user=current_user, strategy_id=strategy_id).get("promotion") or {})
