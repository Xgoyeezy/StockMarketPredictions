from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.evidence_edge_analytics import (
    get_evidence_edge_blockers,
    get_evidence_edge_engines,
    get_evidence_edge_recommendations,
    get_evidence_edge_setups,
    get_evidence_edge_summary,
)

router = APIRouter(prefix="/evidence-edge", tags=["evidence-edge"])


@router.get("/summary", response_model=ApiEnvelope)
def get_evidence_edge_summary_alias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_edge_summary(db, current_user=current_user))


@router.get("/blockers", response_model=ApiEnvelope)
def get_evidence_edge_blockers_alias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_edge_blockers(db, current_user=current_user))


@router.get("/setups", response_model=ApiEnvelope)
def get_evidence_edge_setups_alias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_edge_setups(db, current_user=current_user))


@router.get("/engines", response_model=ApiEnvelope)
def get_evidence_edge_engines_alias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_edge_engines(db, current_user=current_user))


@router.get("/recommendations", response_model=ApiEnvelope)
def get_evidence_edge_recommendations_alias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_edge_recommendations(db, current_user=current_user))
