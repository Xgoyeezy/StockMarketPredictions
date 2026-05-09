from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.research_report_cache import cached_research_report
from backend.services.walk_forward_experiment_registry import (
    clone_walk_forward_experiment,
    create_walk_forward_experiment_from_runtime,
    freeze_walk_forward_experiment,
    get_walk_forward_experiment,
    get_walk_forward_experiments,
    get_walk_forward_summary,
)

router = APIRouter(prefix="/walk-forward", tags=["walk-forward"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary(current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(
        cached_research_report(
            group="walk_forward_summary",
            current_user=current_user,
            builder=lambda: get_walk_forward_summary(),
        )
    )


@router.get("/experiments", response_model=ApiEnvelope)
def get_experiments() -> ApiEnvelope:
    return envelope(get_walk_forward_experiments())


@router.get("/experiments/{experiment_id}", response_model=ApiEnvelope)
def get_experiment(experiment_id: str) -> ApiEnvelope:
    return envelope(get_walk_forward_experiment(experiment_id))


@router.post("/experiments", response_model=ApiEnvelope)
def create_experiment(
    payload: dict[str, Any] = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(create_walk_forward_experiment_from_runtime(payload, db=db, current_user=current_user))


@router.post("/experiments/{experiment_id}/freeze", response_model=ApiEnvelope)
def freeze_experiment(
    experiment_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> ApiEnvelope:
    return envelope(freeze_walk_forward_experiment(experiment_id, current_user=current_user))


@router.post("/experiments/{experiment_id}/clone", response_model=ApiEnvelope)
def clone_experiment(
    experiment_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> ApiEnvelope:
    return envelope(clone_walk_forward_experiment(experiment_id, current_user=current_user))
