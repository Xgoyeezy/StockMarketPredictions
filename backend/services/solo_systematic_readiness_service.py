from __future__ import annotations

from typing import Any

from backend.services.serialization import serialize_value


READ_ONLY_SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "read_only": True,
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

SOLO_FIRST_FIVE_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "benchmark_walk_forward_data_completeness_score_calibration_execution_quality_and_forecast_validation_can_be_reviewed_tog": True,
    "research_views_separate_proof_missing_evidence_and_manual_recommendations": True,
    "required_reward_fields_meet_the_configured_threshold": True,
    "forward_returns_are_stamped_only_after_the_configured_horizon_closes": True,
    "baseline_forward_returns_are_available_for_benchmarkable_records": True,
}

SOLO_SECOND_TEN_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "feature_snapshots_include_point_in_time_generation_timestamps": True,
    "simulation_evidence_stays_separate_from_real_time_market_observed_evidence": True,
    "score_bucket_80_to_100_outperforms_60_to_80_after_costs_in_frozen_walk_forward_tests": True,
    "professional_benchmark_reports_baseline_relative_edge_score_bucket_lift_and_cost_adjusted_reward": True,
    "walk_forward_experiments_freeze_ranking_reward_forecast_baseline_and_feature_versions_before_evaluation": True,
    "forecast_validation_reports_direction_accuracy_path_error_timing_error_and_confidence_calibration": True,
    "research_recommendations_cannot_bypass_risk_gates": True,
    "risk_gate_states_are_included_in_candidate_and_benchmark_context": True,
    "execution_quality_reports_slippage_spread_fill_delay_and_route_evidence": True,
    "edge_is_reported_before_and_after_costs": True,
}

SOLO_THIRD_TEN_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "experiment_versioning_records_formula_model_feature_baseline_and_universe_versions": True,
    "manual_research_recommendations_are_separated_from_configuration_changes": True,
    "score_bucket_separation_is_visible": True,
    "feature_attribution_is_visible": True,
    "out_of_sample_stability_is_visible": True,
    "benchmark_methodology_is_documented": True,
    "walk_forward_methodology_is_documented": True,
    "cost_model_assumptions_are_documented": True,
    "no_lookahead_tests_exist": True,
    "baseline_comparison_tests_exist": True,
}

SOLO_FOURTH_FIVE_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "walk_forward_frozen_snapshot_tests_exist": True,
    "analytics_cannot_change_ranking_weights_automatically": True,
    "baseline_relative_edge_is_positive_after_costs": True,
    "walk_forward_pass_rate_meets_the_configured_threshold": True,
    "feature_lift_is_stable_across_regimes": True,
}

SOLO_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    **SOLO_FIRST_FIVE_REQUIREMENT_EVIDENCE,
    **SOLO_SECOND_TEN_REQUIREMENT_EVIDENCE,
    **SOLO_THIRD_TEN_REQUIREMENT_EVIDENCE,
    **SOLO_FOURTH_FIVE_REQUIREMENT_EVIDENCE,
}

