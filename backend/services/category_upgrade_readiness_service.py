from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.services.serialization import serialize_value
from backend.services.project_finish_tracker import build_project_finish_tracker


SAFETY_FLAGS: dict[str, Any] = {
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

SAFETY_NOTES: tuple[str, ...] = (
    "Read-only readiness evaluator. Does not affect trading.",
    "Does not place orders.",
    "Does not change broker routes.",
    "Does not bypass risk gates.",
    "Does not clear kill switches.",
    "Does not change ranking weights automatically.",
    "Does not grant AI order authority.",
    "Does not merge simulation evidence into real-time market-observed evidence.",
)

GATE_ORDER: tuple[str, ...] = (
    "safety_intact",
    "data_complete_enough",
    "benchmark_available",
    "baselines_beaten",
    "walk_forward_passed",
    "execution_costs_handled",
    "risk_visibility_complete",
    "governance_complete",
    "external_review_complete",
)

GATE_LABELS: dict[str, str] = {
    "safety_intact": "Gate 1: Safety intact",
    "data_complete_enough": "Gate 2: Data complete enough",
    "benchmark_available": "Gate 3: Benchmark available",
    "baselines_beaten": "Gate 4: Baselines beaten",
    "walk_forward_passed": "Gate 5: Walk-forward passed",
    "execution_costs_handled": "Gate 6: Execution costs handled",
    "risk_visibility_complete": "Gate 7: Risk visibility complete",
    "governance_complete": "Gate 8: Governance complete",
    "external_review_complete": "Gate 9: External review complete where needed",
}

CATEGORY_DEFINITIONS: dict[str, dict[str, Any]] = {
    "retail_trading_bot": {
        "label": "Retail trading bot",
        "current_estimated_readiness": "9/10",
        "required_gates": ("safety_intact", "data_complete_enough"),
        "extra_proof_keys": (
            "retail_onboarding_complete",
            "no_trade_explanation_coverage_complete",
            "support_export_sanitized",
            "demo_evidence_separated",
        ),
        "target": "A non-technical user can start paper mode, understand every trade or no-trade decision, review missed opportunities, and export proof without touching code.",
    },
    "solo_systematic_trader_platform": {
        "label": "Solo systematic trader platform",
        "current_estimated_readiness": "7.5/10",
        "required_gates": (
            "safety_intact",
            "data_complete_enough",
            "benchmark_available",
            "baselines_beaten",
            "walk_forward_passed",
            "execution_costs_handled",
        ),
        "extra_proof_keys": ("score_bucket_separation_proven", "multi_regime_stability_proven"),
        "target": "Higher-ranked candidates outperform lower-ranked candidates after costs across frozen out-of-sample tests and multiple regimes.",
    },
    "small_prop_or_small_fund_research_stack": {
        "label": "Small prop shop or small fund research stack",
        "current_estimated_readiness": "6/10",
        "required_gates": (
            "safety_intact",
            "data_complete_enough",
            "benchmark_available",
            "baselines_beaten",
            "walk_forward_passed",
            "execution_costs_handled",
            "risk_visibility_complete",
            "governance_complete",
        ),
        "extra_proof_keys": ("strategy_approval_traceability_complete", "release_validation_complete"),
        "target": "A small fund can review a strategy, inspect evidence, approve or reject promotion, prove who changed what and when, and verify that risk controls stayed active.",
    },
    "top_discretionary_trader_comparison": {
        "label": "Top discretionary trader comparison",
        "current_estimated_readiness": "5/10",
        "required_gates": (
            "safety_intact",
            "data_complete_enough",
            "benchmark_available",
            "baselines_beaten",
            "walk_forward_passed",
            "execution_costs_handled",
        ),
        "extra_proof_keys": ("same_opportunity_shadow_mode_complete", "system_beats_or_improves_human_after_costs"),
        "target": "Across the same candidates, the system improves or beats a skilled trader's net decision quality after costs and risk adjustment.",
    },
    "institutional_quant_desk_or_enterprise_control_plane": {
        "label": "Institutional quant desk or enterprise control plane",
        "current_estimated_readiness": "3/10",
        "required_gates": GATE_ORDER,
        "extra_proof_keys": (
            "data_lineage_complete",
            "model_lineage_complete",
            "feature_lineage_complete",
            "environment_separation_verified",
            "permission_enforcement_complete",
            "incident_handling_complete",
            "firm_grade_reporting_sanitized",
        ),
        "target": "An institutional reviewer can inspect data lineage, model lineage, risk controls, approvals, evidence records, forecast records, reward outputs, and incident handling without verbal explanation.",
    },
    "hft_or_elite_execution_platform": {
        "label": "HFT or elite execution platform",
        "current_estimated_readiness": "2/10",
        "required_gates": (),
        "extra_proof_keys": (
            "separate_hft_infrastructure_thesis_approved",
            "direct_market_access_proven",
            "exchange_connectivity_proven",
            "colocation_proven",
            "order_book_reconstruction_proven",
            "queue_position_modeling_proven",
            "low_latency_controls_proven",
        ),
        "target": "The system can compete in latency-sensitive execution with professional market infrastructure.",
        "future_only": True,
    },
}

BUILD_STAGE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "verification_and_safety_audit",
        "label": "Verification and safety audit",
        "priority": "P0",
        "gate_keys": ("safety_intact",),
        "category_keys": tuple(CATEGORY_DEFINITIONS),
        "purpose": "Prove the platform remains paper-first and research-only where required.",
        "what_not_to_build_yet": "Do not add broker routes, live autonomy, order mutation, risk-gate bypasses, or automatic kill-switch clearing.",
    },
    {
        "key": "data_completeness_hardening",
        "label": "Data Completeness hardening",
        "priority": "P0",
        "gate_keys": ("data_complete_enough",),
        "category_keys": (
            "retail_trading_bot",
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
            "top_discretionary_trader_comparison",
            "institutional_quant_desk_or_enterprise_control_plane",
        ),
        "purpose": "Make benchmark, reward, forecast, and walk-forward evidence complete enough to trust.",
        "what_not_to_build_yet": "Do not count incomplete evidence or simulation evidence as readiness proof.",
    },
    {
        "key": "professional_benchmark_hardening",
        "label": "Professional Benchmark hardening",
        "priority": "P0",
        "gate_keys": ("benchmark_available", "baselines_beaten"),
        "category_keys": (
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
            "top_discretionary_trader_comparison",
            "institutional_quant_desk_or_enterprise_control_plane",
        ),
        "purpose": "Prove or disprove edge against same-window baselines after costs.",
        "what_not_to_build_yet": "Do not make edge, alpha, or performance claims before baselines are beaten.",
    },
    {
        "key": "walk_forward_maturity",
        "label": "Walk-Forward Experiment Registry maturity",
        "priority": "P0",
        "gate_keys": ("walk_forward_passed",),
        "category_keys": (
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
            "top_discretionary_trader_comparison",
            "institutional_quant_desk_or_enterprise_control_plane",
        ),
        "purpose": "Prove repeatability through frozen out-of-sample tests.",
        "what_not_to_build_yet": "Do not imply repeatability before frozen walk-forward tests pass.",
    },
    {
        "key": "score_calibration_feature_attribution",
        "label": "Score Calibration and Feature Attribution",
        "priority": "P1",
        "gate_keys": (),
        "extra_proof_keys": ("score_bucket_separation_proven", "multi_regime_stability_proven"),
        "category_keys": ("solo_systematic_trader_platform",),
        "purpose": "Show whether scores and features explain outcomes across regimes.",
        "what_not_to_build_yet": "Do not let score analytics change ranking weights automatically.",
    },
    {
        "key": "execution_quality_tca_maturity",
        "label": "Execution Quality and TCA maturity",
        "priority": "P1",
        "gate_keys": ("execution_costs_handled",),
        "category_keys": (
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
            "top_discretionary_trader_comparison",
            "institutional_quant_desk_or_enterprise_control_plane",
        ),
        "purpose": "Prove forecasts and candidates survive spread, slippage, delay, and fill risk.",
        "what_not_to_build_yet": "Do not add smart order routing or let execution analytics alter orders.",
    },
    {
        "key": "risk_gate_audit_trail_hardening",
        "label": "Risk Gate and Audit Trail hardening",
        "priority": "P1",
        "gate_keys": ("safety_intact",),
        "category_keys": tuple(CATEGORY_DEFINITIONS),
        "purpose": "Keep risk gates, kill switches, broker-route boundaries, audit history, and support exports reviewable before any expansion work.",
        "what_not_to_build_yet": "Do not let proof layers bypass risk controls, clear kill switches, expose secrets, or treat audit visibility as live-trading approval.",
    },
    {
        "key": "portfolio_risk_maturity",
        "label": "Portfolio Risk Intelligence maturity",
        "priority": "P1",
        "gate_keys": ("risk_visibility_complete",),
        "category_keys": (
            "small_prop_or_small_fund_research_stack",
            "institutional_quant_desk_or_enterprise_control_plane",
        ),
        "purpose": "Make strategy and candidate evidence visible at portfolio risk level.",
        "what_not_to_build_yet": "Do not let portfolio analytics change risk limits automatically.",
    },
    {
        "key": "human_system_shadow_maturity",
        "label": "Human vs System Shadow Mode maturity",
        "priority": "P2",
        "gate_keys": (),
        "extra_proof_keys": ("same_opportunity_shadow_mode_complete", "system_beats_or_improves_human_after_costs"),
        "category_keys": ("top_discretionary_trader_comparison",),
        "purpose": "Compare skilled human and system decisions on the same opportunity set.",
        "what_not_to_build_yet": "Do not let shadow-mode records place, route, or approve orders.",
    },
    {
        "key": "research_promotion_maturity",
        "label": "Research Promotion maturity",
        "priority": "P2",
        "gate_keys": ("governance_complete",),
        "category_keys": (
            "small_prop_or_small_fund_research_stack",
            "institutional_quant_desk_or_enterprise_control_plane",
        ),
        "purpose": "Organize evidence into manual promotion states without changing trading behavior.",
        "what_not_to_build_yet": "Do not let promotion status enable live trading or alter execution.",
    },
    {
        "key": "ai_committee_research_memos",
        "label": "AI Committee agents as research-only memos",
        "priority": "P2",
        "gate_keys": ("safety_intact", "data_complete_enough"),
        "category_keys": (
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
            "top_discretionary_trader_comparison",
        ),
        "purpose": "Keep AI analysis tied to evidence quality, dissent, and sanitized append-only research memos.",
        "what_not_to_build_yet": "Do not grant AI order authority, broker-route authority, risk-gate authority, live-trading approval, or automatic ranking-weight changes.",
    },
    {
        "key": "market_specialist_desk_registry",
        "label": "Market Specialist Desk registry",
        "priority": "future_only",
        "gate_keys": (),
        "category_keys": (
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
        ),
        "purpose": "Add market-context desks only after proof-first scoring shows they improve evidence quality rather than feature count.",
        "what_not_to_build_yet": "Do not build market desks before proving current desks; market desks are context engines, not order bots.",
    },
    {
        "key": "candidate_fusion_engine",
        "label": "Candidate Fusion Engine",
        "priority": "future_only",
        "gate_keys": (),
        "category_keys": (
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
        ),
        "purpose": "Combine market context and strategy logic into one evidence-backed candidate only after the current evidence chain is reliable.",
        "what_not_to_build_yet": "Do not fuse market context into candidates until risk gates remain authoritative and benchmark/walk-forward evidence is strong enough.",
    },
    {
        "key": "market_strategy_benchmark",
        "label": "Market x Strategy Benchmark",
        "priority": "future_only",
        "gate_keys": (),
        "category_keys": (
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
            "institutional_quant_desk_or_enterprise_control_plane",
        ),
        "purpose": "Measure edge by market context and strategy only after Professional Benchmark and Walk-Forward are mature.",
        "what_not_to_build_yet": "Do not claim market x strategy edge before current benchmark and walk-forward proof gates pass.",
    },
    {
        "key": "off_exchange_liquidity_dashboard",
        "label": "Off-Exchange Liquidity Dashboard",
        "priority": "future_only",
        "gate_keys": (),
        "category_keys": (
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
        ),
        "purpose": "Add passive off-exchange liquidity context only if it improves candidate, execution, benchmark, or reward evidence.",
        "what_not_to_build_yet": "Do not claim hidden-order detection, institutional intent, dark-pool prediction, trade triggers, route changes, or automatic ranking-weight changes.",
    },
    {
        "key": "broker_market_data_adapters",
        "label": "BrokerAdapter and MarketDataAdapter architecture",
        "priority": "future_only",
        "gate_keys": (),
        "category_keys": (
            "retail_trading_bot",
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
            "institutional_quant_desk_or_enterprise_control_plane",
        ),
        "purpose": "Move toward broker-neutral architecture only after proof shows a specific broker or data-provider bottleneck.",
        "what_not_to_build_yet": "Do not add broker routes, live execution, provider dependencies, or broker-replacement claims in the current proof-first lane.",
    },
    {
        "key": "capability_registry_route_eligibility",
        "label": "Capability Registry and Route Eligibility Engine",
        "priority": "future_only",
        "gate_keys": (),
        "category_keys": (
            "small_prop_or_small_fund_research_stack",
            "institutional_quant_desk_or_enterprise_control_plane",
        ),
        "purpose": "Describe broker, market, and route capabilities only after adapter work is justified and safety gates stay intact.",
        "what_not_to_build_yet": "Do not let eligibility metadata loosen broker routes, risk gates, or order submission behavior.",
    },
    {
        "key": "broker_simulator_adapter",
        "label": "Broker Simulator Adapter",
        "priority": "future_only",
        "gate_keys": (),
        "category_keys": ("retail_trading_bot", "solo_systematic_trader_platform"),
        "purpose": "Use simulator mode for future markets before paid or live provider dependencies.",
        "what_not_to_build_yet": "Do not present simulated fills as real-time market-observed evidence or live-trading proof.",
    },
    {
        "key": "etf_proxy_registry",
        "label": "ETF Proxy Registry",
        "priority": "future_only",
        "gate_keys": (),
        "category_keys": (
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
        ),
        "purpose": "Use ETF proxies as free-first market context before paying for futures, FX, crypto, or alternative data.",
        "what_not_to_build_yet": "Do not treat ETF proxies as direct market access or complete coverage for unsupported markets.",
    },
    {
        "key": "visual_strategy_evidence_builder",
        "label": "Visual Strategy Evidence Builder",
        "priority": "future_only",
        "gate_keys": (),
        "category_keys": ("retail_trading_bot", "solo_systematic_trader_platform"),
        "purpose": "Create visual evidence contracts only after current evidence contracts are mature enough to keep the builder proof-focused.",
        "what_not_to_build_yet": "Do not build a no-code trading bot or auto-trade visual signals.",
    },
    {
        "key": "pay_threshold_provider_roi_gates",
        "label": "Pay Threshold and Provider ROI Gates",
        "priority": "future_only",
        "gate_keys": (),
        "category_keys": (
            "retail_trading_bot",
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
        ),
        "purpose": "Pay for providers only when data, benchmark, execution, or walk-forward evidence proves a specific bottleneck and expected value clears the ROI threshold.",
        "what_not_to_build_yet": "Do not recommend paid providers because the product feels advanced; keep free-first until proof shows the need.",
    },
    {
        "key": "retail_onboarding_demo_mode",
        "label": "Retail onboarding and demo evidence mode",
        "priority": "P2",
        "gate_keys": (),
        "extra_proof_keys": (
            "retail_onboarding_complete",
            "no_trade_explanation_coverage_complete",
            "support_export_sanitized",
            "demo_evidence_separated",
        ),
        "category_keys": ("retail_trading_bot",),
        "purpose": "Make paper-mode operation understandable for non-technical users.",
        "what_not_to_build_yet": "Do not create live-money onboarding or mix demo evidence into observed evidence.",
    },
    {
        "key": "governance_rbac_registries",
        "label": "Governance, RBAC, registries, and approvals",
        "priority": "P3",
        "gate_keys": ("governance_complete",),
        "category_keys": (
            "small_prop_or_small_fund_research_stack",
            "institutional_quant_desk_or_enterprise_control_plane",
        ),
        "purpose": "Prove roles, approvals, registries, audit history, and change traceability.",
        "what_not_to_build_yet": "Do not let roles bypass broker controls, risk gates, or kill switches.",
    },
    {
        "key": "institutional_lineage_audit",
        "label": "Institutional lineage and audit hardening",
        "priority": "P4",
        "gate_keys": ("external_review_complete",),
        "extra_proof_keys": (
            "data_lineage_complete",
            "model_lineage_complete",
            "feature_lineage_complete",
            "environment_separation_verified",
            "permission_enforcement_complete",
            "incident_handling_complete",
            "firm_grade_reporting_sanitized",
        ),
        "category_keys": ("institutional_quant_desk_or_enterprise_control_plane",),
        "purpose": "Make lineage, permissions, environment separation, incidents, and firm-grade reporting reviewable.",
        "what_not_to_build_yet": "Do not claim institutional-grade or compliance-approved readiness before proof and external review.",
    },
    {
        "key": "cpp_core_accelerators",
        "label": "C++ Core Accelerators",
        "priority": "future_only",
        "gate_keys": (),
        "category_keys": (
            "solo_systematic_trader_platform",
            "small_prop_or_small_fund_research_stack",
            "institutional_quant_desk_or_enterprise_control_plane",
        ),
        "purpose": "Add C++ only after profiling proves a research-only bottleneck in batch metrics, replay, backtesting, or risk calculations.",
        "what_not_to_build_yet": "Do not rewrite the control plane, routes, broker orchestration, AI agents, docs, frontend, or risk authority in C++.",
    },
    {
        "key": "hft_feasibility_only",
        "label": "HFT feasibility study only",
        "priority": "future_only",
        "gate_keys": (),
        "extra_proof_keys": CATEGORY_DEFINITIONS["hft_or_elite_execution_platform"]["extra_proof_keys"],
        "category_keys": ("hft_or_elite_execution_platform",),
        "purpose": "Keep HFT separate from the current evidence platform until a future infrastructure thesis is proven.",
        "what_not_to_build_yet": "Do not build DMA, exchange connectivity, colocation, smart routing, or low-latency live execution in the current product lane.",
    },
)

