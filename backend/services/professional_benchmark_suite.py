from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median, pstdev
from typing import Any, Iterable

from backend.services.evidence_reward_engine import get_evidence_reward_summary
from backend.services.forecast_validation_engine import get_forecast_validation_summary
from backend.services.project_finish_tracker import build_project_finish_tracker
from backend.services.serialization import serialize_value

SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "paper_route_only": True,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
    "mutation": "none",
}

SAFETY_NOTES: tuple[str, ...] = (
    "Research only. Does not affect trading.",
    "Does not place orders.",
    "Does not change broker routes.",
    "Does not bypass risk gates.",
    "Does not change ranking weights automatically.",
    "Does not grant AI order authority.",
)

MIN_EDGE_SAMPLE_SIZE = 5
FALSE_BLOCK_RETURN_THRESHOLD_PCT = 0.25
EDGE_THRESHOLD_PCT = 0.10
MIN_DATA_QUALITY_SCORE = 50.0

BASELINE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "spy_drift",
        "label": "SPY drift",
        "fields": ("spy_forward_return", "spy_drift", "baseline_spy_return"),
        "required": ("spy_forward_return",),
    },
    {
        "key": "qqq_drift",
        "label": "QQQ drift",
        "fields": ("qqq_forward_return", "qqq_drift", "baseline_qqq_return"),
        "required": ("qqq_forward_return",),
    },
    {
        "key": "sector_etf_drift",
        "label": "Sector ETF drift",
        "fields": ("sector_etf_forward_return", "sector_baseline_forward_return", "baseline_sector_return"),
        "required": ("sector_etf_forward_return",),
    },
    {
        "key": "random_candidate",
        "label": "Random candidate",
        "fields": ("random_candidate_forward_return", "baseline_forward_return"),
        "required": ("baseline_forward_return",),
    },
    {
        "key": "simple_momentum",
        "label": "Simple momentum",
        "fields": ("simple_momentum_forward_return", "momentum_baseline_forward_return"),
        "required": ("simple_momentum_forward_return",),
    },
    {
        "key": "simple_mean_reversion",
        "label": "Simple mean reversion",
        "fields": ("simple_mean_reversion_forward_return", "mean_reversion_baseline_forward_return"),
        "required": ("simple_mean_reversion_forward_return",),
    },
    {
        "key": "simple_vwap_reclaim",
        "label": "Simple VWAP reclaim",
        "fields": ("simple_vwap_reclaim_forward_return", "vwap_reclaim_baseline_forward_return"),
        "required": ("simple_vwap_reclaim_forward_return",),
    },
    {
        "key": "opening_range_breakout",
        "label": "Opening range breakout",
        "fields": ("opening_range_breakout_forward_return", "orb_baseline_forward_return"),
        "required": ("opening_range_breakout_forward_return",),
    },
    {
        "key": "previous_close_drift",
        "label": "Previous close drift",
        "fields": ("previous_close_forward_return", "previous_close_drift", "baseline_previous_close_return"),
        "required": ("previous_close_forward_return",),
    },
)

BENCHMARK_PROOF_REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "key": "sample_size",
        "label": "Rewardable sample size",
        "metric": "rewardable_count",
        "threshold": MIN_EDGE_SAMPLE_SIZE,
        "comparison": "greater_or_equal",
        "safe_next_action": "Collect more rewardable forward-outcome rows before treating benchmark results as reviewable.",
    },
    {
        "key": "explicit_baselines",
        "label": "Explicit baselines available",
        "metric": "available_baseline_count",
        "threshold": 1,
        "comparison": "greater_or_equal",
        "safe_next_action": "Attach at least one same-window forward-only baseline before edge review.",
    },
    {
        "key": "baseline_relative_edge",
        "label": "Baseline-relative edge",
        "metric": "baseline_relative_edge",
        "threshold": EDGE_THRESHOLD_PCT,
        "comparison": "greater_or_equal",
        "safe_next_action": "Improve data quality or candidate scoring until system forward returns beat explicit baselines by the v1 threshold.",
    },
    {
        "key": "score_bucket_lift",
        "label": "Score bucket lift",
        "metric": "score_bucket_lift",
        "threshold": 0.0,
        "comparison": "greater_than",
        "safe_next_action": "Verify that higher score buckets outperform lower score buckets before score quality claims.",
    },
    {
        "key": "after_cost_reward",
        "label": "After-cost reward",
        "metric": "slippage_adjusted_reward",
        "threshold": 0.0,
        "comparison": "greater_than",
        "safe_next_action": "Add slippage and spread evidence and verify reward remains positive after paper execution costs.",
    },
    {
        "key": "data_quality",
        "label": "Data quality floor",
        "metric": "data_quality_score",
        "threshold": MIN_DATA_QUALITY_SCORE,
        "comparison": "greater_or_equal",
        "safe_next_action": "Fix missing rewardable outcome fields before using benchmark results as proof.",
    },
)

