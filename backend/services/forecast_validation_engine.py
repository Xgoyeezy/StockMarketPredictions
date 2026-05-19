from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt
from statistics import mean, pstdev
from types import MappingProxyType
from typing import Any

from backend.services.project_finish_tracker import build_project_finish_tracker

SAFETY_NOTES: tuple[str, ...] = (
    "Research only. Does not affect trading.",
    "Does not change broker routes.",
    "Does not bypass risk gates.",
    "Does not place orders.",
    "Does not grant AI order authority.",
)
SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "paper_route_only": True,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
    "can_change_broker_routes": False,
    "can_bypass_risk_gates": False,
    "can_clear_kill_switch": False,
    "can_change_ranking_weights": False,
    "can_grant_ai_order_authority": False,
    "mutation": "none",
}
REQUIRED_FORECAST_FIELDS: tuple[str, ...] = (
    "prediction_id",
    "symbol",
    "prediction_created_at",
    "horizon_minutes",
    "forecast_series",
    "predicted_direction",
    "predicted_target_pct",
    "invalidation_level",
    "confidence",
    "source",
)

FORECAST_VALIDATION_HARDENING_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "forecast_contract_sample",
        "title": "Forecast contract sample",
        "priority": "critical",
        "missing_fields": ("prediction_id", "symbol", "prediction_created_at", "forecast_series"),
        "blocked_claims": ("forecast_review_claim", "benchmark_forecast_support"),
        "safe_next_action": "Keep timestamped forecast contracts visible before treating validation as reviewable.",
        "done_when": "At least one immutable forecast contract exists with symbol, timestamp, horizon, and forecast series.",
    },
    {
        "key": "complete_forecast_contracts",
        "title": "Complete forecast contracts",
        "priority": "critical",
        "missing_fields": REQUIRED_FORECAST_FIELDS,
        "blocked_claims": ("forecast_accuracy_claim", "forecast_reward_claim"),
        "safe_next_action": "Attach direction, target, invalidation, confidence, horizon, source, and forecast series to each forecast contract.",
        "done_when": "Most forecast records have complete pre-outcome forecast contract fields.",
    },
    {
        "key": "actual_path_coverage",
        "title": "Actual path coverage",
        "priority": "critical",
        "missing_fields": ("actual_series", "actual_price", "timestamp_offset"),
        "blocked_claims": ("forecast_accuracy_claim", "path_quality_claim"),
        "safe_next_action": "Attach actual post-prediction price paths broadly, not only fixture examples.",
        "done_when": "Most forecast contracts have aligned post-prediction actual paths for reward calculation.",
    },
    {
        "key": "target_invalidation_metrics",
        "title": "Target and invalidation metrics",
        "priority": "high",
        "missing_fields": ("target_hit", "invalidation_hit", "time_to_target", "max_adverse_excursion"),
        "blocked_claims": ("target_quality_claim", "risk_adjusted_forecast_claim"),
        "safe_next_action": "Keep target hit, invalidation hit, time-to-target, and max-adverse metrics attached to validated forecasts.",
        "done_when": "Validated forecasts include target, invalidation, timing, and adverse-excursion metrics.",
    },
    {
        "key": "calibration_and_regime_context",
        "title": "Calibration and regime context",
        "priority": "high",
        "missing_fields": ("confidence", "confidence_calibration_summary", "regime", "performance_by_regime"),
        "blocked_claims": ("calibrated_forecast_claim", "regime_stability_claim"),
        "safe_next_action": "Report confidence calibration and regime attribution before claiming forecast stability.",
        "done_when": "Forecast validation exposes confidence calibration and regime performance context.",
    },
    {
        "key": "immutable_validation_boundary",
        "title": "Immutable validation boundary",
        "priority": "critical",
        "missing_fields": (),
        "blocked_claims": ("mutable_forecast_record", "hindsight_edited_forecast_claim"),
        "safe_next_action": "Keep original forecast contracts immutable and store validation outcomes separately.",
        "done_when": "Forecast contracts remain immutable and validation output cannot mutate the original forecast record.",
    },
    {
        "key": "research_only_safety_boundary",
        "title": "Research-only safety boundary",
        "priority": "critical",
        "missing_fields": (),
        "blocked_claims": ("automatic_ranking_mutation", "live_trading_readiness", "ai_order_authority"),
        "safe_next_action": "Keep forecast validation read-only; do not alter execution, broker routes, risk gates, or ranking weights.",
        "done_when": "Forecast validation remains analytics-only with no execution, broker, risk, AI order, or ranking authority.",
    },
)


@dataclass(frozen=True)
class ForecastPoint:
    timestamp_offset: int
    predicted_price: float


@dataclass(frozen=True)
class ActualPoint:
    timestamp_offset: int
    price: float


@dataclass(frozen=True)
class ForecastPrediction:
    prediction_id: str
    symbol: str
    timestamp: datetime
    horizon_minutes: int
    forecast_series: tuple[ForecastPoint, ...]
    predicted_direction: str | None = None
    predicted_target_pct: float | None = None
    invalidation_level: float | None = None
    confidence: float | None = None
    engine: str | None = None
    source: str | None = None
    model_name: str | None = None
    regime: str | None = None

    @property
    def prediction_created_at(self) -> datetime:
        return self.timestamp


