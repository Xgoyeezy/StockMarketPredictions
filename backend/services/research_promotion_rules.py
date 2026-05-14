from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.services.data_completeness_audit import get_data_completeness_summary
from backend.services.exceptions import NotFoundError, ValidationServiceError
from backend.services.professional_benchmark_suite import get_professional_benchmark_summary
from backend.services.project_finish_tracker import build_project_finish_tracker
from backend.services.serialization import serialize_value
from backend.services.storage_utils import read_json_file, write_json_file
from backend.services.walk_forward_experiment_registry import get_walk_forward_experiments

SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "paper_route_only": True,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
    "can_change_broker_routes": False,
    "can_bypass_risk_gates": False,
    "can_clear_kill_switch": False,
    "can_change_ranking_weights": False,
    "can_grant_ai_order_authority": False,
    "mutation": "research_metadata_only",
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
    "Does not change risk limits automatically.",
    "Does not grant AI order authority.",
)

ENTITY_TYPES: tuple[str, ...] = (
    "strategy",
    "setup_type",
    "engine",
    "blocker",
    "forecast_model",
    "AI_verdict_policy",
    "ranking_rule",
    "risk_rule",
)
PROMOTION_STATUSES: tuple[str, ...] = (
    "research",
    "candidate",
    "walk_forward_testing",
    "paper_proven",
    "rejected",
    "needs_more_evidence",
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE_PATH = PROJECT_ROOT / "runtime-exports" / "research-promotion" / "promotion_statuses.json"
SECRET_KEY_MARKERS = ("secret", "token", "password", "credential", "api_key", "apikey", "access_key", "private_key")

MIN_SAMPLE_SIZE = 20
MIN_REWARDABLE_COUNT = 5
MIN_COMPLETION_RATE = 0.60
MIN_REWARDABILITY_RATE = 0.50
PAPER_PROVEN_EDGE = 0.10
MAX_ACCEPTABLE_DRAWDOWN = 1.00
MAX_ACCEPTABLE_SLIPPAGE_DRAG = 0.00
MIN_REGIME_STABILITY = 0.50
MIN_FORECAST_ACCURACY = 0.50
MAX_AI_FALSE_POSITIVE_RATE = 0.40
MAX_AI_FALSE_NEGATIVE_RATE = 0.50
MAX_BLOCKER_FALSE_BLOCK_RATE = 0.50
MIN_PROMOTION_TRACEABILITY_COVERAGE = 0.80

RESEARCH_PROMOTION_PROOF_REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "key": "research_entity_sample",
        "label": "Research entity sample",
        "metric": "record_count",
        "threshold": 1,
        "comparison": ">=",
        "safe_next_action": "Collect at least one research promotion entity before treating promotion status as reviewable.",
    },
    {
        "key": "status_traceability",
        "label": "Status traceability",
        "metric": "status_traceability_coverage",
        "threshold": MIN_PROMOTION_TRACEABILITY_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Ensure every promotion entity has a research status and safe explanation.",
    },
    {
        "key": "criteria_traceability",
        "label": "Criteria traceability",
        "metric": "criteria_traceability_coverage",
        "threshold": MIN_PROMOTION_TRACEABILITY_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Keep passed and failed criteria attached to each promotion entity.",
    },
    {
        "key": "benchmark_traceability",
        "label": "Benchmark traceability",
        "metric": "benchmark_traceability_coverage",
        "threshold": MIN_PROMOTION_TRACEABILITY_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Link promotion entities to benchmark verdict, sample, rewardable count, and baseline-relative evidence.",
    },
    {
        "key": "data_quality_traceability",
        "label": "Data quality traceability",
        "metric": "data_quality_traceability_coverage",
        "threshold": MIN_PROMOTION_TRACEABILITY_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Attach completeness, rewardability, and data-quality fields to promotion entities.",
    },
    {
        "key": "walk_forward_traceability",
        "label": "Walk-forward traceability",
        "metric": "walk_forward_traceability_coverage",
        "threshold": MIN_PROMOTION_TRACEABILITY_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Link promotion entities to frozen or completed walk-forward experiment evidence before governance claims.",
    },
    {
        "key": "execution_traceability",
        "label": "Execution traceability",
        "metric": "execution_traceability_coverage",
        "threshold": MIN_PROMOTION_TRACEABILITY_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Attach execution-adjusted reward or explicit execution-quality evidence to promotion entities.",
    },
    {
        "key": "manual_review_traceability",
        "label": "Manual review traceability",
        "metric": "manual_review_record_count",
        "threshold": 1,
        "comparison": ">=",
        "safe_next_action": "Record at least one sanitized manual review metadata event with reviewer, reason, previous status, and evidence snapshot.",
    },
    {
        "key": "promotion_metadata_only",
        "label": "Promotion remains metadata only",
        "metric": "promotion_metadata_only",
        "threshold": 1,
        "comparison": "==",
        "safe_next_action": "Keep promotion status disconnected from execution, broker routes, risk gates, and ranking weights.",
    },
    {
        "key": "safety_boundary_preserved",
        "label": "Safety boundary preserved",
        "metric": "safety_boundary_preserved",
        "threshold": 1,
        "comparison": "==",
        "safe_next_action": "Preserve the paper-first, research-only boundary before treating promotion workflow as governance evidence.",
    },
)

