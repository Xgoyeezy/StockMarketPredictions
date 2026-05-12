from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from backend.services.candidate_outcome_stamping_service import get_evidence_outcomes_summary
from backend.services.data_completeness_audit import get_data_completeness_summary
from backend.services.evidence_reward_engine import get_evidence_reward_summary
from backend.services.execution_quality_tca import get_execution_quality_tca_summary
from backend.services.forecast_validation_engine import get_forecast_validation_summary
from backend.services.hedge_fund_ai_agents import get_ai_agents_summary
from backend.services.human_system_shadow_mode import get_shadow_mode_summary
from backend.services.portfolio_risk_intelligence import get_portfolio_risk_summary
from backend.services.professional_benchmark_suite import get_professional_benchmark_summary
from backend.services.project_finish_tracker import build_project_finish_tracker
from backend.services.productized_control_plane_service import get_risk_audit_hardening as get_runtime_risk_audit_hardening
from backend.services.research_promotion_rules import get_research_promotion_summary
from backend.services.risk_audit_hardening import build_risk_audit_hardening_report
from backend.services.score_calibration_attribution import get_score_calibration_summary
from backend.services.serialization import serialize_value
from backend.services.walk_forward_experiment_registry import get_walk_forward_summary

SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "paper_only": True,
    "paper_route_only": True,
    "read_only": True,
    "proof_visibility_only": True,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
    "can_change_broker_routes": False,
    "can_bypass_risk_gates": False,
    "can_clear_kill_switch": False,
    "can_change_ranking_weights": False,
    "writes_execution_config": False,
    "writes_broker_config": False,
    "writes_risk_config": False,
    "writes_risk_limits": False,
    "writes_ranking_config": False,
    "mutation": "none",
}

SAFETY_NOTES: tuple[str, ...] = (
    "Proof metrics are read-only visibility.",
    "Research only. Does not affect trading.",
    "Does not place orders.",
    "Does not change broker routes.",
    "Does not bypass risk gates.",
    "Does not clear kill switches.",
    "Does not change ranking weights automatically.",
    "Does not grant AI order authority.",
)

MetricBuilder = Callable[[], dict[str, Any]]


METRIC_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "proof_field_coverage",
        "label": "Proof-field coverage",
        "gate": "Data Gate",
        "source": "data_completeness",
        "priority": "critical",
        "value_paths": ("summary.proof_field_coverage_rate",),
        "target": 0.80,
        "ready_paths": ("summary.proof_field_ready", "summary.benchmark_ready"),
        "plan_paths": ("data_cleanup_plan", "aggregations.data_cleanup_plan"),
        "blocked_claims": ("benchmark_ready", "walk_forward_ready", "paper_to_live_review"),
        "safe_next_action": "Raise forward-return, baseline, forecast-actual, execution-cost, regime, and reward-field coverage before proof claims.",
    },
    {
        "key": "outcome_baseline_coverage",
        "label": "Candidate outcome and baseline coverage",
        "gate": "Evidence Outcome Gate",
        "source": "evidence_outcomes",
        "priority": "critical",
        "value_paths": ("summary.baseline_coverage_rate",),
        "target": 0.80,
        "ready_paths": (),
        "blocked_claims": ("baseline_relative_edge", "rewardability", "score_calibration"),
        "safe_next_action": "Stamp closed-horizon outcomes and same-window baselines before reward or benchmark attribution.",
    },
    {
        "key": "execution_cost_coverage",
        "label": "Execution-cost coverage",
        "gate": "Execution Quality Gate",
        "source": "evidence_outcomes",
        "priority": "high",
        "value_paths": ("summary.execution_cost_coverage_rate",),
        "target": 0.80,
        "ready_paths": (),
        "blocked_claims": ("after_cost_edge", "tradability_review", "paper_to_live_review"),
        "safe_next_action": "Attach spread, slippage, paper fill, and route evidence without changing broker routes.",
    },
    {
        "key": "benchmark_proof",
        "label": "Professional benchmark proof",
        "gate": "Benchmark Gate",
        "source": "professional_benchmark",
        "priority": "critical",
        "value_paths": ("summary.benchmark_proof_requirements_passed",),
        "target_paths": ("summary.benchmark_proof_requirements_total",),
        "ready_paths": ("summary.benchmark_proof_ready", "proof_summary.proof_ready"),
        "plan_paths": ("benchmark_hardening_plan", "aggregations.benchmark_hardening_plan"),
        "blocked_claims": ("proven_alpha", "repeatability_claim", "public_edge_claim"),
        "safe_next_action": "Collect enough rewardable rows with explicit baselines, score-bucket lift, after-cost reward, and data quality.",
    },
    {
        "key": "walk_forward_proof",
        "label": "Frozen walk-forward proof",
        "gate": "Walk-Forward Gate",
        "source": "walk_forward",
        "priority": "high",
        "value_paths": ("summary.walk_forward_requirements_passed",),
        "target_paths": ("summary.walk_forward_requirements_total",),
        "ready_paths": ("summary.walk_forward_proof_ready", "proof_summary.proof_ready"),
        "plan_paths": ("walk_forward_validation_plan", "aggregations.walk_forward_validation_plan"),
        "blocked_claims": ("repeatability_claim", "promotion_review", "paper_to_live_review"),
        "safe_next_action": "Freeze experiment versions and evaluate out-of-sample folds against stamped candidate outcomes.",
    },
    {
        "key": "score_calibration_proof",
        "label": "Score calibration proof",
        "gate": "Score Calibration Gate",
        "source": "score_calibration",
        "priority": "high",
        "value_paths": ("summary.calibration_requirements_passed",),
        "target_paths": ("summary.calibration_requirements_total",),
        "ready_paths": ("summary.calibration_proof_ready", "proof_summary.proof_ready"),
        "plan_paths": ("score_calibration_hardening_plan", "aggregations.score_calibration_hardening_plan"),
        "blocked_claims": ("score_quality_claim", "automatic_ranking_change", "promotion_review"),
        "safe_next_action": "Measure score-bucket lift, monotonicity, feature coverage, and after-cost lift on rewardable rows.",
    },
    {
        "key": "evidence_rewardability",
        "label": "Evidence rewardability",
        "gate": "Reward Gate",
        "source": "evidence_reward",
        "priority": "high",
        "value_paths": ("summary.rewardable_prediction_count", "summary.rewardable_count"),
        "target": 1,
        "ready_paths": (),
        "plan_paths": ("evidence_reward_cleanup_plan", "aggregations.evidence_reward_cleanup_plan"),
        "blocked_claims": ("reward_claim", "blocker_value_claim", "benchmark_input_quality"),
        "safe_next_action": "Increase rewardable candidate rows with outcomes, baselines, costs, and blocker context.",
    },
    {
        "key": "execution_quality_proof",
        "label": "Execution quality proof",
        "gate": "Execution Quality Gate",
        "source": "execution_quality",
        "priority": "high",
        "value_paths": ("summary.execution_quality_requirements_passed", "proof_summary.summary.passed_requirement_count"),
        "target_paths": ("summary.execution_quality_requirements_total", "proof_summary.summary.requirement_count"),
        "ready_paths": ("summary.execution_quality_proof_ready", "proof_summary.proof_ready"),
        "plan_paths": ("execution_quality_hardening_plan", "aggregations.execution_quality_hardening_plan"),
        "blocked_claims": ("tradability_claim", "route_quality_claim", "paper_to_live_review"),
        "safe_next_action": "Link paper fills to candidates and verify cost-adjusted outcomes, fill quality, and alpha decay.",
    },
    {
        "key": "risk_audit_hardening",
        "label": "Risk and audit hardening",
        "gate": "Safety Gate",
        "source": "risk_audit",
        "priority": "critical",
        "value_paths": ("risk_audit_hardening_plan.summary.ready_item_count",),
        "target_paths": ("risk_audit_hardening_plan.summary.item_count",),
        "ready_paths": ("risk_audit_hardening_plan.summary.claim_permissions.cautious_internal_risk_audit_review",),
        "plan_paths": ("risk_audit_hardening_plan", "aggregations.risk_audit_hardening_plan"),
        "blocked_claims": ("risk_gate_authority_claim", "kill_switch_clearance", "live_trading_readiness"),
        "safe_next_action": "Keep risk policies, kill-switch events, audit records, replay evidence, and sanitized exports visible.",
    },
    {
        "key": "portfolio_risk_proof",
        "label": "Portfolio risk proof",
        "gate": "Portfolio Risk Gate",
        "source": "portfolio_risk",
        "priority": "high",
        "value_paths": ("summary.portfolio_risk_requirements_passed", "proof_summary.summary.passed_requirement_count"),
        "target_paths": ("summary.portfolio_risk_requirements_total", "proof_summary.summary.requirement_count"),
        "ready_paths": ("summary.portfolio_risk_proof_ready", "proof_summary.proof_ready"),
        "plan_paths": ("portfolio_risk_cleanup_plan", "aggregations.portfolio_risk_cleanup_plan"),
        "blocked_claims": ("portfolio_readiness_claim", "paper_to_live_review"),
        "safe_next_action": "Attach exposure, concentration, factor, liquidity, drawdown, stress, and candidate context to risk rows.",
    },
    {
        "key": "forecast_validation",
        "label": "Forecast validation coverage",
        "gate": "Forecast Validation Gate",
        "source": "forecast_validation",
        "priority": "medium",
        "value_paths": ("summary.validation_requirements_passed", "summary.forecast_validation_requirements_passed", "summary.ready_requirement_count"),
        "target_paths": ("summary.validation_requirements_total", "summary.forecast_validation_requirements_total", "summary.requirement_count"),
        "ready_paths": ("summary.forecast_validation_ready", "summary.proof_ready"),
        "plan_paths": ("forecast_validation_hardening_plan", "aggregations.forecast_validation_hardening_plan"),
        "blocked_claims": ("forecast_accuracy_claim", "benchmark_forecast_support"),
        "safe_next_action": "Preserve immutable forecast contracts and attach actual post-forecast paths before accuracy claims.",
    },
    {
        "key": "shadow_mode_comparisons",
        "label": "Same-opportunity shadow comparisons",
        "gate": "Review Gate",
        "source": "shadow_mode",
        "priority": "medium",
        "value_paths": ("summary.comparison_count",),
        "target": 1,
        "ready_paths": ("summary.shadow_proof_ready", "proof_summary.proof_ready"),
        "plan_paths": ("shadow_validation_plan", "aggregations.shadow_validation_plan"),
        "blocked_claims": ("human_vs_system_quality_claim", "override_quality_claim"),
        "safe_next_action": "Capture human and system decisions against the same opportunity before outcomes are known.",
    },
    {
        "key": "research_promotion_traceability",
        "label": "Research promotion traceability",
        "gate": "Promotion Gate",
        "source": "research_promotion",
        "priority": "high",
        "value_paths": ("summary.promotion_requirements_passed", "proof_summary.summary.passed_requirement_count"),
        "target_paths": ("summary.promotion_requirements_total", "proof_summary.summary.requirement_count"),
        "ready_paths": ("summary.promotion_proof_ready", "proof_summary.proof_ready"),
        "plan_paths": ("research_promotion_cleanup_plan", "aggregations.research_promotion_cleanup_plan"),
        "blocked_claims": ("policy_promotion", "ranking_mutation", "paper_to_live_review"),
        "safe_next_action": "Require benchmark, data, walk-forward, execution, risk, and manual review traceability before promotion.",
    },
    {
        "key": "ai_committee_safety",
        "label": "AI committee safety boundary",
        "gate": "AI Review Gate",
        "source": "ai_committee",
        "priority": "medium",
        "value_paths": ("summary.memo_count",),
        "target": 0,
        "ready_paths": ("safety_checks_passed",),
        "blocked_claims": ("ai_order_authority", "ai_risk_gate_override", "ai_ranking_mutation"),
        "safe_next_action": "Keep AI memos sanitized, deterministic on fallback, and tied to proof reports without execution authority.",
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


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _path_value(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _first_path_value(payload: dict[str, Any], paths: Iterable[str]) -> Any:
    for path in paths:
        value = _path_value(payload, path)
        if _has_value(value) or value is False or value == 0:
            return value
    return None


def _first_plan(payload: dict[str, Any], paths: Iterable[str]) -> dict[str, Any] | None:
    for path in paths:
        value = _path_value(payload, path)
        if isinstance(value, dict) and value:
            return value
    return None


def _truthy_path(payload: dict[str, Any], paths: Iterable[str]) -> bool:
    for path in paths:
        if _path_value(payload, path) is True:
            return True
    return False


def _metric_status(*, available: bool, ready: bool, value: Any, target: Any, raw_status: str | None) -> str:
    if not available:
        return "source_unavailable"
    if ready:
        return "ready"
    value_number = _safe_float(value)
    target_number = _safe_float(target)
    if value_number is not None and target_number is not None and value_number >= target_number:
        return "ready"
    normalized = str(raw_status or "").lower()
    if normalized in {"empty", "no_records"}:
        return "no_records"
    if "blocked" in normalized:
        return "blocked_by_evidence"
    return "needs_evidence"


def _plan_status(plan: dict[str, Any] | None) -> str | None:
    if not isinstance(plan, dict):
        return None
    status = plan.get("status")
    return str(status) if status else None


def _plan_summary(plan: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {}
    summary = plan.get("summary")
    return summary if isinstance(summary, dict) else {}


def _plan_open_item_count(plan: dict[str, Any] | None) -> int | None:
    summary = _plan_summary(plan)
    for key in ("open_item_count", "open_metric_count"):
        value = summary.get(key)
        if value is not None:
            return int(value or 0)
    items = plan.get("items") if isinstance(plan, dict) else None
    if isinstance(items, list):
        return sum(1 for item in items if isinstance(item, dict) and item.get("status") != "ready")
    return None


def _plan_item_count(plan: dict[str, Any] | None) -> int | None:
    summary = _plan_summary(plan)
    for key in ("item_count", "metric_count", "requirement_count"):
        value = summary.get(key)
        if value is not None:
            return int(value or 0)
    items = plan.get("items") if isinstance(plan, dict) else None
    if isinstance(items, list):
        return len(items)
    return None


def _plan_critical_open_item_count(plan: dict[str, Any] | None) -> int:
    summary = _plan_summary(plan)
    value = summary.get("critical_open_items") or summary.get("critical_open_metric_count")
    if value is not None:
        return int(value or 0)
    items = plan.get("items") if isinstance(plan, dict) else None
    if isinstance(items, list):
        return sum(
            1
            for item in items
            if isinstance(item, dict) and item.get("status") != "ready" and item.get("priority") == "critical"
        )
    return 0


def _plan_top_item(plan: dict[str, Any] | None) -> str | None:
    summary = _plan_summary(plan)
    for key in ("top_cleanup_item", "top_hardening_item", "top_validation_item", "top_metric", "top_gap"):
        value = summary.get(key)
        if value:
            return str(value)
    items = plan.get("items") if isinstance(plan, dict) else None
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("status") != "ready":
                return str(item.get("title") or item.get("label") or item.get("key") or "")
    return None


def _source_status(report: dict[str, Any] | None, source_key: str, label: str) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {
            "source": source_key,
            "label": label,
            "available": False,
            "status": "source_unavailable",
            "generated_at": None,
            "warning_count": 1,
            "warnings": [f"{label} source report is unavailable."],
            "finish_tracker_present": False,
        }
    summary = dict(report.get("summary") or {})
    return {
        "source": source_key,
        "label": label,
        "available": True,
        "status": report.get("status") or summary.get("status") or "unknown",
        "generated_at": report.get("generated_at"),
        "warning_count": len(report.get("warnings") or []),
        "warnings": list(report.get("warnings") or [])[:5],
        "missing_field_count": sum(int(count or 0) for count in (report.get("missing_fields") or {}).values()) if isinstance(report.get("missing_fields"), dict) else 0,
        "finish_tracker_present": isinstance(report.get("finish_tracker"), dict),
        "can_submit_orders": bool(report.get("can_submit_orders", False)),
        "can_submit_live_orders": bool(report.get("can_submit_live_orders", False)),
        "mutation": report.get("mutation", "none"),
        "summary": {
            key: summary.get(key)
            for key in sorted(summary.keys())
            if key
            in {
                "status",
                "candidate_count",
                "rewardable_count",
                "rewardable_prediction_count",
                "comparison_count",
                "benchmark_proof_status",
                "walk_forward_proof_status",
                "calibration_proof_status",
                "execution_quality_proof_status",
                "portfolio_risk_proof_status",
                "risk_audit_hardening_status",
                "promotion_proof_status",
                "proof_field_coverage_rate",
                "baseline_coverage_rate",
                "execution_cost_coverage_rate",
            }
        },
    }


def _collect_runtime_reports(db: Any = None, current_user: Any = None) -> tuple[dict[str, dict[str, Any]], list[str]]:
    warnings: list[str] = []
    collectors: tuple[tuple[str, str, MetricBuilder], ...] = (
        ("data_completeness", "Data Completeness", lambda: get_data_completeness_summary(db, current_user=current_user)),
        ("evidence_outcomes", "Evidence Outcomes", lambda: get_evidence_outcomes_summary(db, current_user=current_user)),
        ("professional_benchmark", "Professional Benchmark", lambda: get_professional_benchmark_summary(db, current_user=current_user)),
        ("walk_forward", "Walk-Forward", lambda: get_walk_forward_summary()),
        ("score_calibration", "Score Calibration", lambda: get_score_calibration_summary(db, current_user=current_user)),
        ("evidence_reward", "Evidence Reward", lambda: get_evidence_reward_summary(db, current_user=current_user)),
        ("execution_quality", "Execution Quality", lambda: get_execution_quality_tca_summary(db, current_user=current_user)),
        ("risk_audit", "Risk and Audit", lambda: get_runtime_risk_audit_hardening(db, current_user=current_user) if db is not None and current_user is not None else build_risk_audit_hardening_report()),
        ("portfolio_risk", "Portfolio Risk", lambda: get_portfolio_risk_summary(db, current_user=current_user)),
        ("forecast_validation", "Forecast Validation", lambda: get_forecast_validation_summary()),
        ("shadow_mode", "Human vs System Shadow", lambda: get_shadow_mode_summary(db, current_user=current_user)),
        ("research_promotion", "Research Promotion", lambda: get_research_promotion_summary(db, current_user=current_user)),
        ("ai_committee", "AI Committee", lambda: get_ai_agents_summary()),
    )
    reports: dict[str, dict[str, Any]] = {}
    for key, label, builder in collectors:
        try:
            reports[key] = builder()
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            reports[key] = {
                "status": "source_unavailable",
                "summary": {"status": "source_unavailable"},
                "warnings": [f"{label} source unavailable: {exc.__class__.__name__}."],
            }
            warnings.append(f"{label} source unavailable: {exc.__class__.__name__}.")
    return reports, warnings


def _build_lightweight_source_reports() -> dict[str, dict[str, Any]]:
    sources = sorted({str(definition["source"]) for definition in METRIC_DEFINITIONS})
    return {
        source: {
            "status": "not_collected",
            "summary": {"status": "not_collected"},
            "warnings": [
                "Slow source collection was not run for the default dashboard response. Open the source report for exact runtime evidence.",
            ],
            "finish_tracker": build_project_finish_tracker(report_name=f"proof_metrics_{source}"),
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "mutation": "none",
        }
        for source in sources
    }


def build_proof_metric_rows(source_reports: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for definition in METRIC_DEFINITIONS:
        source_key = str(definition["source"])
        report = source_reports.get(source_key)
        available = isinstance(report, dict) and report.get("status") != "source_unavailable"
        report_payload = report if isinstance(report, dict) else {}
        proof_plan = _first_plan(report_payload, definition.get("plan_paths") or ())
        plan_status = _plan_status(proof_plan)
        plan_open_items = _plan_open_item_count(proof_plan)
        plan_item_count = _plan_item_count(proof_plan)
        plan_critical_open_items = _plan_critical_open_item_count(proof_plan)
        plan_top_item = _plan_top_item(proof_plan)
        value = _first_path_value(report_payload, definition.get("value_paths") or ())
        target = _first_path_value(report_payload, definition.get("target_paths") or ())
        if target is None:
            target = definition.get("target")
        if value is None and plan_open_items is not None and plan_item_count is not None:
            value = max(0, plan_item_count - plan_open_items)
        if target is None and plan_item_count is not None:
            target = plan_item_count
        plan_blocks = plan_open_items is not None and plan_open_items > 0
        ready = _truthy_path(report_payload, definition.get("ready_paths") or ()) and not plan_blocks
        raw_status = str(
            plan_status
            or (
                _first_path_value(
                    report_payload,
                    (
                        "summary.status",
                        "summary.benchmark_proof_status",
                        "summary.walk_forward_proof_status",
                        "summary.calibration_proof_status",
                        "summary.execution_quality_proof_status",
                        "summary.portfolio_risk_proof_status",
                        "summary.risk_audit_hardening_status",
                        "summary.promotion_proof_status",
                        "summary.forecast_hardening_status",
                        "status",
                    ),
                )
                or ""
            )
        )
        status = "blocked_by_evidence" if available and plan_blocks else _metric_status(available=available, ready=ready, value=value, target=target, raw_status=raw_status)
        missing_fields = report_payload.get("missing_fields") if isinstance(report_payload.get("missing_fields"), dict) else {}
        gap = None
        value_number = _safe_float(value)
        target_number = _safe_float(target)
        if target_number is not None:
            if value_number is None:
                gap = target_number
            else:
                gap = max(0.0, round(target_number - value_number, 6))
        rows.append(
            {
                "key": definition["key"],
                "label": definition["label"],
                "gate": definition["gate"],
                "source": source_key,
                "priority": definition["priority"],
                "status": status,
                "raw_status": raw_status or "unknown",
                "value": value,
                "target": target,
                "gap_to_target": gap,
                "ready": status == "ready",
                "blocked": status != "ready",
                "blocked_claims": list(definition.get("blocked_claims") or ()),
                "safe_next_action": definition["safe_next_action"],
                "missing_fields": sorted(missing_fields.keys())[:8],
                "proof_plan": {
                    "status": plan_status,
                    "open_item_count": plan_open_items,
                    "critical_open_items": plan_critical_open_items,
                    "item_count": plan_item_count,
                    "top_item": plan_top_item,
                }
                if proof_plan is not None
                else None,
                "proof_plan_status": plan_status,
                "proof_plan_open_items": plan_open_items,
                "proof_plan_critical_open_items": plan_critical_open_items,
                "proof_plan_item_count": plan_item_count,
                "proof_plan_top_item": plan_top_item,
                "manual_review_only": True,
                "research_only": True,
                **SAFETY_FLAGS,
            }
        )
    return rows


def build_gate_groups(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in metrics:
        grouped[str(row["gate"])].append(row)
    groups: list[dict[str, Any]] = []
    for gate, rows in sorted(grouped.items()):
        open_rows = [row for row in rows if row["status"] != "ready"]
        critical_open = [row for row in open_rows if row.get("priority") == "critical"]
        blocked_claims = sorted({claim for row in open_rows for claim in row.get("blocked_claims") or []})
        groups.append(
            {
                "gate": gate,
                "status": "ready" if not open_rows else "blocked_by_evidence",
                "metric_count": len(rows),
                "ready_metric_count": len(rows) - len(open_rows),
                "open_metric_count": len(open_rows),
                "critical_open_metric_count": len(critical_open),
                "top_gap": open_rows[0]["label"] if open_rows else None,
                "blocked_claims": blocked_claims,
                "safe_next_actions": [row["safe_next_action"] for row in open_rows[:3]],
            }
        )
    return groups


def _source_label(source_key: str) -> str:
    return source_key.replace("_", " ").title()


def build_source_rows(source_reports: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    source_keys = sorted({str(definition["source"]) for definition in METRIC_DEFINITIONS} | set(source_reports.keys()))
    return [_source_status(source_reports.get(key), key, _source_label(key)) for key in source_keys]


def build_proof_metrics_dashboard_report(
    *,
    db: Any = None,
    current_user: Any = None,
    source_reports: dict[str, dict[str, Any]] | None = None,
    collect_sources: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    collection_warnings: list[str] = []
    if source_reports is None:
        if collect_sources:
            source_reports, collection_warnings = _collect_runtime_reports(db=db, current_user=current_user)
        else:
            source_reports = _build_lightweight_source_reports()
            collection_warnings.append("Default Proof Metrics response uses lightweight gate status only; source reports are not synchronously rebuilt.")
    else:
        source_reports = dict(source_reports)
    metrics = build_proof_metric_rows(source_reports)
    gate_groups = build_gate_groups(metrics)
    source_rows = build_source_rows(source_reports)
    tracker = build_project_finish_tracker(report_name="proof_metrics_dashboard")
    deferred_items = [
        item
        for item in tracker.get("items", [])
        if item.get("status") == "deferred" or item.get("priority") == "future"
    ]

    status_counts = Counter(row["status"] for row in metrics)
    priority_open_counts = Counter(row["priority"] for row in metrics if row["status"] != "ready")
    source_unavailable_count = sum(1 for row in source_rows if not row.get("available"))
    open_metrics = [row for row in metrics if row["status"] != "ready"]
    ready_metric_count = len(metrics) - len(open_metrics)
    gates_ready_count = sum(1 for row in gate_groups if row["status"] == "ready")
    all_ready = bool(metrics) and not open_metrics and source_unavailable_count == 0
    warnings = list(collection_warnings)
    warnings.extend(
        warning
        for source in source_rows
        for warning in source.get("warnings") or []
        if warning
    )
    if open_metrics:
        warnings.append("Proof metrics still contain open gaps; expansion and paper-to-live work remain gated.")
    if source_unavailable_count:
        warnings.append("One or more source reports were unavailable, so proof readiness cannot be inferred.")

    summary = {
        "status": "ready_for_human_review" if all_ready else "blocked_by_evidence",
        "metric_count": len(metrics),
        "ready_metric_count": ready_metric_count,
        "open_metric_count": len(open_metrics),
        "critical_open_metric_count": priority_open_counts.get("critical", 0),
        "high_open_metric_count": priority_open_counts.get("high", 0),
        "source_count": len(source_rows),
        "source_unavailable_count": source_unavailable_count,
        "slow_source_collection": bool(collect_sources),
        "gate_count": len(gate_groups),
        "gates_ready_count": gates_ready_count,
        "gates_blocked_count": len(gate_groups) - gates_ready_count,
        "status_counts": dict(status_counts),
        "top_blockers": [row["label"] for row in open_metrics[:5]],
        "deferred_expansion_count": len(deferred_items),
        "proof_ready": all_ready,
        "proof_first_rule": "Ambition is allowed. Proof decides priority.",
        "claim_boundary": "Proof metrics are visibility only. They do not prove alpha, repeatability, institutional readiness, HFT capability, compliance approval, or live-trading readiness.",
        **SAFETY_FLAGS,
    }
    return serialize_value(
        {
            "status": summary["status"],
            "generated_at": generated_at or _utc_now(),
            "summary": summary,
            "metrics": metrics,
            "gate_groups": gate_groups,
            "source_reports": source_rows,
            "warnings": list(dict.fromkeys(warnings)),
            "safe_next_actions": [
                {
                    "metric": row["key"],
                    "gate": row["gate"],
                    "priority": row["priority"],
                    "action": row["safe_next_action"],
                    "manual_review_only": True,
                    "changes_execution": False,
                }
                for row in open_metrics[:10]
            ],
            "deferred_scope": [
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "safe_boundary": item.get("safe_boundary"),
                }
                for item in deferred_items
            ],
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
            "finish_tracker": tracker,
        }
    )


def get_proof_metrics_dashboard_summary(db: Any = None, *, current_user: Any = None, collect_sources: bool = False) -> dict[str, Any]:
    return build_proof_metrics_dashboard_report(db=db, current_user=current_user, collect_sources=collect_sources)