def _safe_mean(values: list[float | None] | tuple[float | None, ...]) -> float | None:
    filtered = [float(value) for value in values if value is not None]
    if not filtered:
        return None
    return mean(filtered)


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _round(value: Any, digits: int = 6) -> float | None:
    parsed = _safe_float(value)
    return None if parsed is None else round(parsed, digits)


def _utc_timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _direction_sign(direction: str | None) -> int | None:
    cleaned = str(direction or "").strip().lower()
    if cleaned in {"bullish", "long", "buy", "up", "higher"}:
        return 1
    if cleaned in {"bearish", "short", "sell", "down", "lower"}:
        return -1
    if cleaned in {"flat", "neutral", "range"}:
        return 0
    return None


def _direction_label(direction: str | None) -> str:
    sign = _direction_sign(direction)
    if sign == 1:
        return "up"
    if sign == -1:
        return "down"
    if sign == 0:
        return "flat"
    return "unknown"


def _series_direction(points: list[float]) -> str:
    if len(points) < 2:
        return "unknown"
    move = points[-1] - points[0]
    if move > 0:
        return "up"
    if move < 0:
        return "down"
    return "flat"


def make_prediction(
    *,
    prediction_id: str,
    symbol: str,
    timestamp: datetime | str | None = None,
    prediction_created_at: datetime | str | None = None,
    horizon_minutes: int,
    forecast_series: list[tuple[int, float]] | tuple[tuple[int, float], ...],
    predicted_direction: str | None = None,
    predicted_target_pct: float | None = None,
    invalidation_level: float | None = None,
    confidence: float | None = None,
    engine: str | None = None,
    source: str | None = None,
    model_name: str | None = None,
    regime: str | None = None,
) -> ForecastPrediction:
    created_at = prediction_created_at if prediction_created_at is not None else timestamp
    if created_at is None:
        raise ValueError("A forecast prediction requires prediction_created_at or timestamp.")
    points = tuple(
        ForecastPoint(timestamp_offset=int(offset), predicted_price=float(price))
        for offset, price in forecast_series
    )
    if len(points) < 2:
        raise ValueError("A forecast prediction requires at least two forecast points.")
    return ForecastPrediction(
        prediction_id=str(prediction_id),
        symbol=str(symbol).strip().upper(),
        timestamp=_utc_timestamp(created_at),
        horizon_minutes=int(horizon_minutes),
        forecast_series=points,
        predicted_direction=predicted_direction,
        predicted_target_pct=None if predicted_target_pct is None else float(predicted_target_pct),
        invalidation_level=None if invalidation_level is None else float(invalidation_level),
        confidence=None if confidence is None else max(0.0, min(float(confidence), 1.0)),
        engine=engine,
        source=source,
        model_name=model_name or engine,
        regime=regime,
    )


def _as_actual_points(series: list[tuple[int, float]] | tuple[tuple[int, float], ...] | tuple[ActualPoint, ...]) -> tuple[ActualPoint, ...]:
    points: list[ActualPoint] = []
    for item in series:
        if isinstance(item, ActualPoint):
            points.append(item)
        else:
            offset, price = item
            points.append(ActualPoint(timestamp_offset=int(offset), price=float(price)))
    return tuple(points)


def align_actual_series(
    prediction: ForecastPrediction,
    actual_series: list[tuple[int, float]] | tuple[tuple[int, float], ...] | tuple[ActualPoint, ...],
) -> dict[str, Any]:
    actual_lookup = {point.timestamp_offset: point.price for point in _as_actual_points(actual_series)}
    rows: list[dict[str, float | int | None]] = []
    missing_offsets: list[int] = []
    for point in prediction.forecast_series:
        actual_price = actual_lookup.get(point.timestamp_offset)
        if actual_price is None:
            missing_offsets.append(point.timestamp_offset)
        rows.append(
            {
                "timestamp_offset": point.timestamp_offset,
                "predicted_price": point.predicted_price,
                "actual_price": actual_price,
            }
        )
    return {
        "rows": rows,
        "missing_data": bool(missing_offsets),
        "missing_offsets": missing_offsets,
    }


def _paired_values(alignment: dict[str, Any]) -> tuple[list[float], list[float], list[int]]:
    predicted: list[float] = []
    actual: list[float] = []
    offsets: list[int] = []
    for row in alignment.get("rows", []):
        actual_price = row.get("actual_price")
        if actual_price is None:
            continue
        predicted.append(float(row["predicted_price"]))
        actual.append(float(actual_price))
        offsets.append(int(row["timestamp_offset"]))
    return predicted, actual, offsets


def _missing_forecast_fields(prediction: ForecastPrediction) -> list[str]:
    missing: list[str] = []
    field_values = {
        "prediction_id": prediction.prediction_id,
        "symbol": prediction.symbol,
        "prediction_created_at": prediction.prediction_created_at,
        "horizon_minutes": prediction.horizon_minutes,
        "forecast_series": prediction.forecast_series,
        "predicted_direction": prediction.predicted_direction,
        "predicted_target_pct": prediction.predicted_target_pct,
        "invalidation_level": prediction.invalidation_level,
        "confidence": prediction.confidence,
        "source": prediction.source,
    }
    for field, value in field_values.items():
        if value in (None, "", ()):
            missing.append(field)
    if prediction.predicted_direction and _direction_sign(prediction.predicted_direction) is None:
        missing.append("predicted_direction")
    return list(dict.fromkeys(missing))


