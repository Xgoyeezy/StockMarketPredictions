from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.research_report_cache import cached_research_report
from backend.services.professional_benchmark_suite import (
    get_professional_benchmark_ai,
    get_professional_benchmark_baselines,
    get_professional_benchmark_blockers,
    get_professional_benchmark_execution,
    get_professional_benchmark_forecast,
    get_professional_benchmark_score_buckets,
    get_professional_benchmark_summary,
)

router = APIRouter(prefix="/professional-benchmark", tags=["professional-benchmark"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        cached_research_report(
            group="professional_benchmark_summary",
            current_user=current_user,
            builder=lambda: get_professional_benchmark_summary(db, current_user=current_user),
        )
    )


@router.get("/baselines", response_model=ApiEnvelope)
def get_baselines(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_professional_benchmark_baselines(db, current_user=current_user))


@router.get("/score-buckets", response_model=ApiEnvelope)
def get_score_buckets(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_professional_benchmark_score_buckets(db, current_user=current_user))


@router.get("/blockers", response_model=ApiEnvelope)
def get_blockers(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_professional_benchmark_blockers(db, current_user=current_user))


@router.get("/ai", response_model=ApiEnvelope)
def get_ai(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_professional_benchmark_ai(db, current_user=current_user))


@router.get("/forecast", response_model=ApiEnvelope)
def get_forecast(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_professional_benchmark_forecast(db, current_user=current_user))


@router.get("/execution", response_model=ApiEnvelope)
def get_execution(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_professional_benchmark_execution(db, current_user=current_user))
