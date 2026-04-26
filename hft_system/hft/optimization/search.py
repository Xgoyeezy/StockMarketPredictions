from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any

from hft.backtest.engine import ReplayBacktestEngine, ReplayRunConfig
from hft.execution.simulator import LatencyProfile
from hft.features.fair_value import (
    FairValueEngine,
    LinearAlphaModel,
    evaluate_linear_alpha_model,
    fit_linear_alpha_model,
)
from hft.features.microstructure import MicrostructureFeatureEngine, MicrostructureFeatureSnapshot
from hft.market_data.schemas import MarketEvent
from hft.optimization.calibration import (
    build_queue_observations,
    calibrate_latency_from_events,
    calibrate_queue_model_from_events,
    evaluate_fill_model,
    latency_artifact_to_profile,
)
from hft.optimization.manifests import build_manifests_from_sessions
from hft.optimization.reports import write_champion_html_report, write_optimization_report
from hft.optimization.scoring import score_result
from hft.optimization.splits import generate_walk_forward_splits
from hft.optimization.types import (
    ChampionSelectionReport,
    MarketMakingParameterSet,
    OptimizationRunConfig,
    SessionManifest,
    WalkForwardSplit,
)
from hft.order_book.book import OrderBook
from hft.risk.limits import HFTLimitConfig
from hft.strategies.market_making import InventoryAwareMarketMakingStrategy
from hft.utils.ids import NamedIdPool


@dataclass
class CandidateEvaluation:
    parameters: MarketMakingParameterSet
    validation_score: float
    holdout_score: float
    holdout_degradation: float
    accepted: bool
    validation_fold_scores: list[float]
    holdout_fold_scores: list[float]
    validation_metrics: list[dict[str, Any]]
    holdout_metrics: list[dict[str, Any]]
    model_accuracy: dict[str, Any]
    chosen_horizon_ns: int


@dataclass
class OptimizationSearchResult:
    run_id: str
    manifests: list[SessionManifest]
    splits: list[WalkForwardSplit]
    calibration: dict[str, Any]
    fill_calibration_summary: dict[str, Any]
    baseline: CandidateEvaluation
    champion: CandidateEvaluation
    champion_report: ChampionSelectionReport
    output_dir: str
    feature_importance: dict[str, Any]
    regime_decomposition: dict[str, Any]
    parameter_sensitivity: dict[str, Any]
    inventory_age_analysis: dict[str, Any]
    edge_decay: dict[str, Any]