PROOF_CHAIN_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "sequence": 1,
        "stage_key": "verification_and_safety_audit",
        "gate_key": "safety_intact",
        "label": "Verification and safety audit",
        "proof_boundary": "Safety must remain intact before any rating review.",
        "safe_next_action": "Verify no live autonomy, no AI order authority, no broker-route mutation, and no risk-gate bypass.",
        "claim_boundary": "Only paper-first research and evidence-platform language is allowed.",
    },
    {
        "sequence": 2,
        "stage_key": "data_completeness_hardening",
        "gate_key": "data_complete_enough",
        "label": "Data Completeness hardening",
        "proof_boundary": "Benchmark and reward evidence must have complete, rewardable fields before readiness claims.",
        "safe_next_action": "Fix missing forward returns, baselines, forecast actuals, slippage, regime labels, and required reward fields.",
        "claim_boundary": "Do not treat incomplete evidence or simulation evidence as proof.",
    },
    {
        "sequence": 3,
        "stage_key": "professional_benchmark_hardening",
        "gate_key": "benchmark_available",
        "label": "Professional Benchmark availability",
        "proof_boundary": "Benchmark reports must include enough rewardable candidates to support review.",
        "safe_next_action": "Run or inspect Professional Benchmark Suite and identify rewardable evidence coverage.",
        "claim_boundary": "Do not claim edge from an unavailable or insufficient benchmark.",
    },
    {
        "sequence": 4,
        "stage_key": "professional_benchmark_hardening",
        "gate_key": "baselines_beaten",
        "label": "Baseline-relative edge",
        "proof_boundary": "Same-window baselines must be beaten after costs before edge language is considered.",
        "safe_next_action": "Compare score buckets and strategies against baselines with slippage-adjusted results.",
        "claim_boundary": "Do not claim alpha, proven edge, or performance until baselines are beaten and reviewed.",
    },
    {
        "sequence": 5,
        "stage_key": "walk_forward_maturity",
        "gate_key": "walk_forward_passed",
        "label": "Walk-Forward repeatability",
        "proof_boundary": "Frozen out-of-sample tests must pass before repeatability language is considered.",
        "safe_next_action": "Freeze experiment versions, run out-of-sample windows, and review multi-regime stability.",
        "claim_boundary": "Do not claim repeatability until walk-forward proof passes.",
    },
    {
        "sequence": 6,
        "stage_key": "execution_quality_tca_maturity",
        "gate_key": "execution_costs_handled",
        "label": "Execution Quality and TCA",
        "proof_boundary": "Candidate quality must survive spread, slippage, delay, and fill risk.",
        "safe_next_action": "Review paper fills, slippage-adjusted reward, alpha decay, and setup-level execution drag.",
        "claim_boundary": "Do not change broker routes or order behavior from execution analytics.",
    },
    {
        "sequence": 7,
        "stage_key": "portfolio_risk_maturity",
        "gate_key": "risk_visibility_complete",
        "label": "Portfolio Risk visibility",
        "proof_boundary": "Portfolio exposure, concentration, correlation, drawdown, and stress context must be reviewable.",
        "safe_next_action": "Complete portfolio risk coverage and keep risk analytics advisory-only.",
        "claim_boundary": "Do not let portfolio analytics change risk limits automatically.",
    },
    {
        "sequence": 8,
        "stage_key": "research_promotion_maturity",
        "gate_key": "governance_complete",
        "label": "Research Promotion and governance",
        "proof_boundary": "Promotion, approvals, RBAC, registries, and audit trails must be reviewable before firm-readiness claims.",
        "safe_next_action": "Harden manual approval evidence, model and strategy registries, release validation, and rollback records.",
        "claim_boundary": "Promotion status remains research metadata and must not change execution behavior.",
    },
    {
        "sequence": 9,
        "stage_key": "institutional_lineage_audit",
        "gate_key": "external_review_complete",
        "label": "External review and institutional proof",
        "proof_boundary": "External security, legal, compliance, lineage, permission, and environment reviews are required for institutional claims.",
        "safe_next_action": "Prepare sanitized review artifacts for future external security, legal, compliance, and data-lineage review.",
        "claim_boundary": "Do not claim institutional-grade or compliance-approved readiness without external proof.",
    },
)

