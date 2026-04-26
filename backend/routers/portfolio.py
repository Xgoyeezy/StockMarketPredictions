from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.portfolio_service import get_open_trades, get_portfolio, get_portfolio_equity_curve, get_portfolio_performance, get_trade_journal
from sqlalchemy.orm import Session

router = APIRouter(tags=["portfolio"])


@router.get("/portfolio", response_model=ApiEnvelope)
def portfolio(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_portfolio(db=db, current_user=current_user))


@router.get("/portfolio/equity", response_model=ApiEnvelope)
def portfolio_equity(current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(get_portfolio_equity_curve(current_user=current_user))


@router.get("/portfolio/performance", response_model=ApiEnvelope)
def portfolio_performance(current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(get_portfolio_performance(current_user=current_user))


@router.get("/trades/journal", response_model=ApiEnvelope)
def trade_journal(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default=""),
    result_filter: str = Query(default="all"),
    direction_filter: str = Query(default="all"),
    attribution_filter: str = Query(default="all"),
    current_user: CurrentUser = Depends(get_current_user),
) -> ApiEnvelope:
    return envelope(
        get_trade_journal(
            limit=limit,
            offset=offset,
            search=search,
            result_filter=result_filter,
            direction_filter=direction_filter,
            attribution_filter=attribution_filter,
            current_user=current_user,
        )
    )


@router.get("/trades/open/preview", response_model=ApiEnvelope)
def open_trades_preview(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default=""),
    action_filter: str = Query(default="all"),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_open_trades(search=search, limit=limit, offset=offset, action_filter=action_filter, db=db, current_user=current_user))