def compute_direction_score(
    prediction: ForecastPrediction,
    actual_series: list[tuple[int, float]] | tuple[tuple[int, float], ...] | tuple[ActualPoint, ...],
) -> dict[str, Any]:
    alignment = align_actual_series(prediction, actual_series)
    predicted, actual, _ = _paired_values(alignment)
    if len(actual) < 2:
        return {
            "score": 0.0,
            "direction_correct": False,
            "predicted_direction": _direction_label(prediction.predicted_direction),
            "forecast_path_direction": _series_direction(predicted),
            "actual_direction": "unknown",
        }
    predicted_direction = _direction_label(prediction.predicted_direction)
    if predicted_direction == "unknown":
        predicted_direction = _series_direction(predicted)
    actual_direction = _series_direction(actual)
    direction_correct = predicted_direction == actual_direction
    return {
        "score": 1.0 if direction_correct else 0.0,
        "direction_correct": direction_correct,
        "predicted_direction": predicted_direction,
        "forecast_path_direction": _series_direction(predicted),
        "actual_direction": actual_direction,
    }


def compute_path_error(
    prediction: ForecastPrediction,
    actual_series: list[tuple[int, float]] | tuple[tuple[int, float], ...] | tuple[ActualPoint, ...],
) -> float | None:
    predicted, actual, _ = _paired_values(align_actual_series(prediction, actual_series))
    if not predicted:
        return None
    return mean(abs(left - right) for left, right in zip(predicted, actual))


def compute_path_rmse(
    prediction: ForecastPrediction,
    actual_series: list[tuple[int, float]] | tuple[tuple[int, float], ...] | tuple[ActualPoint, ...],
) -> float | None:
    predicted, actual, _ = _paired_values(align_actual_series(prediction, actual_series))
    if not predicted:
        return None
    return sqrt(mean((left - right) ** 2 for left, right in zip(predicted, actual)))


def compute_timing_score(
    prediction: ForecastPrediction,
    actual_series: list[tuple[int, float]] | tuple[tuple[int, float], ...] | tuple[ActualPoint, ...],
) -> dict[str, Any]:
    predicted, actual, offsets = _paired_values(align_actual_series(prediction, actual_series))
    if len(predicted) < 2:
        return {"score": 0.0, "timing_error": None, "predicted_turn_offset": None, "actual_turn_offset": None}
    direction = _direction_label(prediction.predicted_direction)
    if direction == "unknown":
        direction = _series_direction(predicted)
    if direction == "down":
        predicted_index = predicted.index(min(predicted))
        actual_index = actual.index(min(actual))
    else:
        predicted_index = predicted.index(max(predicted))
        actual_index = actual.index(max(actual))
    predicted_offset = offsets[predicted_index]
    actual_offset = offsets[actual_index]
    timing_error = abs(predicted_offset - actual_offset)
    score = max(0.0, 1.0 - (timing_error / max(int(prediction.horizon_minutes), 1)))
    return {
        "score": score,
        "timing_error": timing_error,
        "predicted_turn_offset": predicted_offset,
        "actual_turn_offset": actual_offset,
    }


def compute_max_adverse_excursion(
    prediction: ForecastPrediction,
    actual_series: list[tuple[int, float]] | tuple[tuple[int, float], ...] | tuple[ActualPoint, ...],
) -> float | None:
    _, actual, _ = _paired_values(align_actual_series(prediction, actual_series))
    if len(actual) < 2:
        return None
    direction = _direction_label(prediction.predicted_direction)
    anchor = actual[0]
    if anchor == 0:
        return None
    if direction == "down":
        adverse = max(actual) - anchor
    elif direction == "up":
        adverse = anchor - min(actual)
    else:
        adverse = max(abs(price - anchor) for price in actual)
    return max(0.0, adverse / abs(anchor))


def compute_volatility_mismatch(
    prediction: ForecastPrediction,
    actual_series: list[tuple[int, float]] | tuple[tuple[int, float], ...] | tuple[ActualPoint, ...],
) -> dict[str, float | None]:
    predicted, actual, _ = _paired_values(align_actual_series(prediction, actual_series))
    if len(predicted) < 3:
        return {"predicted_volatility": None, "actual_volatility": None, "mismatch": None}
    predicted_returns = [(predicted[index] / predicted[index - 1]) - 1.0 for index in range(1, len(predicted)) if predicted[index - 1] != 0]
    actual_returns = [(actual[index] / actual[index - 1]) - 1.0 for index in range(1, len(actual)) if actual[index - 1] != 0]
    if not predicted_returns or not actual_returns:
        return {"predicted_volatility": None, "actual_volatility": None, "mismatch": None}
    predicted_vol = pstdev(predicted_returns)
    actual_vol = pstdev(actual_returns)
    return {
        "predicted_volatility": predicted_vol,
        "actual_volatility": actual_vol,
        "mismatch": abs(predicted_vol - actual_vol),
    }


