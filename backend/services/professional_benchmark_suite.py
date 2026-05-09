from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median, pstdev
from typing import Any, Iterable

from backend.services.evidence_reward_engine import get_evidence_reward_summary
from backend.services.forecast_validation_engine import get_forecast_validation_summary
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
    reward_by_setup = aggregate_by_key(normalized_records, "setup_type")
    reward_by_engine = aggregate_by_key(normalized_records, "engine")
    reward_by_regime = aggregate_by_key(normalized_records, "regime")
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

    sections = {
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
        "out_of_sample_stability": {
            "available": False,
            "missing_fields": ["sample_split", "experiment_version"],
            "reason": "Walk-forward and out-of-sample labels are required before stability can be scored.",
        },
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
            "sections": sections,
            "warnings": warnings,
            "missing_fields": dict(missing_fields),
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
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
        "warnings": report.get("warnings", []),
        "missing_fields": report.get("missing_fields", {}),
        "safety_notes": report.get("safety_notes", list(SAFETY_NOTES)),
        **SAFETY_FLAGS,
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
