from __future__ import annotations

from typing import Any

from hft.optimization.types import ObjectiveScore, OptimizationRunConfig


def score_result(
    metrics: dict[str, Any],
    *,
    accuracy_metrics: dict[str, float] | None,
    config: OptimizationRunConfig,
) -> ObjectiveScore:
    weights = config.objective_weights
    net_pnl = float(metrics.get("pnl", 0.0))
    drawdown_penalty = abs(float(metrics.get("max_drawdown", 0.0))) * float(weights.get("drawdown", 0.0))
    adverse_selection_penalty = abs(float(metrics.get("adverse_selection_cost", 0.0))) * float(weights.get("adverse_selection", 0.0))
    inventory_penalty = abs(float(metrics.get("inventory_exposure", 0.0))) * float(weights.get("inventory", 0.0))
    instability_penalty = (
        abs(float(metrics.get("cancel_rate", 0.0))) + abs(float(metrics.get("message_rate", 0.0)) / max(config.message_rate_cap, 1e-9))
    ) * float(weights.get("instability", 0.0))
    breach_penalty = 0.0
    if abs(float(metrics.get("max_drawdown", 0.0))) > config.drawdown_cap:
        breach_penalty += float(weights.get("breach", 0.0))
    if abs(float(metrics.get("adverse_selection_cost", 0.0))) > config.adverse_selection_cap:
        breach_penalty += float(weights.get("breach", 0.0))
    if abs(float(metrics.get("inventory_exposure", 0.0))) > config.inventory_cap:
        breach_penalty += float(weights.get("breach", 0.0))
    if float(metrics.get("message_rate", 0.0)) > config.message_rate_cap:
        breach_penalty += float(weights.get("breach", 0.0))

    accuracy_bonus = 0.0
    if accuracy_metrics:
        hit_rate = float(accuracy_metrics.get("hit_rate", 0.0))
        calibration_error = float(accuracy_metrics.get("calibration_error", 0.0))
        accuracy_bonus = (hit_rate - calibration_error) * float(weights.get("accuracy_bonus", 0.0))

    value = net_pnl - drawdown_penalty - adverse_selection_penalty - inventory_penalty - instability_penalty - breach_penalty + accuracy_bonus
    return ObjectiveScore(
        value=value,
        net_pnl=net_pnl,
        drawdown_penalty=drawdown_penalty,
        adverse_selection_penalty=adverse_selection_penalty,
        inventory_penalty=inventory_penalty,
        instability_penalty=instability_penalty,
        breach_penalty=breach_penalty,
        accuracy_bonus=accuracy_bonus,
    )