def _target_and_invalidation(
    prediction: ForecastPrediction,
    actual_series: list[tuple[int, float]] | tuple[tuple[int, float], ...] | tuple[ActualPoint, ...],
) -> dict[str, Any]:
    actual_points = sorted(_as_actual_points(actual_series), key=lambda point: point.timestamp_offset)
    if len(actual_points) < 2:
        return {"target_hit": False, "invalidation_hit": False, "time_to_target": None}
    anchor = actual_points[0].price
    sign = _direction_sign(prediction.predicted_direction)
    target_pct = _safe_float(prediction.predicted_target_pct)
    invalidation = _safe_float(prediction.invalidation_level)
    target_hit = False
    invalidation_hit = False
    time_to_target = None
    if sign == 1 and target_pct is not None:
        target_price = anchor * (1.0 + abs(target_pct) / 100.0)
        for point in actual_points:
            if point.price >= target_price:
                target_hit = True
                time_to_target = point.timestamp_offset
                break
        if invalidation is not None:
            invalidation_hit = any(point.price <= invalidation for point in actual_points)
    elif sign == -1 and target_pct is not None:
        target_price = anchor * (1.0 - abs(target_pct) / 100.0)
        for point in actual_points:
            if point.price <= target_price:
                target_hit = True
                time_to_target = point.timestamp_offset
                break
        if invalidation is not None:
            invalidation_hit = any(point.price >= invalidation for point in actual_points)
    elif invalidation is not None:
        invalidation_hit = any(point.price == invalidation for point in actual_points)
    return {
        "target_hit": target_hit,
        "invalidation_hit": invalidation_hit,
        "time_to_target": time_to_target,
    }


def _confidence_penalty(confidence: float | None, direction_correct: bool | None) -> tuple[float, float | None]:
    if confidence is None or direction_correct is None:
        return 0.0, None
    expected = 1.0 if direction_correct else 0.0
    error = abs(float(confidence) - expected)
    high_confidence_wrong_extra = max(0.0, float(confidence) - 0.70) if not direction_correct else 0.0
    return round(error + high_confidence_wrong_extra, 6), round(error, 6)


def evaluate_prediction(
    prediction: ForecastPrediction,
    actual_series: list[tuple[int, float]] | tuple[tuple[int, float], ...] | tuple[ActualPoint, ...],
) -> dict[str, Any]:
    alignment = align_actual_series(prediction, actual_series)
    direction = compute_direction_score(prediction, actual_series)
    mae = compute_path_error(prediction, actual_series)
    rmse = compute_path_rmse(prediction, actual_series)
    timing = compute_timing_score(prediction, actual_series)
    adverse = compute_max_adverse_excursion(prediction, actual_series)
    volatility = compute_volatility_mismatch(prediction, actual_series)
    target = _target_and_invalidation(prediction, actual_series)
    volatility_mismatch = volatility["mismatch"]
    predicted, actual, _ = _paired_values(alignment)
    anchor = actual[0] if actual else (predicted[0] if predicted else 1.0)
    scale = max(abs(anchor) * 0.02, abs((predicted[-1] - predicted[0]) if len(predicted) >= 2 else 0.0), 1e-9)
    path_fit_score = 0.0 if mae is None else max(0.0, 1.0 - (mae / scale))
    drawdown_penalty = 0.0 if adverse is None else min(1.0, adverse / 0.02)
    volatility_mismatch_penalty = 0.0 if volatility_mismatch is None else min(1.0, volatility_mismatch / 0.01)
    confidence_penalty, confidence_error = _confidence_penalty(prediction.confidence, direction["direction_correct"])
    complete_forward_data = not alignment["missing_data"] and len(actual) == len(prediction.forecast_series) and len(actual) >= 2
    missing_fields = _missing_forecast_fields(prediction)
    missing_data: list[str] = []
    if not complete_forward_data:
        missing_data.append("actual_series")
    rewardable = not missing_fields and not missing_data
    reward = None
    if rewardable:
        reward = (
            direction["score"]
            + path_fit_score
            + timing["score"]
            - drawdown_penalty
            - volatility_mismatch_penalty
            - confidence_penalty
        )
    component_scores = {
        "direction_score": direction["score"] if rewardable else None,
        "path_fit_score": path_fit_score if rewardable else None,
        "timing_score": timing["score"] if rewardable else None,
        "drawdown_penalty": drawdown_penalty if rewardable else None,
        "volatility_mismatch_penalty": volatility_mismatch_penalty if rewardable else None,
        "confidence_penalty": confidence_penalty if rewardable else None,
    }
    warnings: list[str] = []
    if missing_fields:
        warnings.append("Forecast lacks a complete timestamped prediction contract.")
    if missing_data:
        warnings.append("Forward actual data is incomplete; forecast reward is not computed.")
    return {
        "prediction_id": prediction.prediction_id,
        "symbol": prediction.symbol,
        "timestamp": prediction.timestamp.isoformat(),
        "prediction_created_at": prediction.prediction_created_at.isoformat(),
        "horizon_minutes": prediction.horizon_minutes,
        "source": prediction.source,
        "model_name": prediction.model_name or prediction.engine or prediction.source,
        "engine": prediction.engine,
        "regime": prediction.regime or "unknown",
        "confidence": prediction.confidence,
        "predicted_direction": direction["predicted_direction"],
        "predicted_target_pct": prediction.predicted_target_pct,
        "invalidation_level": prediction.invalidation_level,
        "rewardable": rewardable,
        "missing_fields": missing_fields,
        "missing_data": missing_data,
        "direction_accuracy": 1.0 if direction["direction_correct"] else 0.0 if rewardable else None,
        "direction_correct": direction["direction_correct"],
        "actual_direction": direction["actual_direction"],
        "actual_forward_return": _round(((actual[-1] - actual[0]) / actual[0]) * 100.0, 6) if len(actual) >= 2 and actual[0] else None,
        "path_mae": _round(mae),
        "path_rmse": _round(rmse),
        "mae": _round(mae),
        "rmse": _round(rmse),
        "timing_error": timing["timing_error"],
        "max_adverse_excursion": _round(adverse),
        "volatility_mismatch": _round(volatility_mismatch),
        "predicted_volatility": _round(volatility["predicted_volatility"]),
        "actual_volatility": _round(volatility["actual_volatility"]),
        "confidence_calibration": confidence_error,
        "target_hit": target["target_hit"],
        "invalidation_hit": target["invalidation_hit"],
        "time_to_target": target["time_to_target"],
        "time_to_target_minutes": target["time_to_target"],
        "component_scores": component_scores,
        "reward_components": component_scores,
        "forecast_total_reward": _round(reward),
        "reward": _round(reward),
        "interpretation": "Rewardable forecast contract." if rewardable else "Visible but excluded from forecast reward averages.",
        "warnings": warnings,
        "alignment": alignment["rows"],
    }


