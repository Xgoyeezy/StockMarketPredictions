from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.research_report_cache import cached_research_report
from backend.services.data_completeness_audit import (
    get_data_completeness_ai,
    get_data_completeness_benchmark_readiness,
    get_data_completeness_blockers,
    get_data_completeness_candidates,
    get_data_completeness_execution,
    get_data_completeness_forecasts,
    get_data_completeness_summary,
)

router = APIRouter(prefix="/data-completeness", tags=["data-completeness"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        cached_research_report(
            group="data_completeness_summary",
            current_user=current_user,
            builder=lambda: get_data_completeness_summary(db, current_user=current_user),
        )
    )


@router.get("/candidates", response_model=ApiEnvelope)
def get_candidates(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_data_completeness_candidates(db, current_user=current_user))


@router.get("/forecasts", response_model=ApiEnvelope)
def get_forecasts(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_data_completeness_forecasts(db, current_user=current_user))


@router.get("/ai", response_model=ApiEnvelope)
def get_ai(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_data_completeness_ai(db, current_user=current_user))


@router.get("/blockers", response_model=ApiEnvelope)
def get_blockers(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_data_completeness_blockers(db, current_user=current_user))


@router.get("/execution", response_model=ApiEnvelope)
def get_execution(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_data_completeness_execution(db, current_user=current_user))


@router.get("/benchmark-readiness", response_model=ApiEnvelope)
def get_benchmark_readiness(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_data_completeness_benchmark_readiness(db, current_user=current_user))