BENCHMARK_HARDENING_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "rewardable_sample_quality",
        "title": "Rewardable sample and data quality",
        "priority": "critical",
        "proof_keys": ("sample_size", "data_quality"),
        "blocked_claims": ("benchmark_edge_review", "score_quality", "promotion_readiness"),
        "safe_next_action": "Collect rewardable candidate rows with closed-horizon outcomes, total reward, baseline fields, timestamps, horizons, setup, engine, and regime context.",
        "done_when": "Rewardable sample size and data quality both pass the benchmark proof gate.",
    },
    {
        "key": "same_window_baselines",
        "title": "Same-window explicit baselines",
        "priority": "critical",
        "proof_keys": ("explicit_baselines",),
        "blocked_claims": ("baseline_relative_edge", "benchmark_edge_review"),
        "safe_next_action": "Attach forward-only SPY, QQQ, sector, random-candidate, and simple-strategy baseline returns for the same timestamp and horizon.",
        "done_when": "At least one explicit same-window baseline is available and every baseline comparison reports its source field.",
    },
    {
        "key": "baseline_relative_edge",
        "title": "Baseline-relative edge",
        "priority": "high",
        "proof_keys": ("baseline_relative_edge",),
        "blocked_claims": ("benchmark_edge_review", "small_fund_research_claim"),
        "safe_next_action": "Only evaluate edge after rewardable system returns and explicit baselines exist for the same forward window.",
        "done_when": "System forward returns beat explicit baselines by the v1 edge threshold.",
    },
    {
        "key": "score_bucket_lift",
        "title": "Score bucket lift",
        "priority": "high",
        "proof_keys": ("score_bucket_lift",),
        "blocked_claims": ("ranking_quality_claim", "score_quality"),
        "safe_next_action": "Collect rewardable high-score and low-score rows before claiming the score separates outcomes.",
        "done_when": "High score buckets beat low score buckets on rewardable, forward-only outcomes.",
    },
    {
        "key": "after_cost_reward",
        "title": "After-cost reward",
        "priority": "high",
        "proof_keys": ("after_cost_reward",),
        "blocked_claims": ("tradability_claim", "execution_adjusted_edge"),
        "safe_next_action": "Attach paper spread, slippage, fill delay, route, and fill-price evidence before treating reward as cost-adjusted.",
        "done_when": "Reward remains positive after explicit paper execution costs.",
    },
    {
        "key": "out_of_sample_split",
        "title": "Out-of-sample split and frozen versions",
        "priority": "high",
        "proof_keys": (),
        "blocked_claims": ("repeatability_claim", "walk_forward_claim"),
        "safe_next_action": "Create frozen walk-forward experiments with sample splits, experiment versions, reward formula versions, and data filters before repeatability language.",
        "done_when": "Out-of-sample stability is available from frozen walk-forward evidence.",
    },
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


def _mean(values: Iterable[Any]) -> float | None:
    numbers = [_safe_float(value) for value in values]
    clean = [float(value) for value in numbers if value is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 6)


def _median(values: Iterable[Any]) -> float | None:
    numbers = [_safe_float(value) for value in values]
    clean = [float(value) for value in numbers if value is not None]
    if not clean:
        return None
    return round(float(median(clean)), 6)


def _dispersion(values: Iterable[Any]) -> float | None:
    numbers = [_safe_float(value) for value in values]
    clean = [float(value) for value in numbers if value is not None]
    if len(clean) < 2:
        return 0.0 if clean else None
    return round(float(pstdev(clean)), 6)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _first_number(row: dict[str, Any], fields: Iterable[str]) -> float | None:
    for field in fields:
        value = _safe_float(row.get(field))
        if value is not None:
            return value
    contract = row.get("prediction_contract") if isinstance(row.get("prediction_contract"), dict) else {}
    for field in fields:
        value = _safe_float(contract.get(field))
        if value is not None:
            return value
    return None


def _first_text(row: dict[str, Any], fields: Iterable[str], fallback: str = "unknown") -> str:
    for field in fields:
        value = row.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    contract = row.get("prediction_contract") if isinstance(row.get("prediction_contract"), dict) else {}
    for field in fields:
        value = contract.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return fallback


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple) or isinstance(value, set):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