def _calibration_bucket(confidence: float | None) -> str:
    if confidence is None:
        return "unknown"
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.55:
        return "medium"
    return "low"


def aggregate_evaluations(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(key) or "unknown"), []).append(row)
    output: list[dict[str, Any]] = []
    for group_key, group_rows in grouped.items():
        confidence_groups: dict[str, list[dict[str, Any]]] = {}
        for row in group_rows:
            confidence_groups.setdefault(_calibration_bucket(row.get("confidence")), []).append(row)
        evaluated_rows = [row for row in group_rows if row.get("forecast_total_reward") is not None]
        output.append(
            {
                key: group_key,
                "count": len(group_rows),
                "evaluated_count": len(evaluated_rows),
                "validated_forecasts": len(evaluated_rows),
                "non_rewardable_forecasts": len(group_rows) - len(evaluated_rows),
                "missing_data_count": sum(1 for row in group_rows if row.get("missing_data")),
                "avg_reward": _safe_mean([row.get("forecast_total_reward") for row in evaluated_rows]),
                "avg_forecast_reward": _safe_mean([row.get("forecast_total_reward") for row in evaluated_rows]),
                "direction_accuracy": _safe_mean([row.get("direction_accuracy") for row in evaluated_rows]),
                "avg_mae": _safe_mean([row.get("path_mae") for row in evaluated_rows]),
                "avg_rmse": _safe_mean([row.get("path_rmse") for row in evaluated_rows]),
                "avg_timing_error": _safe_mean([row.get("timing_error") for row in evaluated_rows]),
                "calibration_vs_confidence": [
                    {
                        "bucket": bucket,
                        "count": len(bucket_rows),
                        "avg_confidence": _safe_mean([row.get("confidence") for row in bucket_rows]),
                        "direction_accuracy": _safe_mean(
                            [row.get("direction_accuracy") for row in bucket_rows if row.get("forecast_total_reward") is not None]
                        ),
                    }
                    for bucket, bucket_rows in sorted(confidence_groups.items())
                ],
            }
        )
    return sorted(output, key=lambda item: item["avg_forecast_reward"] if item["avg_forecast_reward"] is not None else -999.0, reverse=True)


def _prediction_to_dict(prediction: ForecastPrediction) -> dict[str, Any]:
    return {
        "prediction_id": prediction.prediction_id,
        "symbol": prediction.symbol,
        "timestamp": prediction.timestamp.isoformat(),
        "prediction_created_at": prediction.prediction_created_at.isoformat(),
        "horizon_minutes": prediction.horizon_minutes,
        "forecast_series": [
            {"timestamp_offset": point.timestamp_offset, "predicted_price": point.predicted_price}
            for point in prediction.forecast_series
        ],
        "predicted_direction": prediction.predicted_direction,
        "predicted_target_pct": prediction.predicted_target_pct,
        "invalidation_level": prediction.invalidation_level,
        "confidence": prediction.confidence,
        "engine": prediction.engine,
        "source": prediction.source,
        "model_name": prediction.model_name,
        "regime": prediction.regime,
    }