RESEARCH_PROMOTION_CLEANUP_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "research_entity_sample",
        "title": "Research entity sample",
        "priority": "critical",
        "proof_keys": ("research_entity_sample",),
        "missing_fields": ("entity_id", "entity_type", "promotion_status", "safe_explanation"),
        "blocked_claims": ("promotion_readiness_claim", "governance_workflow_claim"),
        "safe_next_action": "Keep at least one traceable research entity visible before treating promotion workflow as reviewable.",
        "done_when": "Promotion entities exist with status, type, and safe explanation metadata.",
    },
    {
        "key": "status_and_criteria_traceability",
        "title": "Status and criteria traceability",
        "priority": "critical",
        "proof_keys": ("status_traceability", "criteria_traceability"),
        "missing_fields": ("promotion_status", "safe_explanation", "criteria_passed", "criteria_failed"),
        "blocked_claims": ("promotion_readiness_claim", "paper_proven_research_claim"),
        "safe_next_action": "Attach status, safe explanation, passed criteria, and failed criteria to every promotion entity.",
        "done_when": "Most promotion entities have reviewable status and criterion traceability.",
    },
    {
        "key": "benchmark_and_data_traceability",
        "title": "Benchmark and data traceability",
        "priority": "critical",
        "proof_keys": ("benchmark_traceability", "data_quality_traceability"),
        "missing_fields": ("benchmark_verdict", "sample_size", "rewardable_count", "baseline_relative_edge", "completion_rate", "rewardability_rate"),
        "blocked_claims": ("benchmark_backed_promotion_claim", "paper_proven_research_claim"),
        "safe_next_action": "Link promotion entities to benchmark verdict, sample, rewardability, baseline-relative evidence, and data-quality fields.",
        "done_when": "Benchmark and data-quality fields are traceable before any promotion-readiness language.",
    },
    {
        "key": "walk_forward_traceability",
        "title": "Walk-forward traceability",
        "priority": "high",
        "proof_keys": ("walk_forward_traceability",),
        "missing_fields": ("walk_forward_status", "walk_forward_verdict", "frozen_experiment_id"),
        "blocked_claims": ("repeatability_claim", "paper_proven_research_claim"),
        "safe_next_action": "Link promotion entities to frozen or completed walk-forward experiment evidence before repeatability or paper-proven review.",
        "done_when": "Promotion entities show frozen or completed walk-forward context where promotion language depends on repeatability.",
    },
    {
        "key": "execution_traceability",
        "title": "Execution traceability",
        "priority": "high",
        "proof_keys": ("execution_traceability",),
        "missing_fields": ("execution_adjusted_reward", "slippage_adjusted_reward", "execution_quality"),
        "blocked_claims": ("tradability_claim", "paper_to_live_readiness"),
        "safe_next_action": "Attach execution-adjusted reward or explicit execution-quality evidence before treating promoted research as tradable.",
        "done_when": "Promotion entities have execution-cost context tied to the evidence snapshot.",
    },
    {
        "key": "manual_review_metadata",
        "title": "Manual review metadata",
        "priority": "critical",
        "proof_keys": ("manual_review_traceability",),
        "missing_fields": ("manual_status.promotion_status", "manual_status.reason", "manual_status.updated_at", "manual_status.previous_promotion_status", "manual_status.evidence_snapshot"),
        "blocked_claims": ("human_approved_promotion_claim", "small_fund_governance_claim"),
        "safe_next_action": "Record sanitized manual review metadata with reviewer context when available, reason, previous status, and evidence snapshot.",
        "done_when": "At least one manual review metadata event is traceable without carrying broker, account, secret, or raw-path data.",
    },
    {
        "key": "metadata_only_safety_governance",
        "title": "Metadata-only safety governance",
        "priority": "critical",
        "proof_keys": ("promotion_metadata_only", "safety_boundary_preserved"),
        "missing_fields": (),
        "blocked_claims": ("automatic_strategy_promotion", "ranking_weight_change", "risk_limit_change", "broker_route_change", "live_trading_readiness"),
        "safe_next_action": "Keep promotion status disconnected from execution, broker routes, risk gates, risk limits, kill switches, ranking weights, and live trading.",
        "done_when": "Promotion remains research metadata only and cannot mutate trading, broker, risk, or ranking configuration.",
    },
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _safe_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ratio_or_zero(value: Any) -> float:
    parsed = _safe_float(value)
    return parsed if parsed is not None else 0.0


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    try:
        denom = float(denominator)
        if denom == 0:
            return None
        return round(float(numerator) / denom, 6)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _passes_threshold(value: Any, threshold: Any, comparison: str) -> bool:
    if comparison == "truthy":
        return bool(value)
    parsed_value = _safe_float(value)
    parsed_threshold = _safe_float(threshold)
    if parsed_value is None or parsed_threshold is None:
        return False
    if comparison in {">=", "greater_or_equal"}:
        return parsed_value >= parsed_threshold
    if comparison in {">", "greater_than"}:
        return parsed_value > parsed_threshold
    if comparison in {"<=", "less_or_equal"}:
        return parsed_value <= parsed_threshold
    if comparison in {"<", "less_than"}:
        return parsed_value < parsed_threshold
    if comparison in {"==", "equal"}:
        return parsed_value == parsed_threshold
    return False


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple) or isinstance(value, set):
        return list(value)
    return [value]


def _looks_like_local_path(value: str) -> bool:
    normalized = value.strip()
    return (len(normalized) >= 3 and normalized[1:3] in {":\\", ":/"}) or normalized.startswith("\\\\")


def _sanitize_value(value: Any, *, key: str = "") -> Any:
    key_lower = key.lower()
    if any(marker in key_lower for marker in SECRET_KEY_MARKERS):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _sanitize_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, key=key) for item in value]
    if isinstance(value, tuple) or isinstance(value, set):
        return [_sanitize_value(item, key=key) for item in value]
    if isinstance(value, str):
        cleaned = value.strip()
        if _looks_like_local_path(cleaned):
            return "[local_path_redacted]"
        return cleaned
    return value


def _store_path(path: Path | str | None = None) -> Path:
    return Path(path) if path is not None else DEFAULT_STORE_PATH


