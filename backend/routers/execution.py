from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.execution.router import ExecutionRouter

router = APIRouter(prefix="/execution", tags=["execution"])


@router.get("/diagnostics", response_model=ApiEnvelope)
def get_execution_diagnostics(
    instrument_type: str = Query(default="equity"),
    execution_intent: str = Query(default="default"),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    router_service = ExecutionRouter()
    return envelope(
        router_service.diagnostics(
            db=db,
            current_user=current_user,
            instrument_type=instrument_type,
            requested_intent=execution_intent,
        )
    )
