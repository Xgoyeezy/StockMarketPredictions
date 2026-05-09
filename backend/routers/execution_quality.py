from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.research_report_cache import cached_research_report
from backend.services.execution_quality_tca import (
    get_execution_quality_tca_alpha_decay,
    get_execution_quality_tca_engines,
    get_execution_quality_tca_setups,
    get_execution_quality_tca_slippage,
    get_execution_quality_tca_summary,
    get_execution_quality_tca_trades,
)

router = APIRouter(prefix="/execution-quality", tags=["execution-quality"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        cached_research_report(
            group="execution_quality_summary",
            current_user=current_user,
            builder=lambda: get_execution_quality_tca_summary(db, current_user=current_user),
        )
    )


@router.get("/trades", response_model=ApiEnvelope)
def get_trades(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_execution_quality_tca_trades(db, current_user=current_user))


@router.get("/slippage", response_model=ApiEnvelope)
def get_slippage(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_execution_quality_tca_slippage(db, current_user=current_user))


@router.get("/alpha-decay", response_model=ApiEnvelope)
def get_alpha_decay(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_execution_quality_tca_alpha_decay(db, current_user=current_user))


@router.get("/engines", response_model=ApiEnvelope)
def get_engines(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_execution_quality_tca_engines(db, current_user=current_user))


@router.get("/setups", response_model=ApiEnvelope)
def get_setups(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_execution_quality_tca_setups(db, current_user=current_user))
