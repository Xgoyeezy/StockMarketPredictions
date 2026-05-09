from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.research_report_cache import cached_research_report
from backend.services.human_system_shadow_mode import (
    create_human_thesis,
    get_shadow_mode_bias,
    get_shadow_mode_comparisons,
    get_shadow_mode_records,
    get_shadow_mode_summary,
)

router = APIRouter(prefix="/shadow-mode", tags=["shadow-mode"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        cached_research_report(
            group="shadow_mode_summary",
            current_user=current_user,
            builder=lambda: get_shadow_mode_summary(db, current_user=current_user),
        )
    )


@router.get("/records", response_model=ApiEnvelope)
def get_records(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_shadow_mode_records(db, current_user=current_user))


@router.get("/comparisons", response_model=ApiEnvelope)
def get_comparisons(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_shadow_mode_comparisons(db, current_user=current_user))


@router.get("/bias", response_model=ApiEnvelope)
def get_bias(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_shadow_mode_bias(db, current_user=current_user))


@router.post("/human-thesis", response_model=ApiEnvelope)
def post_human_thesis(
    payload: dict[str, Any] = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
) -> ApiEnvelope:
    return envelope(create_human_thesis(payload, current_user=current_user))
