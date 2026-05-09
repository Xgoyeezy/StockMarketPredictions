from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median, pstdev
from typing import Any, Iterable

from backend.services.professional_benchmark_suite import get_professional_benchmark_summary
from backend.services.serialization import serialize_value

SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "paper_route_only": True,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
    "mutation": "none",
    "writes_execution_config": False,
    "writes_broker_config": False,
    "writes_risk_config": False,
    "writes_ranking_config": False,
}

SAFETY_NOTES: tuple[str, ...] = (
    "Research only. Does not affect trading.",
    "Does not place orders.",
    "Does not change broker routes.",
    "Does not bypass risk gates.",
    "Does not change ranking weights automatically.",
    "Does not grant AI order authority.",
)

SCORE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0_20", 0.0, 20.0),
    ("20_40", 20.0, 40.0),
    ("40_60", 40.0, 60.0),
    ("60_80", 60.0, 80.0),
    ("80_100", 80.0, 100.0),
)
FALSE_POSITIVE_RETURN_THRESHOLD = 0.0
FALSE_NEGATIVE_RETURN_THRESHOLD = 0.25
MIN_FEATURE_SAMPLE = 3
STRONG_LIFT_THRESHOLD = 0.10

SECRET_KEY_MARKERS = ("secret", "token", "password", "credential", "api_key", "apikey", "access_key", "private_key")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _mean(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in (_safe_float(item) for item in values) if value is not None]
    return round(sum(clean) / len(clean), 6) if clean else None


def _median(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in (_safe_float(item) for item in values) if value is not None]
    return round(float(median(clean)), 6) if clean else None


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _dispersion(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in (_safe_float(item) for item in values) if value is not None]
    if not clean:
        return None
    if len(clean) == 1:
        return 0.0
    return round(float(pstdev(clean)), 6)


def _correlation(xs: Iterable[Any], ys: Iterable[Any]) -> float | None:
    pairs = [
        (float(x), float(y))
        for x, y in ((_safe_float(x), _safe_float(y)) for x, y in zip(xs, ys))
        if x is not None and y is not None
    ]
    if len(pairs) < 2:
        return None
    x_values = [pair[0] for pair in pairs]
    y_values = [pair[1] for pair in pairs]
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    x_denominator = sum((x - x_mean) ** 2 for x in x_values)
    y_denominator = sum((y - y_mean) ** 2 for y in y_values)
    if x_denominator <= 0 or y_denominator <= 0:
        return None
    return round(numerator / ((x_denominator * y_denominator) ** 0.5), 6)


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple) or isinstance(value, set):
        return list(value)
    text = str(value).strip()
    if not text:
        return []
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


def _nested_dicts(row: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [row]
    for key in ("prediction_contract", "reward_components", "component_scores", "ranking_components", "features", "feature_values"):
        value = row.get(key)
        if isinstance(value, dict):
            sources.append(value)
    return sources


def _first_number(row: dict[str, Any], fields: Iterable[str]) -> float | None:
    for source in _nested_dicts(row):
        for field in fields:
            value = _safe_float(source.get(field))
            if value is not None:
                return value
    return None


def _first_text(row: dict[str, Any], fields: Iterable[str], fallback: str = "unknown") -> str:
    for source in _nested_dicts(row):
        for field in fields:
            value = source.get(field)
            if value is not None and str(value).strip():
                return str(value).strip()
    return fallback


def _looks_like_local_path(value: str) -> bool:
    cleaned = value.strip()
    return (len(cleaned) >= 3 and cleaned[1:3] in {":\\", ":/"}) or cleaned.startswith("\\\\")


def _sanitize_value(value: Any, *, key: str = "") -> Any:
    if any(marker in key.lower() for marker in SECRET_KEY_MARKERS):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _sanitize_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, key=key) for item in value]
    if isinstance(value, tuple) or isinstance(value, set):
        return [_sanitize_value(item, key=key) for item in value]
    if isinstance(value, str) and _looks_like_local_path(value):
        return "[local_path_redacted]"
    return value