def _score_bucket_from_score(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 90:
        return "90_100"
    if score >= 80:
        return "80_89"
    if score >= 60:
        return "60_79"
    if score >= 40:
        return "40_59"
    return "0_39"


def _normalize_benchmark_record(row: dict[str, Any]) -> dict[str, Any]:
    contract = row.get("prediction_contract") if isinstance(row.get("prediction_contract"), dict) else {}
    reward_components = row.get("reward_components") if isinstance(row.get("reward_components"), dict) else {}
    component_scores = row.get("component_scores") if isinstance(row.get("component_scores"), dict) else {}
    total_reward = _safe_float(row.get("total_reward"))
    if total_reward is None:
        total_reward = _safe_float(reward_components.get("total_reward"))
    if total_reward is None:
        total_reward = _safe_float(component_scores.get("total_reward"))

    actual_forward_return = _first_number(row, ("actual_forward_return", "forward_return", "actual_return_pct", "missed_move_magnitude"))
    baseline_forward_return = _first_number(row, ("baseline_forward_return", "baseline_return", "random_candidate_forward_return"))
    score = _safe_float(row.get("score"))
    score_bucket = str(row.get("score_bucket") or _score_bucket_from_score(score))
    slippage_bps = _first_number(row, ("slippage_bps", "slippage_estimate_bps", "slippage_estimate"))
    spread_bps = _first_number(row, ("spread_bps", "spread_cost_bps"))
    rewardable = bool(row.get("rewardable")) and total_reward is not None
    if row.get("prediction_contract_status") == "rewardable" and total_reward is not None:
        rewardable = True

    missing_fields = set(_listify(row.get("missing_fields")))
    if total_reward is None:
        missing_fields.add("total_reward")
    if actual_forward_return is None:
        missing_fields.add("actual_forward_return")
    if baseline_forward_return is None:
        missing_fields.add("baseline_forward_return")

    return {
        **row,
        "record_id": _first_text(row, ("record_id", "candidate_lifecycle_id", "prediction_id", "symbol"), "unknown"),
        "symbol": _first_text(row, ("symbol",), "unknown"),
        "timestamp": _first_text(row, ("prediction_created_at", "timestamp", "created_at"), ""),
        "engine": _first_text(row, ("engine", "source", "model_name"), "unknown"),
        "setup_type": _first_text(row, ("setup_type", "opportunity_type"), "unknown"),
        "regime": _first_text(row, ("regime", "market_regime"), "unknown"),
        "score": score,
        "score_bucket": score_bucket,
        "blockers": _listify(row.get("blockers") or row.get("blocker")),
        "ai_verdict": _first_text(row, ("ai_verdict",), ""),
        "rewardable": rewardable,
        "total_reward": total_reward,
        "actual_forward_return": actual_forward_return,
        "baseline_forward_return": baseline_forward_return,
        "slippage_bps": slippage_bps,
        "spread_bps": spread_bps,
        "confidence": _first_number(row, ("confidence",)),
        "trade_executed": bool(row.get("trade_executed")),
        "blocked": bool(row.get("blocked")),
        "allowed": bool(row.get("allowed")),
        "missed_move_outcome": row.get("missed_move_outcome"),
        "missing_fields": sorted(missing_fields),
    }


def _normalize_records(records: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [_normalize_benchmark_record(row) for row in records or [] if isinstance(row, dict)]


def _rewardable(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in records if row.get("rewardable") and _safe_float(row.get("total_reward")) is not None]


def compute_core_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    rewardable = _rewardable(records)
    rewards = [_safe_float(row.get("total_reward")) for row in rewardable]
    actual_returns = [_safe_float(row.get("actual_forward_return")) for row in rewardable]
    positive_rewards = [float(value) for value in rewards if value is not None and value > 0]
    negative_rewards = [float(value) for value in rewards if value is not None and value < 0]
    slippage_adjusted = []
    spread_costs = []
    for row in rewardable:
        reward = _safe_float(row.get("total_reward"))
        slippage = _safe_float(row.get("slippage_bps"))
        spread = _safe_float(row.get("spread_bps"))
        if reward is not None and slippage is not None:
            slippage_adjusted.append(reward - abs(slippage) / 100.0)
        if spread is not None:
            spread_costs.append(spread / 100.0)
    profit_factor = None
    if negative_rewards:
        profit_factor = round(sum(positive_rewards) / abs(sum(negative_rewards)), 6) if positive_rewards else 0.0
    elif positive_rewards:
        profit_factor = None
    return {
        "hit_rate": _ratio(sum(1 for value in actual_returns if value is not None and value > 0), len([value for value in actual_returns if value is not None])),
        "expected_value": _mean(actual_returns),
        "average_reward": _mean(rewards),
        "median_reward": _median(rewards),
        "reward_dispersion": _dispersion(rewards),
        "max_drawdown": compute_max_drawdown([float(value) for value in rewards if value is not None]),
        "profit_factor": profit_factor,
        "slippage_adjusted_reward": _mean(slippage_adjusted),
        "spread_cost": _mean(spread_costs),
        "turnover": _mean([row.get("turnover") for row in rewardable]),
        "confidence_calibration": compute_confidence_calibration(rewardable),
        "regime_stability": compute_regime_stability(rewardable),
    }


def compute_max_drawdown(rewards: list[float]) -> float | None:
    if not rewards:
        return None
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for reward in rewards:
        equity += reward
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return round(abs(max_drawdown), 6)


def compute_confidence_calibration(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for row in records:
        confidence = _safe_float(row.get("confidence"))
        actual = _safe_float(row.get("actual_forward_return"))
        if confidence is None or actual is None:
            continue
        confidence = max(0.0, min(1.0, confidence))
        outcome = 1.0 if actual > 0 else 0.0
        rows.append(abs(confidence - outcome))
    return {
        "available": bool(rows),
        "mean_absolute_error": _mean(rows),
        "sample_size": len(rows),
        "missing_fields": [] if rows else ["confidence", "actual_forward_return"],
    }


def compute_regime_stability(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in records:
        reward = _safe_float(row.get("total_reward"))
        if reward is not None:
            grouped[str(row.get("regime") or "unknown")].append(reward)
    regime_avgs = [_mean(values) for values in grouped.values() if values]
    clean = [float(value) for value in regime_avgs if value is not None]
    dispersion = _dispersion(clean)
    return {
        "available": len(clean) >= 2,
        "regime_count": len(clean),
        "average_reward_dispersion": dispersion,
        "stability_score": round(max(0.0, 1.0 - min(float(dispersion or 0.0), 2.0) / 2.0), 6) if clean else None,
        "missing_fields": [] if len(clean) >= 2 else ["regime"],
    }


def aggregate_by_key(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[str(row.get(key) or "unknown")].append(row)
    rows = []
    for label, items in grouped.items():
        rewardable = _rewardable(items)
        rewards = [row.get("total_reward") for row in rewardable]
        actuals = [row.get("actual_forward_return") for row in rewardable]
        rows.append(
            {
                key: label,
                "candidate_count": len(items),
                "rewardable_count": len(rewardable),
                "avg_reward": _mean(rewards),
                "median_reward": _median(rewards),
                "reward_dispersion": _dispersion(rewards),
                "win_rate": _ratio(sum(1 for value in actuals if _safe_float(value) is not None and float(value) > 0), len([value for value in actuals if _safe_float(value) is not None])),
            }
        )
    return sorted(rows, key=lambda row: (row["avg_reward"] is None, -(row["avg_reward"] or -9999), -row["candidate_count"]))


def compute_score_bucket_separation(records: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = aggregate_by_key(records, "score_bucket")
    high = [row for row in buckets if row["score_bucket"] in {"90_100", "80_89"} and row.get("avg_reward") is not None]
    low = [row for row in buckets if row["score_bucket"] in {"40_59", "0_39"} and row.get("avg_reward") is not None]
    high_avg = _mean([row["avg_reward"] for row in high])
    low_avg = _mean([row["avg_reward"] for row in low])
    lift = round(high_avg - low_avg, 6) if high_avg is not None and low_avg is not None else None
    return {
        "available": lift is not None,
        "items": buckets,
        "score_bucket_lift": lift,
        "missing_fields": [] if lift is not None else ["score_bucket", "total_reward"],
        "reason": "Score bucket lift compares average reward in 80+ buckets against 0-59 buckets." if lift is not None else "Need rewardable high- and low-score bucket rows.",
    }


def compute_blocker_value(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        for blocker in row.get("blockers") or []:
            grouped[str(blocker)].append(row)
    items = []
    for blocker, rows in grouped.items():
        blocked_rows = [row for row in rows if row.get("blocked") or row.get("blockers")]
        outcomes = [_safe_float(row.get("actual_forward_return")) for row in blocked_rows]
        observed = [float(value) for value in outcomes if value is not None]
        false_blocks = [value for value in observed if value >= FALSE_BLOCK_RETURN_THRESHOLD_PCT]
        avg_forward = _mean(observed)
        items.append(
            {
                "blocker": blocker,
                "times_seen": len(rows),
                "times_blocked": len(blocked_rows),
                "observed_outcome_count": len(observed),
                "average_forward_return_after_block": avg_forward,
                "estimated_blocker_value": round(-avg_forward, 6) if avg_forward is not None else None,
                "false_block_rate": _ratio(len(false_blocks), len(observed)),
                "avg_reward_when_blocked": _mean([row.get("total_reward") for row in blocked_rows if row.get("rewardable")]),
            }
        )
    return {
        "available": bool(items),
        "items": sorted(items, key=lambda row: (row["estimated_blocker_value"] is None, -(row["estimated_blocker_value"] or -9999), -row["times_seen"])),
        "missing_fields": [] if items else ["blockers"],
    }


def compute_ai_verdict_accuracy(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in records if row.get("ai_verdict")]
    by_verdict = aggregate_by_key(rows, "ai_verdict") if rows else []
    approvals = [row for row in rows if str(row.get("ai_verdict")).lower() in {"approve", "approved", "approve_evidence"}]
    rejects = [row for row in rows if str(row.get("ai_verdict")).lower() in {"reject", "rejected", "reject_evidence", "wait_for_confirmation", "size_down"}]
    false_positives = [row for row in approvals if (_safe_float(row.get("actual_forward_return")) or 0.0) <= -FALSE_BLOCK_RETURN_THRESHOLD_PCT]
    false_negatives = [row for row in rejects if (_safe_float(row.get("actual_forward_return")) or 0.0) >= FALSE_BLOCK_RETURN_THRESHOLD_PCT]
    return {
        "available": bool(rows),
        "items": by_verdict,
        "verdict_count": len(rows),
        "false_positive_rate": _ratio(len(false_positives), len(approvals)),
        "false_negative_rate": _ratio(len(false_negatives), len(rejects)),
        "missing_fields": [] if rows else ["ai_verdict"],
    }


def compute_missed_move_recovery(records: list[dict[str, Any]]) -> dict[str, Any]:
    missed = [
        row
        for row in records
        if row.get("missed_move_outcome")
        or (row.get("blocked") and (_safe_float(row.get("actual_forward_return")) or 0.0) >= FALSE_BLOCK_RETURN_THRESHOLD_PCT)
    ]
    return {
        "available": bool(missed),
        "missed_move_count": len(missed),
        "avg_missed_forward_return": _mean([row.get("actual_forward_return") for row in missed]),
        "items": missed[:25],
        "missing_fields": [] if missed else ["missed_move_outcome"],
    }


def compute_execution_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    rewardable = _rewardable(records)
    rows = [row for row in rewardable if _safe_float(row.get("slippage_bps")) is not None or _safe_float(row.get("spread_bps")) is not None]
    slippage_adjusted = []
    for row in rows:
        reward = _safe_float(row.get("total_reward"))
        slippage = _safe_float(row.get("slippage_bps")) or 0.0
        spread = _safe_float(row.get("spread_bps")) or 0.0
        if reward is not None:
            slippage_adjusted.append(reward - abs(slippage) / 100.0 - max(0.0, spread) / 100.0)
    return {
        "available": bool(rows),
        "sample_size": len(rows),
        "avg_slippage_bps": _mean([row.get("slippage_bps") for row in rows]),
        "avg_spread_bps": _mean([row.get("spread_bps") for row in rows]),
        "slippage_adjusted_reward": _mean(slippage_adjusted),
        "spread_cost": _mean([(_safe_float(row.get("spread_bps")) or 0.0) / 100.0 for row in rows if _safe_float(row.get("spread_bps")) is not None]),
        "missing_fields": [] if rows else ["slippage_bps", "spread_bps"],
    }


def _normalize_forecast_record(row: dict[str, Any]) -> dict[str, Any]:
    evaluation = row.get("evaluation") if isinstance(row.get("evaluation"), dict) else row
    source = row.get("forecast") if isinstance(row.get("forecast"), dict) else row
    return {
        **evaluation,
        "prediction_id": evaluation.get("prediction_id") or source.get("prediction_id"),
        "symbol": evaluation.get("symbol") or source.get("symbol"),
        "model_name": evaluation.get("model_name") or source.get("model_name") or source.get("source"),
        "direction_accuracy": _safe_float(evaluation.get("direction_accuracy")),
        "path_mae": _safe_float(evaluation.get("path_mae")),
        "path_rmse": _safe_float(evaluation.get("path_rmse")),
        "timing_error": _safe_float(evaluation.get("timing_error")),
        "confidence_calibration": _safe_float(evaluation.get("confidence_calibration")),
        "forecast_total_reward": _safe_float(evaluation.get("forecast_total_reward")),
        "rewardable": bool(evaluation.get("rewardable")) and _safe_float(evaluation.get("forecast_total_reward")) is not None,
        "missing_fields": _listify(evaluation.get("missing_fields") or row.get("missing_fields")),
    }


def compute_forecast_accuracy(forecast_records: Iterable[dict[str, Any]] | None) -> dict[str, Any]:
    rows = [_normalize_forecast_record(row) for row in forecast_records or [] if isinstance(row, dict)]
    evaluated = [row for row in rows if row.get("rewardable")]
    return {
        "available": bool(evaluated),
        "total_forecasts": len(rows),
        "validated_forecasts": len(evaluated),
        "direction_accuracy": _mean([row.get("direction_accuracy") for row in evaluated]),
        "avg_path_mae": _mean([row.get("path_mae") for row in evaluated]),
        "avg_path_rmse": _mean([row.get("path_rmse") for row in evaluated]),
        "avg_timing_error": _mean([row.get("timing_error") for row in evaluated]),
        "avg_forecast_reward": _mean([row.get("forecast_total_reward") for row in evaluated]),
        "confidence_calibration": _mean([row.get("confidence_calibration") for row in evaluated]),
        "items": rows[:25],
        "missing_fields": [] if evaluated else ["forecast_total_reward", "actual_series"],
    }


def compute_baseline_comparison(records: list[dict[str, Any]]) -> dict[str, Any]:
    rewardable = _rewardable(records)
    system_returns = [row.get("actual_forward_return") for row in rewardable]
    system_expected_value = _mean(system_returns)
    baselines = []
    available_edges = []
    for definition in BASELINE_DEFINITIONS:
        baseline_values = []
        used_field = None
        for row in rewardable:
            value = _first_number(row, definition["fields"])
            if value is not None:
                baseline_values.append(value)
                if used_field is None:
                    used_field = next((field for field in definition["fields"] if _first_number(row, (field,)) is not None), None)
        avg_baseline = _mean(baseline_values)
        edge = round(system_expected_value - avg_baseline, 6) if system_expected_value is not None and avg_baseline is not None else None
        if edge is not None:
            available_edges.append(edge)
        baselines.append(
            {
                "key": definition["key"],
                "label": definition["label"],
                "available": avg_baseline is not None,
                "sample_size": len(baseline_values),
                "system_expected_value": system_expected_value,
                "baseline_expected_value": avg_baseline,
                "baseline_relative_edge": edge,
                "beat_baseline": edge is not None and edge > 0,
                "source_field": used_field,
                "missing_fields": [] if avg_baseline is not None else list(definition["required"]),
                "reason": "Compared against explicit baseline return fields." if avg_baseline is not None else f"No explicit {definition['label']} baseline evidence was found.",
            }
        )
    return {
        "available": any(item["available"] for item in baselines),
        "items": baselines,
        "average_baseline_relative_edge": _mean(available_edges),
        "missing_fields": sorted({field for item in baselines if not item["available"] for field in item["missing_fields"]}),
    }


def compute_data_quality(records: list[dict[str, Any]], forecast_records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    rewardable_count = len(_rewardable(records))
    missing_counter: Counter[str] = Counter()
    for row in records:
        missing_counter.update(_listify(row.get("missing_fields")))
    missing_ratio = round((total - rewardable_count) / total, 6) if total else 0.0
    quality_score = round(max(0.0, 100.0 * (1.0 - missing_ratio)), 2) if total else 0.0
    return {
        "available": bool(records or forecast_records),
        "candidate_count": total,
        "rewardable_count": rewardable_count,
        "non_rewardable_count": total - rewardable_count,
        "forecast_count": len(forecast_records),
        "missing_field_counts": dict(missing_counter),
        "missing_ratio": missing_ratio,
        "quality_score": quality_score,
        "severe_missing_data": bool(total and missing_ratio > 0.5),
    }


def determine_benchmark_verdict(
    *,
    records: list[dict[str, Any]],
    core_metrics: dict[str, Any],
    baselines: dict[str, Any],
    score_buckets: dict[str, Any],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    total_records = len(records)
    rewardable_count = len(_rewardable(records))
    if total_records == 0:
        return {"verdict": "insufficient_evidence", "reason": "No rewardable benchmark evidence has been collected yet."}
    if data_quality.get("severe_missing_data"):
        return {"verdict": "data_quality_too_weak", "reason": "More than half of candidate rows are missing rewardable outcome fields."}
    if rewardable_count < MIN_EDGE_SAMPLE_SIZE:
        return {"verdict": "insufficient_evidence", "reason": f"Need at least {MIN_EDGE_SAMPLE_SIZE} rewardable rows before making an edge call."}

    average_reward = _safe_float(core_metrics.get("average_reward")) or 0.0
    baseline_edge = _safe_float(baselines.get("average_baseline_relative_edge")) or 0.0
    bucket_lift = _safe_float(score_buckets.get("score_bucket_lift")) or 0.0
    if average_reward > 0 and baseline_edge >= EDGE_THRESHOLD_PCT and bucket_lift > 0:
        return {"verdict": "edge_detected", "reason": "Average reward is positive, explicit baseline edge is positive, and high score buckets beat low score buckets."}
    if average_reward > 0 or baseline_edge > 0:
        return {"verdict": "weak_edge_detected", "reason": "Some positive reward or baseline-relative edge exists, but score separation or edge magnitude is not strong enough yet."}
    return {"verdict": "no_edge_detected", "reason": "Rewardable rows do not currently beat simple baselines after observed costs."}


def _passes_threshold(value: float | int | None, threshold: float | int, comparison: str) -> bool:
    if value is None:
        return False
    if comparison == "greater_than":
        return float(value) > float(threshold)
    return float(value) >= float(threshold)


def build_benchmark_proof_summary(
    *,
    normalized_records: list[dict[str, Any]],
    core_metrics: dict[str, Any],
    baselines: dict[str, Any],
    score_buckets: dict[str, Any],
    execution: dict[str, Any],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    rewardable_count = len(_rewardable(normalized_records))
    available_baseline_count = sum(1 for item in baselines.get("items") or [] if item.get("available"))
    baseline_edge = _safe_float(baselines.get("average_baseline_relative_edge"))
    bucket_lift = _safe_float(score_buckets.get("score_bucket_lift"))
    after_cost_reward = _safe_float(execution.get("slippage_adjusted_reward"))
    if after_cost_reward is None:
        after_cost_reward = _safe_float(core_metrics.get("slippage_adjusted_reward"))
    values = {
        "rewardable_count": rewardable_count,
        "available_baseline_count": available_baseline_count,
        "baseline_relative_edge": baseline_edge,
        "score_bucket_lift": bucket_lift,
        "slippage_adjusted_reward": after_cost_reward,
        "data_quality_score": _safe_float(data_quality.get("quality_score")),
    }
    rows: list[dict[str, Any]] = []
    for requirement in BENCHMARK_PROOF_REQUIREMENTS:
        value = values.get(str(requirement["metric"]))
        passed = _passes_threshold(value, requirement["threshold"], str(requirement["comparison"]))
        missing_fields: list[str] = []
        if requirement["key"] == "explicit_baselines":
            missing_fields = list(baselines.get("missing_fields") or [])
        elif requirement["key"] == "score_bucket_lift" and not score_buckets.get("available"):
            missing_fields = list(score_buckets.get("missing_fields") or [])
        elif requirement["key"] == "after_cost_reward" and not execution.get("available"):
            missing_fields = list(execution.get("missing_fields") or [])
        elif requirement["key"] == "data_quality":
            missing_fields = list(data_quality.get("missing_field_counts", {}).keys())[:8]
        rows.append(
            {
                "key": requirement["key"],
                "label": requirement["label"],
                "metric": requirement["metric"],
                "status": "ready" if passed else "needs_evidence",
                "passed": passed,
                "value": value,
                "threshold": requirement["threshold"],
                "comparison": requirement["comparison"],
                "missing_fields": missing_fields,
                "safe_next_action": requirement["safe_next_action"],
                "claim_boundary": "Benchmark proof is for human research review only; it is not proof of alpha, investor performance, repeatability, or live-trading readiness.",
                "research_only": True,
                "changes_execution": False,
                "changes_ranking_weights": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
            }
        )
    passed_count = sum(1 for row in rows if row["passed"])
    proof_ready = passed_count == len(rows)
    return {
        "status": "ready_for_human_review" if proof_ready else "needs_evidence",
        "proof_ready": proof_ready,
        "requirements": rows,
        "summary": {
            "requirement_count": len(rows),
            "passed_requirement_count": passed_count,
            "missing_requirement_count": len(rows) - passed_count,
            "baseline_relative_edge": baseline_edge,
            "score_bucket_lift": bucket_lift,
            "slippage_adjusted_reward": after_cost_reward,
            "available_baseline_count": available_baseline_count,
            "rewardable_count": rewardable_count,
            "claim_boundary": "Do not claim proven alpha, guaranteed returns, repeatability, institutional readiness, HFT capability, or live-trading readiness from benchmark proof alone.",
        },
        "safe_next_actions": [
            {
                "field": row["key"],
                "action": row["safe_next_action"],
                "manual_review_only": True,
                "changes_execution": False,
            }
            for row in rows
            if not row["passed"]
        ],
        "research_only": True,
        **SAFETY_FLAGS,
    }


def build_benchmark_hardening_plan(
    *,
    proof_summary: dict[str, Any],
    out_of_sample_stability: dict[str, Any],
    normalized_records: list[dict[str, Any]],
) -> dict[str, Any]:
    proof_rows = {
        str(row.get("key")): row
        for row in proof_summary.get("requirements") or []
        if isinstance(row, dict)
    }
    all_missing_fields = Counter()
    for row in normalized_records:
        all_missing_fields.update(_listify(row.get("missing_fields")))

    items: list[dict[str, Any]] = []
    for definition in BENCHMARK_HARDENING_DEFINITIONS:
        proof_keys = tuple(definition.get("proof_keys") or ())
        related_proof_rows = [
            proof_rows[key]
            for key in proof_keys
            if isinstance(proof_rows.get(key), dict)
        ]
        if definition["key"] == "out_of_sample_split":
            passed = bool(out_of_sample_stability.get("available"))
            status = "ready" if passed else "needs_evidence"
            values = {"available": out_of_sample_stability.get("available")}
            missing_fields = list(out_of_sample_stability.get("missing_fields") or [])
            safe_next_actions = [definition["safe_next_action"]]
        else:
            passed = bool(related_proof_rows) and all(bool(row.get("passed")) for row in related_proof_rows)
            if not normalized_records:
                status = "no_records"
            else:
                status = "ready" if passed else "needs_evidence"
            values = {str(row.get("metric")): row.get("value") for row in related_proof_rows}
            missing_fields = sorted(
                {
                    field
                    for row in related_proof_rows
                    for field in _listify(row.get("missing_fields"))
                }
            )
            if not missing_fields and not passed:
                missing_fields = [field for field, _count in all_missing_fields.most_common(8)]
            safe_next_actions = [
                str(row.get("safe_next_action"))
                for row in related_proof_rows
                if row.get("safe_next_action")
            ] or [definition["safe_next_action"]]
        items.append(
            {
                "key": definition["key"],
                "title": definition["title"],
                "priority": definition["priority"],
                "status": status,
                "passed": passed,
                "proof_keys": list(proof_keys),
                "values": values,
                "missing_fields": missing_fields,
                "blocked_claims": list(definition.get("blocked_claims") or ()),
                "safe_next_action": safe_next_actions[0],
                "safe_next_actions": safe_next_actions,
                "done_when": definition["done_when"],
                "claim_boundary": "Hardening plan items are internal research gates only; they do not prove alpha, repeatability, investor performance, institutional readiness, or live-trading readiness.",
                "manual_review_only": True,
                "research_only": True,
                "changes_execution": False,
                "changes_ranking_weights": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
            }
        )

    open_items = [row for row in items if row["status"] != "ready"]
    critical_open_items = [row for row in open_items if row.get("priority") == "critical"]
    proof_ready = bool(proof_summary.get("proof_ready"))
    out_of_sample_ready = bool(out_of_sample_stability.get("available"))
    return {
        "status": "ready_for_human_review" if proof_ready and not open_items else "blocked_by_evidence",
        "summary": {
            "item_count": len(items),
            "open_item_count": len(open_items),
            "critical_open_items": len(critical_open_items),
            "ready_item_count": len(items) - len(open_items),
            "top_hardening_item": open_items[0]["title"] if open_items else None,
            "proof_first_rule": "Ambition is allowed. Proof decides priority.",
            "claim_permissions": {
                "cautious_internal_benchmark_review": proof_ready,
                "public_alpha_claim": False,
                "repeatability_claim": bool(proof_ready and out_of_sample_ready),
                "live_trading_readiness": False,
                "institutional_readiness": False,
            },
            "blocked_claims": [
                "proven_alpha",
                "guaranteed_returns",
                "repeatability",
                "institutional_readiness",
                "live_trading_readiness",
            ],
            "safe_boundary": "Professional Benchmark hardening only records proof gaps and claim boundaries. It does not authorize orders, route changes, risk-gate changes, or ranking-weight mutation.",
        },
        "items": items,
        "safe_next_actions": [
            {
                "field": row["key"],
                "action": row["safe_next_action"],
                "manual_review_only": True,
                "changes_execution": False,
            }
            for row in open_items
        ],
        "research_only": True,
        **SAFETY_FLAGS,
    }


def _extract_runtime_records(db: Any = None, current_user: Any = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    reward_records: list[dict[str, Any]] = []
    forecast_records: list[dict[str, Any]] = []
    try:
        reward_report = get_evidence_reward_summary(db, current_user=current_user)
        reward_records = list(reward_report.get("records") or reward_report.get("candidate_rows") or [])
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Evidence Reward source unavailable: {exc.__class__.__name__}.")
    try:
        forecast_report = get_forecast_validation_summary()
        forecast_records = list(forecast_report.get("records") or forecast_report.get("items") or [])
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Forecast Validation source unavailable: {exc.__class__.__name__}.")
    return reward_records, forecast_records, warnings


def build_professional_benchmark_report(
    *,
    records: Iterable[dict[str, Any]] | None = None,
    forecast_records: Iterable[dict[str, Any]] | None = None,
    db: Any = None,
    current_user: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    source_warnings: list[str] = []
    if records is None and forecast_records is None:
        runtime_records, runtime_forecasts, source_warnings = _extract_runtime_records(db, current_user)
        records = runtime_records
        forecast_records = runtime_forecasts

    normalized_records = _normalize_records(records)
    normalized_forecasts = [_normalize_forecast_record(row) for row in forecast_records or [] if isinstance(row, dict)]
    core_metrics = compute_core_metrics(normalized_records)
    score_buckets = compute_score_bucket_separation(normalized_records)
    baselines = compute_baseline_comparison(normalized_records)
    blockers = compute_blocker_value(normalized_records)
    ai = compute_ai_verdict_accuracy(normalized_records)
    forecast = compute_forecast_accuracy(normalized_forecasts)
    execution = compute_execution_quality(normalized_records)
    missed = compute_missed_move_recovery(normalized_records)
    data_quality = compute_data_quality(normalized_records, normalized_forecasts)
    proof_summary = build_benchmark_proof_summary(
        normalized_records=normalized_records,
        core_metrics=core_metrics,
        baselines=baselines,
        score_buckets=score_buckets,
        execution=execution,
        data_quality=data_quality,
    )
    reward_by_setup = aggregate_by_key(normalized_records, "setup_type")
    reward_by_engine = aggregate_by_key(normalized_records, "engine")
    reward_by_regime = aggregate_by_key(normalized_records, "regime")
    out_of_sample_stability = {
        "available": False,
        "missing_fields": ["sample_split", "experiment_version"],
        "reason": "Walk-forward and out-of-sample labels are required before stability can be scored.",
    }
    hardening_plan = build_benchmark_hardening_plan(
        proof_summary=proof_summary,
        out_of_sample_stability=out_of_sample_stability,
        normalized_records=normalized_records,
    )
    verdict = determine_benchmark_verdict(
        records=normalized_records,
        core_metrics=core_metrics,
        baselines=baselines,
        score_buckets=score_buckets,
        data_quality=data_quality,
    )

    missing_fields = Counter(data_quality.get("missing_field_counts") or {})
    missing_fields.update(baselines.get("missing_fields") or [])
    if not forecast.get("available"):
        missing_fields.update(forecast.get("missing_fields") or [])
    warnings = list(source_warnings)
    if verdict["verdict"] in {"insufficient_evidence", "data_quality_too_weak"}:
        warnings.append(verdict["reason"])
    if not baselines["available"]:
        warnings.append("No explicit baseline evidence was found; baseline comparison remains unavailable.")
    if not forecast["available"]:
        warnings.append("Forecast accuracy requires rewardable forecast validation rows.")
    if not execution["available"]:
        warnings.append("Execution-adjusted reward requires slippage or spread cost fields.")
    if not proof_summary["proof_ready"]:
        warnings.append("Benchmark proof is not ready for human review until sample size, baselines, baseline-relative edge, score-bucket lift, after-cost reward, and data quality pass.")
    if hardening_plan["summary"]["open_item_count"]:
        warnings.append("Benchmark hardening still blocks alpha, repeatability, institutional, and live-trading readiness claims.")

    sections = {
        "benchmark_proof": proof_summary,
        "benchmark_hardening_plan": hardening_plan,
        "forecast_accuracy": forecast,
        "reward_by_setup": {"available": bool(reward_by_setup), "items": reward_by_setup},
        "reward_by_engine": {"available": bool(reward_by_engine), "items": reward_by_engine},
        "reward_by_regime": {"available": bool(reward_by_regime), "items": reward_by_regime},
        "score_bucket_separation": score_buckets,
        "blocker_value": blockers,
        "ai_verdict_accuracy": ai,
        "missed_move_recovery": missed,
        "execution_quality": execution,
        "slippage_adjusted_reward": {"available": execution["available"], "value": execution.get("slippage_adjusted_reward"), "missing_fields": execution.get("missing_fields", [])},
        "baseline_comparison": baselines,
        "out_of_sample_stability": out_of_sample_stability,
        "data_quality": data_quality,
    }
    summary = {
        "benchmark_verdict": verdict["verdict"],
        "verdict_reason": verdict["reason"],
        "candidate_count": len(normalized_records),
        "rewardable_count": len(_rewardable(normalized_records)),
        "forecast_count": len(normalized_forecasts),
        "data_quality_score": data_quality["quality_score"],
        "hit_rate": core_metrics["hit_rate"],
        "expected_value": core_metrics["expected_value"],
        "average_reward": core_metrics["average_reward"],
        "median_reward": core_metrics["median_reward"],
        "reward_dispersion": core_metrics["reward_dispersion"],
        "slippage_adjusted_reward": core_metrics["slippage_adjusted_reward"],
        "score_bucket_lift": score_buckets.get("score_bucket_lift"),
        "baseline_relative_edge": baselines.get("average_baseline_relative_edge"),
        "benchmark_proof_ready": proof_summary["proof_ready"],
        "benchmark_proof_status": proof_summary["status"],
        "benchmark_proof_requirements_passed": proof_summary["summary"]["passed_requirement_count"],
        "benchmark_proof_requirements_total": proof_summary["summary"]["requirement_count"],
        "benchmark_hardening_status": hardening_plan["status"],
        "benchmark_hardening_open_items": hardening_plan["summary"]["open_item_count"],
        "benchmark_hardening_critical_open_items": hardening_plan["summary"]["critical_open_items"],
        "top_hardening_item": hardening_plan["summary"]["top_hardening_item"],
        "claim_permissions": hardening_plan["summary"]["claim_permissions"],
        "edge_after_costs": proof_summary["summary"]["slippage_adjusted_reward"],
        "sample_size_warning": len(_rewardable(normalized_records)) < MIN_EDGE_SAMPLE_SIZE,
        "out_of_sample_status": "missing_sample_split",
        "verdict_rules": {
            "min_edge_sample_size": MIN_EDGE_SAMPLE_SIZE,
            "edge_detected": "positive average reward, baseline edge >= 0.10 percentage points, and positive score bucket lift",
            "weak_edge_detected": "positive reward or positive baseline edge without complete score separation",
            "no_edge_detected": "rewardable rows fail to beat baselines",
            "insufficient_evidence": "no rows or fewer than minimum rewardable rows",
            "data_quality_too_weak": "more than half of rows are missing rewardable outcome fields",
        },
        **SAFETY_FLAGS,
    }
    aggregations = {
        "core_metrics": core_metrics,
        "benchmark_proof": proof_summary,
        "benchmark_hardening_plan": hardening_plan,
        "reward_by_setup": reward_by_setup,
        "reward_by_engine": reward_by_engine,
        "reward_by_regime": reward_by_regime,
        "score_bucket_separation": score_buckets,
        "blocker_value": blockers,
        "ai_verdict_accuracy": ai,
        "missed_move_recovery": missed,
        "execution_quality": execution,
        "forecast_accuracy": forecast,
        "out_of_sample_stability": sections["out_of_sample_stability"],
        "data_quality": data_quality,
    }
    return serialize_value(
        {
            "status": verdict["verdict"],
            "generated_at": generated_at or _utc_now(),
            "research_only": True,
            "mode": "research_only",
            "summary": summary,
            "records": normalized_records[:250],
            "aggregations": aggregations,
            "baselines": baselines,
            "proof_summary": proof_summary,
            "benchmark_hardening_plan": hardening_plan,
            "sections": sections,
            "warnings": warnings,
            "missing_fields": dict(missing_fields),
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
            "finish_tracker": build_project_finish_tracker(report_name="professional_benchmark"),
        }
    )


def _report_subset(report: dict[str, Any], *, records: list[dict[str, Any]] | None = None, aggregations: dict[str, Any] | None = None, baselines: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": report.get("status", "unknown"),
        "generated_at": report.get("generated_at"),
        "research_only": True,
        "mode": "research_only",
        "summary": report.get("summary", {}),
        "records": records if records is not None else [],
        "aggregations": aggregations if aggregations is not None else {},
        "baselines": baselines if baselines is not None else report.get("baselines", {}),
        "proof_summary": report.get("proof_summary", {}),
        "benchmark_hardening_plan": report.get("benchmark_hardening_plan", {}),
        "warnings": report.get("warnings", []),
        "missing_fields": report.get("missing_fields", {}),
        "safety_notes": report.get("safety_notes", list(SAFETY_NOTES)),
        **SAFETY_FLAGS,
        "finish_tracker": report.get("finish_tracker") or build_project_finish_tracker(report_name="professional_benchmark_subset"),
    }


def get_professional_benchmark_summary(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    return build_professional_benchmark_report(db=db, current_user=current_user)


def get_professional_benchmark_baselines(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = get_professional_benchmark_summary(db, current_user=current_user)
    return _report_subset(report, records=report.get("baselines", {}).get("items", []), baselines=report.get("baselines", {}))


def get_professional_benchmark_score_buckets(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = get_professional_benchmark_summary(db, current_user=current_user)
    section = report.get("sections", {}).get("score_bucket_separation", {})
    return _report_subset(report, records=section.get("items", []), aggregations={"score_bucket_separation": section})


def get_professional_benchmark_blockers(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = get_professional_benchmark_summary(db, current_user=current_user)
    section = report.get("sections", {}).get("blocker_value", {})
    return _report_subset(report, records=section.get("items", []), aggregations={"blocker_value": section})


def get_professional_benchmark_ai(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = get_professional_benchmark_summary(db, current_user=current_user)
    section = report.get("sections", {}).get("ai_verdict_accuracy", {})
    return _report_subset(report, records=section.get("items", []), aggregations={"ai_verdict_accuracy": section})


def get_professional_benchmark_forecast(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = get_professional_benchmark_summary(db, current_user=current_user)
    section = report.get("sections", {}).get("forecast_accuracy", {})
    return _report_subset(report, records=section.get("items", []), aggregations={"forecast_accuracy": section})


def get_professional_benchmark_execution(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = get_professional_benchmark_summary(db, current_user=current_user)
    section = report.get("sections", {}).get("execution_quality", {})
    return _report_subset(report, records=[], aggregations={"execution_quality": section})
