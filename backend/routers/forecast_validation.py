from __future__ import annotations

from fastapi import APIRouter

from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.forecast_validation_engine import (
    get_forecast_validation_models,
    get_forecast_validation_predictions,
    get_forecast_validation_regimes,
    get_forecast_validation_summary,
)

router = APIRouter(prefix="/forecast-validation", tags=["forecast-validation"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary() -> ApiEnvelope:
    return envelope(get_forecast_validation_summary())


@router.get("/predictions", response_model=ApiEnvelope)
def get_predictions() -> ApiEnvelope:
    return envelope(get_forecast_validation_predictions())


@router.get("/models", response_model=ApiEnvelope)
def get_models() -> ApiEnvelope:
    return envelope(get_forecast_validation_models())


@router.get("/regimes", response_model=ApiEnvelope)
def get_regimes() -> ApiEnvelope:
    return envelope(get_forecast_validation_regimes())
