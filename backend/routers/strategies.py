from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import (
    ApiEnvelope,
    StrategyActionRequest,
    StrategyCreateRequest,
    StrategyPromotionRequest,
    StrategyRollbackRequest,
    StrategyUpdateRequest,
    StrategyVersionCreateRequest,
)
from backend.services.productized_control_plane_service import (
    create_strategy,
    create_strategy_version,
    get_strategy,
    get_strategy_metrics,
    list_strategies,
    list_strategy_runs,
    list_strategy_versions,
    promote_strategy,
    rollback_strategy,
    start_strategy,
    stop_strategy,
    update_strategy,
)

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("", response_model=ApiEnvelope)
def get_strategies(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(list_strategies(db, current_user=current_user))


@router.post("", response_model=ApiEnvelope)
def post_strategy(request: StrategyCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(create_strategy(db, current_user=current_user, request=request))


@router.get("/{strategy_id}", response_model=ApiEnvelope)
def get_strategy_detail(strategy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(get_strategy(db, current_user=current_user, strategy_id=strategy_id))


@router.patch("/{strategy_id}", response_model=ApiEnvelope)
def patch_strategy(strategy_id: str, request: StrategyUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(update_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request))


@router.post("/{strategy_id}/versions", response_model=ApiEnvelope)
def post_strategy_version(strategy_id: str, request: StrategyVersionCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(create_strategy_version(db, current_user=current_user, strategy_id=strategy_id, request=request))


@router.get("/{strategy_id}/versions", response_model=ApiEnvelope)
def get_strategy_versions(strategy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(list_strategy_versions(db, current_user=current_user, strategy_id=strategy_id))


@router.post("/{strategy_id}/start", response_model=ApiEnvelope)
def post_strategy_start(strategy_id: str, request: StrategyActionRequest | None = None, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(start_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request or StrategyActionRequest()))


@router.post("/{strategy_id}/stop", response_model=ApiEnvelope)
def post_strategy_stop(strategy_id: str, request: StrategyActionRequest | None = None, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(stop_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request or StrategyActionRequest()))


@router.post("/{strategy_id}/promote", response_model=ApiEnvelope)
def post_strategy_promote(strategy_id: str, request: StrategyPromotionRequest, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(promote_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request))


@router.post("/{strategy_id}/rollback", response_model=ApiEnvelope)
def post_strategy_rollback(strategy_id: str, request: StrategyRollbackRequest | None = None, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(rollback_strategy(db, current_user=current_user, strategy_id=strategy_id, request=request or StrategyRollbackRequest()))


@router.get("/{strategy_id}/runs", response_model=ApiEnvelope)
def get_strategy_runs(strategy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(list_strategy_runs(db, current_user=current_user, strategy_id=strategy_id))


@router.get("/{strategy_id}/metrics", response_model=ApiEnvelope)
def get_strategy_metric_snapshot(strategy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(get_strategy_metrics(db, current_user=current_user, strategy_id=strategy_id))
