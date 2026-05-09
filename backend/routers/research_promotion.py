from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.research_report_cache import cached_research_report
from backend.services.research_promotion_rules import (
    get_research_promotion_entities,
    get_research_promotion_entity,
    get_research_promotion_summary,
    update_research_promotion_status,
)

router = APIRouter(prefix="/research-promotion", tags=["research-promotion"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        cached_research_report(
            group="research_promotion_summary",
            current_user=current_user,
            builder=lambda: get_research_promotion_summary(db, current_user=current_user),
        )
    )


@router.get("/entities", response_model=ApiEnvelope)
def get_entities(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_research_promotion_entities(db, current_user=current_user))


@router.get("/entities/{entity_id}", response_model=ApiEnvelope)
def get_entity(
    entity_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_research_promotion_entity(entity_id, db, current_user=current_user))


@router.post("/entities/{entity_id}/status", response_model=ApiEnvelope)
def set_entity_status(
    entity_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(update_research_promotion_status(entity_id, payload, db=db, current_user=current_user))
