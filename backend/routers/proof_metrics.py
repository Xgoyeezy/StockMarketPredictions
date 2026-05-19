from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.proof_metrics_dashboard import get_proof_metrics_dashboard_summary
from backend.services.research_report_cache import cached_research_report

router = APIRouter(prefix="/proof-metrics", tags=["proof-metrics"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary(
    include_slow_sources: bool = Query(default=False, description="Build expensive source reports synchronously. Default stays lightweight for app responsiveness."),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        cached_research_report(
            group="proof_metrics_summary_slow" if include_slow_sources else "proof_metrics_summary",
            current_user=current_user,
            builder=lambda: get_proof_metrics_dashboard_summary(db, current_user=current_user, collect_sources=include_slow_sources),
        )
    )
