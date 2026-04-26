from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from hft.features.microstructure import MicrostructureFeatureSnapshot


@dataclass(frozen=True)
class FairValueEstimate:
    timestamp_ns: int
    symbol: str
    fair_value: float
    confidence_score: float
    expected_next_tick_direction: float
    expected_holding_time_ms: float


FEATURE_VECTOR_FIELDS: tuple[str, ...] = (
    "spread",
    "mid_price_movement",
    "order_book_imbalance",
    "queue_imbalance",
    "trade_imbalance",
    "short_term_volatility",
    "quote_update_rate",
    "cancel_rate",
    "trade_arrival_rate",
    "depth_imbalance",
    "price_impact_estimate",
    "adverse_selection_estimate",
    "microprice",
    "top_k_queue_depletion_rate",
    "signed_trade_intensity",
    "cancellation_burst_score",
    "quote_age_ns",
    "stale_book_indicator",
    "short_horizon_realized_impact",
)


@dataclass(frozen=True)
class LinearAlphaModel:
    feature_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float
    feature_means: tuple[float, ...]
    feature_stds: tuple[float, ...]
    ridge_lambda: float
    horizon_ns: int
    symbol_scope: str = "default"

    def predict_edge(self, features: MicrostructureFeatureSnapshot) -> float:
        vector = feature_snapshot_to_array(features, self.feature_names)
        means = np.array(self.feature_means, dtype=float)
        stds = np.array(self.feature_stds, dtype=float)
        standardized = (vector - means) / np.where(stds <= 0, 1.0, stds)
        coeffs = np.array(self.coefficients, dtype=float)
        return float(self.intercept + standardized.dot(coeffs))


class FairValueEngine:
    def __init__(
        self,
        *,
        imbalance_weight: float = 0.35,
        trade_weight: float = 0.25,
        momentum_weight: float = 0.20,
        adverse_selection_weight: float = 0.20,
        model: LinearAlphaModel | None = None,
        fair_value_sensitivity: float = 1.0,
    ):
        self.imbalance_weight = float(imbalance_weight)
        self.trade_weight = float(trade_weight)
        self.momentum_weight = float(momentum_weight)
        self.adverse_selection_weight = float(adverse_selection_weight)
        self.model = model
        self.fair_value_sensitivity = float(fair_value_sensitivity)

    def estimate(self, features: MicrostructureFeatureSnapshot) -> FairValueEstimate:
        if self.model is not None:
            predicted_edge = self.model.predict_edge(features)
            directional_score = predicted_edge / max(features.spread, 0.01)
            fair_value = features.mid_price + (predicted_edge * self.fair_value_sensitivity)
            confidence = max(0.0, min(1.0, abs(predicted_edge) / max(features.spread, 0.01)))
        else:
            directional_score = (
                self.imbalance_weight * features.order_book_imbalance
                + self.trade_weight * features.trade_imbalance
                + self.momentum_weight * features.mid_price_movement
                - self.adverse_selection_weight * features.adverse_selection_estimate
            )
            fair_value = features.mid_price + directional_score * max(features.spread, 0.01)
            confidence = max(0.0, min(1.0, abs(directional_score) + features.short_term_volatility))
        expected_holding_time_ms = max(
            50.0,
            500.0 - (confidence * 200.0) + (features.short_term_volatility * 1000.0),
        )
        return FairValueEstimate(
            timestamp_ns=features.timestamp_ns,
            symbol=features.symbol,
            fair_value=fair_value,
            confidence_score=confidence,
            expected_next_tick_direction=directional_score,
            expected_holding_time_ms=expected_holding_time_ms,
        )


def feature_snapshot_to_array(
    features: MicrostructureFeatureSnapshot,
    fields: tuple[str, ...] = FEATURE_VECTOR_FIELDS,
) -> np.ndarray:
    values: list[float] = []
    for field in fields:
        raw = getattr(features, field, 0.0)
        if isinstance(raw, str):
            mapping = {"tight": -1.0, "normal": 0.0, "wide": 1.0, "low": -1.0, "high": 1.0}
            values.append(mapping.get(raw, 0.0))
        else:
            values.append(float(raw))
    return np.array(values, dtype=float)


def fit_linear_alpha_model(
    samples: list[tuple[MicrostructureFeatureSnapshot, float]],
    *,
    ridge_lambda: float = 1e-3,
    horizon_ns: int = 0,
    symbol_scope: str = "default",
    feature_names: tuple[str, ...] = FEATURE_VECTOR_FIELDS,
) -> LinearAlphaModel:
    if not samples:
        raise ValueError("Cannot fit alpha model without samples.")
    x = np.vstack([feature_snapshot_to_array(features, feature_names) for features, _ in samples])
    y = np.array([float(target) for _, target in samples], dtype=float)
    means = x.mean(axis=0)
    stds = x.std(axis=0)
    standardized = (x - means) / np.where(stds <= 0, 1.0, stds)
    ones = np.ones((standardized.shape[0], 1), dtype=float)
    design = np.hstack([ones, standardized])
    ridge = np.eye(design.shape[1], dtype=float) * float(ridge_lambda)
    ridge[0, 0] = 0.0
    solution = np.linalg.pinv(design.T @ design + ridge) @ design.T @ y
    return LinearAlphaModel(
        feature_names=feature_names,
        coefficients=tuple(float(item) for item in solution[1:]),
        intercept=float(solution[0]),
        feature_means=tuple(float(item) for item in means),
        feature_stds=tuple(float(item) for item in stds),
        ridge_lambda=float(ridge_lambda),
        horizon_ns=int(horizon_ns),
        symbol_scope=symbol_scope,
    )


def evaluate_linear_alpha_model(
    model: LinearAlphaModel,
    samples: list[tuple[MicrostructureFeatureSnapshot, float]],
) -> dict[str, Any]:
    if not samples:
        return {"count": 0, "hit_rate": 0.0, "calibration_error": 0.0, "mse": 0.0}
    predictions = [model.predict_edge(features) for features, _ in samples]
    targets = [float(target) for _, target in samples]
    hits = 0
    calibration_error = 0.0
    mse = 0.0
    for prediction, target in zip(predictions, targets):
        if prediction == 0 or target == 0 or (prediction > 0 and target > 0) or (prediction < 0 and target < 0):
            hits += 1
        calibration_error += abs(prediction - target)
        mse += (prediction - target) ** 2
    count = len(samples)
    return {
        "count": count,
        "hit_rate": hits / count,
        "calibration_error": calibration_error / count,
        "mse": mse / count,
    }
