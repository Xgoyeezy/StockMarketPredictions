from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.research_report_cache import cached_research_report
from backend.services.evidence_reward_engine import (
    get_evidence_reward_ai,
    get_evidence_reward_blockers,
    get_evidence_reward_candidates,
    get_evidence_reward_engines,
    get_evidence_reward_regimes,
    get_evidence_reward_setups,
    get_evidence_reward_summary,
)

router = APIRouter(prefix="/evidence-reward", tags=["evidence-reward"])


@router.get("/summary", response_model=ApiEnvelope)
def get_evidence_reward_summary_alias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        cached_research_report(
            group="evidence_reward_summary",
            current_user=current_user,
            builder=lambda: get_evidence_reward_summary(db, current_user=current_user),
        )
    )


@router.get("/candidates", response_model=ApiEnvelope)
def get_evidence_reward_candidates_alias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_reward_candidates(db, current_user=current_user))


@router.get("/blockers", response_model=ApiEnvelope)
def get_evidence_reward_blockers_alias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_reward_blockers(db, current_user=current_user))


@router.get("/engines", response_model=ApiEnvelope)
def get_evidence_reward_engines_alias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_reward_engines(db, current_user=current_user))


@router.get("/setups", response_model=ApiEnvelope)
def get_evidence_reward_setups_alias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_reward_setups(db, current_user=current_user))


@router.get("/ai", response_model=ApiEnvelope)
def get_evidence_reward_ai_alias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_reward_ai(db, current_user=current_user))


@router.get("/regimes", response_model=ApiEnvelope)
def get_evidence_reward_regimes_alias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_evidence_reward_regimes(db, current_user=current_user))
