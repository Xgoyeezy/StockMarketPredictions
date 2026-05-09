from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.research_report_cache import cached_research_report
from backend.services.candidate_outcome_stamping_service import (
    get_evidence_outcomes_due,
    get_evidence_outcomes_records,
    get_evidence_outcomes_summary,
    post_evidence_outcomes_stamp_due,
)

router = APIRouter(prefix="/evidence-outcomes", tags=["evidence-outcomes"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        cached_research_report(
            group="evidence_outcomes_summary",
            current_user=current_user,
            builder=lambda: get_evidence_outcomes_summary(db, current_user=current_user),
        )
    )


@router.get("/due", response_model=ApiEnvelope)
def get_due(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_outcomes_due(db, current_user=current_user))


@router.get("/records", response_model=ApiEnvelope)
def get_records(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_outcomes_records(db, current_user=current_user))


@router.post("/stamp-due", response_model=ApiEnvelope)
def stamp_due(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(post_evidence_outcomes_stamp_due(db, current_user=current_user))