def optimize_market_making(
    *,
    session_events: dict[str, list[MarketEvent]],
    base_dir: str | Path,
    config: OptimizationRunConfig,
    risk_limits: HFTLimitConfig,
    fee_model: dict[str, float] | None = None,
) -> OptimizationSearchResult:
    fee_model = dict(fee_model or {})
    manifests = build_manifests_from_sessions(session_events)
    splits = generate_walk_forward_splits(
        manifests,
        train_sessions=config.train_sessions,
        validation_sessions=config.validation_sessions,
        holdout_sessions=config.holdout_sessions,
    )
    optimization_ids = NamedIdPool()
    run_id = optimization_ids.next("optimization")
    output_dir = Path(base_dir) / "replay" / "optimization" / f"run_id={run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    train_validation_session_ids = {
        session_id
        for split in splits
        for session_id in (*split.train_sessions, *split.validation_sessions)
    }
    calibration_events = [event for session_id in train_validation_session_ids for event in session_events[session_id]]
    latency_artifact = calibrate_latency_from_events(calibration_events)
    queue_artifact = calibrate_queue_model_from_events(calibration_events)
    fill_report = evaluate_fill_model(
        build_queue_observations(calibration_events),
        artifact=queue_artifact,
    )
    latency_profile = latency_artifact_to_profile(latency_artifact)

    baseline = _evaluate_candidate(
        parameters=MarketMakingParameterSet(),
        use_trained_model=False,
        session_events=session_events,
        splits=splits,
        base_dir=base_dir,
        search_config=config,
        latency_profile=latency_profile,
        queue_artifact=queue_artifact,
        risk_limits=risk_limits,
        fee_model=fee_model,
    )

    rng = random.Random(config.seed)
    sampled_candidates = _latin_hypercube_candidates(config.parameter_bounds, config.random_candidates, rng)
    candidate_evaluations = [
        _evaluate_candidate(
            parameters=parameters,
            use_trained_model=True,
            session_events=session_events,
            splits=splits,
            base_dir=base_dir,
            search_config=config,
            latency_profile=latency_profile,
            queue_artifact=queue_artifact,
            risk_limits=risk_limits,
            fee_model=fee_model,
        )
        for parameters in sampled_candidates
    ]
    refined = _refine_candidates(
        candidates=candidate_evaluations,
        session_events=session_events,
        splits=splits,
        base_dir=base_dir,
        search_config=config,
        latency_profile=latency_profile,
        queue_artifact=queue_artifact,
        risk_limits=risk_limits,
        fee_model=fee_model,
    )
    candidate_evaluations.extend(refined)
    accepted = [item for item in candidate_evaluations if item.accepted]
    champion = max(accepted or candidate_evaluations, key=lambda item: item.validation_score)
    champion_report = ChampionSelectionReport(
        baseline_validation_score=baseline.validation_score,
        champion_validation_score=champion.validation_score,
        holdout_score=champion.holdout_score,
        holdout_degradation=champion.holdout_degradation,
        accepted=champion.accepted and champion.validation_score > baseline.validation_score,
        reason=(
            "Champion accepted."
            if champion.accepted and champion.validation_score > baseline.validation_score
            else "Champion failed acceptance gates or did not beat baseline."
        ),
        champion_parameters=asdict(champion.parameters),
    )

    feature_importance = _build_feature_importance(champion)
    regime_decomposition = _build_regime_decomposition(manifests, session_events, splits, champion)
    parameter_sensitivity = _build_parameter_sensitivity(candidate_evaluations)
    inventory_age_analysis = _build_inventory_age_analysis(champion)
    edge_decay = _build_edge_decay(champion)
    read_only_bridge = _build_read_only_bridge(champion, champion_report)

    payload = {
        "run_id": run_id,
        "split_manifest": [asdict(split) for split in splits],
        "session_manifest": [asdict(item) for item in manifests],
        "calibration": {
            "latency_artifact": asdict(latency_artifact),
            "queue_artifact": asdict(queue_artifact),
        },
        "fill_calibration_summary": asdict(fill_report),
        "baseline_vs_champion": {
            "baseline": _serialize_candidate(baseline),
            "champion": _serialize_candidate(champion),
            "selection_report": asdict(champion_report),
        },
        "feature_importance": feature_importance,
        "regime_decomposition": regime_decomposition,
        "parameter_sensitivity": parameter_sensitivity,
        "inventory_age_analysis": inventory_age_analysis,
        "edge_decay": edge_decay,
        "read_only_bridge": read_only_bridge,
    }
    write_optimization_report(output_dir / "optimization_summary.json", payload)
    write_optimization_report(output_dir / "split_manifest.json", {"run_id": run_id, "splits": payload["split_manifest"]})
    write_optimization_report(
        output_dir / "calibration_artifacts.json",
        {
            "run_id": run_id,
            "latency_artifact": asdict(latency_artifact),
            "queue_artifact": asdict(queue_artifact),
            "fill_calibration_summary": asdict(fill_report),
        },
    )
    write_optimization_report(
        output_dir / "fold_metrics.json",
        {
            "run_id": run_id,
            "baseline": {
                "validation": baseline.validation_metrics,
                "holdout": baseline.holdout_metrics,
            },
            "champion": {
                "validation": champion.validation_metrics,
                "holdout": champion.holdout_metrics,
            },
        },
    )
    write_optimization_report(
        output_dir / "selected_champion.json",
        {
            "run_id": run_id,
            "champion": _serialize_candidate(champion),
            "selection_report": asdict(champion_report),
        },
    )
    write_optimization_report(
        output_dir / "holdout_report.json",
        {
            "run_id": run_id,
            "holdout_score": champion.holdout_score,
            "holdout_degradation": champion.holdout_degradation,
            "accepted": champion.accepted,
            "holdout_fold_scores": champion.holdout_fold_scores,
            "holdout_metrics": champion.holdout_metrics,
        },
    )
    write_optimization_report(output_dir / "bridge_export.json", read_only_bridge)
    write_champion_html_report(output_dir / "champion_report.html", champion=champion_report, payload=payload)
    (output_dir / "config_candidates.json").write_text(
        json.dumps([_serialize_candidate(item) for item in candidate_evaluations], indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return OptimizationSearchResult(
        run_id=run_id,
        manifests=manifests,
        splits=splits,
        calibration={"latency_artifact": asdict(latency_artifact), "queue_artifact": asdict(queue_artifact)},
        fill_calibration_summary=asdict(fill_report),
        baseline=baseline,
        champion=champion,
        champion_report=champion_report,
        output_dir=str(output_dir),
        feature_importance=feature_importance,
        regime_decomposition=regime_decomposition,
        parameter_sensitivity=parameter_sensitivity,
        inventory_age_analysis=inventory_age_analysis,
        edge_decay=edge_decay,
    )


def _evaluate_candidate(
    *,
    parameters: MarketMakingParameterSet,
    use_trained_model: bool,
    session_events: dict[str, list[MarketEvent]],
    splits: list[WalkForwardSplit],
    base_dir: str | Path,
    search_config: OptimizationRunConfig,
    latency_profile: LatencyProfile,
    queue_artifact,
    risk_limits: HFTLimitConfig,
    fee_model: dict[str, float],
) -> CandidateEvaluation:
    engine = ReplayBacktestEngine(base_dir=base_dir)
    validation_scores: list[float] = []
    holdout_scores: list[float] = []
    validation_metrics: list[dict[str, Any]] = []
    holdout_metrics: list[dict[str, Any]] = []
    best_model_accuracy: dict[str, Any] = {"hit_rate": 0.0, "calibration_error": 1.0}
    chosen_horizon = 0

    for split in splits:
        trained_model, model_accuracy, chosen_horizon = _train_best_alpha_model(
            session_events=session_events,
            train_sessions=split.train_sessions,
            validation_sessions=split.validation_sessions,
            horizons_ns=search_config.horizons_ns,
            ridge_lambda=search_config.ridge_lambda,
        )
        if model_accuracy.get("hit_rate", 0.0) > best_model_accuracy.get("hit_rate", 0.0):
            best_model_accuracy = model_accuracy
        strategy = _build_strategy(parameters, trained_model if use_trained_model else None)
        split_validation_scores: list[float] = []
        for session_id in split.validation_sessions:
            result = engine.run(
                events=session_events[session_id],
                strategy=_build_strategy(parameters, trained_model if use_trained_model else None),
                config=ReplayRunConfig(
                    seed=search_config.seed,
                    symbol=session_events[session_id][0].symbol,
                    strategy_name=strategy.strategy_name,
                    latency_profile=latency_profile,
                    risk_limits=risk_limits,
                    fee_model=fee_model,
                    queue_calibration=queue_artifact,
                ),
            )
            objective = score_result(result.metrics, accuracy_metrics=model_accuracy, config=search_config)
            split_validation_scores.append(objective.value)
            validation_metrics.append({"session_id": session_id, "metrics": result.metrics, "objective": asdict(objective)})
        validation_scores.append(median(split_validation_scores) if split_validation_scores else 0.0)

        holdout_training_sessions = tuple(dict.fromkeys((*split.train_sessions, *split.validation_sessions)))
        holdout_model, holdout_accuracy, _ = _train_best_alpha_model(
            session_events=session_events,
            train_sessions=holdout_training_sessions,
            validation_sessions=split.holdout_sessions,
            horizons_ns=search_config.horizons_ns,
            ridge_lambda=search_config.ridge_lambda,
        )
        split_holdout_scores: list[float] = []
        for session_id in split.holdout_sessions:
            result = engine.run(
                events=session_events[session_id],
                strategy=_build_strategy(parameters, holdout_model if use_trained_model else None),
                config=ReplayRunConfig(
                    seed=search_config.seed,
                    symbol=session_events[session_id][0].symbol,
                    strategy_name=strategy.strategy_name,
                    latency_profile=latency_profile,
                    risk_limits=risk_limits,
                    fee_model=fee_model,
                    queue_calibration=queue_artifact,
                ),
            )
            objective = score_result(result.metrics, accuracy_metrics=holdout_accuracy, config=search_config)
            split_holdout_scores.append(objective.value)
            holdout_metrics.append({"session_id": session_id, "metrics": result.metrics, "objective": asdict(objective)})
        holdout_scores.append(median(split_holdout_scores) if split_holdout_scores else 0.0)

    validation_score = median(validation_scores) if validation_scores else 0.0
    holdout_score = median(holdout_scores) if holdout_scores else 0.0
    holdout_degradation = (
        max(validation_score - holdout_score, 0.0) / max(abs(validation_score), 1.0)
        if validation_score
        else 0.0
    )
    accepted = (
        holdout_degradation <= search_config.holdout_degradation_cap
        and _metrics_within_caps(validation_metrics + holdout_metrics, search_config)
    )
    return CandidateEvaluation(
        parameters=parameters,
        validation_score=validation_score,
        holdout_score=holdout_score,
        holdout_degradation=holdout_degradation,
        accepted=accepted,
        validation_fold_scores=validation_scores,
        holdout_fold_scores=holdout_scores,
        validation_metrics=validation_metrics,
        holdout_metrics=holdout_metrics,
        model_accuracy=best_model_accuracy,
        chosen_horizon_ns=chosen_horizon,
    )


def _build_strategy(parameters: MarketMakingParameterSet, model: LinearAlphaModel | None) -> InventoryAwareMarketMakingStrategy:
    fair_value_engine = FairValueEngine(model=model, fair_value_sensitivity=parameters.fair_value_sensitivity)
    return InventoryAwareMarketMakingStrategy(parameter_set=parameters, fair_value_engine=fair_value_engine)


def _train_best_alpha_model(
    *,
    session_events: dict[str, list[MarketEvent]],
    train_sessions: tuple[str, ...],
    validation_sessions: tuple[str, ...],
    horizons_ns: tuple[int, ...],
    ridge_lambda: float,
) -> tuple[LinearAlphaModel | None, dict[str, Any], int]:
    best_model: LinearAlphaModel | None = None
    best_metrics = {"hit_rate": 0.0, "calibration_error": 1e9}
    best_horizon = 0
    symbol_scope = session_events[train_sessions[0]][0].symbol if train_sessions else "default"
    for horizon in horizons_ns:
        train_samples = _build_alpha_training_samples(
            [event for session_id in train_sessions for event in session_events[session_id]],
            horizon_ns=horizon,
        )
        validation_samples = _build_alpha_training_samples(
            [event for session_id in validation_sessions for event in session_events[session_id]],
            horizon_ns=horizon,
        )
        if len(train_samples) < 5 or len(validation_samples) < 3:
            continue
        model = fit_linear_alpha_model(
            train_samples,
            ridge_lambda=ridge_lambda,
            horizon_ns=horizon,
            symbol_scope=symbol_scope,
        )
        metrics = evaluate_linear_alpha_model(model, validation_samples)
        quality = metrics["hit_rate"] - metrics["calibration_error"]
        best_quality = best_metrics["hit_rate"] - best_metrics["calibration_error"]
        if quality > best_quality:
            best_model = model
            best_metrics = metrics
            best_horizon = horizon
    return best_model, best_metrics, best_horizon


def _build_alpha_training_samples(events: list[MarketEvent], *, horizon_ns: int) -> list[tuple[MicrostructureFeatureSnapshot, float]]:
    if not events:
        return []
    ordered = sorted(events)
    book = OrderBook(ordered[0].symbol)
    feature_engine = MicrostructureFeatureEngine()
    snapshots: list[MicrostructureFeatureSnapshot] = []
    for event in ordered:
        book.apply_event(event)
        snapshot = book.snapshot()
        if snapshot.mid_price <= 0:
            continue
        snapshots.append(feature_engine.update(book_state=snapshot, event=event))

    samples: list[tuple[MicrostructureFeatureSnapshot, float]] = []
    for index, features in enumerate(snapshots):
        forward_index = index + 1
        while forward_index < len(snapshots) and snapshots[forward_index].timestamp_ns < features.timestamp_ns + horizon_ns:
            forward_index += 1
        if forward_index >= len(snapshots):
            break
        target = snapshots[forward_index].mid_price - features.mid_price
        samples.append((features, target))
    return samples


def _latin_hypercube_candidates(
    bounds: dict[str, tuple[float, float]],
    count: int,
    rng: random.Random,
) -> list[MarketMakingParameterSet]:
    keys = tuple(bounds.keys())
    buckets: dict[str, list[float]] = {}
    for key, (lower, upper) in bounds.items():
        width = upper - lower
        values = [lower + (((index + rng.random()) / count) * width) for index in range(count)]
        rng.shuffle(values)
        buckets[key] = values
    candidates: list[MarketMakingParameterSet] = []
    for index in range(count):
        params = {key: _coerce_parameter_value(key, buckets[key][index]) for key in keys}
        candidates.append(MarketMakingParameterSet(**params))
    return candidates


def _refine_candidates(
    *,
    candidates: list[CandidateEvaluation],
    session_events: dict[str, list[MarketEvent]],
    splits: list[WalkForwardSplit],
    base_dir: str | Path,
    search_config: OptimizationRunConfig,
    latency_profile: LatencyProfile,
    queue_artifact,
    risk_limits: HFTLimitConfig,
    fee_model: dict[str, float],
) -> list[CandidateEvaluation]:
    rng = random.Random(search_config.seed + 100)
    top = sorted(candidates, key=lambda item: item.validation_score, reverse=True)[: search_config.top_candidates]
    evaluations: list[CandidateEvaluation] = []
    for round_index in range(search_config.refinement_rounds):
        radius_scale = search_config.refinement_radius / max(round_index + 1, 1)
        for candidate in top:
            for key, (lower, upper) in search_config.parameter_bounds.items():
                current_value = float(getattr(candidate.parameters, key))
                span = upper - lower
                for direction in (-1.0, 1.0):
                    proposed = current_value + (direction * span * radius_scale * (0.5 + rng.random() * 0.5))
                    params = asdict(candidate.parameters)
                    params[key] = _coerce_parameter_value(key, max(lower, min(upper, proposed)))
                    evaluation = _evaluate_candidate(
                        parameters=MarketMakingParameterSet(**params),
                        use_trained_model=True,
                        session_events=session_events,
                        splits=splits,
                        base_dir=base_dir,
                        search_config=search_config,
                        latency_profile=latency_profile,
                        queue_artifact=queue_artifact,
                        risk_limits=risk_limits,
                        fee_model=fee_model,
                    )
                    evaluations.append(evaluation)
    return evaluations


def _coerce_parameter_value(key: str, value: float) -> Any:
    if key.endswith("_ns"):
        return int(round(value))
    return float(value)


def _metrics_within_caps(metric_rows: list[dict[str, Any]], config: OptimizationRunConfig) -> bool:
    for row in metric_rows:
        metrics = row["metrics"]
        if abs(float(metrics.get("max_drawdown", 0.0))) > config.drawdown_cap:
            return False
        if abs(float(metrics.get("adverse_selection_cost", 0.0))) > config.adverse_selection_cap:
            return False
        if abs(float(metrics.get("inventory_exposure", 0.0))) > config.inventory_cap:
            return False
        if float(metrics.get("message_rate", 0.0)) > config.message_rate_cap:
            return False
    return True


def _serialize_candidate(candidate: CandidateEvaluation) -> dict[str, Any]:
    return {
        "parameters": asdict(candidate.parameters),
        "validation_score": candidate.validation_score,
        "holdout_score": candidate.holdout_score,
        "holdout_degradation": candidate.holdout_degradation,
        "accepted": candidate.accepted,
        "validation_fold_scores": candidate.validation_fold_scores,
        "holdout_fold_scores": candidate.holdout_fold_scores,
        "model_accuracy": candidate.model_accuracy,
        "chosen_horizon_ns": candidate.chosen_horizon_ns,
    }


def _build_feature_importance(candidate: CandidateEvaluation) -> dict[str, Any]:
    return {
        "chosen_horizon_ns": candidate.chosen_horizon_ns,
        "model_accuracy": candidate.model_accuracy,
    }


def _build_regime_decomposition(
    manifests: list[SessionManifest],
    session_events: dict[str, list[MarketEvent]],
    splits: list[WalkForwardSplit],
    champion: CandidateEvaluation,
) -> dict[str, Any]:
    per_regime: dict[str, list[float]] = {}
    for row in champion.validation_metrics:
        session_id = row["session_id"]
        manifest = next(item for item in manifests if item.session_id == session_id)
        regime_key = f"{manifest.volatility_regime}/{manifest.liquidity_regime}"
        per_regime.setdefault(regime_key, []).append(float(row["objective"]["value"]))
    return {key: {"count": len(values), "median_score": median(values)} for key, values in per_regime.items()}


def _build_parameter_sensitivity(candidates: list[CandidateEvaluation]) -> dict[str, Any]:
    best_by_param: dict[str, dict[str, float]] = {}
    for candidate in candidates:
        for key, value in asdict(candidate.parameters).items():
            record = best_by_param.setdefault(key, {"min": float(value), "max": float(value), "best_score": candidate.validation_score})
            record["min"] = min(record["min"], float(value))
            record["max"] = max(record["max"], float(value))
            record["best_score"] = max(record["best_score"], candidate.validation_score)
    return best_by_param


def _build_inventory_age_analysis(candidate: CandidateEvaluation) -> dict[str, Any]:
    holding_times = [float(row["metrics"].get("holding_time_ns", 0.0)) for row in candidate.validation_metrics]
    return {
        "median_holding_time_ns": median(holding_times) if holding_times else 0.0,
        "max_holding_time_ns": max(holding_times) if holding_times else 0.0,
    }


def _build_edge_decay(candidate: CandidateEvaluation) -> dict[str, Any]:
    scores = candidate.validation_fold_scores
    if len(scores) < 2:
        return {"slope": 0.0, "scores": scores}
    slope = scores[-1] - scores[0]
    return {"slope": slope, "scores": scores}


def _build_read_only_bridge(
    candidate: CandidateEvaluation,
    champion_report: ChampionSelectionReport,
) -> dict[str, Any]:
    holdout_metrics = [row["metrics"] for row in candidate.holdout_metrics] or [row["metrics"] for row in candidate.validation_metrics]
    pnl_values = [float(metrics.get("pnl", 0.0)) for metrics in holdout_metrics]
    realized_values = [float(metrics.get("realized_pnl", 0.0)) for metrics in holdout_metrics]
    unrealized_values = [float(metrics.get("unrealized_pnl", 0.0)) for metrics in holdout_metrics]
    inventory_values = [float(metrics.get("inventory_exposure", 0.0)) for metrics in holdout_metrics]
    drawdown_values = [float(metrics.get("max_drawdown", 0.0)) for metrics in holdout_metrics]
    adverse_values = [float(metrics.get("adverse_selection_cost", 0.0)) for metrics in holdout_metrics]
    message_rates = [float(metrics.get("message_rate", 0.0)) for metrics in holdout_metrics]
    fill_rates = [float(metrics.get("fill_rate", 0.0)) for metrics in holdout_metrics]
    holding_times = [float(metrics.get("holding_time_ns", 0.0)) for metrics in holdout_metrics]

    return {
        "simulated_pnl": {
            "median_pnl": median(pnl_values) if pnl_values else 0.0,
            "median_realized_pnl": median(realized_values) if realized_values else 0.0,
            "median_unrealized_pnl": median(unrealized_values) if unrealized_values else 0.0,
            "validation_objective": candidate.validation_score,
            "holdout_objective": candidate.holdout_score,
        },
        "inventory_summary": {
            "median_inventory_exposure": median(inventory_values) if inventory_values else 0.0,
            "max_inventory_exposure": max(inventory_values) if inventory_values else 0.0,
            "median_holding_time_ns": median(holding_times) if holding_times else 0.0,
        },
        "risk_summary": {
            "max_drawdown": max(drawdown_values) if drawdown_values else 0.0,
            "median_adverse_selection_cost": median(adverse_values) if adverse_values else 0.0,
            "max_message_rate": max(message_rates) if message_rates else 0.0,
            "median_fill_rate": median(fill_rates) if fill_rates else 0.0,
        },
        "strategy_health_metrics": {
            "accepted": champion_report.accepted,
            "holdout_degradation": candidate.holdout_degradation,
            "chosen_horizon_ns": candidate.chosen_horizon_ns,
            "model_accuracy": candidate.model_accuracy,
            "champion_parameters": asdict(candidate.parameters),
        },
    }
