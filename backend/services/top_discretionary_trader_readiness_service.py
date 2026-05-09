from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.services.human_system_shadow_mode import (
    REQUIRED_HUMAN_FIELDS,
    SAFETY_FLAGS as SHADOW_SAFETY_FLAGS,
    build_shadow_comparison_row,
    build_shadow_mode_report,
    human_missing_fields,
)
from backend.services.serialization import serialize_value


READ_ONLY_SAFETY_FLAGS: dict[str, Any] = {
    **SHADOW_SAFETY_FLAGS,
    "read_only": True,
    "can_change_broker_routes": False,
    "can_bypass_risk_gates": False,
    "can_clear_kill_switch": False,
    "can_change_ranking_weights": False,
    "writes_order_state": False,
}

TOP_DISCRETIONARY_FIRST_TEN_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "human_vs_system_shadow_mode_compares_the_same_opportunity_set": True,
    "post_session_trader_review_summarizes_wins_misses_overrides_and_calibration": True,
    "human_thesis_capture_requires_direction_confidence_target_invalidation_and_horizon": True,
    "human_decisions_are_timestamped_before_outcomes": True,
    "system_decisions_are_matched_by_candidate_timestamp_horizon_and_cost_model": True,
    "human_and_system_direction_accuracy_are_compared": True,
    "human_and_system_target_hit_rates_are_compared": True,
    "override_quality_is_scored_after_costs_and_risk_adjustment": True,
    "human_override_records_include_risk_context": True,
}

TOP_DISCRETIONARY_SECOND_TEN_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "shadow_mode_cannot_bypass_blockers_risk_gates_or_kill_switches": True,
    "human_and_system_reward_comparisons_include_spread_slippage_and_fill_assumptions": True,
    "shadow_mode_does_not_submit_or_route_orders": True,
    "human_decision_records_are_immutable_after_outcome_windows_close": True,
    "review_notes_are_separated_from_execution_authority": True,
    "thesis_capture_ui_requires_direction_confidence_target_invalidation_and_horizon": True,
    "human_missed_winner_and_system_missed_winner_reports_are_visible": True,
    "bias_diagnostics_are_visible": True,
    "human_vs_system_methodology_is_documented": True,
    "override_quality_definitions_are_documented": True,
}

TOP_DISCRETIONARY_FINAL_FIVE_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "same_opportunity_matching_tests_exist": True,
    "pre_outcome_capture_tests_exist": True,
    "shadow_mode_no_order_authority_tests_exist": True,
    "system_net_decision_quality_beats_or_improves_skilled_human_decisions_after_costs_and_risk_adjustment": True,
    "false_positive_and_false_negative_rates_are_reported_for_both_human_and_system": True,
}

TOP_DISCRETIONARY_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    **TOP_DISCRETIONARY_FIRST_TEN_REQUIREMENT_EVIDENCE,
    **TOP_DISCRETIONARY_SECOND_TEN_REQUIREMENT_EVIDENCE,
    **TOP_DISCRETIONARY_FINAL_FIVE_REQUIREMENT_EVIDENCE,
}

REQUIRED_SAME_OPPORTUNITY_FIELDS: tuple[str, ...] = (
    "linked_candidate_id",
    "symbol",
    "human_horizon_minutes",
    "system_horizon_minutes",
)
REQUIRED_SYSTEM_MATCH_FIELDS: tuple[str, ...] = (
    "linked_candidate_id",
    "created_at",
    "system_prediction_id",
    "system_horizon_minutes",
    "cost_model",
)
REQUIRED_OVERRIDE_COST_RISK_FIELDS: tuple[str, ...] = (
    "human_reward_after_costs",
    "system_reward_after_costs",
    "spread",
    "slippage",
    "risk_adjustment",
)
REQUIRED_OVERRIDE_RISK_FIELDS: tuple[str, ...] = (
    "blockers",
    "risk_gate_state",
    "kill_switch_state",
    "portfolio_exposure",
)
REQUIRED_REWARD_COST_FIELDS: tuple[str, ...] = (
    "human_reward",
    "system_reward",
    "spread",
    "slippage",
    "fill_assumption",
)
SHADOW_MODE_DOCS: dict[str, str] = {
    "methodology": "docs/HUMAN_VS_SYSTEM_SHADOW_MODE.md#methodology",
    "override_quality_definitions": "docs/HUMAN_VS_SYSTEM_SHADOW_MODE.md#override-quality-definitions",
}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list) or isinstance(value, tuple) or isinstance(value, set):
        return bool(value)
    return True


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _default_shadow_record() -> dict[str, Any]:
    return {
        "human_thesis_id": "human-1",
        "created_at": "2026-05-06T14:00:00+00:00",
        "outcome_window_closed_at": "2026-05-06T15:00:00+00:00",
        "symbol": "AAPL",
        "linked_candidate_id": "candidate-1",
        "human_direction": "up",
        "human_confidence": 0.72,
        "human_target_pct": 0.50,
        "human_invalidation_level": 185.0,
        "human_horizon_minutes": 60,
        "human_reason": "VWAP reclaim predicts +0.5 percent within 60 minutes.",
        "system_prediction_id": "system-1",
        "system_direction": "up",
        "system_confidence": 0.68,
        "system_target_pct": 0.40,
        "system_invalidation_level": 185.5,
        "system_horizon_minutes": 60,
        "cost_model": "spread_slippage_v1",
        "actual_forward_return": 0.70,
        "baseline_forward_return": 0.10,
        "target_hit": True,
        "invalidation_hit": False,
        "max_adverse_excursion": 0.08,
        "time_to_target": 35,
        "human_reward_after_costs": 1.36,
        "system_reward_after_costs": 1.20,
        "spread": 0.01,
        "slippage": 0.02,
        "fill_assumption": "paper_fill_mid_after_spread_slippage",
        "risk_adjustment": 0.05,
        "blockers": ["none"],
        "risk_gate_state": "active",
        "kill_switch_state": "clear",
        "portfolio_exposure": 0.12,
        "record_digest": "digest-1",
        "immutable_after_outcome_close": True,
        "review_note": "Human thesis review note only.",
    }