def _build_fixture_store() -> tuple[MappingProxyType[str, ForecastPrediction], MappingProxyType[str, tuple[ActualPoint, ...]]]:
    fixtures = [
        (
            make_prediction(
                prediction_id="fv-spy-perfect-trend",
                symbol="SPY",
                prediction_created_at="2026-05-01T14:30:00+00:00",
                horizon_minutes=60,
                forecast_series=[(0, 510.0), (15, 511.25), (30, 512.5), (45, 513.25), (60, 514.0)],
                predicted_direction="bullish",
                predicted_target_pct=0.6,
                invalidation_level=508.0,
                confidence=0.82,
                engine="tradable_direction_overlay",
                source="chart_overlay",
                model_name="tradable_direction_overlay_v1",
                regime="trending",
            ),
            [(0, 510.0), (15, 511.3), (30, 512.4), (45, 513.2), (60, 514.1)],
        ),
        (
            make_prediction(
                prediction_id="fv-qqq-wrong-direction",
                symbol="QQQ",
                prediction_created_at="2026-05-01T15:00:00+00:00",
                horizon_minutes=60,
                forecast_series=[(0, 438.0), (15, 439.0), (30, 440.1), (45, 441.0), (60, 442.0)],
                predicted_direction="bullish",
                predicted_target_pct=0.6,
                invalidation_level=436.0,
                confidence=0.71,
                engine="tradable_direction_overlay",
                source="chart_overlay",
                model_name="tradable_direction_overlay_v1",
                regime="choppy",
            ),
            [(0, 438.0), (15, 437.3), (30, 436.5), (45, 435.9), (60, 435.2)],
        ),
        (
            make_prediction(
                prediction_id="fv-spy-timing-miss",
                symbol="SPY",
                prediction_created_at="2026-05-02T16:00:00+00:00",
                horizon_minutes=60,
                forecast_series=[(0, 514.0), (15, 513.5), (30, 512.8), (45, 512.2), (60, 511.5)],
                predicted_direction="bearish",
                predicted_target_pct=0.45,
                invalidation_level=515.0,
                confidence=0.63,
                engine="range_pressure_overlay",
                source="chart_overlay",
                model_name="range_pressure_overlay_v1",
                regime="high_vol",
            ),
            [(0, 514.0), (15, 515.2), (30, 512.9), (45, 511.9), (60, 511.4)],
        ),
        (
            make_prediction(
                prediction_id="fv-iwm-missing-data",
                symbol="IWM",
                prediction_created_at="2026-05-02T17:00:00+00:00",
                horizon_minutes=60,
                forecast_series=[(0, 207.0), (15, 207.1), (30, 207.25), (45, 207.4), (60, 207.55)],
                predicted_direction="bullish",
                predicted_target_pct=0.25,
                invalidation_level=206.5,
                confidence=0.48,
                engine="tradable_direction_overlay",
                source="chart_overlay",
                model_name="tradable_direction_overlay_v1",
                regime="low_vol",
            ),
            [(0, 207.0), (15, 207.05), (60, 207.2)],
        ),
    ]
    predictions = MappingProxyType({prediction.prediction_id: prediction for prediction, _ in fixtures})
    actuals = MappingProxyType(
        {
            prediction.prediction_id: _as_actual_points(actual_series)
            for prediction, actual_series in fixtures
        }
    )
    return predictions, actuals


_PREDICTIONS, _ACTUALS = _build_fixture_store()


def get_actual_series_for_prediction(prediction_id: str) -> tuple[ActualPoint, ...]:
    actual_series = _ACTUALS.get(str(prediction_id))
    if actual_series is None:
        raise KeyError(f"Unknown forecast prediction id: {prediction_id}")
    return actual_series


def list_predictions() -> list[dict[str, Any]]:
    return [
        {**_prediction_to_dict(prediction), "evaluation": evaluate_prediction(prediction, _ACTUALS[prediction.prediction_id])}
        for prediction in _PREDICTIONS.values()
    ]


def _evaluations() -> list[dict[str, Any]]:
    return [item["evaluation"] for item in list_predictions()]


def model_summary() -> list[dict[str, Any]]:
    return aggregate_evaluations(_evaluations(), "engine")


def regime_summary() -> list[dict[str, Any]]:
    return aggregate_evaluations(_evaluations(), "regime")


def _missing_field_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        counter.update(row.get("missing_fields") or [])
        counter.update(row.get("missing_data") or [])
    return dict(counter)


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 6)


