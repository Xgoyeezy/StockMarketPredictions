from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.research_report_cache import cached_research_report
from backend.services.portfolio_risk_intelligence import (
    get_portfolio_risk_concentration,
    get_portfolio_risk_correlation,
    get_portfolio_risk_exposures,
    get_portfolio_risk_regimes,
    get_portfolio_risk_stress_tests,
    get_portfolio_risk_summary,
)

router = APIRouter(prefix="/portfolio-risk", tags=["portfolio-risk"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        cached_research_report(
            group="portfolio_risk_summary",
            current_user=current_user,
            builder=lambda: get_portfolio_risk_summary(db, current_user=current_user),
        )
    )


@router.get("/exposures", response_model=ApiEnvelope)
def get_exposures(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_portfolio_risk_exposures(db, current_user=current_user))


@router.get("/concentration", response_model=ApiEnvelope)
def get_concentration(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_portfolio_risk_concentration(db, current_user=current_user))


@router.get("/correlation", response_model=ApiEnvelope)
def get_correlation(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_portfolio_risk_correlation(db, current_user=current_user))


@router.get("/stress-tests", response_model=ApiEnvelope)
def get_stress_tests(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_portfolio_risk_stress_tests(db, current_user=current_user))


@router.get("/regimes", response_model=ApiEnvelope)
def get_regimes(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_portfolio_risk_regimes(db, current_user=current_user))