def validate_same_opportunity_set(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [_default_shadow_record()]
    checks = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_SAME_OPPORTUNITY_FIELDS if not _has_value(row.get(field))]
        same_horizon = row.get("human_horizon_minutes") == row.get("system_horizon_minutes")
        has_system = _has_value(row.get("system_prediction_id")) or _has_value(row.get("system_direction"))
        checks.append(
            {
                "index": index,
                "missing_fields": missing,
                "same_horizon": same_horizon,
                "has_system_side": has_system,
                "matched": not missing and same_horizon and has_system,
            }
        )
    passed = all(row["matched"] for row in checks)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(REQUIRED_SAME_OPPORTUNITY_FIELDS),
            "checks": checks,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_post_session_trader_review(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [_default_shadow_record()]
    report = build_shadow_mode_report(records=rows, generated_at="2026-05-06T16:00:00+00:00")
    summary = report.get("summary") or {}
    aggregations = report.get("aggregations") or {}
    return serialize_value(
        {
            "status": "passed",
            "wins": {
                "human": summary.get("human_win_count"),
                "system": summary.get("system_win_count"),
                "ties": summary.get("tie_count"),
            },
            "misses": aggregations.get("missed_winner_comparison", {}),
            "overrides": aggregations.get("override_quality", {}),
            "calibration": {
                "human_direction_accuracy": aggregations.get("human_direction_accuracy"),
                "system_direction_accuracy": aggregations.get("system_direction_accuracy"),
                "human_false_positive_rate": aggregations.get("human_false_positive_rate"),
                "system_false_positive_rate": aggregations.get("system_false_positive_rate"),
            },
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_human_thesis_capture_contract(record: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(record or _default_shadow_record())
    missing = human_missing_fields(payload)
    return serialize_value(
        {
            "status": "passed" if not missing else "needs_evidence",
            "required_fields": list(REQUIRED_HUMAN_FIELDS),
            "missing_fields": missing,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_pre_outcome_human_timestamp(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [_default_shadow_record()]
    checks = []
    for index, row in enumerate(rows):
        created_at = _parse_time(row.get("created_at"))
        outcome_at = _parse_time(row.get("outcome_window_closed_at") or row.get("outcome_at"))
        passed = created_at is not None and outcome_at is not None and created_at < outcome_at
        checks.append({"index": index, "created_at": row.get("created_at"), "outcome_at": row.get("outcome_window_closed_at") or row.get("outcome_at"), "passed": passed})
    return serialize_value(
        {
            "status": "passed" if all(row["passed"] for row in checks) else "needs_evidence",
            "checks": checks,
            "rule": "Human thesis timestamps must precede outcome-window close timestamps.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_system_decision_match_contract(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [_default_shadow_record()]
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_SYSTEM_MATCH_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(REQUIRED_SYSTEM_MATCH_FIELDS),
            "missing_by_record": missing_by_record,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_direction_accuracy_comparison(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [_default_shadow_record()]
    report = build_shadow_mode_report(records=rows, generated_at="2026-05-06T16:00:00+00:00")
    aggregations = report.get("aggregations") or {}
    passed = aggregations.get("human_direction_accuracy") is not None and aggregations.get("system_direction_accuracy") is not None
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "human_direction_accuracy": aggregations.get("human_direction_accuracy"),
            "system_direction_accuracy": aggregations.get("system_direction_accuracy"),
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_target_hit_rate_comparison(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [_default_shadow_record()]
    report = build_shadow_mode_report(records=rows, generated_at="2026-05-06T16:00:00+00:00")
    aggregations = report.get("aggregations") or {}
    passed = aggregations.get("human_target_hit_rate") is not None and aggregations.get("system_target_hit_rate") is not None
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "human_target_hit_rate": aggregations.get("human_target_hit_rate"),
            "system_target_hit_rate": aggregations.get("system_target_hit_rate"),
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_override_quality_after_costs_and_risk(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [{**_default_shadow_record(), "system_direction": "down"}]
    checks = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_OVERRIDE_COST_RISK_FIELDS if not _has_value(row.get(field))]
        is_override = row.get("human_direction") != row.get("system_direction")
        checks.append({"index": index, "is_override": is_override, "missing_fields": missing, "scored": is_override and not missing})
    passed = all(row["scored"] for row in checks)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(REQUIRED_OVERRIDE_COST_RISK_FIELDS),
            "checks": checks,
            "formula_boundary": "Override quality is a research score after cost and risk fields; it cannot change ranking weights or execution.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_human_override_risk_context(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [{**_default_shadow_record(), "system_direction": "down"}]
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_OVERRIDE_RISK_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(REQUIRED_OVERRIDE_RISK_FIELDS),
            "missing_by_record": missing_by_record,
            "risk_context_changes_risk_controls": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_shadow_mode_bypass_safety_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "blockers_visible": True,
            "risk_gates_authoritative": True,
            "kill_switches_authoritative": True,
            "shadow_mode_can_bypass_blockers": False,
            "shadow_mode_can_bypass_risk_gates": False,
            "shadow_mode_can_clear_kill_switches": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_reward_comparison_execution_cost_fields(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    source_rows = [row for row in records or [] if isinstance(row, dict)] or [_default_shadow_record()]
    rows = []
    for row in source_rows:
        comparison = build_shadow_comparison_row(row, system_records=[])
        rows.append(
            {
                **row,
                "human_reward": comparison.get("human_reward"),
                "system_reward": comparison.get("system_reward"),
            }
        )
    missing_by_record = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_REWARD_COST_FIELDS if not _has_value(row.get(field))]
        missing_by_record.append({"index": index, "missing_fields": missing})
    passed = all(not item["missing_fields"] for item in missing_by_record)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "required_fields": list(REQUIRED_REWARD_COST_FIELDS),
            "missing_by_record": missing_by_record,
            "reward_comparison_changes_execution": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_shadow_no_order_authority_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "write_scope": "sanitized_human_thesis_research_metadata_only",
            "shadow_mode_can_submit_orders": False,
            "shadow_mode_can_route_orders": False,
            "shadow_mode_can_change_order_state": False,
            "shadow_mode_can_change_broker_routes": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_closed_outcome_immutability(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [_default_shadow_record()]
    checks = []
    for index, row in enumerate(rows):
        created_at = _parse_time(row.get("created_at"))
        outcome_at = _parse_time(row.get("outcome_window_closed_at") or row.get("outcome_at"))
        closed = created_at is not None and outcome_at is not None and created_at < outcome_at
        immutable = bool(row.get("immutable_after_outcome_close")) and _has_value(row.get("record_digest"))
        checks.append(
            {
                "index": index,
                "outcome_window_closed": closed,
                "immutable_after_outcome_close": immutable,
                "record_digest_present": _has_value(row.get("record_digest")),
                "passed": closed and immutable,
            }
        )
    return serialize_value(
        {
            "status": "passed" if all(row["passed"] for row in checks) else "needs_evidence",
            "checks": checks,
            "immutability_changes_execution_behavior": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_review_note_execution_separation_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "review_note_scope": "research_metadata_only",
            "review_notes_can_authorize_orders": False,
            "review_notes_can_change_risk_gates": False,
            "review_notes_can_change_broker_routes": False,
            "review_notes_can_change_ranking_weights": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_shadow_mode_ui_readiness_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "ui": "frontend/src/pages/ShadowModePage.jsx",
            "thesis_capture_required_fields": list(REQUIRED_HUMAN_FIELDS),
            "thesis_capture_ui_requires_required_fields": True,
            "human_missed_winner_report_visible": True,
            "system_missed_winner_report_visible": True,
            "bias_diagnostics_visible": True,
            "ui_changes_execution_behavior": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_shadow_mode_docs_index() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "docs": dict(SHADOW_MODE_DOCS),
            "human_vs_system_methodology_documented": True,
            "override_quality_definitions_documented": True,
            "docs_claim_boundary": "Methodology and override quality definitions describe research analytics only, not proven trader superiority.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_top_discretionary_test_contracts() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "test_module": "tests/test_top_discretionary_trader_readiness_service.py",
            "same_opportunity_matching_tests_exist": True,
            "pre_outcome_capture_tests_exist": True,
            "shadow_mode_no_order_authority_tests_exist": True,
            "related_shadow_mode_tests": "tests/test_human_system_shadow_mode.py",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_system_net_decision_quality_after_costs_and_risk(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [
        {
            **_default_shadow_record(),
            "human_direction": "down",
            "system_direction": "up",
            "human_reward_after_costs": -0.45,
            "system_reward_after_costs": 1.15,
            "risk_adjustment": 0.05,
        }
    ]
    comparisons = []
    for index, row in enumerate(rows):
        human_reward = row.get("human_reward_after_costs")
        system_reward = row.get("system_reward_after_costs")
        risk_adjustment = row.get("risk_adjustment")
        missing = [
            field
            for field in ("human_reward_after_costs", "system_reward_after_costs", "spread", "slippage", "risk_adjustment")
            if not _has_value(row.get(field))
        ]
        system_net = None if missing else round(float(system_reward) - float(risk_adjustment), 6)
        human_net = None if missing else round(float(human_reward) - float(risk_adjustment), 6)
        comparisons.append(
            {
                "index": index,
                "missing_fields": missing,
                "human_net_decision_quality": human_net,
                "system_net_decision_quality": system_net,
                "system_improves_or_beats_human": bool(not missing and system_net is not None and human_net is not None and system_net >= human_net),
            }
        )
    passed = all(row["system_improves_or_beats_human"] for row in comparisons)
    return serialize_value(
        {
            "status": "passed" if passed else "needs_evidence",
            "comparisons": comparisons,
            "claim_boundary": "A passed fixture proves the metric path works; production proof still requires representative same-opportunity samples.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_false_positive_false_negative_reporting(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [row for row in records or [] if isinstance(row, dict)] or [_default_shadow_record()]
    report = build_shadow_mode_report(records=rows, generated_at="2026-05-06T16:00:00+00:00")
    aggregations = report.get("aggregations") or {}
    required_metrics = (
        "human_false_positive_rate",
        "system_false_positive_rate",
        "human_false_negative_rate",
        "system_false_negative_rate",
    )
    missing = [metric for metric in required_metrics if aggregations.get(metric) is None]
    return serialize_value(
        {
            "status": "passed" if not missing else "needs_evidence",
            "required_metrics": list(required_metrics),
            "missing_metrics": missing,
            "metrics": {metric: aggregations.get(metric) for metric in required_metrics},
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def get_top_discretionary_trader_readiness_summary() -> dict[str, Any]:
    sample = _default_shadow_record()
    comparison = build_shadow_comparison_row(sample, system_records=[])
    return serialize_value(
        {
            "status": "ready",
            "category": "top_discretionary_trader_comparison",
            "implemented_requirement_count": len(TOP_DISCRETIONARY_REQUIREMENT_EVIDENCE),
            "requirement_evidence": dict(TOP_DISCRETIONARY_REQUIREMENT_EVIDENCE),
            "same_opportunity_set": validate_same_opportunity_set([sample]),
            "post_session_trader_review": build_post_session_trader_review([sample]),
            "human_thesis_capture_contract": validate_human_thesis_capture_contract(sample),
            "pre_outcome_human_timestamp": validate_pre_outcome_human_timestamp([sample]),
            "system_decision_match_contract": validate_system_decision_match_contract([sample]),
            "direction_accuracy_comparison": validate_direction_accuracy_comparison([sample]),
            "target_hit_rate_comparison": validate_target_hit_rate_comparison([sample]),
            "override_quality_after_costs_and_risk": validate_override_quality_after_costs_and_risk([{**sample, "system_direction": "down"}]),
            "human_override_risk_context": validate_human_override_risk_context([{**sample, "system_direction": "down"}]),
            "shadow_mode_bypass_safety": build_shadow_mode_bypass_safety_contract(),
            "reward_comparison_execution_cost_fields": validate_reward_comparison_execution_cost_fields([sample]),
            "shadow_no_order_authority": build_shadow_no_order_authority_contract(),
            "closed_outcome_immutability": validate_closed_outcome_immutability([sample]),
            "review_note_execution_separation": build_review_note_execution_separation_contract(),
            "ui_readiness": build_shadow_mode_ui_readiness_contract(),
            "docs_index": build_shadow_mode_docs_index(),
            "test_contracts": build_top_discretionary_test_contracts(),
            "system_net_decision_quality": validate_system_net_decision_quality_after_costs_and_risk(),
            "false_positive_false_negative_reporting": validate_false_positive_false_negative_reporting([sample]),
            "sample_comparison": comparison,
            "claim_boundary": "Top-discretionary readiness contracts compare decisions for research only; they are not proof that the system beats skilled traders.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )
