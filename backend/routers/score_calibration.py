from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.research_report_cache import cached_research_report
from backend.services.score_calibration_attribution import (
    get_score_calibration_buckets,
    get_score_calibration_features,
    get_score_calibration_recommendations,
    get_score_calibration_regimes,
    get_score_calibration_summary,
)

router = APIRouter(prefix="/score-calibration", tags=["score-calibration"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        cached_research_report(
            group="score_calibration_summary",
            current_user=current_user,
            builder=lambda: get_score_calibration_summary(db, current_user=current_user),
        )
    )


@router.get("/buckets", response_model=ApiEnvelope)
def get_buckets(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_score_calibration_buckets(db, current_user=current_user))


@router.get("/features", response_model=ApiEnvelope)
def get_features(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_score_calibration_features(db, current_user=current_user))


@router.get("/regimes", response_model=ApiEnvelope)
def get_regimes(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_score_calibration_regimes(db, current_user=current_user))


@router.get("/recommendations", response_model=ApiEnvelope)
def get_recommendations(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_score_calibration_recommendations(db, current_user=current_user))