def build_forecast_validation_hardening_plan(
    *,
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    aggregations: dict[str, Any],
) -> dict[str, Any]:
    total_forecasts = int(summary.get("total_forecasts") or summary.get("count") or len(records))
    evaluated_count = int(summary.get("evaluated_count") or summary.get("validated_forecasts") or 0)
    missing_data_count = int(summary.get("missing_data_count") or 0)
    missing_counts = dict(summary.get("missing_field_counts") or {})
    actual_path_coverage = _ratio(evaluated_count, total_forecasts) or 0.0
    complete_contract_count = max(0, total_forecasts - sum(int(value or 0) for value in missing_counts.values()))
    complete_contract_coverage = _ratio(complete_contract_count, total_forecasts) or 0.0
    target_metric_count = sum(
        1
        for row in (record.get("evaluation") or record for record in records)
        if row.get("target_hit") is not None
        and row.get("invalidation_hit") is not None
        and row.get("time_to_target") is not None
    )
    target_metric_coverage = _ratio(target_metric_count, total_forecasts) or 0.0
    calibration_rows = list(summary.get("confidence_calibration_summary") or [])
    regime_rows = list(aggregations.get("performance_by_regime") or [])
    metric_values = {
        "forecast_contract_sample": total_forecasts,
        "complete_forecast_contracts": complete_contract_coverage,
        "actual_path_coverage": actual_path_coverage,
        "target_invalidation_metrics": target_metric_coverage,
        "calibration_and_regime_context": 1 if calibration_rows and regime_rows else 0,
        "immutable_validation_boundary": 1,
        "research_only_safety_boundary": int(
            SAFETY_FLAGS["can_submit_orders"] is False
            and SAFETY_FLAGS["can_submit_live_orders"] is False
            and SAFETY_FLAGS["mutation"] == "none"
        ),
    }
    thresholds = {
        "forecast_contract_sample": 1,
        "complete_forecast_contracts": 0.80,
        "actual_path_coverage": 0.80,
        "target_invalidation_metrics": 0.80,
        "calibration_and_regime_context": 1,
        "immutable_validation_boundary": 1,
        "research_only_safety_boundary": 1,
    }
    items: list[dict[str, Any]] = []
    for definition in FORECAST_VALIDATION_HARDENING_DEFINITIONS:
        key = str(definition["key"])
        value = metric_values[key]
        threshold = thresholds[key]
        passed = bool(value >= threshold)
        status = "no_records" if total_forecasts == 0 and key not in {"immutable_validation_boundary", "research_only_safety_boundary"} else "ready" if passed else "needs_evidence"
        missing_fields = list(definition.get("missing_fields") or ())
        if key in {"complete_forecast_contracts", "actual_path_coverage"} and missing_counts:
            missing_fields = [
                field
                for field, _count in sorted(missing_counts.items(), key=lambda item: (-int(item[1]), item[0]))
            ][:8] or missing_fields
        items.append(
            {
                "key": key,
                "title": definition["title"],
                "priority": definition["priority"],
                "status": status,
                "passed": passed,
                "value": value,
                "threshold": threshold,
                "missing_fields": missing_fields,
                "blocked_claims": list(definition.get("blocked_claims") or ()),
                "safe_next_action": definition["safe_next_action"],
                "done_when": definition["done_when"],
                "claim_boundary": "Forecast Validation hardening is internal research review only; it is not proof of alpha, forecast edge, repeatability, paper-to-live readiness, or live-trading readiness.",
                "manual_review_only": True,
                "research_only": True,
                "changes_execution": False,
                "changes_order_submission": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
                "changes_ranking_weights": False,
                "can_submit_orders": False,
                "can_submit_live_orders": False,
                "can_change_broker_routes": False,
                "can_bypass_risk_gates": False,
                "can_change_ranking_weights": False,
                "can_grant_ai_order_authority": False,
            }
        )

    open_items = [row for row in items if row["status"] != "ready"]
    critical_open_items = [row for row in open_items if row.get("priority") == "critical"]
    return {
        "status": "ready_for_human_review" if evaluated_count > 0 and not open_items else "blocked_by_evidence",
        "summary": {
            "item_count": len(items),
            "open_item_count": len(open_items),
            "critical_open_items": len(critical_open_items),
            "ready_item_count": len(items) - len(open_items),
            "top_hardening_item": open_items[0]["title"] if open_items else None,
            "actual_path_coverage": actual_path_coverage,
            "complete_contract_coverage": complete_contract_coverage,
            "target_metric_coverage": target_metric_coverage,
            "missing_data_count": missing_data_count,
            "proof_first_rule": "Ambition is allowed. Proof decides priority.",
            "claim_permissions": {
                "cautious_internal_forecast_review": evaluated_count > 0,
                "forecast_accuracy_claim": False,
                "benchmark_forecast_support": False,
                "automatic_ranking_mutation": False,
                "paper_to_live_readiness": False,
                "live_trading_readiness": False,
            },
            "blocked_claims": [
                "forecast_accuracy_claim",
                "forecast_edge_claim",
                "repeatability_claim",
                "automatic_ranking_mutation",
                "paper_to_live_readiness",
                "live_trading_readiness",
            ],
            "safe_boundary": "Forecast Validation hardening records missing actual-path, contract, calibration, and claim-boundary evidence only. It does not mutate forecast contracts, submit orders, change broker routes, bypass risk gates, or change ranking weights.",
        },
        "items": items,
        "safe_next_actions": [
            {
                "field": row["key"],
                "action": row["safe_next_action"],
                "manual_review_only": True,
                "changes_execution": False,
                "changes_order_submission": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
                "changes_ranking_weights": False,
                "can_change_broker_routes": False,
                "can_bypass_risk_gates": False,
                "can_change_ranking_weights": False,
                "can_grant_ai_order_authority": False,
            }
            for row in open_items
        ],
        "research_only": True,
        **SAFETY_FLAGS,
    }