def _detect_score_scale(raw_scores: list[float]) -> dict[str, Any]:
    if not raw_scores:
        return {
            "scale": "missing",
            "description": "No score fields were present.",
            "multiplier": 1.0,
            "min_score": None,
            "max_score": None,
        }
    max_score = max(raw_scores)
    min_score = min(raw_scores)
    if max_score <= 1.5:
        return {
            "scale": "0_to_1_scaled_to_100",
            "description": "Scores appear to use a 0-1 scale and are multiplied by 100 for bucket analysis.",
            "multiplier": 100.0,
            "min_score": round(min_score, 6),
            "max_score": round(max_score, 6),
        }
    if max_score > 100.0 or min_score < 0.0:
        return {
            "scale": "extended_clamped_to_0_100",
            "description": "Scores extend outside 0-100 and are clamped for bucket analysis.",
            "multiplier": 1.0,
            "min_score": round(min_score, 6),
            "max_score": round(max_score, 6),
        }
    return {
        "scale": "0_to_100",
        "description": "Scores appear to use the expected 0-100 scale.",
        "multiplier": 1.0,
        "min_score": round(min_score, 6),
        "max_score": round(max_score, 6),
    }


def assign_score_bucket(score: Any, *, multiplier: float = 1.0) -> str:
    numeric = _safe_float(score)
    if numeric is None:
        return "unknown"
    normalized = max(0.0, min(100.0, numeric * multiplier))
    for label, lower, upper in SCORE_BUCKETS:
        if normalized >= lower and (normalized < upper or (label == "80_100" and normalized <= upper)):
            return label
    return "unknown"


def _score_for_row(row: dict[str, Any]) -> float | None:
    return _first_number(row, ("score", "ranking_score", "setup_score", "opportunity_score", "candidate_score"))


def _execution_adjusted_reward(row: dict[str, Any]) -> float | None:
    explicit = _first_number(row, ("execution_adjusted_reward", "slippage_adjusted_reward"))
    if explicit is not None:
        return explicit
    reward = _first_number(row, ("total_reward", "reward"))
    if reward is None:
        return None
    slippage_bps = _first_number(row, ("slippage_bps", "slippage_estimate_bps", "slippage"))
    spread_bps = _first_number(row, ("spread_bps", "spread_at_signal", "spread_cost_bps"))
    adjusted = reward
    if slippage_bps is not None:
        adjusted -= abs(slippage_bps) / 100.0
    if spread_bps is not None:
        adjusted -= max(0.0, spread_bps) / 100.0
    return round(adjusted, 6)


def _forecast_accuracy(row: dict[str, Any]) -> float | None:
    explicit = _first_number(row, ("forecast_accuracy", "direction_accuracy", "forecast_direction_accuracy"))
    if explicit is not None:
        return explicit
    direction_correct = row.get("direction_correct")
    contract = row.get("prediction_contract") if isinstance(row.get("prediction_contract"), dict) else {}
    if direction_correct is None:
        direction_correct = contract.get("direction_correct")
    if direction_correct is None:
        return None
    return 1.0 if bool(direction_correct) else 0.0


def normalize_calibration_records(records: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    raw = [row for row in records or [] if isinstance(row, dict)]
    raw_scores = [value for value in (_score_for_row(row) for row in raw) if value is not None]
    scale = _detect_score_scale(raw_scores)
    multiplier = float(scale["multiplier"])
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(raw):
        raw_score = _score_for_row(row)
        normalized_score = None if raw_score is None else max(0.0, min(100.0, raw_score * multiplier))
        total_reward = _first_number(row, ("total_reward", "reward"))
        actual_forward_return = _first_number(row, ("actual_forward_return", "forward_return", "actual_return_pct"))
        baseline_forward_return = _first_number(row, ("baseline_forward_return", "baseline_return", "random_candidate_forward_return"))
        missing_fields = set(str(item) for item in _listify(row.get("missing_fields")) if str(item).strip())
        if raw_score is None:
            missing_fields.add("score")
        if total_reward is None:
            missing_fields.add("total_reward")
        if baseline_forward_return is None:
            missing_fields.add("baseline_forward_return")
        normalized.append(
            _sanitize_value(
                {
                    **row,
                    "record_id": _first_text(row, ("record_id", "candidate_lifecycle_id", "prediction_id", "symbol"), f"record-{index + 1}"),
                    "symbol": _first_text(row, ("symbol", "ticker"), "unknown"),
                    "engine": _first_text(row, ("engine", "desk_key", "source"), "unknown"),
                    "setup_type": _first_text(row, ("setup_type", "opportunity_type"), "unknown"),
                    "regime": _first_text(row, ("regime", "market_regime", "regime_state"), "unknown"),
                    "ai_verdict": _first_text(row, ("ai_verdict", "ai_evidence_verdict"), ""),
                    "score": round(normalized_score, 6) if normalized_score is not None else None,
                    "raw_score": raw_score,
                    "score_bucket": assign_score_bucket(raw_score, multiplier=multiplier),
                    "rewardable": bool(row.get("rewardable")) and total_reward is not None,
                    "total_reward": total_reward,
                    "actual_forward_return": actual_forward_return,
                    "baseline_forward_return": baseline_forward_return,
                    "baseline_relative_edge": round(actual_forward_return - baseline_forward_return, 6)
                    if actual_forward_return is not None and baseline_forward_return is not None
                    else None,
                    "forecast_accuracy": _forecast_accuracy(row),
                    "execution_adjusted_reward": _execution_adjusted_reward(row),
                    "blockers": [str(item) for item in _listify(row.get("blockers") or row.get("blocker")) if str(item).strip()],
                    "allowed": bool(row.get("allowed")),
                    "blocked": bool(row.get("blocked")),
                    "missing_fields": sorted(missing_fields),
                    "score_scale": scale["scale"],
                }
            )
        )
    return normalized


def _rewardable(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in records if row.get("rewardable") and _safe_float(row.get("total_reward")) is not None]


def compute_score_bucket_analysis(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {label: [] for label, _, _ in SCORE_BUCKETS}
    grouped["unknown"] = []
    for row in records:
        grouped.setdefault(str(row.get("score_bucket") or "unknown"), []).append(row)
    items: list[dict[str, Any]] = []
    for label, _, _ in (*SCORE_BUCKETS, ("unknown", 0.0, 0.0)):
        rows = grouped.get(label, [])
        rewardable = _rewardable(rows)
        rewards = [row.get("total_reward") for row in rewardable]
        actuals = [row.get("actual_forward_return") for row in rewardable]
        missing_count = sum(1 for row in rows if row.get("missing_fields"))
        items.append(
            {
                "score_bucket": label,
                "candidate_count": len(rows),
                "rewardable_count": len(rewardable),
                "average_reward": _mean(rewards),
                "median_reward": _median(rewards),
                "hit_rate": _ratio(sum(1 for value in actuals if _safe_float(value) is not None and float(value) > 0), len([value for value in actuals if _safe_float(value) is not None])),
                "baseline_relative_edge": _mean(row.get("baseline_relative_edge") for row in rewardable),
                "forecast_accuracy": _mean(row.get("forecast_accuracy") for row in rewardable),
                "execution_adjusted_reward": _mean(row.get("execution_adjusted_reward") for row in rewardable),
                "missing_data_rate": _ratio(missing_count, len(rows)) if rows else None,
            }
        )
    high = next((row for row in items if row["score_bucket"] == "80_100"), {})
    low_rows = [row for row in items if row["score_bucket"] in {"0_20", "20_40"} and row.get("average_reward") is not None]
    low_avg = _mean(row.get("average_reward") for row in low_rows)
    high_avg = _safe_float(high.get("average_reward"))
    bucket_lift = round(high_avg - low_avg, 6) if high_avg is not None and low_avg is not None else None
    ordered_rewards = [row.get("average_reward") for row in items if row["score_bucket"] != "unknown" and row.get("average_reward") is not None]
    comparisons = [(left, right) for left, right in zip(ordered_rewards, ordered_rewards[1:])]
    monotonicity = _ratio(sum(1 for left, right in comparisons if right >= left), len(comparisons)) if comparisons else None
    if bucket_lift is None or monotonicity is None:
        warning = "Need rewardable records in multiple score buckets before calibration can be trusted."
    elif bucket_lift <= 0 or monotonicity < 0.5:
        warning = "Score bucket separation is poor; high scores are not reliably outperforming low scores."
    elif monotonicity < 0.75:
        warning = "Score bucket separation exists but is not strongly monotonic."
    else:
        warning = "Score bucket separation is strong but still requires walk-forward validation."
    return {
        "available": bucket_lift is not None,
        "items": items,
        "bucket_lift": bucket_lift,
        "monotonicity_score": monotonicity,
        "calibration_warning": warning,
        "missing_fields": [] if bucket_lift is not None else ["score", "total_reward", "baseline_forward_return"],
    }


def _feature_values(row: dict[str, Any]) -> set[str]:
    values = {
        f"setup_type:{row.get('setup_type') or 'unknown'}",
        f"engine:{row.get('engine') or 'unknown'}",
        f"regime:{row.get('regime') or 'unknown'}",
    }
    if row.get("ai_verdict"):
        values.add(f"ai_verdict:{row['ai_verdict']}")
    for blocker in row.get("blockers") or []:
        values.add(f"blocker:{blocker}")
    for source_key in ("reward_components", "component_scores", "ranking_components", "features", "feature_values"):
        source = row.get(source_key)
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            numeric = _safe_float(value)
            if numeric is not None:
                if key in {"total_reward", "reward"}:
                    continue
                if numeric > 0:
                    values.add(f"component:{key}:positive")
            elif isinstance(value, bool) and value:
                values.add(f"feature:{key}")
            elif isinstance(value, str) and value.strip():
                values.add(f"{key}:{value.strip()}")
    return {str(item) for item in values if str(item).strip() and not str(item).endswith(":unknown")}


def _confidence_bucket(count: int) -> str:
    if count >= 50:
        return "high"
    if count >= 20:
        return "medium"
    if count >= 5:
        return "low"
    return "insufficient"


def compute_feature_attribution(records: list[dict[str, Any]]) -> dict[str, Any]:
    rewardable = _rewardable(records)
    feature_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rewardable:
        for feature in _feature_values(row):
            feature_map[feature].append(row)
    all_rewards = [row.get("total_reward") for row in rewardable]
    items: list[dict[str, Any]] = []
    for feature, present in feature_map.items():
        absent = [row for row in rewardable if row not in present]
        present_reward = _mean(row.get("total_reward") for row in present)
        absent_reward = _mean(row.get("total_reward") for row in absent)
        lift = round(present_reward - absent_reward, 6) if present_reward is not None and absent_reward is not None else None
        present_high_score = [row for row in present if (_safe_float(row.get("score")) or 0.0) >= 60.0]
        false_positive_count = sum(
            1
            for row in present_high_score
            if (_safe_float(row.get("actual_forward_return")) is not None and float(row["actual_forward_return"]) <= FALSE_POSITIVE_RETURN_THRESHOLD)
            or (_safe_float(row.get("total_reward")) is not None and float(row["total_reward"]) < 0.0)
        )
        present_low_or_blocked = [row for row in present if (_safe_float(row.get("score")) or 100.0) < 40.0 or row.get("blocked")]
        false_negative_count = sum(
            1
            for row in present_low_or_blocked
            if (_safe_float(row.get("actual_forward_return")) is not None and float(row["actual_forward_return"]) >= FALSE_NEGATIVE_RETURN_THRESHOLD)
            or (_safe_float(row.get("total_reward")) is not None and float(row["total_reward"]) >= FALSE_NEGATIVE_RETURN_THRESHOLD)
        )
        by_regime: dict[str, list[float]] = defaultdict(list)
        for row in present:
            reward = _safe_float(row.get("total_reward"))
            if reward is not None:
                by_regime[str(row.get("regime") or "unknown")].append(reward)
        regime_avgs = {regime: _mean(values) for regime, values in by_regime.items() if values}
        clean_regime_avgs = [float(value) for value in regime_avgs.values() if value is not None]
        regime_dependency = round(max(clean_regime_avgs) - min(clean_regime_avgs), 6) if len(clean_regime_avgs) >= 2 else None
        warnings = []
        if len(present) < MIN_FEATURE_SAMPLE:
            warnings.append("Small sample; review only.")
        if lift is not None and lift < 0:
            warnings.append("Negative lift versus absent records.")
        fp_rate = _ratio(false_positive_count, len(present_high_score))
        fn_rate = _ratio(false_negative_count, len(present_low_or_blocked))
        if fp_rate is not None and fp_rate >= 0.5:
            warnings.append("High false-positive rate in high-score records.")
        if fn_rate is not None and fn_rate >= 0.5:
            warnings.append("High false-negative or missed-winner rate.")
        items.append(
            {
                "feature": feature,
                "times_seen": len(present),
                "average_reward_when_present": present_reward,
                "average_reward_when_absent": absent_reward,
                "lift": lift,
                "false_positive_rate": fp_rate,
                "false_positive_count": false_positive_count,
                "false_negative_rate": fn_rate,
                "false_negative_count": false_negative_count,
                "regime_dependency": regime_dependency,
                "reward_dispersion_when_present": _dispersion(row.get("total_reward") for row in present),
                "confidence_bucket": _confidence_bucket(len(present)),
                "warnings": warnings,
            }
        )
    false_positive_drivers = sorted(items, key=lambda row: (row.get("false_positive_rate") is None, -(row.get("false_positive_rate") or -1), -row["times_seen"]))[:10]
    false_negative_drivers = sorted(items, key=lambda row: (row.get("false_negative_rate") is None, -(row.get("false_negative_rate") or -1), -row["times_seen"]))[:10]
    return {
        "available": bool(items),
        "items": sorted(items, key=lambda row: (row["lift"] is None, -abs(row["lift"] or 0.0), -row["times_seen"])),
        "top_positive_features": sorted([row for row in items if (row.get("lift") or 0.0) > 0], key=lambda row: (-(row.get("lift") or 0.0), -row["times_seen"]))[:10],
        "top_negative_features": sorted([row for row in items if (row.get("lift") or 0.0) < 0], key=lambda row: ((row.get("lift") or 0.0), -row["times_seen"]))[:10],
        "false_positive_drivers": false_positive_drivers,
        "false_negative_drivers": false_negative_drivers,
        "overall_average_reward": _mean(all_rewards),
        "missing_fields": [] if items else ["feature_fields", "total_reward"],
    }


def _segment_lift(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    rewardable = _rewardable(records)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_avg = _mean(row.get("total_reward") for row in rewardable)
    for row in rewardable:
        grouped[str(row.get(key) or "unknown")].append(row)
    rows = []
    for segment, items in grouped.items():
        avg_reward = _mean(row.get("total_reward") for row in items)
        rows.append(
            {
                key: segment,
                "rewardable_count": len(items),
                "average_reward": avg_reward,
                "lift": round(avg_reward - all_avg, 6) if avg_reward is not None and all_avg is not None else None,
                "hit_rate": _ratio(sum(1 for row in items if (_safe_float(row.get("actual_forward_return")) or 0.0) > 0), len(items)),
                "baseline_relative_edge": _mean(row.get("baseline_relative_edge") for row in items),
                "execution_adjusted_reward": _mean(row.get("execution_adjusted_reward") for row in items),
            }
        )
    return sorted(rows, key=lambda row: (row["lift"] is None, -(row["lift"] or -999), -row["rewardable_count"]))


def compute_relationships(records: list[dict[str, Any]]) -> dict[str, Any]:
    rewardable = _rewardable(records)
    scores = [row.get("score") for row in rewardable]
    return {
        "score_to_reward_correlation": _correlation(scores, [row.get("total_reward") for row in rewardable]),
        "score_to_baseline_edge_correlation": _correlation(scores, [row.get("baseline_relative_edge") for row in rewardable]),
        "score_to_forecast_accuracy_correlation": _correlation(scores, [row.get("forecast_accuracy") for row in rewardable]),
        "score_to_execution_adjusted_reward_correlation": _correlation(scores, [row.get("execution_adjusted_reward") for row in rewardable]),
    }


def generate_safe_recommendations(bucket_report: dict[str, Any], feature_report: dict[str, Any], relationships: dict[str, Any]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    bucket_lift = _safe_float(bucket_report.get("bucket_lift"))
    monotonicity = _safe_float(bucket_report.get("monotonicity_score"))
    if bucket_lift is None:
        recommendations.append(
            {
                "type": "insufficient_data",
                "recommendation": "Collect rewardable score records before reviewing score calibration.",
                "manual_review_only": True,
            }
        )
    elif bucket_lift <= 0 or (monotonicity is not None and monotonicity < 0.5):
        recommendations.append(
            {
                "type": "review_score_formula",
                "recommendation": "Score bucket separation is poor; review the score formula before changing ranking logic.",
                "manual_review_only": True,
            }
        )
    elif monotonicity is not None and monotonicity >= 0.75:
        recommendations.append(
            {
                "type": "validate_score_separation",
                "recommendation": "Score bucket separation is strong but needs walk-forward validation before any ranking change is considered.",
                "manual_review_only": True,
            }
        )
    for row in feature_report.get("top_positive_features") or []:
        if (row.get("lift") or 0.0) >= STRONG_LIFT_THRESHOLD:
            recommendations.append(
                {
                    "type": "review_feature_weight",
                    "feature": row["feature"],
                    "recommendation": f"Review weight of {row['feature']}; it has positive research lift but must not change automatically.",
                    "manual_review_only": True,
                }
            )
    for row in feature_report.get("top_negative_features") or []:
        if (row.get("lift") or 0.0) <= -STRONG_LIFT_THRESHOLD:
            recommendations.append(
                {
                    "type": "feature_weak_lift",
                    "feature": row["feature"],
                    "recommendation": f"{row['feature']} has weak or negative lift; review whether it should remain prominent in research scoring.",
                    "manual_review_only": True,
                }
            )
    for row in feature_report.get("false_positive_drivers") or []:
        if (row.get("false_positive_rate") or 0.0) >= 0.5:
            recommendations.append(
                {
                    "type": "false_positive_driver",
                    "feature": row["feature"],
                    "recommendation": f"{row['feature']} is associated with high-score losing records; review false-positive handling.",
                    "manual_review_only": True,
                }
            )
    for row in feature_report.get("false_negative_drivers") or []:
        if (row.get("false_negative_rate") or 0.0) >= 0.5:
            recommendations.append(
                {
                    "type": "false_negative_driver",
                    "feature": row["feature"],
                    "recommendation": f"{row['feature']} appears in low-ranked or blocked winners; review missed-winner handling.",
                    "manual_review_only": True,
                }
            )
    forecast_corr = _safe_float(relationships.get("score_to_forecast_accuracy_correlation"))
    if forecast_corr is not None and forecast_corr < 0:
        recommendations.append(
            {
                "type": "forecast_quality_misalignment",
                "recommendation": "Candidate scores are negatively correlated with forecast quality; review forecast-score alignment.",
                "manual_review_only": True,
            }
        )
    return recommendations[:25]


def build_score_calibration_report(
    *,
    records: Iterable[dict[str, Any]] | None = None,
    benchmark_report: dict[str, Any] | None = None,
    db: Any = None,
    current_user: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    source_warnings: list[str] = []
    report = benchmark_report
    if records is None:
        if report is None:
            try:
                report = get_professional_benchmark_summary(db, current_user=current_user)
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                report = {}
                source_warnings.append(f"Professional Benchmark source unavailable: {exc.__class__.__name__}.")
        records = (report or {}).get("records") or []
    normalized = normalize_calibration_records(records)
    raw_scores = [row["raw_score"] for row in normalized if row.get("raw_score") is not None]
    score_scale = _detect_score_scale([float(score) for score in raw_scores])
    bucket_report = compute_score_bucket_analysis(normalized)
    feature_report = compute_feature_attribution(normalized)
    setup_lift = _segment_lift(normalized, "setup_type")
    engine_lift = _segment_lift(normalized, "engine")
    regime_lift = _segment_lift(normalized, "regime")
    relationships = compute_relationships(normalized)
    recommendations = generate_safe_recommendations(bucket_report, feature_report, relationships)
    missing_counter: Counter[str] = Counter()
    for row in normalized:
        missing_counter.update(row.get("missing_fields") or [])
    rewardable_count = len(_rewardable(normalized))
    status = "ready" if rewardable_count else "insufficient_evidence" if normalized else "empty"
    warnings = [*source_warnings]
    if bucket_report.get("calibration_warning"):
        warnings.append(str(bucket_report["calibration_warning"]))
    if missing_counter:
        warnings.append("Some score calibration records are missing fields required for full attribution.")
    summary = {
        "status": status,
        "candidate_count": len(normalized),
        "rewardable_count": rewardable_count,
        "non_rewardable_count": len(normalized) - rewardable_count,
        "score_scale": score_scale,
        "bucket_lift": bucket_report.get("bucket_lift"),
        "monotonicity_score": bucket_report.get("monotonicity_score"),
        "calibration_warning": bucket_report.get("calibration_warning"),
        "score_to_reward_correlation": relationships.get("score_to_reward_correlation"),
        "score_to_forecast_accuracy_correlation": relationships.get("score_to_forecast_accuracy_correlation"),
        "score_to_execution_adjusted_reward_correlation": relationships.get("score_to_execution_adjusted_reward_correlation"),
        **SAFETY_FLAGS,
    }
    aggregations = {
        "score_bucket_separation": bucket_report,
        "feature_attribution": feature_report,
        "setup_specific_lift": setup_lift,
        "engine_specific_lift": engine_lift,
        "regime_specific_lift": regime_lift,
        "forecast_quality_relationship": {
            "correlation": relationships.get("score_to_forecast_accuracy_correlation"),
            "by_score_bucket": bucket_report.get("items", []),
        },
        "execution_adjusted_relationship": {
            "correlation": relationships.get("score_to_execution_adjusted_reward_correlation"),
            "by_score_bucket": bucket_report.get("items", []),
        },
        "relationships": relationships,
        "recommendations": recommendations,
    }
    return serialize_value(
        {
            "status": status,
            "generated_at": generated_at or _utc_now(),
            "research_only": True,
            "summary": summary,
            "records": normalized[:250],
            "aggregations": aggregations,
            "warnings": list(dict.fromkeys(warnings)),
            "missing_fields": dict(missing_counter),
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def _report_subset(report: dict[str, Any], *, records: list[dict[str, Any]], aggregations: dict[str, Any]) -> dict[str, Any]:
    return serialize_value(
        {
            **report,
            "records": records,
            "aggregations": aggregations,
            "research_only": True,
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def get_score_calibration_summary(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    return build_score_calibration_report(db=db, current_user=current_user)


def get_score_calibration_buckets(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_score_calibration_report(db=db, current_user=current_user)
    section = report.get("aggregations", {}).get("score_bucket_separation", {})
    return _report_subset(report, records=section.get("items", []), aggregations={"score_bucket_separation": section})


def get_score_calibration_features(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_score_calibration_report(db=db, current_user=current_user)
    section = report.get("aggregations", {}).get("feature_attribution", {})
    return _report_subset(report, records=section.get("items", []), aggregations={"feature_attribution": section})


def get_score_calibration_regimes(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_score_calibration_report(db=db, current_user=current_user)
    records = report.get("aggregations", {}).get("regime_specific_lift", [])
    return _report_subset(report, records=records, aggregations={"regime_specific_lift": records})


def get_score_calibration_recommendations(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_score_calibration_report(db=db, current_user=current_user)
    records = report.get("aggregations", {}).get("recommendations", [])
    return _report_subset(report, records=records, aggregations={"recommendations": records})