def _read_manual_statuses(store_path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    payload = read_json_file(_store_path(store_path), {"statuses": {}})
    statuses = payload.get("statuses") if isinstance(payload, dict) else {}
    return {str(key): value for key, value in (statuses or {}).items() if isinstance(value, dict)}


def _write_manual_statuses(statuses: dict[str, dict[str, Any]], store_path: Path | str | None = None) -> None:
    payload = {
        "schema_version": "research_promotion_statuses_v1",
        "updated_at": _utc_now(),
        "statuses": _sanitize_value(statuses),
    }
    write_json_file(_store_path(store_path), serialize_value(payload))


def _created_by(current_user: Any = None) -> str | None:
    for field in ("user_id", "auth_subject", "name"):
        value = getattr(current_user, field, None)
        if value:
            return str(value)
    return None


def _safe_entity_part(value: Any, fallback: str = "unknown") -> str:
    cleaned = str(value or fallback).strip().lower()
    cleaned = cleaned.replace(" ", "_").replace("/", "_")
    return cleaned or fallback


def _section_items(sections: dict[str, Any], section_key: str) -> list[dict[str, Any]]:
    section = sections.get(section_key)
    if isinstance(section, dict):
        return [row for row in section.get("items") or [] if isinstance(row, dict)]
    return []


def _walk_forward_snapshot(walk_forward_report: dict[str, Any]) -> dict[str, Any]:
    records = [row for row in walk_forward_report.get("records") or [] if isinstance(row, dict)]
    frozen = [row for row in records if str(row.get("status") or "") in {"frozen", "running", "completed", "rejected", "needs_more_evidence"}]
    passed = [row for row in records if str((row.get("metrics") or {}).get("verdict") or "") in {"passed", "weak_pass"}]
    latest = records[-1] if records else {}
    return {
        "experiment_count": len(records),
        "frozen_exists": bool(frozen),
        "pass_or_weak_pass_exists": bool(passed),
        "latest_status": latest.get("status"),
        "latest_verdict": (latest.get("metrics") or {}).get("verdict"),
        "latest_experiment_id": latest.get("experiment_id"),
    }


def _evidence_context(
    *,
    benchmark_report: dict[str, Any],
    completeness_report: dict[str, Any],
    walk_forward_report: dict[str, Any],
) -> dict[str, Any]:
    benchmark_summary = benchmark_report.get("summary") if isinstance(benchmark_report.get("summary"), dict) else {}
    benchmark_sections = benchmark_report.get("sections") if isinstance(benchmark_report.get("sections"), dict) else {}
    completeness_summary = completeness_report.get("summary") if isinstance(completeness_report.get("summary"), dict) else {}
    execution = benchmark_sections.get("execution_quality") if isinstance(benchmark_sections.get("execution_quality"), dict) else {}
    forecast = benchmark_sections.get("forecast_accuracy") if isinstance(benchmark_sections.get("forecast_accuracy"), dict) else {}
    ai = benchmark_sections.get("ai_verdict_accuracy") if isinstance(benchmark_sections.get("ai_verdict_accuracy"), dict) else {}
    score_bucket = benchmark_sections.get("score_bucket_separation") if isinstance(benchmark_sections.get("score_bucket_separation"), dict) else {}
    return {
        "benchmark_verdict": benchmark_summary.get("benchmark_verdict") or benchmark_report.get("status") or "insufficient_evidence",
        "sample_size": _safe_int(benchmark_summary.get("candidate_count")),
        "rewardable_count": _safe_int(benchmark_summary.get("rewardable_count")),
        "data_quality_score": _safe_float(benchmark_summary.get("data_quality_score")),
        "baseline_relative_edge": _safe_float(benchmark_summary.get("baseline_relative_edge")),
        "score_bucket_lift": _safe_float(benchmark_summary.get("score_bucket_lift")),
        "max_drawdown": _safe_float(benchmark_summary.get("max_drawdown")),
        "profit_factor": _safe_float(benchmark_summary.get("profit_factor")),
        "completion_rate": _ratio_or_zero(completeness_summary.get("completion_rate")),
        "rewardability_rate": _ratio_or_zero(completeness_summary.get("rewardability_rate")),
        "benchmark_ready": bool(completeness_summary.get("benchmark_ready")),
        "highest_priority_missing_fields": completeness_summary.get("highest_priority_missing_fields") or [],
        "execution_adjusted_reward": _safe_float(execution.get("slippage_adjusted_reward")),
        "forecast_accuracy": _safe_float(forecast.get("direction_accuracy")),
        "ai_false_positive_rate": _safe_float(ai.get("false_positive_rate")),
        "ai_false_negative_rate": _safe_float(ai.get("false_negative_rate")),
        "score_bucket_available": bool(score_bucket.get("available")),
        "walk_forward": _walk_forward_snapshot(walk_forward_report),
        "warnings": [str(item) for item in (_listify(benchmark_report.get("warnings")) + _listify(completeness_report.get("warnings")) + _listify(walk_forward_report.get("warnings"))) if item],
    }


def _base_evidence(entity_type: str, name: str, context: dict[str, Any], extras: dict[str, Any] | None = None) -> dict[str, Any]:
    evidence = {
        "entity_type": entity_type,
        "sample_size": context["sample_size"],
        "rewardable_count": context["rewardable_count"],
        "benchmark_verdict": context["benchmark_verdict"],
        "data_quality_score": context["data_quality_score"],
        "completion_rate": context["completion_rate"],
        "rewardability_rate": context["rewardability_rate"],
        "benchmark_ready": context["benchmark_ready"],
        "baseline_relative_edge": context["baseline_relative_edge"],
        "score_bucket_lift": context["score_bucket_lift"],
        "forecast_accuracy": context["forecast_accuracy"],
        "execution_adjusted_reward": context["execution_adjusted_reward"],
        "walk_forward_status": context["walk_forward"].get("latest_status"),
        "walk_forward_verdict": context["walk_forward"].get("latest_verdict"),
        "walk_forward_experiment_id": context["walk_forward"].get("latest_experiment_id"),
        "name": name,
    }
    if extras:
        evidence.update(extras)
    return serialize_value(evidence)


def _entity(entity_type: str, key: str, name: str, context: dict[str, Any], extras: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "entity_id": f"{entity_type}:{_safe_entity_part(key)}",
        "entity_type": entity_type,
        "name": name,
        "evidence_used": _base_evidence(entity_type, name, context, extras),
    }


def _build_entities(context: dict[str, Any], benchmark_report: dict[str, Any]) -> list[dict[str, Any]]:
    sections = benchmark_report.get("sections") if isinstance(benchmark_report.get("sections"), dict) else {}
    entities: list[dict[str, Any]] = [
        _entity("strategy", "quant_evidence_os", "Quant Evidence OS paper strategy stack", context),
        _entity("ranking_rule", "score_bucket_separation_v1", "Score bucket separation rule", context, {"score_bucket_available": context["score_bucket_available"]}),
        _entity("risk_rule", "hard_gate_research_boundary_v1", "Hard risk gate research boundary", context, {"risk_gate_authority": "unchanged"}),
        _entity("AI_verdict_policy", "ai_referee_policy_v1", "AI Evidence Referee policy", context, {"false_positive_rate": context["ai_false_positive_rate"], "false_negative_rate": context["ai_false_negative_rate"]}),
    ]
    for row in _section_items(sections, "reward_by_setup"):
        setup = row.get("setup_type") or "unknown"
        entities.append(_entity("setup_type", setup, f"Setup: {setup}", context, row))
    for row in _section_items(sections, "reward_by_engine"):
        engine = row.get("engine") or "unknown"
        entities.append(_entity("engine", engine, f"Engine: {engine}", context, row))
    for row in _section_items(sections, "blocker_value"):
        blocker = row.get("blocker") or "unknown"
        entities.append(_entity("blocker", blocker, f"Blocker: {blocker}", context, row))
    forecast_items = _section_items(sections, "forecast_accuracy")
    if forecast_items:
        seen: set[str] = set()
        for row in forecast_items:
            model = row.get("model_name") or row.get("source") or "forecast_validation_v1"
            if str(model) in seen:
                continue
            seen.add(str(model))
            entities.append(_entity("forecast_model", model, f"Forecast model: {model}", context, row))
    else:
        entities.append(_entity("forecast_model", "forecast_validation_v1", "Forecast validation v1", context, {"missing_fields": ["forecast_model_outcomes"]}))
    return entities


def _criterion(label: str, passed: bool, detail: str, *, severity: str = "normal") -> dict[str, Any]:
    return {"criterion": label, "passed": bool(passed), "detail": detail, "severity": severity}


def _critical_missing_fields(context: dict[str, Any]) -> set[str]:
    rows = context.get("highest_priority_missing_fields") or []
    return {str(row.get("field")) for row in rows if isinstance(row, dict) and row.get("field")}


def evaluate_research_status(entity: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    evidence = entity.get("evidence_used") or {}
    sample_size = _safe_int(evidence.get("sample_size"))
    rewardable_count = _safe_int(evidence.get("rewardable_count"))
    baseline_edge = _safe_float(evidence.get("baseline_relative_edge"))
    data_quality = _safe_float(evidence.get("data_quality_score"))
    drawdown = _safe_float(evidence.get("max_drawdown") or context.get("max_drawdown"))
    slippage_reward = _safe_float(evidence.get("execution_adjusted_reward"))
    score_lift = _safe_float(evidence.get("score_bucket_lift"))
    forecast_accuracy = _safe_float(evidence.get("direction_accuracy") or evidence.get("forecast_accuracy"))
    false_positive_rate = _safe_float(evidence.get("false_positive_rate") or context.get("ai_false_positive_rate"))
    false_negative_rate = _safe_float(evidence.get("false_negative_rate") or context.get("ai_false_negative_rate"))
    false_block_rate = _safe_float(evidence.get("false_block_rate"))
    blocker_value = _safe_float(evidence.get("estimated_blocker_value"))
    completion_rate = _ratio_or_zero(evidence.get("completion_rate"))
    rewardability_rate = _ratio_or_zero(evidence.get("rewardability_rate"))
    benchmark_verdict = str(evidence.get("benchmark_verdict") or context.get("benchmark_verdict") or "").lower()
    missing_fields = _critical_missing_fields(context)
    walk_forward = context["walk_forward"]

    criteria = [
        _criterion("minimum sample size", sample_size >= MIN_SAMPLE_SIZE, f"{sample_size} samples / required {MIN_SAMPLE_SIZE}"),
        _criterion("minimum rewardable count", rewardable_count >= MIN_REWARDABLE_COUNT, f"{rewardable_count} rewardable / required {MIN_REWARDABLE_COUNT}"),
        _criterion("minimum data completeness", completion_rate >= MIN_COMPLETION_RATE, f"{completion_rate:.2f} completion / required {MIN_COMPLETION_RATE:.2f}"),
        _criterion("minimum rewardability", rewardability_rate >= MIN_REWARDABILITY_RATE, f"{rewardability_rate:.2f} rewardability / required {MIN_REWARDABILITY_RATE:.2f}"),
        _criterion("baseline comparison available", baseline_edge is not None, "Explicit baseline-relative edge is present."),
        _criterion("positive baseline-relative reward", baseline_edge is not None and baseline_edge >= PAPER_PROVEN_EDGE, f"{baseline_edge} edge / required {PAPER_PROVEN_EDGE}"),
        _criterion("score bucket separation", score_lift is not None and score_lift > 0, f"{score_lift} score bucket lift."),
        _criterion("frozen walk-forward experiment exists", bool(walk_forward.get("frozen_exists")), "Frozen experiment snapshot is available."),
        _criterion("walk-forward passed or weak-passed", bool(walk_forward.get("pass_or_weak_pass_exists")), f"latest verdict {walk_forward.get('latest_verdict') or 'none'}"),
        _criterion("acceptable drawdown", drawdown is None or drawdown <= MAX_ACCEPTABLE_DRAWDOWN, f"{drawdown} max drawdown / limit {MAX_ACCEPTABLE_DRAWDOWN}"),
        _criterion("acceptable execution-adjusted reward", slippage_reward is None or slippage_reward >= MAX_ACCEPTABLE_SLIPPAGE_DRAG, f"{slippage_reward} execution-adjusted reward."),
    ]
    severe_failures: list[str] = []
    if benchmark_verdict == "data_quality_too_weak" or (data_quality is not None and data_quality < 35):
        severe_failures.append("severe data quality failure")
    if baseline_edge is not None and baseline_edge <= -PAPER_PROVEN_EDGE:
        severe_failures.append("negative baseline-relative reward")
    if forecast_accuracy is not None and forecast_accuracy < MIN_FORECAST_ACCURACY and entity.get("entity_type") == "forecast_model":
        severe_failures.append("poor forecast accuracy")
    if false_positive_rate is not None and false_positive_rate > MAX_AI_FALSE_POSITIVE_RATE and entity.get("entity_type") == "AI_verdict_policy":
        severe_failures.append("high AI false-positive rate")
    if false_negative_rate is not None and false_negative_rate > MAX_AI_FALSE_NEGATIVE_RATE and entity.get("entity_type") == "AI_verdict_policy":
        severe_failures.append("high AI false-negative rate")
    if false_block_rate is not None and false_block_rate > MAX_BLOCKER_FALSE_BLOCK_RATE and entity.get("entity_type") == "blocker":
        severe_failures.append("severe blocker false-block rate")
    if blocker_value is not None and blocker_value < -PAPER_PROVEN_EDGE and entity.get("entity_type") == "blocker":
        severe_failures.append("severe blocker value failure")
    if slippage_reward is not None and slippage_reward < -PAPER_PROVEN_EDGE:
        severe_failures.append("poor execution-adjusted reward")

    missing_core = missing_fields.intersection({"actual_forward_return", "baseline_forward_return", "slippage", "spread_at_signal", "regime", "forecast_series", "actual_series"})
    if severe_failures and sample_size >= MIN_SAMPLE_SIZE:
        status = "rejected"
        explanation = "Rejected for research because severe evidence failures were observed."
    elif sample_size < MIN_SAMPLE_SIZE or rewardable_count < MIN_REWARDABLE_COUNT or missing_core:
        status = "needs_more_evidence"
        explanation = "Needs more evidence before research promotion can be trusted."
    elif (
        baseline_edge is not None
        and baseline_edge >= PAPER_PROVEN_EDGE
        and (drawdown is None or drawdown <= MAX_ACCEPTABLE_DRAWDOWN)
        and (slippage_reward is None or slippage_reward >= MAX_ACCEPTABLE_SLIPPAGE_DRAG)
        and (score_lift is None or score_lift > 0)
        and bool(walk_forward.get("pass_or_weak_pass_exists"))
    ):
        status = "paper_proven"
        explanation = "Paper-proven research status only; this is not live approval."
    elif bool(walk_forward.get("frozen_exists")) and bool(context.get("benchmark_ready")) and baseline_edge is not None and rewardable_count >= MIN_REWARDABLE_COUNT:
        status = "walk_forward_testing"
        explanation = "Frozen experiment exists and benchmark-ready evidence is available for forward testing."
    elif (
        sample_size >= MIN_SAMPLE_SIZE
        and completion_rate >= MIN_COMPLETION_RATE
        and rewardability_rate >= MIN_REWARDABILITY_RATE
        and benchmark_verdict in {"edge_detected", "weak_edge_detected"}
    ):
        status = "candidate"
        explanation = "Candidate research status; evidence is promising but not paper-proven."
    else:
        status = "research"
        explanation = "Research status; keep collecting and reviewing evidence."

    passed = [row for row in criteria if row["passed"]]
    failed = [row for row in criteria if not row["passed"]]
    warnings = [*context.get("warnings", [])]
    if severe_failures:
        warnings.extend(severe_failures)
    if status == "paper_proven":
        warnings.append("Paper-proven is a research status only. It does not authorize live trading.")
    return {
        "promotion_status": status,
        "criteria_passed": passed,
        "criteria_failed": failed,
        "safe_explanation": explanation,
        "warnings": list(dict.fromkeys(str(item) for item in warnings if item)),
    }


def _apply_manual_status(entity: dict[str, Any], manual_statuses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    manual = manual_statuses.get(str(entity.get("entity_id")))
    if not manual:
        return entity
    merged = dict(entity)
    merged["computed_promotion_status"] = entity.get("promotion_status")
    merged["manual_status"] = manual
    merged["promotion_status"] = manual.get("promotion_status") or entity.get("promotion_status")
    warnings = list(_listify(entity.get("warnings")))
    warnings.append("Manual research status metadata is applied. It is not live approval and does not change trading behavior.")
    merged["warnings"] = list(dict.fromkeys(str(item) for item in warnings if item))
    return merged


def _promotion_row_readiness(entity: dict[str, Any]) -> dict[str, Any]:
    evidence = _as_dict(entity.get("evidence_used"))
    manual = _as_dict(entity.get("manual_status"))
    status_traceable = _has_value(entity.get("promotion_status")) and _has_value(entity.get("safe_explanation"))
    criteria_traceable = bool(_listify(entity.get("criteria_passed")) or _listify(entity.get("criteria_failed")))
    benchmark_traceable = (
        _has_value(evidence.get("benchmark_verdict"))
        and _safe_int(evidence.get("sample_size")) > 0
        and _safe_int(evidence.get("rewardable_count")) > 0
        and _safe_float(evidence.get("baseline_relative_edge")) is not None
    )
    data_quality_traceable = (
        _safe_float(evidence.get("completion_rate")) is not None
        and _safe_float(evidence.get("rewardability_rate")) is not None
        and _safe_float(evidence.get("data_quality_score")) is not None
    )
    walk_forward_traceable = any(
        _has_value(evidence.get(field))
        for field in ("walk_forward_status", "walk_forward_verdict", "walk_forward_experiment_id")
    )
    execution_traceable = _safe_float(evidence.get("execution_adjusted_reward")) is not None
    manual_review_traceable = (
        _has_value(manual.get("promotion_status"))
        and _has_value(manual.get("reason"))
        and _has_value(manual.get("updated_at"))
        and _has_value(manual.get("previous_promotion_status"))
        and _has_value(manual.get("evidence_snapshot"))
    )
    safety_boundary_preserved = (
        bool(entity.get("research_only"))
        and not bool(entity.get("can_submit_orders"))
        and not bool(entity.get("can_submit_live_orders"))
        and not bool(entity.get("writes_execution_config"))
        and not bool(entity.get("writes_broker_config"))
        and not bool(entity.get("writes_risk_config"))
        and not bool(entity.get("writes_ranking_config"))
    )
    return serialize_value(
        {
            "entity_id": entity.get("entity_id"),
            "entity_type": entity.get("entity_type"),
            "name": entity.get("name"),
            "promotion_status": entity.get("promotion_status"),
            "status_traceable": status_traceable,
            "criteria_traceable": criteria_traceable,
            "benchmark_traceable": benchmark_traceable,
            "data_quality_traceable": data_quality_traceable,
            "walk_forward_traceable": walk_forward_traceable,
            "execution_traceable": execution_traceable,
            "manual_review_traceable": manual_review_traceable,
            "safety_boundary_preserved": safety_boundary_preserved,
        }
    )


def build_research_promotion_proof_summary(entities: list[dict[str, Any]]) -> dict[str, Any]:
    record_count = len(entities)
    readiness = [_promotion_row_readiness(entity) for entity in entities]

    def coverage(field: str) -> float:
        return _ratio(sum(1 for row in readiness if row.get(field)), record_count) or 0.0

    status_coverage = coverage("status_traceable")
    criteria_coverage = coverage("criteria_traceable")
    benchmark_coverage = coverage("benchmark_traceable")
    data_quality_coverage = coverage("data_quality_traceable")
    walk_forward_coverage = coverage("walk_forward_traceable")
    execution_coverage = coverage("execution_traceable")
    manual_review_count = sum(1 for row in readiness if row.get("manual_review_traceable"))
    safety_count = sum(1 for row in readiness if row.get("safety_boundary_preserved"))
    safety_boundary_preserved = 1 if record_count > 0 and safety_count == record_count else 0
    promotion_metadata_only = 1 if all(
        not bool(entity.get("can_submit_orders"))
        and not bool(entity.get("can_submit_live_orders"))
        and not bool(entity.get("writes_execution_config"))
        and not bool(entity.get("writes_broker_config"))
        and not bool(entity.get("writes_risk_config"))
        and not bool(entity.get("writes_ranking_config"))
        for entity in entities
    ) else 0
    promotion_traceability_coverage = round(
        (
            status_coverage
            + criteria_coverage
            + benchmark_coverage
            + data_quality_coverage
            + walk_forward_coverage
            + execution_coverage
        )
        / 6,
        6,
    )
    values = {
        "record_count": record_count,
        "status_traceability_coverage": status_coverage,
        "criteria_traceability_coverage": criteria_coverage,
        "benchmark_traceability_coverage": benchmark_coverage,
        "data_quality_traceability_coverage": data_quality_coverage,
        "walk_forward_traceability_coverage": walk_forward_coverage,
        "execution_traceability_coverage": execution_coverage,
        "manual_review_record_count": manual_review_count,
        "manual_review_traceability_coverage": _ratio(manual_review_count, record_count) or 0.0,
        "promotion_traceability_coverage": promotion_traceability_coverage,
        "promotion_metadata_only": promotion_metadata_only,
        "safety_boundary_preserved": safety_boundary_preserved,
    }
    requirement_rows: list[dict[str, Any]] = []
    for requirement in RESEARCH_PROMOTION_PROOF_REQUIREMENTS:
        value = values.get(str(requirement["metric"]))
        passed = _passes_threshold(value, requirement["threshold"], str(requirement["comparison"]))
        requirement_rows.append(
            {
                "key": requirement["key"],
                "label": requirement["label"],
                "metric": requirement["metric"],
                "status": "passed" if passed else "needs_evidence",
                "passed": passed,
                "value": value,
                "threshold": requirement["threshold"],
                "comparison": requirement["comparison"],
                "safe_next_action": requirement["safe_next_action"],
                "claim_boundary": "Research promotion proof is governance metadata only; it is not live approval, alpha proof, order authority, or permission to change risk, ranking, broker, or execution settings.",
                "research_only": True,
                "changes_execution": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
                "changes_risk_limits": False,
                "changes_ranking_weights": False,
            }
        )
    proof_ready = bool(requirement_rows) and all(row["passed"] for row in requirement_rows)
    return serialize_value(
        {
            "status": "ready_for_human_review" if proof_ready else "needs_evidence",
            "proof_ready": proof_ready,
            "requirements": requirement_rows,
            "summary": {
                **values,
                "requirement_count": len(requirement_rows),
                "passed_requirement_count": sum(1 for row in requirement_rows if row["passed"]),
                "missing_requirement_count": sum(1 for row in requirement_rows if not row["passed"]),
            },
            "record_readiness": readiness[:100],
            "safe_next_actions": [row["safe_next_action"] for row in requirement_rows if not row["passed"]],
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def build_research_promotion_cleanup_plan(
    *,
    entities: list[dict[str, Any]],
    proof_summary: dict[str, Any],
) -> dict[str, Any]:
    proof_rows = {
        str(row.get("key")): row
        for row in proof_summary.get("requirements") or []
        if isinstance(row, dict)
    }
    items: list[dict[str, Any]] = []
    for definition in RESEARCH_PROMOTION_CLEANUP_DEFINITIONS:
        proof_keys = tuple(definition.get("proof_keys") or ())
        related_rows = [
            proof_rows[key]
            for key in proof_keys
            if isinstance(proof_rows.get(key), dict)
        ]
        passed = bool(related_rows) and all(bool(row.get("passed")) for row in related_rows)
        status = "no_records" if not entities and definition["key"] != "metadata_only_safety_governance" else "ready" if passed else "needs_evidence"
        safe_next_actions = [
            str(row.get("safe_next_action"))
            for row in related_rows
            if row.get("safe_next_action")
        ] or [str(definition["safe_next_action"])]
        items.append(
            {
                "key": definition["key"],
                "title": definition["title"],
                "priority": definition["priority"],
                "status": status,
                "passed": passed,
                "proof_keys": list(proof_keys),
                "values": {str(row.get("metric")): row.get("value") for row in related_rows},
                "missing_fields": list(definition.get("missing_fields") or ()),
                "blocked_claims": list(definition.get("blocked_claims") or ()),
                "safe_next_action": safe_next_actions[0],
                "safe_next_actions": safe_next_actions,
                "done_when": definition["done_when"],
                "claim_boundary": "Research Promotion cleanup is internal governance metadata only; it is not live approval, alpha proof, paper-to-live readiness, or permission to mutate execution, broker, risk, ranking, or strategy policy.",
                "manual_review_only": True,
                "research_only": True,
                "changes_execution": False,
                "changes_order_submission": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
                "changes_risk_limits": False,
                "changes_ranking_weights": False,
                "clears_kill_switch": False,
                "can_submit_orders": False,
                "can_submit_live_orders": False,
            }
        )

    open_items = [row for row in items if row["status"] != "ready"]
    critical_open_items = [row for row in open_items if row.get("priority") == "critical"]
    proof_ready = bool(proof_summary.get("proof_ready"))
    return serialize_value(
        {
            "status": "ready_for_human_review" if proof_ready and not open_items else "blocked_by_evidence",
            "summary": {
                "item_count": len(items),
                "open_item_count": len(open_items),
                "critical_open_items": len(critical_open_items),
                "ready_item_count": len(items) - len(open_items),
                "top_cleanup_item": open_items[0]["title"] if open_items else None,
                "proof_first_rule": "Ambition is allowed. Proof decides priority.",
                "claim_permissions": {
                    "cautious_internal_promotion_review": proof_ready,
                    "paper_proven_research_review": proof_ready,
                    "small_fund_governance_claim": False,
                    "automatic_strategy_promotion": False,
                    "ranking_weight_change": False,
                    "risk_limit_change": False,
                    "broker_route_change": False,
                    "paper_to_live_readiness": False,
                    "live_trading_readiness": False,
                },
                "blocked_claims": [
                    "paper_proven_research_claim",
                    "small_fund_governance_claim",
                    "automatic_strategy_promotion",
                    "ranking_weight_change",
                    "risk_limit_change",
                    "broker_route_change",
                    "paper_to_live_readiness",
                    "live_trading_readiness",
                ],
                "safe_boundary": "Research Promotion cleanup records missing governance evidence and blocked claims only. It does not approve live trading, place orders, change broker routes, clear kill switches, bypass risk gates, change risk limits, change strategy configs, or mutate ranking weights.",
            },
            "items": items,
            "safe_next_actions": [
                {
                    "field": row["key"],
                    "action": row["safe_next_action"],
                    "manual_review_only": True,
                    "changes_execution": False,
                    "changes_order_submission": False,
                    "changes_broker_routes": False,
                    "changes_risk_gates": False,
                    "changes_risk_limits": False,
                    "changes_ranking_weights": False,
                }
                for row in open_items
            ],
            "research_only": True,
            **SAFETY_FLAGS,
        }
    )


def _build_report_from_sources(
    *,
    benchmark_report: dict[str, Any],
    completeness_report: dict[str, Any],
    walk_forward_report: dict[str, Any],
    manual_statuses: dict[str, dict[str, Any]] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    context = _evidence_context(benchmark_report=benchmark_report, completeness_report=completeness_report, walk_forward_report=walk_forward_report)
    raw_entities = _build_entities(context, benchmark_report)
    entities = []
    for entity in raw_entities:
        evaluated = {**entity, **evaluate_research_status(entity, context)}
        evaluated = _apply_manual_status(evaluated, manual_statuses or {})
        evaluated.update(SAFETY_FLAGS)
        evaluated["safety_notes"] = list(SAFETY_NOTES)
        entities.append(serialize_value(evaluated))
    status_counts: dict[str, int] = {status: 0 for status in PROMOTION_STATUSES}
    type_counts: dict[str, int] = {entity_type: 0 for entity_type in ENTITY_TYPES}
    for entity in entities:
        status_counts[str(entity.get("promotion_status"))] = status_counts.get(str(entity.get("promotion_status")), 0) + 1
        type_counts[str(entity.get("entity_type"))] = type_counts.get(str(entity.get("entity_type")), 0) + 1
    proof_summary = build_research_promotion_proof_summary(entities)
    cleanup_plan = build_research_promotion_cleanup_plan(entities=entities, proof_summary=proof_summary)
    summary = {
        "entity_count": len(entities),
        "status_counts": status_counts,
        "type_counts": type_counts,
        "paper_proven_count": status_counts.get("paper_proven", 0),
        "needs_more_evidence_count": status_counts.get("needs_more_evidence", 0),
        "rejected_count": status_counts.get("rejected", 0),
        "benchmark_verdict": context["benchmark_verdict"],
        "walk_forward_status": context["walk_forward"].get("latest_status"),
        "walk_forward_verdict": context["walk_forward"].get("latest_verdict"),
        "data_quality_score": context["data_quality_score"],
        "promotion_proof_ready": proof_summary["proof_ready"],
        "promotion_proof_status": proof_summary["status"],
        "promotion_requirements_passed": proof_summary["summary"]["passed_requirement_count"],
        "promotion_requirements_total": proof_summary["summary"]["requirement_count"],
        "promotion_traceability_coverage": proof_summary["summary"]["promotion_traceability_coverage"],
        "benchmark_traceability_coverage": proof_summary["summary"]["benchmark_traceability_coverage"],
        "walk_forward_traceability_coverage": proof_summary["summary"]["walk_forward_traceability_coverage"],
        "execution_traceability_coverage": proof_summary["summary"]["execution_traceability_coverage"],
        "manual_review_record_count": proof_summary["summary"]["manual_review_record_count"],
        "research_promotion_cleanup_status": cleanup_plan["status"],
        "research_promotion_cleanup_open_items": cleanup_plan["summary"]["open_item_count"],
        "research_promotion_cleanup_critical_open_items": cleanup_plan["summary"]["critical_open_items"],
        "top_cleanup_item": cleanup_plan["summary"]["top_cleanup_item"],
        "claim_permissions": cleanup_plan["summary"]["claim_permissions"],
        **SAFETY_FLAGS,
    }
    warnings = list(dict.fromkeys(str(item) for item in context.get("warnings", []) if item))
    if not proof_summary["proof_ready"]:
        warnings.append("Research promotion proof requirements are incomplete.")
    return serialize_value(
        {
            "status": "ready" if entities else "empty",
            "generated_at": generated_at or _utc_now(),
            "research_only": True,
            "summary": summary,
            "promotion_status": "summary",
            "record": None,
            "records": entities,
            "evidence_used": {
                "benchmark_verdict": context["benchmark_verdict"],
                "walk_forward": context["walk_forward"],
                "benchmark_ready": context["benchmark_ready"],
            },
            "proof_summary": proof_summary,
            "research_promotion_cleanup_plan": cleanup_plan,
            "aggregations": {"research_promotion_proof": proof_summary, "research_promotion_cleanup_plan": cleanup_plan},
            "warnings": warnings,
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
            "finish_tracker": build_project_finish_tracker(report_name="research_promotion"),
        }
    )


def build_research_promotion_report(
    *,
    benchmark_report: dict[str, Any] | None = None,
    completeness_report: dict[str, Any] | None = None,
    walk_forward_report: dict[str, Any] | None = None,
    manual_statuses: dict[str, dict[str, Any]] | None = None,
    db: Any = None,
    current_user: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    benchmark = benchmark_report or get_professional_benchmark_summary(db, current_user=current_user)
    completeness = completeness_report or get_data_completeness_summary(db, current_user=current_user)
    walk_forward = walk_forward_report or get_walk_forward_experiments()
    return _build_report_from_sources(
        benchmark_report=benchmark,
        completeness_report=completeness,
        walk_forward_report=walk_forward,
        manual_statuses=manual_statuses,
        generated_at=generated_at,
    )


def get_research_promotion_summary(db: Any = None, *, current_user: Any = None, store_path: Path | str | None = None) -> dict[str, Any]:
    return build_research_promotion_report(db=db, current_user=current_user, manual_statuses=_read_manual_statuses(store_path))


def get_research_promotion_entities(db: Any = None, *, current_user: Any = None, store_path: Path | str | None = None) -> dict[str, Any]:
    return get_research_promotion_summary(db, current_user=current_user, store_path=store_path)


def get_research_promotion_entity(entity_id: str, db: Any = None, *, current_user: Any = None, store_path: Path | str | None = None) -> dict[str, Any]:
    report = get_research_promotion_summary(db, current_user=current_user, store_path=store_path)
    for entity in report.get("records") or []:
        if str(entity.get("entity_id")) == str(entity_id):
            return {**report, "record": entity, "records": [entity], "promotion_status": entity.get("promotion_status")}
    raise NotFoundError("Research promotion entity was not found.", details={"entity_id": entity_id})


def update_research_promotion_status(
    entity_id: str,
    payload: dict[str, Any],
    *,
    db: Any = None,
    current_user: Any = None,
    store_path: Path | str | None = None,
) -> dict[str, Any]:
    status = str((payload or {}).get("promotion_status") or "").strip()
    if status not in PROMOTION_STATUSES:
        raise ValidationServiceError(
            "Invalid research promotion status.",
            details={"promotion_status": status, "valid_statuses": list(PROMOTION_STATUSES)},
        )
    current_report = get_research_promotion_summary(db, current_user=current_user, store_path=store_path)
    entity = next((row for row in current_report.get("records") or [] if str(row.get("entity_id")) == str(entity_id)), None)
    if entity is None:
        raise NotFoundError("Research promotion entity was not found.", details={"entity_id": entity_id})
    statuses = _read_manual_statuses(store_path)
    previous_manual = statuses.get(str(entity_id)) or {}
    updated_at = _utc_now()
    evidence = _as_dict(entity.get("evidence_used"))
    statuses[str(entity_id)] = _sanitize_value(
        {
            "entity_id": entity_id,
            "promotion_status": status,
            "reason": (payload or {}).get("reason") or "Manual research status metadata update.",
            "updated_at": updated_at,
            "updated_by": _created_by(current_user),
            "previous_promotion_status": previous_manual.get("promotion_status") or entity.get("promotion_status"),
            "computed_promotion_status": entity.get("computed_promotion_status") or entity.get("promotion_status"),
            "review_action": "manual_research_status_update",
            "approval_trace_id": f"research_promotion:{_safe_entity_part(entity_id)}:{_safe_entity_part(updated_at)}",
            "evidence_snapshot": {
                "benchmark_verdict": evidence.get("benchmark_verdict"),
                "sample_size": evidence.get("sample_size"),
                "rewardable_count": evidence.get("rewardable_count"),
                "baseline_relative_edge": evidence.get("baseline_relative_edge"),
                "score_bucket_lift": evidence.get("score_bucket_lift"),
                "walk_forward_status": evidence.get("walk_forward_status"),
                "walk_forward_verdict": evidence.get("walk_forward_verdict"),
                "walk_forward_experiment_id": evidence.get("walk_forward_experiment_id"),
                "execution_adjusted_reward": evidence.get("execution_adjusted_reward"),
                "completion_rate": evidence.get("completion_rate"),
                "rewardability_rate": evidence.get("rewardability_rate"),
            },
            "research_only": True,
            **SAFETY_FLAGS,
        }
    )
    _write_manual_statuses(statuses, store_path)
    report = get_research_promotion_summary(db, current_user=current_user, store_path=store_path)
    record = next(row for row in report.get("records") or [] if str(row.get("entity_id")) == str(entity_id))
    return {**report, "status": "updated", "record": record, "records": [record], "promotion_status": record.get("promotion_status")}