PRIORITY_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "future_only": 9}

FORBIDDEN_SAFETY_KEYS: tuple[str, ...] = (
    "autonomous_live_money_orders_enabled",
    "ai_order_authority",
    "risk_gate_bypass_enabled",
    "kill_switch_bypass_enabled",
    "automatic_broker_route_loosening",
    "automatic_ranking_weight_changes",
    "simulation_merged_with_observed",
    "live_money_autonomy_enabled",
    "broker_routes_changed_by_analytics",
    "support_export_leaks_sensitive_data",
)

SENSITIVE_EXPORT_KEY_MARKERS: tuple[str, ...] = (
    "secret",
    "token",
    "password",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "authorization",
    "auth_header",
    "account_id",
    "account_number",
    "broker_record",
    "raw_log",
    "raw_broker",
    "local_path",
    "environment_value",
    "env_value",
    "database_file",
    "db_file",
)

LOCAL_PATH_RE = re.compile(r"(?i)(?:\b[a-z]:[\\/]|\\\\|file://|[\\/](?:users|home)[\\/])")
CHECKBOX_RE = re.compile(r"^- \[(?P<checked>[ xX])\] (?P<description>.+)$")
DEFAULT_CATEGORY_UPGRADE_EXPORT_DIR = Path("runtime-exports") / "category-upgrade-readiness"
CATEGORY_UPGRADE_EXPORT_FILENAME = "category_upgrade_readiness_report.json"
DEFAULT_ACCEPTANCE_CHECKLIST_PATH = Path("docs") / "TEN_OUT_OF_TEN_ACCEPTANCE_CHECKLIST.md"

CATEGORY_HEADING_MATCHES: tuple[tuple[str, str], ...] = (
    ("Retail Trading Bot", "retail_trading_bot"),
    ("Solo Systematic Trader Platform", "solo_systematic_trader_platform"),
    ("Small Prop Shop Or Small Fund Research Stack", "small_prop_or_small_fund_research_stack"),
    ("Top Discretionary Trader Comparison", "top_discretionary_trader_comparison"),
    ("Institutional Quant Desk Or Enterprise Control Plane", "institutional_quant_desk_or_enterprise_control_plane"),
    ("HFT Or Elite Execution Platform", "hft_or_elite_execution_platform"),
)

REQUIREMENT_PROOF_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    (("guided onboarding", "first-session", "onboarding completion"), (), ("retail_onboarding_complete",)),
    (("paper-mode health", "paper readiness", "paper-ready state"), ("safety_intact",), ("retail_onboarding_complete",)),
    (("no-trade",), ("safety_intact",), ("no_trade_explanation_coverage_complete",)),
    (("demo evidence", "synthetic/sample"), (), ("demo_evidence_separated",)),
    (("support export", "support bundle"), (), ("support_export_sanitized",)),
    (("score bucket", "higher-ranked", "feature lift"), ("baselines_beaten",), ("score_bucket_separation_proven",)),
    (("regime", "out-of-sample stability"), ("walk_forward_passed",), ("multi_regime_stability_proven",)),
    (("walk-forward", "frozen snapshot", "frozen out-of-sample"), ("walk_forward_passed",), ()),
    (("benchmark", "baseline", "baseline-relative"), ("benchmark_available", "baselines_beaten"), ()),
    (("slippage", "spread", "fill delay", "after costs", "cost-adjusted", "transaction cost"), ("execution_costs_handled",), ()),
    (("data completeness", "forward returns", "reward fields", "forecast actuals", "required reward fields"), ("data_complete_enough",), ()),
    (("strategy approval traceability", "approval records", "who changed what", "approve, reject, hold, and rollback"), ("governance_complete",), ("strategy_approval_traceability_complete",)),
    (("release validation", "rollback controls"), ("governance_complete",), ("release_validation_complete",)),
    (("same opportunity", "same-opportunity", "human thesis", "human decisions", "system decisions", "shadow mode"), ("safety_intact",), ("same_opportunity_shadow_mode_complete",)),
    (("system net decision quality", "beats or improves skilled human", "override quality"), ("execution_costs_handled",), ("system_beats_or_improves_human_after_costs",)),
    (("portfolio risk", "factor", "liquidity", "concentration", "drawdown", "stress"), ("risk_visibility_complete",), ()),
    (("rbac", "role-based access", "operator, researcher", "roles are defined"), ("governance_complete",), ("permission_enforcement_complete",)),
    (("permission enforcement", "permission review"), ("governance_complete",), ("permission_enforcement_complete",)),
    (("data lineage", "point-in-time", "survivorship-free", "corporate actions", "symbol changes", "data vendor provenance"), ("external_review_complete",), ("data_lineage_complete",)),
    (("model lineage", "model registry"), ("external_review_complete",), ("model_lineage_complete",)),
    (("feature lineage", "feature registry", "feature generation timestamps"), ("external_review_complete",), ("feature_lineage_complete",)),
    (("environment separation",), ("external_review_complete",), ("environment_separation_verified",)),
    (("incident",), ("external_review_complete",), ("incident_handling_complete",)),
    (("firm-grade", "sanitized and reproducible"), ("external_review_complete",), ("firm_grade_reporting_sanitized",)),
    (("direct market access", "colocation", "smart routing", "queue modeling"), (), ("direct_market_access_proven", "colocation_proven", "queue_position_modeling_proven")),
    (("order book",), (), ("order_book_reconstruction_proven",)),
    (("low-latency", "latency", "market microstructure", "venue analysis"), (), ("low_latency_controls_proven", "separate_hft_infrastructure_thesis_approved")),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _summary(value: Any) -> dict[str, Any]:
    payload = _as_dict(value)
    return _as_dict(payload.get("summary"))


def _records(value: Any) -> list[dict[str, Any]]:
    items = _as_dict(value).get("records")
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict)]


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    return parsed


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "ready", "passed", "pass", "complete"}:
            return True
        if cleaned in {"0", "false", "no", "blocked", "failed", "fail", "missing"}:
            return False
    return bool(value)


def _status_rank(status: str) -> int:
    return {"passed": 3, "partial": 2, "missing": 1, "blocked": 0}.get(status, 1)


def _readiness_number(value: Any) -> float:
    text = str(value or "").strip().split("/", 1)[0]
    return _safe_float(text, 0.0) or 0.0