def _forecast_unified_response(
    *,
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    aggregations: dict[str, Any],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    hardening_plan = build_forecast_validation_hardening_plan(
        summary=summary,
        records=records,
        aggregations=aggregations,
    )
    summary = {
        **summary,
        "forecast_hardening_status": hardening_plan["status"],
        "forecast_hardening_open_items": hardening_plan["summary"]["open_item_count"],
        "forecast_hardening_critical_open_items": hardening_plan["summary"]["critical_open_items"],
        "top_hardening_item": hardening_plan["summary"]["top_hardening_item"],
        "claim_permissions": hardening_plan["summary"]["claim_permissions"],
    }
    aggregations = {**aggregations, "forecast_validation_hardening_plan": hardening_plan}
    return {
        "status": summary.get("data_status", "ready"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "summary": summary,
        "records": records,
        "aggregations": aggregations,
        "forecast_validation_hardening_plan": hardening_plan,
        "missing_fields": _missing_field_counts([row.get("evaluation", row) for row in records]),
        "warnings": warnings or [],
        "safety_notes": list(SAFETY_NOTES),
        **SAFETY_FLAGS,
        "finish_tracker": build_project_finish_tracker(report_name="forecast_validation"),
    }


def validation_summary() -> dict[str, Any]:
    prediction_items = list_predictions()
    rows = [item["evaluation"] for item in prediction_items]
    evaluated_rows = [row for row in rows if row.get("forecast_total_reward") is not None]
    best = max(evaluated_rows, key=lambda row: row["forecast_total_reward"]) if evaluated_rows else None
    worst = min(evaluated_rows, key=lambda row: row["forecast_total_reward"]) if evaluated_rows else None
    missing_counts = _missing_field_counts(rows)
    return {
        "mode": "research_only",
        "safety": "Forecast validation is read-only and never adjusts execution, ranking weights, or risk gates.",
        "total_forecasts": len(rows),
        "count": len(rows),
        "validated_forecasts": len(evaluated_rows),
        "evaluated_count": len(evaluated_rows),
        "non_rewardable_forecasts": len(rows) - len(evaluated_rows),
        "avg_forecast_reward": _safe_mean([row.get("forecast_total_reward") for row in evaluated_rows]),
        "avg_reward": _safe_mean([row.get("forecast_total_reward") for row in evaluated_rows]),
        "direction_accuracy": _safe_mean([row.get("direction_accuracy") for row in evaluated_rows]),
        "avg_path_mae": _safe_mean([row.get("path_mae") for row in evaluated_rows]),
        "avg_path_rmse": _safe_mean([row.get("path_rmse") for row in evaluated_rows]),
        "avg_mae": _safe_mean([row.get("path_mae") for row in evaluated_rows]),
        "avg_rmse": _safe_mean([row.get("path_rmse") for row in evaluated_rows]),
        "avg_timing_error": _safe_mean([row.get("timing_error") for row in evaluated_rows]),
        "missing_data_count": sum(1 for row in rows if row.get("missing_data")),
        "missing_field_counts": missing_counts,
        "best_prediction": best,
        "worst_prediction": worst,
        "data_status": "ready" if evaluated_rows else "no_rewardable_forecasts" if rows else "empty",
        "reward_formula": "direction_score + path_fit_score + timing_score - drawdown_penalty - volatility_mismatch_penalty - confidence_penalty",
        "forecast_reward_formula": "direction_score + path_fit_score + timing_score - drawdown_penalty - volatility_mismatch_penalty - confidence_penalty",
        "confidence_calibration_summary": [
            row
            for aggregate in aggregate_evaluations(rows, "engine")
            for row in aggregate.get("calibration_vs_confidence", [])
        ],
        **SAFETY_FLAGS,
    }


def get_forecast_validation_summary() -> dict[str, Any]:
    prediction_items = list_predictions()
    rows = [item["evaluation"] for item in prediction_items]
    summary = validation_summary()
    aggregations = {
        "performance_by_model": aggregate_evaluations(rows, "model_name"),
        "performance_by_regime": regime_summary(),
        "confidence_calibration_summary": summary["confidence_calibration_summary"],
        "best_forecasts": sorted(rows, key=lambda row: row.get("forecast_total_reward") if row.get("forecast_total_reward") is not None else -9999, reverse=True)[:5],
        "worst_forecasts": sorted(rows, key=lambda row: row.get("forecast_total_reward") if row.get("forecast_total_reward") is not None else 9999)[:5],
        "missing_field_counts": summary["missing_field_counts"],
    }
    warnings = []
    if summary["non_rewardable_forecasts"]:
        warnings.append("Some forecasts are visible but excluded from reward averages because contract or actual data is incomplete.")
    response = _forecast_unified_response(summary=summary, records=prediction_items, aggregations=aggregations, warnings=warnings)
    return {
        **response,
        **summary,
        "items": prediction_items,
        "mode": "research_only",
    }


def get_forecast_validation_predictions() -> dict[str, Any]:
    predictions = list_predictions()
    summary = validation_summary()
    response = _forecast_unified_response(
        summary=summary,
        records=predictions,
        aggregations={"prediction_count": len(predictions)},
        warnings=["Forecast rows are immutable; validation data is reported separately."],
    )
    return {
        **response,
        "mode": "research_only",
        "items": predictions,
        "count": len(predictions),
    }


def get_forecast_validation_models() -> dict[str, Any]:
    rows = _evaluations()
    by_engine = aggregate_evaluations(rows, "engine")
    by_source = aggregate_evaluations(rows, "source")
    by_model = aggregate_evaluations(rows, "model_name")
    response = _forecast_unified_response(
        summary=validation_summary(),
        records=by_model,
        aggregations={"performance_by_model": by_model, "by_engine": by_engine, "by_source": by_source},
    )
    return {
        **response,
        "mode": "research_only",
        "by_engine": by_engine,
        "by_source": by_source,
        "by_model": by_model,
    }


def get_forecast_validation_regimes() -> dict[str, Any]:
    items = regime_summary()
    response = _forecast_unified_response(
        summary=validation_summary(),
        records=items,
        aggregations={"performance_by_regime": items},
    )
    return {
        **response,
        "mode": "research_only",
        "items": items,
    }
