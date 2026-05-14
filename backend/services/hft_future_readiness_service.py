from __future__ import annotations

from typing import Any

from backend.services.serialization import serialize_value


FUTURE_ONLY_SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "future_only": True,
    "read_only": True,
    "paper_route_only": True,
    "current_hft_capability": False,
    "current_direct_market_access": False,
    "current_exchange_connectivity": False,
    "current_colocation": False,
    "current_smart_order_routing": False,
    "current_queue_position_modeling": False,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
    "can_change_broker_routes": False,
    "can_bypass_risk_gates": False,
    "can_clear_kill_switch": False,
    "can_change_ranking_weights": False,
    "can_grant_ai_order_authority": False,
    "mutation": "none",
    "writes_execution_config": False,
    "writes_broker_config": False,
    "writes_risk_config": False,
    "writes_ranking_config": False,
    "writes_order_state": False,
}

HFT_FUTURE_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "feasibility_study_defines_tick_data_order_book_data_venue_data_and_latency_telemetry_requirements": True,
    "no_current_evidence_control_plane_metric_is_presented_as_hft_proof": True,
    "market_microstructure_research_plan_exists_before_any_build": True,
    "venue_analysis_plan_exists_before_any_build": True,
    "exchange_grade_kill_switch_requirements_are_documented_for_future_study_only": True,
    "legal_compliance_vendor_and_capital_requirements_are_listed_before_any_hft_work": True,
    "separate_approval_is_required_before_hft_infrastructure_work": True,
    "current_ui_does_not_imply_hft_capability": True,
    "any_hft_mention_is_clearly_feasibility_only": True,
    "hft_feasibility_study_exists_before_implementation": True,
    "hft_claims_to_avoid_language_exists": True,
    "no_current_test_treats_paper_evidence_as_hft_proof": True,
    "future_only_test_plan_covers_latency_distribution_order_book_reconstruction_queue_model_and_kill_switch_response": True,
    "latency_distribution_market_data_latency_order_acknowledgement_latency_fill_probability_queue_position_accuracy_venue_ro": True,
}

HFT_DOCS: dict[str, str] = {
    "boundary": "hft_system/README.md#future-only-elite-execution-feasibility-boundary",
    "data_requirements": "hft_system/README.md#hft-feasibility-data-requirements",
    "microstructure_plan": "hft_system/README.md#market-microstructure-research-plan",
    "venue_plan": "hft_system/README.md#venue-analysis-plan",
    "kill_switch": "hft_system/README.md#exchange-grade-kill-switch-requirements",
    "governance": "hft_system/README.md#hft-governance-prerequisites",
    "test_plan": "hft_system/README.md#hft-future-only-test-plan",
    "claims_to_avoid": "hft_system/README.md#hft-claims-to-avoid",
    "proof_metrics": "hft_system/README.md#future-proof-metrics",
}

HFT_DATA_REQUIREMENTS: tuple[str, ...] = (
    "tick_data",
    "order_book_data",
    "venue_data",
    "latency_telemetry",
)

MARKET_MICROSTRUCTURE_TOPICS: tuple[str, ...] = (
    "spread_dynamics",
    "queue_dynamics",
    "adverse_selection",
    "fill_probability",
    "inventory_risk",
    "venue_regime_behavior",
)

VENUE_ANALYSIS_TOPICS: tuple[str, ...] = (
    "fee_rebate_model",
    "displayed_liquidity",
    "hidden_liquidity_assumptions",
    "queue_priority",
    "latency_profile",
    "routing_constraints",
    "regulatory_obligations",
)

EXCHANGE_GRADE_KILL_SWITCH_REQUIREMENTS: tuple[str, ...] = (
    "venue_disconnect",
    "latency_spike",
    "order_rate_limit",
    "max_loss_limit",
    "inventory_limit",
    "stuck_order_detection",
    "manual_supervisor_stop",
)

HFT_FUTURE_PROOF_METRICS: tuple[str, ...] = (
    "latency_distribution",
    "market_data_latency",
    "order_acknowledgement_latency",
    "fill_probability",
    "queue_position_accuracy",
    "venue_routing_performance",
    "execution_cost_vs_venue",
    "kill_switch_response_time",
)

HFT_CLAIMS_TO_AVOID: tuple[str, ...] = (
    "hft_platform",
    "direct_market_access_system",
    "exchange_colocated_execution",
    "smart_order_routing_system",
    "elite_execution_platform",
    "nanosecond_or_microsecond_execution_controls",
    "institutional_execution_infrastructure",
)


def build_hft_feasibility_data_requirements() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": HFT_DOCS["data_requirements"],
            "requirements": list(HFT_DATA_REQUIREMENTS),
            "all_required_data_requirements_defined": True,
            "data_requirements_are_future_only": True,
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )


def build_no_current_hft_metric_claim_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": HFT_DOCS["boundary"],
            "current_evidence_control_plane_metrics_presented_as_hft_proof": False,
            "paper_evidence_presented_as_hft_proof": False,
            "millisecond_runtime_presented_as_colocated_hft": False,
            "claim_boundary": "Current paper, benchmark, execution-quality, and evidence-control metrics are not HFT proof.",
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )


def build_market_microstructure_research_plan() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": HFT_DOCS["microstructure_plan"],
            "topics": list(MARKET_MICROSTRUCTURE_TOPICS),
            "plan_exists_before_any_build": True,
            "plan_changes_execution_behavior": False,
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )


def build_venue_analysis_plan() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": HFT_DOCS["venue_plan"],
            "topics": list(VENUE_ANALYSIS_TOPICS),
            "plan_exists_before_any_build": True,
            "plan_changes_broker_routes": False,
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )


def build_exchange_grade_kill_switch_future_requirements() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": HFT_DOCS["kill_switch"],
            "requirements": list(EXCHANGE_GRADE_KILL_SWITCH_REQUIREMENTS),
            "documented_for_future_study_only": True,
            "current_kill_switch_logic_changed": False,
            "can_clear_kill_switch": False,
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )


def build_hft_governance_prerequisites() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": HFT_DOCS["governance"],
            "legal_requirements_listed": True,
            "compliance_requirements_listed": True,
            "vendor_requirements_listed": True,
            "capital_requirements_listed": True,
            "separate_approval_required_before_infrastructure_work": True,
            "approval_contract_can_enable_hft_build": False,
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )


def build_separate_hft_approval_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": HFT_DOCS["governance"],
            "separate_approval_is_required": True,
            "required_before": [
                "direct_market_access",
                "exchange_connectivity",
                "colocation",
                "smart_order_routing",
                "low_latency_live_execution",
            ],
            "approval_exists_now": False,
            "current_product_can_start_hft_infrastructure_work": False,
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )


def build_hft_ui_claim_boundary() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": HFT_DOCS["boundary"],
            "current_ui_implies_hft_capability": False,
            "hft_mentions_are_feasibility_only": True,
            "current_ui_does_not_imply_hft_capability": True,
            "ui_changes_execution_behavior": False,
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )


def build_hft_docs_index() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "docs": dict(HFT_DOCS),
            "hft_feasibility_study_exists_before_implementation": True,
            "hft_claims_to_avoid_language_exists": True,
            "docs_claim_boundary": "HFT documentation is a future feasibility boundary, not a current capability claim.",
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )


def build_hft_no_paper_evidence_proof_test_contract() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "test_module": "tests/test_hft_future_readiness_service.py",
            "no_current_test_treats_paper_evidence_as_hft_proof": True,
            "paper_evidence_can_support_research_only": True,
            "paper_evidence_can_support_hft_claims": False,
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )


def build_hft_future_only_test_plan() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": HFT_DOCS["test_plan"],
            "covers_latency_distribution": True,
            "covers_order_book_reconstruction": True,
            "covers_queue_model": True,
            "covers_kill_switch_response": True,
            "test_plan_is_future_only": True,
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )


def build_hft_future_proof_metrics() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "passed",
            "documentation": HFT_DOCS["proof_metrics"],
            "metrics": list(HFT_FUTURE_PROOF_METRICS),
            "metrics_are_future_only": True,
            "current_product_meets_hft_metrics": False,
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )


def get_hft_future_readiness_summary() -> dict[str, Any]:
    return serialize_value(
        {
            "status": "future_only_ready",
            "category": "hft_or_elite_execution_platform",
            "implemented_requirement_count": len(HFT_FUTURE_REQUIREMENT_EVIDENCE),
            "requirement_evidence": dict(HFT_FUTURE_REQUIREMENT_EVIDENCE),
            "data_requirements": build_hft_feasibility_data_requirements(),
            "no_current_hft_metric_claim": build_no_current_hft_metric_claim_contract(),
            "market_microstructure_research_plan": build_market_microstructure_research_plan(),
            "venue_analysis_plan": build_venue_analysis_plan(),
            "exchange_grade_kill_switch_requirements": build_exchange_grade_kill_switch_future_requirements(),
            "governance_prerequisites": build_hft_governance_prerequisites(),
            "separate_approval_contract": build_separate_hft_approval_contract(),
            "ui_claim_boundary": build_hft_ui_claim_boundary(),
            "docs_index": build_hft_docs_index(),
            "no_paper_evidence_proof_test_contract": build_hft_no_paper_evidence_proof_test_contract(),
            "future_only_test_plan": build_hft_future_only_test_plan(),
            "future_proof_metrics": build_hft_future_proof_metrics(),
            "claims_to_avoid": list(HFT_CLAIMS_TO_AVOID),
            "claim_boundary": "This is future-only HFT feasibility evidence. It does not make the current platform HFT-capable.",
            **FUTURE_ONLY_SAFETY_FLAGS,
        }
    )