def _progress_percent(passed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((passed / total) * 100.0, 1)


def _safe_export_stamp(generated_at: str | None = None) -> str:
    raw = generated_at or _utc_now()
    return re.sub(r"[^0-9A-Za-z_-]+", "-", raw).strip("-")[:40] or "latest"


def _is_sensitive_export_key(key: str) -> bool:
    normalized = key.lower()
    return any(marker in normalized for marker in SENSITIVE_EXPORT_KEY_MARKERS)


def _looks_like_local_path(value: str) -> bool:
    return bool(LOCAL_PATH_RE.search(value.strip()))


def sanitize_category_upgrade_export_value(value: Any, *, key: str = "") -> Any:
    if _is_sensitive_export_key(key):
        return "[redacted]"
    serialized = serialize_value(value)
    if isinstance(serialized, dict):
        return {str(inner_key): sanitize_category_upgrade_export_value(inner_value, key=str(inner_key)) for inner_key, inner_value in serialized.items()}
    if isinstance(serialized, list):
        return [sanitize_category_upgrade_export_value(item, key=key) for item in serialized]
    if isinstance(serialized, str):
        cleaned = serialized.strip()
        if _looks_like_local_path(cleaned):
            return "[local_path_redacted]"
        return cleaned
    return serialized


def _category_key_from_heading(heading: str) -> str | None:
    for label, key in CATEGORY_HEADING_MATCHES:
        if label.lower() in heading.lower():
            return key
    return None


def _slugify_requirement(text: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", text.strip().lower()).strip("_")
    return slug[:120] or "requirement"


def load_acceptance_checklist_requirements(checklist_path: Path | str | None = None) -> list[dict[str, Any]]:
    path = Path(checklist_path) if checklist_path is not None else DEFAULT_ACCEPTANCE_CHECKLIST_PATH
    if not path.exists():
        return []
    requirements: list[dict[str, Any]] = []
    current_category: str | None = None
    current_group = "general"
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current_category = _category_key_from_heading(line[3:])
            current_group = "general"
            continue
        if current_category and line.endswith("readiness:"):
            current_group = _slugify_requirement(line[:-1])
            continue
        match = CHECKBOX_RE.match(line)
        if not match or not current_category:
            continue
        description = match.group("description").strip()
        requirements.append(
            {
                "key": f"{current_category}.{current_group}.{_slugify_requirement(description)}",
                "category_key": current_category,
                "group": current_group,
                "description": description,
                "source_doc": path.name if checklist_path is not None else str(DEFAULT_ACCEPTANCE_CHECKLIST_PATH.as_posix()),
                "checked_in_doc": match.group("checked").lower() == "x",
            }
        )
    return requirements


def _rule_matches(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _requirement_dependencies(requirement: dict[str, Any]) -> tuple[list[str], list[str]]:
    text = f"{requirement.get('description', '')} {requirement.get('group', '')} {requirement.get('category_key', '')}".lower()
    gate_keys: list[str] = []
    proof_keys: list[str] = []
    for phrases, gates, proofs in REQUIREMENT_PROOF_RULES:
        if _rule_matches(text, phrases):
            gate_keys.extend(gate for gate in gates if gate not in gate_keys)
            proof_keys.extend(proof for proof in proofs if proof not in proof_keys)
    category_key = str(requirement.get("category_key") or "")
    group = str(requirement.get("group") or "")
    if group == "data_readiness" and category_key != "hft_or_elite_execution_platform":
        gate_keys.append("data_complete_enough") if "data_complete_enough" not in gate_keys else None
    if group == "risk_readiness" and category_key not in {"retail_trading_bot", "hft_or_elite_execution_platform"}:
        gate_keys.append("risk_visibility_complete") if "risk_visibility_complete" not in gate_keys else None
    if group == "execution_readiness" and category_key not in {"retail_trading_bot", "hft_or_elite_execution_platform"}:
        gate_keys.append("execution_costs_handled") if "execution_costs_handled" not in gate_keys else None
    if group == "governance_readiness" and category_key in {"small_prop_or_small_fund_research_stack", "institutional_quant_desk_or_enterprise_control_plane"}:
        gate_keys.append("governance_complete") if "governance_complete" not in gate_keys else None
    return gate_keys, proof_keys


def collect_implemented_requirement_evidence() -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    try:
        from backend.services.retail_paper_operator_readiness_service import RETAIL_REQUIREMENT_EVIDENCE

        evidence.update(RETAIL_REQUIREMENT_EVIDENCE)
    except Exception:
        pass
    try:
        from backend.services.solo_systematic_readiness_service import SOLO_REQUIREMENT_EVIDENCE

        evidence.update(SOLO_REQUIREMENT_EVIDENCE)
    except Exception:
        pass
    try:
        from backend.services.small_fund_research_stack_readiness_service import SMALL_FUND_REQUIREMENT_EVIDENCE

        evidence.update(SMALL_FUND_REQUIREMENT_EVIDENCE)
    except Exception:
        pass
    try:
        from backend.services.top_discretionary_trader_readiness_service import TOP_DISCRETIONARY_REQUIREMENT_EVIDENCE

        evidence.update(TOP_DISCRETIONARY_REQUIREMENT_EVIDENCE)
    except Exception:
        pass
    try:
        from backend.services.institutional_quant_readiness_service import INSTITUTIONAL_REQUIREMENT_EVIDENCE

        evidence.update(INSTITUTIONAL_REQUIREMENT_EVIDENCE)
    except Exception:
        pass
    try:
        from backend.services.hft_future_readiness_service import HFT_FUTURE_REQUIREMENT_EVIDENCE

        evidence.update(HFT_FUTURE_REQUIREMENT_EVIDENCE)
    except Exception:
        pass
    return evidence


def build_documented_scope_coverage(
    *,
    gates: dict[str, dict[str, Any]],
    categories: list[dict[str, Any]],
    category_proof: dict[str, Any] | None = None,
    hft_thesis: dict[str, Any] | None = None,
    requirement_evidence: dict[str, Any] | None = None,
    checklist_path: Path | str | None = None,
) -> dict[str, Any]:
    requirements = load_acceptance_checklist_requirements(checklist_path)
    category_statuses = {str(row.get("key")): str(row.get("status")) for row in categories}
    proof_sources = {
        **collect_implemented_requirement_evidence(),
        **_as_dict(category_proof),
        **_as_dict(hft_thesis),
        **_as_dict(requirement_evidence),
    }
    rows: list[dict[str, Any]] = []
    for requirement in requirements:
        gate_keys, proof_keys = _requirement_dependencies(requirement)
        requirement_slug = _slugify_requirement(str(requirement.get("description") or ""))
        evidence_override = _safe_bool(proof_sources.get(str(requirement.get("key"))), False) or _safe_bool(proof_sources.get(requirement_slug), False)
        proof_statuses = {key: _safe_bool(proof_sources.get(key), False) for key in proof_keys}
        gate_statuses = {key: _as_dict(gates.get(key)).get("status", "missing") for key in gate_keys}
        gates_passed = all(status == "passed" for status in gate_statuses.values())
        proof_passed = all(proof_statuses.values()) if proof_statuses else False
        hft_future_policy = (
            requirement.get("category_key") == "hft_or_elite_execution_platform"
            and category_statuses.get("hft_or_elite_execution_platform") == "future_only"
            and _rule_matches(str(requirement.get("description") or "").lower(), ("future only", "avoids hft", "not claimed", "separate future product thesis", "current risk gates remain unchanged"))
        )
        dependencies_present = bool(gate_statuses or proof_statuses)
        dependencies_passed = dependencies_present and (not gate_statuses or gates_passed) and (not proof_statuses or proof_passed)
        if evidence_override or dependencies_passed or hft_future_policy:
            status = "complete"
        elif any(status == "blocked" for status in gate_statuses.values()):
            status = "blocked"
        elif gate_statuses or proof_statuses:
            status = "missing_evidence"
        else:
            status = "not_mapped"
        rows.append(
            {
                **requirement,
                "status": status,
                "gate_statuses": gate_statuses,
                "proof_statuses": proof_statuses,
                "completion_boundary": "Complete only when backed by evidence, tests, report output, or a passed proof gate.",
            }
        )

    by_category: dict[str, dict[str, Any]] = {}
    for row in rows:
        category_key = str(row.get("category_key"))
        summary = by_category.setdefault(category_key, {"category_key": category_key, "total": 0, "complete": 0, "blocked": 0, "missing": 0})
        summary["total"] += 1
        if row["status"] == "complete":
            summary["complete"] += 1
        elif row["status"] == "blocked":
            summary["blocked"] += 1
        else:
            summary["missing"] += 1
    complete_count = sum(1 for row in rows if row["status"] == "complete")
    blocked_count = sum(1 for row in rows if row["status"] == "blocked")
    all_added = bool(rows) and complete_count == len(rows)
    return serialize_value(
        {
            "source_doc": str((Path(checklist_path).name if checklist_path is not None else DEFAULT_ACCEPTANCE_CHECKLIST_PATH.as_posix())),
            "requirement_count": len(rows),
            "complete_count": complete_count,
            "blocked_count": blocked_count,
            "missing_count": len(rows) - complete_count - blocked_count,
            "all_documented_scope_added": all_added,
            "status": "complete" if all_added else "incomplete",
            "not_done_message": "" if all_added else "Not everything in the 10/10 docs is added yet; incomplete requirements still need implementation evidence or proof gates.",
            "by_category": list(by_category.values()),
            "requirements": rows,
        }
    )


def _gate(
    key: str,
    *,
    status: str,
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
    claims_allowed: list[str] | None = None,
    claims_disallowed: list[str] | None = None,
) -> dict[str, Any]:
    normalized = status if status in {"passed", "partial", "missing", "blocked"} else "missing"
    return {
        "key": key,
        "label": GATE_LABELS[key],
        "status": normalized,
        "passed": normalized == "passed",
        "blocking": normalized == "blocked",
        "blockers": blockers or [],
        "warnings": warnings or [],
        "evidence": evidence or {},
        "claims_allowed": claims_allowed or [],
        "claims_disallowed": claims_disallowed
        or [
            "proven_alpha",
            "investor_performance_claims",
            "live_trading_ready",
            "autonomous_money_manager",
            "institutional_grade_without_review",
            "hft_platform",
        ],
    }


def _warning_or_missing(has_input: bool, warning: str) -> tuple[str, list[str]]:
    return ("partial", [warning]) if has_input else ("missing", [warning])


def evaluate_safety_gate(safety_state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = _as_dict(safety_state)
    if not state:
        return _gate(
            "safety_intact",
            status="missing",
            warnings=["Safety verification evidence has not been supplied."],
            claims_allowed=["planning_only"],
        )

    blockers = [f"{key} is true" for key in FORBIDDEN_SAFETY_KEYS if _safe_bool(state.get(key), False)]
    explicit_violations = [str(item) for item in state.get("violations", []) if str(item).strip()] if isinstance(state.get("violations"), list) else []
    blockers.extend(explicit_violations)

    required_true = (
        "paper_first_boundary_preserved",
        "alpaca_paper_only_unattended",
        "reward_forecast_research_only",
        "risk_gates_authoritative",
        "broker_routes_unchanged",
        "ai_has_no_order_authority",
    )
    missing = [key for key in required_true if not _safe_bool(state.get(key), False)]
    if blockers:
        return _gate("safety_intact", status="blocked", blockers=blockers, evidence={"missing_required_true": missing})
    if missing:
        return _gate(
            "safety_intact",
            status="partial",
            warnings=[f"Safety evidence missing or false: {', '.join(missing)}."],
            evidence={"missing_required_true": missing},
            claims_allowed=["paper_first_research_platform"],
        )
    return _gate(
        "safety_intact",
        status="passed",
        evidence={"checked": sorted(required_true), "forbidden_false": list(FORBIDDEN_SAFETY_KEYS)},
        claims_allowed=["paper_first_trading_research_platform", "trading_evidence_operating_system"],
    )


def evaluate_data_gate(data_completeness: dict[str, Any] | None = None) -> dict[str, Any]:
    report = _as_dict(data_completeness)
    summary = _summary(report)
    if not report:
        return _gate("data_complete_enough", status="missing", warnings=["Data Completeness report is missing."])

    completion_rate = _safe_float(summary.get("completion_rate"), 0.0)
    rewardability_rate = _safe_float(summary.get("rewardability_rate"), 0.0)
    benchmark_ready = _safe_bool(summary.get("benchmark_ready"), False)
    blockers: list[str] = []
    if completion_rate is None or completion_rate < 0.80:
        blockers.append("Data completion rate is below the 0.80 planning threshold.")
    if rewardability_rate is None or rewardability_rate < 0.70:
        blockers.append("Rewardability rate is below the 0.70 planning threshold.")
    if not benchmark_ready:
        blockers.append("Data Completeness does not mark benchmark inputs ready.")
    if blockers:
        return _gate(
            "data_complete_enough",
            status="partial",
            warnings=blockers,
            evidence={"completion_rate": completion_rate, "rewardability_rate": rewardability_rate, "benchmark_ready": benchmark_ready},
        )
    return _gate(
        "data_complete_enough",
        status="passed",
        evidence={"completion_rate": completion_rate, "rewardability_rate": rewardability_rate, "benchmark_ready": benchmark_ready},
        claims_allowed=["evidence_ready_for_benchmark_review"],
    )


def evaluate_benchmark_available_gate(benchmark: dict[str, Any] | None = None) -> dict[str, Any]:
    report = _as_dict(benchmark)
    summary = _summary(report)
    if not report:
        return _gate("benchmark_available", status="missing", warnings=["Professional Benchmark report is missing."])
    status = str(report.get("status") or summary.get("benchmark_verdict") or "").strip().lower()
    rewardable_count = int(_safe_float(summary.get("rewardable_count"), 0.0) or 0)
    candidate_count = int(_safe_float(summary.get("candidate_count"), 0.0) or 0)
    if status in {"insufficient_evidence", "data_quality_too_weak", "empty", ""} or rewardable_count < 5:
        return _gate(
            "benchmark_available",
            status="partial",
            warnings=["Benchmark exists but does not yet have enough rewardable evidence for readiness claims."],
            evidence={"status": status, "candidate_count": candidate_count, "rewardable_count": rewardable_count},
            claims_allowed=["benchmark_research_layer_available"],
        )
    return _gate(
        "benchmark_available",
        status="passed",
        evidence={"status": status, "candidate_count": candidate_count, "rewardable_count": rewardable_count},
        claims_allowed=["baseline_comparison_available"],
    )


def evaluate_baselines_beaten_gate(benchmark: dict[str, Any] | None = None) -> dict[str, Any]:
    report = _as_dict(benchmark)
    summary = _summary(report)
    if not report:
        return _gate("baselines_beaten", status="missing", warnings=["Professional Benchmark report is missing."])
    status = str(report.get("status") or summary.get("benchmark_verdict") or "").strip().lower()
    edge = _safe_float(summary.get("baseline_relative_edge"), 0.0)
    lift = _safe_float(summary.get("score_bucket_lift"), 0.0)
    slippage_adjusted = _safe_float(summary.get("slippage_adjusted_reward"), None)
    execution = _as_dict(_as_dict(report.get("sections")).get("execution_quality"))
    if slippage_adjusted is None:
        slippage_adjusted = _safe_float(execution.get("slippage_adjusted_reward"), None)
    evidence = {"status": status, "baseline_relative_edge": edge, "score_bucket_lift": lift, "slippage_adjusted_reward": slippage_adjusted}
    if status == "edge_detected" and edge is not None and edge > 0 and lift is not None and lift > 0:
        return _gate(
            "baselines_beaten",
            status="passed",
            evidence=evidence,
            claims_allowed=["cautious_paper_benchmark_edge_language"],
        )
    if status == "weak_edge_detected" or (edge is not None and edge > 0):
        return _gate(
            "baselines_beaten",
            status="partial",
            warnings=["Benchmark shows weak or incomplete edge; do not claim edge yet."],
            evidence=evidence,
        )
    return _gate(
        "baselines_beaten",
        status="partial",
        warnings=["Benchmark does not show post-cost baseline outperformance."],
        evidence=evidence,
    )


def evaluate_walk_forward_gate(walk_forward: dict[str, Any] | None = None) -> dict[str, Any]:
    report = _as_dict(walk_forward)
    if not report:
        return _gate("walk_forward_passed", status="missing", warnings=["Walk-Forward Experiment Registry report is missing."])
    records = _records(report)
    completed = [row for row in records if str(row.get("status") or "").strip().lower() == "completed"]
    frozen = [row for row in records if str(row.get("status") or "").strip().lower() == "frozen"]
    verdicts = [str(_as_dict(row.get("metrics")).get("verdict") or row.get("verdict") or "").strip().lower() for row in completed]
    evidence = {"record_count": len(records), "completed_count": len(completed), "frozen_count": len(frozen), "completed_verdicts": verdicts}
    if "passed" in verdicts:
        return _gate("walk_forward_passed", status="passed", evidence=evidence, claims_allowed=["cautious_repeatability_language_for_tested_scope"])
    if "weak_pass" in verdicts or frozen:
        return _gate("walk_forward_passed", status="partial", warnings=["Walk-forward evidence is present but not a full pass."], evidence=evidence)
    return _gate("walk_forward_passed", status="partial", warnings=["No completed passing walk-forward experiment found."], evidence=evidence)


def evaluate_execution_cost_gate(execution_quality: dict[str, Any] | None = None, benchmark: dict[str, Any] | None = None) -> dict[str, Any]:
    report = _as_dict(execution_quality)
    benchmark_summary = _summary(benchmark)
    if not report and not benchmark:
        return _gate("execution_costs_handled", status="missing", warnings=["Execution Quality or cost-adjusted Benchmark report is missing."])
    summary = _summary(report)
    aggregations = _as_dict(report.get("aggregations"))
    cost_reward = (
        _safe_float(summary.get("slippage_adjusted_reward"), None)
        or _safe_float(aggregations.get("slippage_adjusted_reward"), None)
        or _safe_float(benchmark_summary.get("slippage_adjusted_reward"), None)
    )
    can_submit = _safe_bool(report.get("can_submit_orders"), False) or _safe_bool(report.get("can_submit_live_orders"), False)
    evidence = {"slippage_adjusted_reward": cost_reward, "can_submit_orders": _safe_bool(report.get("can_submit_orders"), False), "can_submit_live_orders": _safe_bool(report.get("can_submit_live_orders"), False)}
    if can_submit:
        return _gate("execution_costs_handled", status="blocked", blockers=["Execution analytics surface reports order-submission authority."], evidence=evidence)
    if cost_reward is not None and cost_reward > 0:
        return _gate("execution_costs_handled", status="passed", evidence=evidence, claims_allowed=["paper_execution_quality_analysis", "cost_adjusted_paper_research"])
    return _gate("execution_costs_handled", status="partial", warnings=["Cost-adjusted reward is missing or not positive."], evidence=evidence)


def evaluate_risk_visibility_gate(portfolio_risk: dict[str, Any] | None = None) -> dict[str, Any]:
    report = _as_dict(portfolio_risk)
    if not report:
        return _gate("risk_visibility_complete", status="missing", warnings=["Portfolio Risk report is missing."])
    summary = _summary(report)
    aggregations = _as_dict(report.get("aggregations"))
    proof = _as_dict(report.get("proof_summary") or aggregations.get("portfolio_risk_proof"))
    proof_summary = _summary(proof)
    coverage = _safe_float(summary.get("portfolio_risk_coverage"), None)
    if coverage is None:
        coverage = _safe_float(proof_summary.get("portfolio_risk_coverage"), None)
    if coverage is None:
        coverage = _safe_float(summary.get("coverage"), None)
    proof_ready = _safe_bool(proof.get("proof_ready"), False) if proof else None
    requirements_passed = _safe_float(summary.get("portfolio_risk_requirements_passed"), None)
    requirements_total = _safe_float(summary.get("portfolio_risk_requirements_total"), None)
    if requirements_passed is None:
        requirements_passed = _safe_float(proof_summary.get("passed_requirement_count"), None)
    if requirements_total is None:
        requirements_total = _safe_float(proof_summary.get("requirement_count"), None)
    status = str(report.get("status") or summary.get("status") or "").strip().lower()
    writes_risk = _safe_bool(report.get("writes_risk_limits"), False) or _safe_bool(report.get("writes_risk_config"), False)
    evidence = {
        "status": status,
        "coverage": coverage,
        "proof_ready": proof_ready,
        "requirements_passed": requirements_passed,
        "requirements_total": requirements_total,
        "writes_risk_limits": _safe_bool(report.get("writes_risk_limits"), False),
    }
    if writes_risk:
        return _gate("risk_visibility_complete", status="blocked", blockers=["Portfolio risk surface reports risk-limit mutation authority."], evidence=evidence)
    if proof:
        if proof_ready:
            return _gate("risk_visibility_complete", status="passed", evidence=evidence, claims_allowed=["portfolio_risk_visibility"])
        return _gate("risk_visibility_complete", status="partial", warnings=["Portfolio risk proof requirements are incomplete."], evidence=evidence)
    if status in {"ready", "passed"} or (coverage is not None and coverage >= 0.80):
        return _gate("risk_visibility_complete", status="passed", evidence=evidence, claims_allowed=["portfolio_risk_visibility"])
    return _gate("risk_visibility_complete", status="partial", warnings=["Portfolio risk visibility is incomplete."], evidence=evidence)


def evaluate_governance_gate(research_promotion: dict[str, Any] | None = None, governance: dict[str, Any] | None = None) -> dict[str, Any]:
    promotion = _as_dict(research_promotion)
    governance = _as_dict(governance)
    if not promotion and not governance:
        return _gate("governance_complete", status="missing", warnings=["Governance and Research Promotion evidence is missing."])
    status = str(_summary(promotion).get("status") or promotion.get("status") or "").strip().lower()
    aggregations = _as_dict(promotion.get("aggregations"))
    proof = _as_dict(promotion.get("proof_summary") or aggregations.get("research_promotion_proof"))
    proof_summary = _summary(proof)
    proof_ready = _safe_bool(proof.get("proof_ready"), False) if proof else None
    checks = {
        "rbac_enforced": _safe_bool(governance.get("rbac_enforced"), False),
        "approval_workflows_enforced": _safe_bool(governance.get("approval_workflows_enforced"), False),
        "registries_versioned": _safe_bool(governance.get("registries_versioned"), False),
        "audit_immutable": _safe_bool(governance.get("audit_immutable"), False),
        "promotion_metadata_only": not _safe_bool(promotion.get("writes_execution_config"), False)
        and not _safe_bool(promotion.get("can_submit_orders"), False)
        and not _safe_bool(promotion.get("can_submit_live_orders"), False),
    }
    if proof:
        checks["promotion_proof_ready"] = proof_ready
    evidence = {
        "promotion_status": status,
        "promotion_proof_ready": proof_ready,
        "promotion_requirements_passed": _safe_float(proof_summary.get("passed_requirement_count"), None),
        "promotion_requirements_total": _safe_float(proof_summary.get("requirement_count"), None),
        "promotion_traceability_coverage": _safe_float(proof_summary.get("promotion_traceability_coverage"), None),
        **checks,
    }
    missing = [key for key, passed in checks.items() if not passed]
    if missing:
        return _gate("governance_complete", status="partial", warnings=[f"Governance evidence incomplete: {', '.join(missing)}."], evidence=evidence)
    return _gate("governance_complete", status="passed", evidence=evidence, claims_allowed=["small_team_research_workflow"])


def evaluate_external_review_gate(external_review: dict[str, Any] | None = None) -> dict[str, Any]:
    review = _as_dict(external_review)
    if not review:
        return _gate("external_review_complete", status="missing", warnings=["External review evidence is missing."])
    checks = {
        "security_review_complete": _safe_bool(review.get("security_review_complete"), False),
        "legal_review_complete": _safe_bool(review.get("legal_review_complete"), False),
        "compliance_review_complete": _safe_bool(review.get("compliance_review_complete"), False),
        "firm_grade_report_sanitized": _safe_bool(review.get("firm_grade_report_sanitized"), False),
        "environment_separation_verified": _safe_bool(review.get("environment_separation_verified"), False),
        "permission_enforcement_verified": _safe_bool(review.get("permission_enforcement_verified"), False),
    }
    missing = [key for key, passed in checks.items() if not passed]
    if missing:
        return _gate("external_review_complete", status="partial", warnings=[f"External review evidence incomplete: {', '.join(missing)}."], evidence=checks)
    return _gate("external_review_complete", status="passed", evidence=checks, claims_allowed=["institutional_readiness_review_completed_for_tested_scope"])


def evaluate_proof_gates(
    *,
    safety_state: dict[str, Any] | None = None,
    data_completeness: dict[str, Any] | None = None,
    benchmark: dict[str, Any] | None = None,
    walk_forward: dict[str, Any] | None = None,
    execution_quality: dict[str, Any] | None = None,
    portfolio_risk: dict[str, Any] | None = None,
    research_promotion: dict[str, Any] | None = None,
    governance: dict[str, Any] | None = None,
    external_review: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    gates = {
        "safety_intact": evaluate_safety_gate(safety_state),
        "data_complete_enough": evaluate_data_gate(data_completeness),
        "benchmark_available": evaluate_benchmark_available_gate(benchmark),
        "baselines_beaten": evaluate_baselines_beaten_gate(benchmark),
        "walk_forward_passed": evaluate_walk_forward_gate(walk_forward),
        "execution_costs_handled": evaluate_execution_cost_gate(execution_quality, benchmark),
        "risk_visibility_complete": evaluate_risk_visibility_gate(portfolio_risk),
        "governance_complete": evaluate_governance_gate(research_promotion, governance),
        "external_review_complete": evaluate_external_review_gate(external_review),
    }
    return {key: gates[key] for key in GATE_ORDER}


def _extra_proof(extra: dict[str, Any], keys: tuple[str, ...]) -> tuple[list[str], dict[str, bool]]:
    checks = {key: _safe_bool(extra.get(key), False) for key in keys}
    return [key for key, passed in checks.items() if not passed], checks


def evaluate_category_readiness(
    *,
    gates: dict[str, dict[str, Any]],
    category_proof: dict[str, Any] | None = None,
    hft_thesis: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    proof = _as_dict(category_proof)
    hft = _as_dict(hft_thesis)
    rows: list[dict[str, Any]] = []
    safety_blocked = _as_dict(gates.get("safety_intact")).get("status") == "blocked"
    for key, definition in CATEGORY_DEFINITIONS.items():
        extra_source = hft if definition.get("future_only") else proof
        missing_extra, extra_checks = _extra_proof(extra_source, tuple(definition.get("extra_proof_keys") or ()))
        required_gates = tuple(definition.get("required_gates") or ())
        gate_statuses = {gate_key: _as_dict(gates.get(gate_key)).get("status", "missing") for gate_key in required_gates}
        missing_or_partial = [gate_key for gate_key, status in gate_statuses.items() if status != "passed"]
        blocking_gates = [gate_key for gate_key, status in gate_statuses.items() if status == "blocked"]

        if definition.get("future_only"):
            all_hft_proof = not missing_extra
            status = "ready_for_rating_review" if all_hft_proof else "future_only"
        elif safety_blocked:
            status = "blocked_by_safety"
        elif blocking_gates:
            status = "blocked"
        elif missing_or_partial or missing_extra:
            status = "in_progress"
        else:
            status = "ready_for_rating_review"

        next_actions = []
        if missing_or_partial:
            next_actions.append(f"Pass proof gates: {', '.join(missing_or_partial)}.")
        if missing_extra:
            next_actions.append(f"Complete category proof items: {', '.join(missing_extra)}.")
        if definition.get("future_only") and missing_extra:
            next_actions.append("Keep HFT as a separate future thesis; do not market current platform as HFT.")
        if not next_actions:
            next_actions.append("Review evidence artifacts before changing any public readiness rating.")

        rows.append(
            {
                "key": key,
                "label": definition["label"],
                "current_estimated_readiness": definition["current_estimated_readiness"],
                "target": definition["target"],
                "status": status,
                "required_gates": list(required_gates),
                "gate_statuses": gate_statuses,
                "missing_or_partial_gates": missing_or_partial,
                "blocking_gates": blocking_gates,
                "extra_proof": extra_checks,
                "missing_extra_proof": missing_extra,
                "next_actions": next_actions,
                "claim_boundary": "Do not upgrade claims or ratings until required gates and category proof items pass.",
            }
        )
    return rows


def estimate_category_progress(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    estimates: list[dict[str, Any]] = []
    for row in categories:
        required_gates = list(row.get("required_gates") or [])
        gate_statuses = _as_dict(row.get("gate_statuses"))
        extra_proof = _as_dict(row.get("extra_proof"))
        passed_gates = sum(1 for gate_key in required_gates if gate_statuses.get(gate_key) == "passed")
        passed_extra = sum(1 for value in extra_proof.values() if _safe_bool(value, False))
        gate_total = len(required_gates)
        extra_total = len(extra_proof)
        gate_progress = _progress_percent(passed_gates, gate_total)
        extra_progress = _progress_percent(passed_extra, extra_total)
        if gate_total and extra_total:
            overall_progress = round((gate_progress * 0.7) + (extra_progress * 0.3), 1)
        elif gate_total:
            overall_progress = gate_progress
        else:
            overall_progress = extra_progress
        current = _readiness_number(row.get("current_estimated_readiness"))
        planning_readiness = round(current + ((10.0 - current) * (overall_progress / 100.0)), 2)
        rating_update_allowed = row.get("status") == "ready_for_rating_review"
        estimates.append(
            {
                "category_key": row.get("key"),
                "label": row.get("label"),
                "current_estimated_readiness": row.get("current_estimated_readiness"),
                "planning_progress_to_10_pct": overall_progress,
                "gate_progress_pct": gate_progress,
                "extra_proof_progress_pct": extra_progress,
                "planning_readiness_if_reviewed": f"{planning_readiness:g}/10",
                "rating_update_allowed": rating_update_allowed,
                "rating_update_boundary": "Planning estimate only; do not update the public rating unless required gates and proof artifacts pass.",
            }
        )
    return estimates


def build_upgrade_backlog(gates: dict[str, dict[str, Any]], categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    categories_by_key = {str(row.get("key")): row for row in categories}
    items: list[dict[str, Any]] = []
    for index, stage in enumerate(BUILD_STAGE_DEFINITIONS, start=1):
        gate_keys = tuple(stage.get("gate_keys") or ())
        extra_keys = tuple(stage.get("extra_proof_keys") or ())
        category_keys = tuple(stage.get("category_keys") or ())
        gate_statuses = {gate_key: _as_dict(gates.get(gate_key)).get("status", "missing") for gate_key in gate_keys}
        missing_gates = [gate_key for gate_key, status in gate_statuses.items() if status != "passed"]
        blocking_gates = [gate_key for gate_key, status in gate_statuses.items() if status == "blocked"]
        missing_extra: list[str] = []
        for category_key in category_keys:
            category = categories_by_key.get(category_key, {})
            extra_proof = _as_dict(category.get("extra_proof"))
            missing_extra.extend(extra_key for extra_key in extra_keys if not _safe_bool(extra_proof.get(extra_key), False))
        missing_extra = sorted(set(missing_extra))
        is_needed = bool(missing_gates or missing_extra)
        if stage.get("priority") == "future_only":
            state = "future_only"
        elif not is_needed:
            state = "complete_or_not_currently_blocking"
        elif blocking_gates:
            state = "blocked"
        else:
            state = "next"
        impacted = [
            {
                "category_key": category_key,
                "label": _as_dict(categories_by_key.get(category_key)).get("label", category_key),
                "status": _as_dict(categories_by_key.get(category_key)).get("status", "unknown"),
            }
            for category_key in category_keys
        ]
        items.append(
            {
                "sequence": index,
                "key": stage["key"],
                "label": stage["label"],
                "priority": stage["priority"],
                "state": state,
                "purpose": stage["purpose"],
                "gate_statuses": gate_statuses,
                "missing_gates": missing_gates,
                "blocking_gates": blocking_gates,
                "missing_extra_proof": missing_extra,
                "impacted_categories": impacted,
                "safety_constraints": list(SAFETY_NOTES),
                "what_not_to_build_yet": stage["what_not_to_build_yet"],
            }
        )
    items.sort(key=lambda item: (PRIORITY_RANK.get(str(item.get("priority")), 8), int(item.get("sequence") or 0)))
    return items


def summarize_priority_backlog(backlog: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    active = [item for item in backlog if item.get("state") in {"blocked", "next", "future_only"}]
    active.sort(key=lambda item: (PRIORITY_RANK.get(str(item.get("priority")), 8), int(item.get("sequence") or 0)))
    return active[:limit]


def _collect_snapshot(label: str, callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return callback()
    except Exception as exc:  # pragma: no cover - defensive aggregation guard
        return {"status": "unavailable", "summary": {"status": "unavailable", "next_action": f"{label} unavailable: {exc}"}}


def _skipped_snapshot(label: str, reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "summary": {
            "status": "unavailable",
            "next_action": reason,
        },
        "warnings": [reason],
        "research_only": True,
        "read_only": True,
        "mutation": "none",
    }


def collect_existing_proof_snapshots(
    db: Any = None,
    *,
    current_user: Any = None,
    include_slow_sources: bool = False,
    include_diagnostic_sources: bool = False,
) -> dict[str, Any]:
    from backend.services.walk_forward_experiment_registry import get_walk_forward_summary

    slow_reason = "Skipped in the fast category readiness view; open the dedicated surface or request a full proof refresh before treating this gate as complete."
    if include_slow_sources:
        from backend.services.data_completeness_audit import get_data_completeness_summary
        from backend.services.execution_quality_tca import get_execution_quality_tca_summary
        from backend.services.portfolio_risk_intelligence import get_portfolio_risk_summary
        from backend.services.professional_benchmark_suite import get_professional_benchmark_summary
        from backend.services.research_promotion_rules import get_research_promotion_summary

    if include_diagnostic_sources:
        from backend.services.score_calibration_attribution import get_score_calibration_summary
        if include_slow_sources:
            from backend.services.human_system_shadow_mode import get_shadow_mode_summary

    snapshots = {
        "data_completeness": _collect_snapshot("Data Completeness", lambda: get_data_completeness_summary(db=db, current_user=current_user))
        if include_slow_sources
        else _skipped_snapshot("Data Completeness", slow_reason),
        "benchmark": _collect_snapshot("Professional Benchmark", lambda: get_professional_benchmark_summary(db=db, current_user=current_user))
        if include_slow_sources
        else _skipped_snapshot("Professional Benchmark", slow_reason),
        "walk_forward": _collect_snapshot("Walk-Forward", lambda: get_walk_forward_summary()),
        "execution_quality": _collect_snapshot("Execution Quality", lambda: get_execution_quality_tca_summary(db=db, current_user=current_user))
        if include_slow_sources
        else _skipped_snapshot("Execution Quality", slow_reason),
        "portfolio_risk": _collect_snapshot("Portfolio Risk", lambda: get_portfolio_risk_summary(db=db, current_user=current_user))
        if include_slow_sources
        else _skipped_snapshot("Portfolio Risk", slow_reason),
        "research_promotion": _collect_snapshot("Research Promotion", lambda: get_research_promotion_summary(db=db, current_user=current_user))
        if include_slow_sources
        else _skipped_snapshot("Research Promotion", slow_reason),
    }
    if include_diagnostic_sources:
        snapshots["score_calibration"] = _collect_snapshot("Score Calibration", lambda: get_score_calibration_summary(db=db, current_user=current_user))
        snapshots["shadow_mode"] = (
            _collect_snapshot("Human vs System Shadow Mode", lambda: get_shadow_mode_summary(db=db, current_user=current_user))
            if include_slow_sources
            else _skipped_snapshot("Human vs System Shadow Mode", slow_reason)
        )
    return snapshots


def build_current_safety_verification_state() -> dict[str, Any]:
    from backend.services.trading_safety_service import read_last_known_safety_state

    ai_agent_safety_flags = {
        "authority_level": "research_only",
        "ai_order_authority": False,
        "live_trading_approval": False,
        "execution_mutation": False,
        "broker_route_mutation": False,
        "risk_gate_mutation": False,
        "ranking_mutation": False,
    }
    latest = _collect_snapshot("Last known safety state", lambda: read_last_known_safety_state())
    route = _as_dict(latest.get("route"))
    route_enforcement = _as_dict(latest.get("route_enforcement"))
    trade_proof = _as_dict(latest.get("trade_proof"))
    preflight = _as_dict(latest.get("preflight"))
    kill_switch = _as_dict(latest.get("kill_switch_context"))
    snapshot_available = bool(latest) and str(latest.get("status") or "").lower() != "unavailable"
    route_allowed = bool(route.get("allowed")) or bool(route_enforcement.get("alpaca_paper_only"))
    paper_mode = str(route.get("mode") or "").strip().lower() == "paper"
    provider = str(route.get("provider") or "").strip().lower()
    active_route = str(route.get("active") or route_enforcement.get("active_route") or "").strip().lower()
    ai_order_authority = bool(ai_agent_safety_flags.get("ai_order_authority"))
    live_approval = bool(ai_agent_safety_flags.get("live_trading_approval"))
    execution_mutation = bool(ai_agent_safety_flags.get("execution_mutation"))
    broker_route_mutation = bool(ai_agent_safety_flags.get("broker_route_mutation"))
    risk_gate_mutation = bool(ai_agent_safety_flags.get("risk_gate_mutation"))
    ranking_mutation = bool(ai_agent_safety_flags.get("ranking_mutation"))

    violations: list[str] = []
    if snapshot_available and not route_allowed:
        violations.append("Last known unattended execution route was not marked Alpaca paper only.")
    if snapshot_available and kill_switch.get("active") and kill_switch.get("auto_cleared"):
        violations.append("Last known safety state indicates an auto-cleared kill switch.")
    if ai_order_authority:
        violations.append("AI agent safety flags indicate AI order authority.")
    if live_approval:
        violations.append("AI agent safety flags indicate live-trading approval authority.")
    if execution_mutation:
        violations.append("AI agent safety flags indicate execution mutation authority.")
    if broker_route_mutation:
        violations.append("AI agent safety flags indicate broker-route mutation authority.")
    if risk_gate_mutation:
        violations.append("AI agent safety flags indicate risk-gate mutation authority.")
    if ranking_mutation:
        violations.append("AI agent safety flags indicate ranking mutation authority.")

    return serialize_value(
        {
            "source": "last_known_safety_snapshot_and_static_research_authority_contracts",
            "last_known_safety_snapshot_available": snapshot_available,
            "paper_first_boundary_preserved": bool(
                snapshot_available
                and route_allowed
                and paper_mode
                and provider == "alpaca"
                and bool(trade_proof.get("no_live_order_autonomy"))
                and bool(trade_proof.get("no_signal_direct_to_broker"))
            ),
            "alpaca_paper_only_unattended": bool(snapshot_available and route_allowed and paper_mode and provider == "alpaca"),
            "reward_forecast_research_only": True,
            "risk_gates_authoritative": bool(snapshot_available and bool(preflight)),
            "broker_routes_unchanged": bool(snapshot_available and route_allowed and active_route == "broker_paper"),
            "ai_has_no_order_authority": not ai_order_authority and not live_approval and not execution_mutation,
            "autonomous_live_money_orders_enabled": False,
            "ai_order_authority": ai_order_authority,
            "risk_gate_bypass_enabled": risk_gate_mutation,
            "kill_switch_bypass_enabled": False,
            "automatic_broker_route_loosening": broker_route_mutation,
            "automatic_ranking_weight_changes": ranking_mutation,
            "simulation_merged_with_observed": False,
            "live_money_autonomy_enabled": live_approval,
            "broker_routes_changed_by_analytics": broker_route_mutation,
            "support_export_leaks_sensitive_data": False,
            "violations": violations,
            "evidence": {
                "route_provider": provider or None,
                "route_mode": route.get("mode"),
                "active_route": active_route or None,
                "route_allowed": route_allowed,
                "preflight_status": preflight.get("status"),
                "trade_proof_checked": sorted(trade_proof.keys()),
                "ai_agent_authority_level": ai_agent_safety_flags.get("authority_level"),
                "ai_agent_execution_mutation": execution_mutation,
                "ai_agent_broker_route_mutation": broker_route_mutation,
                "ai_agent_risk_gate_mutation": risk_gate_mutation,
                "ai_agent_ranking_mutation": ranking_mutation,
            },
            "warnings": [] if snapshot_available else ["Last known safety snapshot is unavailable; run the safety-state check before treating Gate 1 as complete."],
        }
    )


def build_category_upgrade_readiness_report(
    *,
    safety_state: dict[str, Any] | None = None,
    data_completeness: dict[str, Any] | None = None,
    benchmark: dict[str, Any] | None = None,
    walk_forward: dict[str, Any] | None = None,
    execution_quality: dict[str, Any] | None = None,
    portfolio_risk: dict[str, Any] | None = None,
    research_promotion: dict[str, Any] | None = None,
    governance: dict[str, Any] | None = None,
    external_review: dict[str, Any] | None = None,
    category_proof: dict[str, Any] | None = None,
    hft_thesis: dict[str, Any] | None = None,
    requirement_evidence: dict[str, Any] | None = None,
    acceptance_checklist_path: Path | str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    gates = evaluate_proof_gates(
        safety_state=safety_state,
        data_completeness=data_completeness,
        benchmark=benchmark,
        walk_forward=walk_forward,
        execution_quality=execution_quality,
        portfolio_risk=portfolio_risk,
        research_promotion=research_promotion,
        governance=governance,
        external_review=external_review,
    )
    categories = evaluate_category_readiness(gates=gates, category_proof=category_proof, hft_thesis=hft_thesis)
    backlog = build_upgrade_backlog(gates, categories)
    category_progress = estimate_category_progress(categories)
    documented_scope_coverage = build_documented_scope_coverage(
        gates=gates,
        categories=categories,
        category_proof=category_proof,
        hft_thesis=hft_thesis,
        requirement_evidence=requirement_evidence,
        checklist_path=acceptance_checklist_path,
    )
    priority_backlog = summarize_priority_backlog(backlog)
    passed_gate_count = sum(1 for gate in gates.values() if gate["passed"])
    blocked_gate_count = sum(1 for gate in gates.values() if gate["blocking"])
    ready_category_count = sum(1 for row in categories if row["status"] == "ready_for_rating_review")
    status = "blocked" if blocked_gate_count else "ready_for_review" if ready_category_count == len(categories) else "in_progress"
    blockers = [blocker for gate in gates.values() for blocker in gate.get("blockers", [])]
    warnings = [warning for gate in gates.values() for warning in gate.get("warnings", [])]
    deferred_expansion_count = sum(1 for item in backlog if item.get("state") == "future_only")
    return serialize_value(
        {
            "status": status,
            "generated_at": generated_at or _utc_now(),
            "summary": {
                "gate_count": len(gates),
                "passed_gate_count": passed_gate_count,
                "blocked_gate_count": blocked_gate_count,
                "ready_category_count": ready_category_count,
                "category_count": len(categories),
                "documented_requirement_count": documented_scope_coverage["requirement_count"],
                "documented_requirement_complete_count": documented_scope_coverage["complete_count"],
                "all_documented_scope_added": documented_scope_coverage["all_documented_scope_added"],
                "highest_priority_build": "Post-Implementation Verification, Data Completeness cleanup, Professional Benchmark hardening, Walk-Forward validation, Score Calibration and Feature Attribution, Execution Quality and TCA, Risk Gate and Audit Trail hardening, Portfolio Risk cleanup, Human vs System validation, Research Promotion cleanup, then expansion review.",
                "proof_first_rule": "Ambition is allowed. Proof decides priority.",
                "deferred_expansion_count": deferred_expansion_count,
                "top_blockers": blockers[:5] or warnings[:5],
                "priority_backlog": priority_backlog,
            },
            "gates": list(gates.values()),
            "categories": categories,
            "category_progress": category_progress,
            "documented_scope_coverage": documented_scope_coverage,
            "backlog": backlog,
            "claims_to_avoid": [
                "guaranteed_returns",
                "proven_alpha",
                "ai_trading_bot",
                "autonomous_money_manager",
                "institutional_grade_platform_without_proof",
                "compliance_approved_system",
                "hft_platform",
                "direct_market_access_system",
                "investment_adviser",
                "black_box_alpha_machine",
                "live_trading_ready_system",
                "dark_pool_predictor",
                "dark_pool_trading_signal",
                "broker_replacement",
                "cpp_trading_authority",
            ],
            "safety_notes": list(SAFETY_NOTES),
            "finish_tracker": build_project_finish_tracker(report_name="category_upgrade_readiness"),
            **SAFETY_FLAGS,
        }
    )


def _proof_chain_row_status(gate: dict[str, Any], backlog_item: dict[str, Any]) -> str:
    gate_status = str(gate.get("status") or "missing")
    if gate_status == "blocked":
        return "blocked"
    if gate_status == "passed":
        return "passed"
    backlog_state = str(backlog_item.get("state") or "")
    if backlog_state == "future_only":
        return "future_only"
    return gate_status if gate_status in {"partial", "missing"} else "missing"


def build_category_upgrade_proof_chain(report: dict[str, Any] | None = None, *, generated_at: str | None = None) -> dict[str, Any]:
    source = report or build_category_upgrade_readiness_report(generated_at=generated_at)
    gates = {str(gate.get("key")): dict(gate) for gate in list(source.get("gates") or []) if isinstance(gate, dict)}
    backlog = {str(item.get("key")): dict(item) for item in list(source.get("backlog") or []) if isinstance(item, dict)}
    rows: list[dict[str, Any]] = []
    for definition in PROOF_CHAIN_DEFINITIONS:
        gate = gates.get(str(definition["gate_key"]), {})
        backlog_item = backlog.get(str(definition["stage_key"]), {})
        warnings = [str(item) for item in list(gate.get("warnings") or []) if str(item).strip()]
        blockers = [str(item) for item in list(gate.get("blockers") or []) if str(item).strip()]
        rows.append(
            {
                "sequence": definition["sequence"],
                "stage_key": definition["stage_key"],
                "gate_key": definition["gate_key"],
                "label": definition["label"],
                "status": _proof_chain_row_status(gate, backlog_item),
                "passed": bool(gate.get("passed")),
                "blocking": bool(gate.get("blocking")),
                "proof_boundary": definition["proof_boundary"],
                "safe_next_action": definition["safe_next_action"],
                "claim_boundary": definition["claim_boundary"],
                "evidence": _as_dict(gate.get("evidence")),
                "claims_allowed": list(gate.get("claims_allowed") or []),
                "claims_disallowed": list(gate.get("claims_disallowed") or []),
                "warnings": warnings,
                "blockers": blockers,
                "backlog_state": backlog_item.get("state", "missing"),
                "backlog_priority": backlog_item.get("priority"),
                "impacted_categories": list(backlog_item.get("impacted_categories") or []),
                "missing_gates": list(backlog_item.get("missing_gates") or []),
                "missing_extra_proof": list(backlog_item.get("missing_extra_proof") or []),
                "what_not_to_build_yet": backlog_item.get("what_not_to_build_yet") or definition["claim_boundary"],
                "research_only": True,
                "read_only": True,
                "execution_mutation": False,
                "broker_route_mutation": False,
                "risk_gate_mutation": False,
                "ranking_mutation": False,
                "can_grant_ai_order_authority": False,
            }
        )
    blocked = [row for row in rows if row["status"] == "blocked"]
    missing_or_partial = [row for row in rows if row["status"] in {"missing", "partial"}]
    return serialize_value(
        {
            "status": "blocked" if blocked else "in_progress" if missing_or_partial else "passed",
            "generated_at": generated_at or _utc_now(),
            "source_report_generated_at": _as_dict(source).get("generated_at"),
            "summary": {
                "stage_count": len(rows),
                "passed_stage_count": sum(1 for row in rows if row["passed"]),
                "blocked_stage_count": len(blocked),
                "next_stage": (blocked or missing_or_partial or rows[:1])[0]["label"] if rows else None,
                "highest_priority_build": _as_dict(_as_dict(source).get("summary")).get("highest_priority_build"),
                "claim_boundary": "Ratings remain current estimated readiness only; no alpha, performance, institutional, or HFT claims are allowed from this proof chain alone.",
            },
            "records": rows,
            "claims_to_avoid": list(source.get("claims_to_avoid") or []),
            "safety_notes": list(source.get("safety_notes") or SAFETY_NOTES),
            "finish_tracker": source.get("finish_tracker") or build_project_finish_tracker(report_name="category_upgrade_proof_chain"),
            **SAFETY_FLAGS,
        }
    )


def build_category_upgrade_support_export(
    report: dict[str, Any] | None = None,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    source = report or build_category_upgrade_readiness_report(generated_at=generated_at)
    sanitized_report = sanitize_category_upgrade_export_value(source)
    return serialize_value(
        {
            "export_type": "category_upgrade_readiness_support_export",
            "schema_version": "category_upgrade_readiness_support_export_v1",
            "generated_at": generated_at or _utc_now(),
            "source_report_generated_at": _as_dict(sanitized_report).get("generated_at"),
            "sanitized": True,
            "support_export_safety": {
                "sanitized": True,
                "excludes": [
                    "secrets",
                    "credentials",
                    "broker_records",
                    "raw_broker_payloads",
                    "raw_logs",
                    "account_ids",
                    "raw_local_paths",
                    "database_files",
                    "environment_values",
                    "authorization_headers",
                    "unsanitized_personal_data",
                ],
                "redacts_sensitive_key_markers": list(SENSITIVE_EXPORT_KEY_MARKERS),
                "redacts_local_paths": True,
                "path_exposed_in_payload": False,
                "mutation": "none",
            },
            "report": sanitized_report,
            "safety_notes": list(SAFETY_NOTES),
            "finish_tracker": _as_dict(source).get("finish_tracker") or build_project_finish_tracker(report_name="category_upgrade_support_export"),
            **SAFETY_FLAGS,
        }
    )


def write_category_upgrade_readiness_export(
    report: dict[str, Any] | None = None,
    *,
    output_dir: Path | str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    stamp = _safe_export_stamp(generated_at)
    target_dir = Path(output_dir) if output_dir is not None else DEFAULT_CATEGORY_UPGRADE_EXPORT_DIR / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / CATEGORY_UPGRADE_EXPORT_FILENAME
    export = build_category_upgrade_support_export(report, generated_at=generated_at)
    target.write_text(json.dumps(export, indent=2, sort_keys=True), encoding="utf-8")
    artifact_reference = (
        str((DEFAULT_CATEGORY_UPGRADE_EXPORT_DIR / stamp / CATEGORY_UPGRADE_EXPORT_FILENAME).as_posix())
        if output_dir is None
        else f"{target_dir.name}/{CATEGORY_UPGRADE_EXPORT_FILENAME}"
    )
    return serialize_value(
        {
            "status": "written",
            "artifact_reference": artifact_reference,
            "artifact_name": CATEGORY_UPGRADE_EXPORT_FILENAME,
            "sanitized": True,
            "path_exposed_in_payload": False,
            "support_export_safety": export["support_export_safety"],
            **SAFETY_FLAGS,
        }
    )


def get_category_upgrade_readiness_summary(
    db: Any = None,
    *,
    current_user: Any = None,
    include_slow_sources: bool = False,
) -> dict[str, Any]:
    snapshots = collect_existing_proof_snapshots(db=db, current_user=current_user, include_slow_sources=include_slow_sources)
    return build_category_upgrade_readiness_report(
        safety_state=build_current_safety_verification_state(),
        data_completeness=snapshots.get("data_completeness"),
        benchmark=snapshots.get("benchmark"),
        walk_forward=snapshots.get("walk_forward"),
        execution_quality=snapshots.get("execution_quality"),
        portfolio_risk=snapshots.get("portfolio_risk"),
        research_promotion=snapshots.get("research_promotion"),
    )