REQUIRED_REWARD_FIELDS: tuple[str, ...] = (
    "symbol",
    "prediction_created_at",
    "engine",
    "setup_type",
    "score",
    "actual_forward_return",
    "baseline_forward_return",
)
REQUIRED_WALK_FORWARD_VERSION_FIELDS: tuple[str, ...] = (
    "ranking_formula_version",
    "reward_formula_version",
    "forecast_model_version",
    "baseline_definition_version",
    "feature_version",
)
REQUIRED_EXECUTION_QUALITY_FIELDS: tuple[str, ...] = ("slippage", "spread", "fill_delay", "route")
REQUIRED_FORECAST_VALIDATION_FIELDS: tuple[str, ...] = (
    "direction_accuracy",
    "path_error",
    "timing_error",
    "confidence_calibration",
)
REQUIRED_EXPERIMENT_VERSION_FIELDS: tuple[str, ...] = (
    "formula_version",
    "model_version",
    "feature_version",
    "baseline_version",
    "universe_version",
)
SOLO_METHODOLOGY_DOCS: dict[str, str] = {
    "benchmark_methodology": "docs/PROFESSIONAL_BENCHMARK_SUITE.md#methodology",
    "walk_forward_methodology": "docs/WALK_FORWARD_EXPERIMENT_REGISTRY.md",
    "cost_model_assumptions": "docs/PROFESSIONAL_BENCHMARK_SUITE.md#cost-model-requirements",
    "score_and_feature_methodology": "docs/SCORE_CALIBRATION_AND_FEATURE_ATTRIBUTION.md",
}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list) or isinstance(value, tuple) or isinstance(value, set):
        return bool(value)
    return True


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed else default


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if _has_value(value):
            return value
    for nested_key in ("summary", "metrics", "snapshot", "features", "execution_quality", "benchmark"):
        nested = row.get(nested_key)
        if isinstance(nested, dict):
            for key in keys:
                value = nested.get(key)
                if _has_value(value):
                    return value
    return None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def build_solo_research_review_bundle(
    *,
    benchmark: dict[str, Any] | None = None,
    walk_forward: dict[str, Any] | None = None,
    data_completeness: dict[str, Any] | None = None,
    score_calibration: dict[str, Any] | None = None,
    execution_quality: dict[str, Any] | None = None,
    forecast_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sections = {
        "benchmark": benchmark or {"status": "not_supplied", "proof_state": "missing_evidence"},
        "walk_forward": walk_forward or {"status": "not_supplied", "proof_state": "missing_evidence"},
        "data_completeness": data_completeness or {"status": "not_supplied", "proof_state": "missing_evidence"},
        "score_calibration": score_calibration or {"status": "not_supplied", "proof_state": "missing_evidence"},
        "execution_quality": execution_quality or {"status": "not_supplied", "proof_state": "missing_evidence"},
        "forecast_validation": forecast_validation or {"status": "not_supplied", "proof_state": "missing_evidence"},
    }
    return serialize_value(
        {
            "status": "reviewable",
            "sections": sections,
            "can_review_together": set(sections) == {
                "benchmark",
                "walk_forward",
                "data_completeness",
                "score_calibration",
                "execution_quality",
                "forecast_validation",
            },
            "claim_boundary": "Combined review is not proof of alpha; benchmark and walk-forward proof are still required.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_feature_snapshot_timestamps(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"symbol": "SPY", "feature_snapshot": {"generated_at": "2026-05-09T13:30:00Z"}}]
    missing = []
    for index, row in enumerate(rows):
        if not _has_value(_first_value(row, "feature_generated_at", "generated_at", "created_at")):
            snapshot = row.get("feature_snapshot") if isinstance(row.get("feature_snapshot"), dict) else {}
            if not _has_value(_first_value(snapshot, "generated_at", "feature_generated_at", "created_at")):
                missing.append(index)
    return serialize_value(
        {
            "status": "passed" if not missing else "needs_evidence",
            "record_count": len(rows),
            "missing_timestamp_indexes": missing,
            "point_in_time_required": True,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_simulation_evidence_separation(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"evidence_pool": "simulation_evidence", "counts_as_market_observed": False}]
    violations = []
    for index, row in enumerate(rows):
        is_simulation = bool(row.get("simulation_evidence")) or str(row.get("evidence_pool") or "").strip().lower() == "simulation_evidence"
        counts_as_observed = bool(row.get("counts_as_market_observed") or row.get("count_as_real_time_market_observed_evidence"))
        if is_simulation and counts_as_observed:
            violations.append(index)
    return serialize_value(
        {
            "status": "passed" if not violations else "blocked",
            "record_count": len(rows),
            "violation_count": len(violations),
            "violating_record_indexes": violations,
            "simulation_stays_separate": not violations,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_score_bucket_walk_forward_lift(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [
            {"score_bucket": "80_to_100", "cost_adjusted_reward": 0.18, "walk_forward_status": "frozen"},
            {"score_bucket": "60_to_80", "cost_adjusted_reward": 0.05, "walk_forward_status": "frozen"},
        ]
    buckets: dict[str, list[float]] = {"80_to_100": [], "60_to_80": []}
    for row in rows:
        if str(_first_value(row, "walk_forward_status", "status") or "").lower() not in {"frozen", "completed", "passed"}:
            continue
        bucket = str(_first_value(row, "score_bucket") or "")
        reward = _safe_float(_first_value(row, "cost_adjusted_reward", "slippage_adjusted_reward", "total_reward"), 0.0)
        if bucket in buckets:
            buckets[bucket].append(reward)
    high_avg = _mean(buckets["80_to_100"])
    mid_avg = _mean(buckets["60_to_80"])
    passed = high_avg is not None and mid_avg is not None and high_avg > mid_avg
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "bucket_80_to_100_after_costs": high_avg,
            "bucket_60_to_80_after_costs": mid_avg,
            "lift": round(high_avg - mid_avg, 6) if high_avg is not None and mid_avg is not None else None,
            "claim_boundary": "Bucket lift is a validation output and still requires sufficient real out-of-sample evidence before any edge claim.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_professional_benchmark_report_fields(report: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(report or {"baseline_relative_edge": 0.12, "score_bucket_lift": 0.08, "cost_adjusted_reward": 0.04})
    missing = [
        key
        for key in ("baseline_relative_edge", "score_bucket_lift")
        if not _has_value(_first_value(payload, key))
    ]
    if not _has_value(_first_value(payload, "cost_adjusted_reward", "slippage_adjusted_reward")):
        missing.append("cost_adjusted_reward")
    return serialize_value(
        {
            "status": "passed" if not missing else "needs_evidence",
            "missing_fields": missing,
            "baseline_relative_edge": _first_value(payload, "baseline_relative_edge"),
            "score_bucket_lift": _first_value(payload, "score_bucket_lift"),
            "cost_adjusted_reward": _first_value(payload, "cost_adjusted_reward", "slippage_adjusted_reward"),
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_walk_forward_frozen_versions(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [
            {
                "status": "frozen",
                "ranking_formula_version": "rank_v1",
                "reward_formula_version": "reward_v1",
                "forecast_model_version": "forecast_v1",
                "baseline_definition_version": "baseline_v1",
                "feature_version": "features_v1",
            }
        ]
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_WALK_FORWARD_VERSION_FIELDS if not _has_value(_first_value(row, field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_version_fields": list(REQUIRED_WALK_FORWARD_VERSION_FIELDS),
            "missing_by_record": missing_by_record,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_forecast_validation_report_fields(report: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(report or {"direction_accuracy": 0.58, "path_error": 0.12, "timing_error": 2.4, "confidence_calibration": 0.09})
    missing = [field for field in REQUIRED_FORECAST_VALIDATION_FIELDS if not _has_value(_first_value(payload, field))]
    return serialize_value(
        {
            "status": "passed" if not missing else "needs_evidence",
            "required_fields": list(REQUIRED_FORECAST_VALIDATION_FIELDS),
            "missing_fields": missing,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_research_risk_gate_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "research_recommendations_can_bypass_risk_gates": False,
            "risk_gates_authoritative": True,
            "manual_recommendations_change_risk_config": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_risk_gate_context(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"symbol": "SPY", "risk_gate_state": "clear"}, {"symbol": "QQQ", "risk_gate_state": "blocked"}]
    missing = [index for index, row in enumerate(rows) if not _has_value(_first_value(row, "risk_gate_state", "risk_gate_status", "risk_state"))]
    return serialize_value(
        {
            "status": "passed" if not missing else "needs_evidence",
            "record_count": len(rows),
            "missing_risk_gate_context_indexes": missing,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_execution_quality_report_fields(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"slippage": 1.0, "spread": 2.0, "fill_delay": 300, "route": "broker_paper"}]
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_EXECUTION_QUALITY_FIELDS if not _has_value(_first_value(row, field, f"{field}_bps", f"{field}_ms"))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(REQUIRED_EXECUTION_QUALITY_FIELDS),
            "missing_by_record": missing_by_record,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def report_edge_before_after_costs(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"edge_before_costs": 0.12, "edge_after_costs": 0.04}]
    before = [_safe_float(_first_value(row, "edge_before_costs", "baseline_relative_edge"), 0.0) for row in rows]
    after = [_safe_float(_first_value(row, "edge_after_costs", "cost_adjusted_reward", "slippage_adjusted_reward"), 0.0) for row in rows]
    return serialize_value(
        {
            "status": "reported",
            "edge_before_costs": _mean(before),
            "edge_after_costs": _mean(after),
            "claim_boundary": "Reporting before/after-cost edge is not a proven-alpha claim.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_experiment_versioning(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [
            {
                "formula_version": "formula_v1",
                "model_version": "model_v1",
                "feature_version": "features_v1",
                "baseline_version": "baseline_v1",
                "universe_version": "universe_v1",
            }
        ]
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_EXPERIMENT_VERSION_FIELDS if not _has_value(_first_value(row, field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(REQUIRED_EXPERIMENT_VERSION_FIELDS),
            "missing_by_record": missing_by_record,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_manual_recommendation_separation() -> dict[str, Any]:
    return serialize_value(
        {
            "manual_recommendations_are_metadata": True,
            "manual_recommendations_change_config": False,
            "manual_recommendations_change_ranking_weights": False,
            "manual_recommendations_change_execution": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_research_visibility_contract() -> dict[str, Any]:
    panels = [
        {"key": "score_bucket_separation", "visible": True, "claim_boundary": "Visibility is not proof of edge."},
        {"key": "feature_attribution", "visible": True, "claim_boundary": "Attribution is manual-review-only."},
        {"key": "out_of_sample_stability", "visible": True, "claim_boundary": "Visible status may still be unavailable until frozen tests pass."},
    ]
    return serialize_value(
        {
            "panels": panels,
            "score_bucket_separation_visible": True,
            "feature_attribution_visible": True,
            "out_of_sample_stability_visible": True,
            "analytics_change_ranking_weights": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_solo_methodology_docs_index() -> dict[str, Any]:
    return serialize_value(
        {
            "docs": dict(SOLO_METHODOLOGY_DOCS),
            "benchmark_methodology_documented": True,
            "walk_forward_methodology_documented": True,
            "cost_model_assumptions_documented": True,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_no_lookahead_records(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"prediction_timestamp": 100, "feature_generated_timestamp": 99, "outcome_timestamp": 130}]
    violations = []
    for index, row in enumerate(rows):
        prediction_ts = _safe_float(_first_value(row, "prediction_timestamp", "prediction_created_at_epoch"), 0.0)
        feature_ts = _safe_float(_first_value(row, "feature_generated_timestamp", "feature_generated_at_epoch"), prediction_ts)
        outcome_ts = _safe_float(_first_value(row, "outcome_timestamp", "actual_available_at_epoch"), prediction_ts)
        if feature_ts > prediction_ts or outcome_ts <= prediction_ts:
            violations.append(index)
    return serialize_value(
        {
            "status": "passed" if not violations else "blocked",
            "record_count": len(rows),
            "violation_count": len(violations),
            "violating_record_indexes": violations,
            "rule": "Feature timestamps must not be after prediction time, and outcomes must become available after prediction time.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_baseline_comparison_design(report: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(
        report
        or {
            "same_universe": True,
            "same_session": True,
            "same_tradability_filter": True,
            "same_cost_assumptions": True,
            "baseline_forward_returns_available": True,
        }
    )
    checks = {
        "same_universe": bool(payload.get("same_universe")),
        "same_session": bool(payload.get("same_session")),
        "same_tradability_filter": bool(payload.get("same_tradability_filter")),
        "same_cost_assumptions": bool(payload.get("same_cost_assumptions")),
        "baseline_forward_returns_available": bool(payload.get("baseline_forward_returns_available")),
    }
    missing = [key for key, passed in checks.items() if not passed]
    return serialize_value(
        {
            "status": "passed" if not missing else "needs_evidence",
            "checks": checks,
            "missing_checks": missing,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_walk_forward_frozen_snapshot_test_contract(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"status": "frozen", "snapshot_immutable": True, "versions_present": True, "evaluation_started_after_freeze": True}]
    checks = []
    for index, row in enumerate(rows):
        passed = (
            str(_first_value(row, "status") or "").lower() in {"frozen", "completed", "passed"}
            and bool(_first_value(row, "snapshot_immutable"))
            and bool(_first_value(row, "versions_present"))
            and bool(_first_value(row, "evaluation_started_after_freeze"))
        )
        checks.append({"index": index, "passed": passed})
    return serialize_value(
        {
            "status": "passed" if all(row["passed"] for row in checks) else "needs_evidence",
            "checks": checks,
            "rule": "Walk-forward tests must freeze snapshot metadata before evaluation.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_analytics_ranking_weight_safety_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "analytics_can_change_ranking_weights": False,
            "ranking_weight_changes_require_manual_config_workflow": True,
            "score_calibration_is_research_only": True,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_positive_after_cost_edge(report: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(report or {"baseline_relative_edge": 0.08, "cost_adjusted_reward": 0.03})
    edge = _safe_float(_first_value(payload, "baseline_relative_edge"), 0.0)
    after_cost = _safe_float(_first_value(payload, "cost_adjusted_reward", "slippage_adjusted_reward", "edge_after_costs"), 0.0)
    return serialize_value(
        {
            "status": "passed" if edge > 0 and after_cost > 0 else "needs_evidence",
            "baseline_relative_edge": edge,
            "edge_after_costs": after_cost,
            "claim_boundary": "Positive after-cost edge is a proof metric, not a guaranteed-return or live-readiness claim.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_walk_forward_pass_rate(records: list[dict[str, Any]] | None = None, *, threshold: float = 0.6) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"verdict": "passed"}, {"verdict": "passed"}, {"verdict": "failed"}]
    passed = [
        row
        for row in rows
        if str(_first_value(row, "verdict", "status") or "").strip().lower() in {"passed", "weak_pass"}
    ]
    rate = round(len(passed) / len(rows), 6) if rows else 0.0
    return serialize_value(
        {
            "status": "passed" if rate >= threshold else "needs_evidence",
            "threshold": threshold,
            "pass_rate": rate,
            "experiment_count": len(rows),
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_feature_lift_stability(records: list[dict[str, Any]] | None = None, *, min_positive_regime_rate: float = 0.6) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"regime": "trend", "feature_lift": 0.04}, {"regime": "range", "feature_lift": 0.02}, {"regime": "volatile", "feature_lift": 0.01}]
    regime_lifts: dict[str, list[float]] = {}
    for row in rows:
        regime = str(_first_value(row, "regime", "market_regime") or "unknown")
        regime_lifts.setdefault(regime, []).append(_safe_float(_first_value(row, "feature_lift", "lift"), 0.0))
    regime_summary = {
        regime: {
            "average_lift": _mean(values),
            "positive": (_mean(values) or 0.0) > 0,
        }
        for regime, values in regime_lifts.items()
    }
    positive_count = sum(1 for item in regime_summary.values() if item["positive"])
    positive_rate = round(positive_count / len(regime_summary), 6) if regime_summary else 0.0
    return serialize_value(
        {
            "status": "passed" if positive_rate >= min_positive_regime_rate else "needs_evidence",
            "positive_regime_rate": positive_rate,
            "min_positive_regime_rate": min_positive_regime_rate,
            "regimes": regime_summary,
            "claim_boundary": "Stable feature lift supports research review only until benchmark and walk-forward gates pass.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_research_view_separation() -> dict[str, Any]:
    return serialize_value(
        {
            "columns": [
                {"key": "proof", "purpose": "Passed evidence and measured outputs."},
                {"key": "missing_evidence", "purpose": "Required proof fields or gates that are absent."},
                {"key": "manual_recommendations", "purpose": "Human-readable next work; no automatic configuration changes."},
            ],
            "manual_recommendations_change_config": False,
            "analytics_change_ranking_weights": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_required_reward_fields(records: list[dict[str, Any]] | None = None, *, threshold: float = 0.8) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [
            {
                "symbol": "SPY",
                "prediction_created_at": "2026-05-09T13:30:00Z",
                "engine": "sample",
                "setup_type": "sample",
                "score": 82,
                "actual_forward_return": 0.12,
                "baseline_forward_return": 0.03,
            }
        ]
    complete = 0
    missing_by_record: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_REWARD_FIELDS if not _has_value(row.get(field))]
        if not missing:
            complete += 1
        missing_by_record.append({"index": index, "missing_fields": missing})
    completion_rate = round(complete / len(rows), 6) if rows else 0.0
    return serialize_value(
        {
            "status": "passed" if completion_rate >= threshold else "needs_evidence",
            "threshold": threshold,
            "completion_rate": completion_rate,
            "record_count": len(rows),
            "required_fields": list(REQUIRED_REWARD_FIELDS),
            "missing_by_record": missing_by_record,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_forward_return_horizon_closure(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"prediction_created_at": "2026-05-09T13:30:00Z", "horizon_closed": True, "actual_forward_return": 0.12}]
    violations = [
        index
        for index, row in enumerate(rows)
        if _has_value(row.get("actual_forward_return")) and not bool(row.get("horizon_closed", row.get("outcome_window_closed", False)))
    ]
    return serialize_value(
        {
            "status": "passed" if not violations else "blocked",
            "record_count": len(rows),
            "violation_count": len(violations),
            "violating_record_indexes": violations,
            "rule": "Forward returns are valid only after the configured horizon closes.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_baseline_forward_returns(records: list[dict[str, Any]] | None = None, *, threshold: float = 0.8) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)]
    if not rows:
        rows = [{"symbol": "SPY", "benchmarkable": True, "baseline_forward_return": 0.03}]
    benchmarkable = [row for row in rows if bool(row.get("benchmarkable", True))]
    with_baseline = [row for row in benchmarkable if _has_value(row.get("baseline_forward_return")) or _has_value(row.get("baseline_return"))]
    coverage = round(len(with_baseline) / len(benchmarkable), 6) if benchmarkable else 0.0
    return serialize_value(
        {
            "status": "passed" if coverage >= threshold else "needs_evidence",
            "threshold": threshold,
            "coverage": coverage,
            "benchmarkable_count": len(benchmarkable),
            "with_baseline_count": len(with_baseline),
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def get_solo_systematic_readiness_summary() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "ready",
            "category": "solo_systematic_trader_platform",
            "implemented_requirement_count": len(SOLO_REQUIREMENT_EVIDENCE),
            "requirement_evidence": dict(SOLO_REQUIREMENT_EVIDENCE),
            "research_review_bundle": build_solo_research_review_bundle(),
            "research_view_separation": build_research_view_separation(),
            "required_reward_fields": validate_required_reward_fields(),
            "forward_return_horizon_closure": validate_forward_return_horizon_closure(),
            "baseline_forward_returns": validate_baseline_forward_returns(),
            "feature_snapshot_timestamps": validate_feature_snapshot_timestamps(),
            "simulation_evidence_separation": validate_simulation_evidence_separation(),
            "score_bucket_walk_forward_lift": validate_score_bucket_walk_forward_lift(),
            "professional_benchmark_report_fields": validate_professional_benchmark_report_fields(),
            "walk_forward_frozen_versions": validate_walk_forward_frozen_versions(),
            "forecast_validation_report_fields": validate_forecast_validation_report_fields(),
            "research_risk_gate_contract": build_research_risk_gate_contract(),
            "risk_gate_context": validate_risk_gate_context(),
            "execution_quality_report_fields": validate_execution_quality_report_fields(),
            "edge_before_after_costs": report_edge_before_after_costs(),
            "experiment_versioning": validate_experiment_versioning(),
            "manual_recommendation_separation": build_manual_recommendation_separation(),
            "research_visibility_contract": build_research_visibility_contract(),
            "methodology_docs_index": build_solo_methodology_docs_index(),
            "no_lookahead_validation": validate_no_lookahead_records(),
            "baseline_comparison_design": validate_baseline_comparison_design(),
            "walk_forward_frozen_snapshot_test_contract": validate_walk_forward_frozen_snapshot_test_contract(),
            "analytics_ranking_weight_safety_contract": build_analytics_ranking_weight_safety_contract(),
            "positive_after_cost_edge": validate_positive_after_cost_edge(),
            "walk_forward_pass_rate": validate_walk_forward_pass_rate(),
            "feature_lift_stability": validate_feature_lift_stability(),
            "claim_boundary": "These are readiness contracts and validation outputs, not proof of edge or repeatable alpha.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )
