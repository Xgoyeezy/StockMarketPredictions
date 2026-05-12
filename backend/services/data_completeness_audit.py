from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from backend.services.evidence_reward_engine import get_evidence_reward_summary
from backend.services.forecast_validation_engine import get_forecast_validation_summary
from backend.services.professional_benchmark_suite import get_professional_benchmark_summary
from backend.services.project_finish_tracker import build_project_finish_tracker
from backend.services.productized_control_plane_service import execution_quality_summary
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

CONTRACTS: dict[str, tuple[dict[str, Any], ...]] = {
    "candidate": (
        {"name": "symbol", "fields": ("symbol",)},
        {"name": "timestamp", "fields": ("timestamp", "prediction_created_at", "created_at", "scan_time")},
        {"name": "engine", "fields": ("engine",)},
        {"name": "setup_type", "fields": ("setup_type", "opportunity_type")},
        {"name": "score", "fields": ("score",)},
        {"name": "allowed_or_blocked", "fields": ("allowed", "blocked"), "mode": "present_any"},
        {"name": "blockers", "fields": ("blockers", "blocker"), "mode": "blocker_context"},
        {"name": "actual_forward_return", "fields": ("actual_forward_return", "actual_forward_return_pct", "forward_return_30m_pct")},
        {"name": "baseline_forward_return", "fields": ("baseline_forward_return", "baseline_return")},
    ),
    "forecast": (
        {"name": "prediction_id", "fields": ("prediction_id",)},
        {"name": "symbol", "fields": ("symbol",)},
        {"name": "prediction_created_at", "fields": ("prediction_created_at", "timestamp")},
        {"name": "horizon_minutes", "fields": ("horizon_minutes", "prediction_horizon_minutes")},
        {"name": "forecast_series", "fields": ("forecast_series", "predicted_series")},
        {"name": "predicted_direction", "fields": ("predicted_direction", "forecast_direction")},
        {"name": "predicted_target_pct", "fields": ("predicted_target_pct", "target_pct")},
        {"name": "invalidation_level", "fields": ("invalidation_level", "invalidation_price")},
        {"name": "confidence", "fields": ("confidence", "forecast_confidence")},
        {"name": "actual_series", "fields": ("actual_series",)},
        {"name": "actual_forward_return", "fields": ("actual_forward_return", "actual_forward_return_pct")},
        {"name": "baseline_forward_return", "fields": ("baseline_forward_return", "baseline_return")},
    ),
    "execution": (
        {"name": "symbol", "fields": ("symbol",)},
        {"name": "timestamp", "fields": ("timestamp", "created_at", "submitted_at")},
        {"name": "order_id_or_trade_id", "fields": ("order_id", "trade_id", "broker_order_id", "order_event_id")},
        {"name": "intended_price", "fields": ("intended_price", "expected_price", "submitted_price")},
        {"name": "fill_price", "fields": ("fill_price", "filled_price")},
        {"name": "spread_at_signal", "fields": ("spread_at_signal", "spread_bps")},
        {"name": "slippage", "fields": ("slippage", "slippage_bps")},
        {"name": "fill_delay", "fields": ("fill_delay", "latency_ms", "fill_delay_ms")},
        {"name": "route", "fields": ("route", "route_state", "broker")},
        {"name": "paper_fill_status", "fields": ("paper_fill_status", "status", "route_state")},
    ),
    "ai_review": (
        {"name": "symbol", "fields": ("symbol",)},
        {"name": "timestamp", "fields": ("timestamp", "prediction_created_at", "created_at")},
        {"name": "ai_verdict", "fields": ("ai_verdict", "verdict")},
        {"name": "confidence", "fields": ("confidence", "ai_confidence")},
        {"name": "reason", "fields": ("reason", "ai_reason", "not_rewarded_reason")},
        {"name": "linked_candidate_id", "fields": ("linked_candidate_id", "evidence_id", "candidate_lifecycle_id", "record_id")},
        {"name": "actual_outcome", "fields": ("actual_outcome", "actual_forward_return", "total_reward")},
    ),
    "blocker": (
        {"name": "symbol", "fields": ("symbol",)},
        {"name": "timestamp", "fields": ("timestamp", "prediction_created_at", "created_at")},
        {"name": "blocked_reason", "fields": ("blocked_reason", "blocker", "blockers")},
        {"name": "actual_forward_return", "fields": ("actual_forward_return", "actual_forward_return_pct")},
        {"name": "baseline_forward_return", "fields": ("baseline_forward_return", "baseline_return")},
    ),
    "missed_move": (
        {"name": "symbol", "fields": ("symbol",)},
        {"name": "timestamp", "fields": ("timestamp", "prediction_created_at", "created_at")},
        {"name": "blocked_reason", "fields": ("blocked_reason", "blocker", "blockers")},
        {"name": "forward_return", "fields": ("forward_return", "actual_forward_return", "actual_forward_return_pct")},
        {"name": "baseline_forward_return", "fields": ("baseline_forward_return", "baseline_return")},
        {"name": "move_magnitude", "fields": ("move_magnitude", "missed_move_magnitude")},
        {"name": "recoverable_flag", "fields": ("recoverable_flag", "would_catch_now", "recoverable")},
    ),
    "paper_trade": (
        {"name": "symbol", "fields": ("symbol",)},
        {"name": "timestamp", "fields": ("timestamp", "created_at", "submitted_at", "filled_at")},
        {"name": "order_id_or_trade_id", "fields": ("order_id", "trade_id", "broker_order_id", "candidate_lifecycle_id")},
        {"name": "route", "fields": ("route", "route_state", "broker")},
        {"name": "paper_fill_status", "fields": ("paper_fill_status", "status", "route_state")},
        {"name": "fill_price", "fields": ("fill_price", "filled_price")},
        {"name": "paper_trade_outcome", "fields": ("paper_trade_outcome", "realized_return", "total_reward")},
    ),
    "benchmark": (
        {"name": "status", "fields": ("status",)},
        {"name": "generated_at", "fields": ("generated_at",)},
        {"name": "benchmark_verdict", "fields": ("benchmark_verdict",)},
        {"name": "candidate_count", "fields": ("candidate_count",)},
        {"name": "rewardable_count", "fields": ("rewardable_count",)},
        {"name": "missing_fields", "fields": ("missing_fields",), "mode": "present_any"},
    ),
}

SOURCE_LABELS: dict[str, str] = {
    "candidate": "Candidate records",
    "forecast": "Forecast validation records",
    "ai_review": "AI review records",
    "blocker": "Blocker records",
    "missed_move": "Missed move records",
    "paper_trade": "Paper trade outcome records",
    "execution": "Execution quality records",
    "benchmark": "Benchmark readiness records",
}

PROOF_FIELD_REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "key": "forward_returns",
        "label": "Forward return outcomes",
        "source_types": ("candidate", "forecast", "blocker", "missed_move"),
        "threshold": 0.80,
        "field_groups": (
            {"key": "forward_return", "label": "actual forward return", "fields": ("actual_forward_return", "forward_return")},
        ),
        "safe_next_action": "Stamp forward returns only after each candidate or forecast horizon closes.",
    },
    {
        "key": "baseline_returns",
        "label": "Baseline returns",
        "source_types": ("candidate", "forecast", "blocker", "missed_move"),
        "threshold": 0.80,
        "field_groups": (
            {"key": "baseline_return", "label": "same-window baseline return", "fields": ("baseline_forward_return", "baseline_return")},
        ),
        "safe_next_action": "Attach matched baseline returns at the same timestamp and horizon before benchmark claims.",
    },
    {
        "key": "forecast_actuals",
        "label": "Forecast actuals",
        "source_types": ("forecast",),
        "threshold": 0.80,
        "field_groups": (
            {"key": "actual_series", "label": "actual path series", "fields": ("actual_series",)},
            {"key": "actual_forward_return", "label": "forecast actual forward return", "fields": ("actual_forward_return",)},
        ),
        "safe_next_action": "Store actual post-forecast path data separately from immutable forecast contracts.",
    },
    {
        "key": "execution_costs",
        "label": "Execution costs",
        "source_types": ("execution",),
        "threshold": 0.80,
        "field_groups": (
            {"key": "spread_at_signal", "label": "spread at signal", "fields": ("spread_at_signal",)},
            {"key": "slippage", "label": "slippage", "fields": ("slippage",)},
            {"key": "fill_delay", "label": "fill delay", "fields": ("fill_delay",)},
        ),
        "safe_next_action": "Capture spread, slippage, and fill delay for paper execution-quality review without changing routes.",
    },
    {
        "key": "regime_labels",
        "label": "Regime labels",
        "source_types": ("candidate", "forecast", "blocker", "missed_move", "paper_trade", "execution"),
        "threshold": 0.80,
        "field_groups": (
            {"key": "regime", "label": "market or volatility regime", "fields": ("regime", "market_regime", "volatility_regime")},
        ),
        "safe_next_action": "Add point-in-time regime labels so benchmark and walk-forward reports can separate market conditions.",
    },
    {
        "key": "required_reward_fields",
        "label": "Required reward fields",
        "source_types": ("candidate",),
        "threshold": 0.80,
        "field_groups": (
            {"key": "symbol", "label": "symbol", "fields": ("symbol",)},
            {"key": "timestamp", "label": "timestamp", "fields": ("timestamp",)},
            {"key": "engine", "label": "engine", "fields": ("engine",)},
            {"key": "setup_type", "label": "setup type", "fields": ("setup_type",)},
            {"key": "score", "label": "score", "fields": ("score",)},
            {"key": "allowed_or_blocked", "label": "allowed or blocked state", "fields": ("allowed_or_blocked",)},
            {"key": "actual_forward_return", "label": "actual forward return", "fields": ("actual_forward_return",)},
            {"key": "baseline_forward_return", "label": "baseline forward return", "fields": ("baseline_forward_return",)},
        ),
        "safe_next_action": "Complete candidate reward contracts before using records as benchmark-ready proof.",
    },
)

CLEANUP_PLAN_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "missing_forward_returns",
        "title": "Missing forward returns",
        "priority": "critical",
        "source_types": ("candidate", "forecast", "blocker", "missed_move"),
        "fields": ("actual_forward_return", "forward_return"),
        "proof_requirements": ("forward_returns",),
        "blocks": ("Professional Benchmark", "Evidence Reward", "Walk-Forward", "Score Calibration"),
        "safe_next_action": "Stamp actual forward returns only after the candidate, blocker, or forecast horizon closes with observed market data.",
        "done_when": "Forward-return coverage reaches the configured proof threshold without using current prices as substitutes.",
    },
    {
        "key": "missing_baselines",
        "title": "Missing baselines",
        "priority": "critical",
        "source_types": ("candidate", "forecast", "blocker", "missed_move"),
        "fields": ("baseline_forward_return", "baseline_return"),
        "proof_requirements": ("baseline_returns",),
        "blocks": ("Professional Benchmark", "Evidence Reward", "Walk-Forward", "Score Calibration"),
        "safe_next_action": "Attach same-window SPY, QQQ, or configured peer baseline returns at the same timestamp and horizon as each evaluated record.",
        "done_when": "Baseline coverage reaches the configured proof threshold and every benchmark comparison cites a same-window baseline.",
    },
    {
        "key": "missing_forecast_actuals",
        "title": "Missing forecast actuals",
        "priority": "high",
        "source_types": ("forecast",),
        "fields": ("actual_series", "actual_forward_return"),
        "proof_requirements": ("forecast_actuals",),
        "blocks": ("Forecast Validation", "Professional Benchmark", "Walk-Forward"),
        "safe_next_action": "Store actual post-forecast path data separately from immutable prediction contracts after the forecast window matures.",
        "done_when": "Forecast actual-series and forward-return coverage reaches the configured proof threshold.",
    },
    {
        "key": "missing_execution_evidence",
        "title": "Missing execution evidence",
        "priority": "high",
        "source_types": ("execution", "paper_trade"),
        "fields": ("spread_at_signal", "slippage", "fill_delay", "route", "fill_price", "order_id_or_trade_id", "paper_trade_outcome"),
        "proof_requirements": ("execution_costs",),
        "blocks": ("Professional Benchmark", "Execution Quality", "Score Calibration", "Research Promotion"),
        "safe_next_action": "Capture spread, slippage, fill delay, route, and paper-fill linkage for paper execution review without changing routes.",
        "done_when": "Execution-cost coverage reaches the configured proof threshold with candidate-to-fill lineage.",
    },
    {
        "key": "missing_lineage_and_context",
        "title": "Missing lineage and context",
        "priority": "high",
        "source_types": ("candidate", "forecast", "ai_review", "blocker", "missed_move", "paper_trade", "execution"),
        "fields": ("timestamp", "prediction_created_at", "prediction_horizon_minutes", "engine", "setup_type", "regime"),
        "proof_requirements": ("regime_labels",),
        "blocks": ("Data Completeness", "Walk-Forward", "Score Calibration", "Audit Trail"),
        "safe_next_action": "Backfill only point-in-time metadata that is already known from the original event, source file, or append-only lifecycle artifact.",
        "done_when": "Timestamp, engine, setup, horizon, and regime coverage is sufficient for grouped proof and audit review.",
    },
    {
        "key": "missing_reward_contract_fields",
        "title": "Missing reward contract fields",
        "priority": "critical",
        "source_types": ("candidate", "ai_review", "paper_trade"),
        "fields": ("score", "allowed_or_blocked", "blockers", "actual_outcome", "paper_trade_outcome", "total_reward"),
        "proof_requirements": ("required_reward_fields",),
        "blocks": ("Evidence Reward", "Professional Benchmark", "Research Promotion"),
        "safe_next_action": "Complete reward contract fields from recorded candidate, AI review, blocker, and paper-trade evidence before using rows for benchmark proof.",
        "done_when": "Candidate reward contracts are complete enough to be rewardable and benchmarkable with explicit blockers or allowed state.",
    },
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple) or isinstance(value, set):
        return list(value)
    return [value]


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list) or isinstance(value, tuple) or isinstance(value, set):
        return len(value) > 0
    if isinstance(value, dict):
        return bool(value)
    return True


def _nested_sources(row: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [row]
    for key in ("prediction_contract", "forecast", "evaluation", "summary", "payload"):
        value = row.get(key)
        if isinstance(value, dict):
            sources.append(value)
    return sources


def _first_value(row: dict[str, Any], fields: Iterable[str]) -> Any:
    for source in _nested_sources(row):
        for field in fields:
            if field in source and _has_value(source.get(field)):
                return source.get(field)
    return None


def _has_any_field_present(row: dict[str, Any], fields: Iterable[str]) -> bool:
    for source in _nested_sources(row):
        for field in fields:
            if field in source:
                return True
    return False


def _field_missing(row: dict[str, Any], requirement: dict[str, Any]) -> bool:
    fields = tuple(requirement.get("fields") or ())
    mode = requirement.get("mode")
    if mode == "present_any":
        return not _has_any_field_present(row, fields)
    if mode == "blocker_context":
        blocked = bool(_first_value(row, ("blocked",)))
        if blocked:
            return _first_value(row, fields) is None
        return not _has_any_field_present(row, fields)
    return _first_value(row, fields) is None


def _field_value(row: dict[str, Any], name: str, fields: Iterable[str]) -> Any:
    if name == "missing_fields":
        return _listify(row.get("missing_fields"))
    return _first_value(row, fields)


def _source_id(source_type: str, row: dict[str, Any], index: int) -> str:
    fields = (
        "record_id",
        "candidate_lifecycle_id",
        "prediction_id",
        "trade_id",
        "order_id",
        "id",
        "symbol",
    )
    value = _first_value(row, fields)
    if value is None:
        return f"{source_type}-{index + 1}"
    return str(value)


def _source_label(source_type: str) -> str:
    return SOURCE_LABELS.get(source_type, source_type.replace("_", " ").title())


def audit_record(row: dict[str, Any], *, source_type: str, index: int = 0) -> dict[str, Any]:
    contract = CONTRACTS[source_type]
    missing_fields = [requirement["name"] for requirement in contract if _field_missing(row, requirement)]
    complete = not missing_fields
    rewardable = bool(row.get("rewardable")) if source_type in {"candidate", "forecast"} and row.get("rewardable") is not None else complete
    if source_type not in {"candidate", "forecast"}:
        rewardable = complete
    reason = (
        f"{_source_label(source_type)} satisfy the completeness contract."
        if complete
        else f"Missing required {source_type.replace('_', ' ')} fields: {', '.join(missing_fields)}."
    )
    warnings: list[str] = []
    if not complete:
        warnings.append("Record is visible but not complete enough for reward or benchmark attribution.")
    if row.get("simulation_evidence") or row.get("evidence_pool") == "simulation_evidence":
        warnings.append("Simulation evidence remains separate and is not counted as real-time market-observed evidence.")
        rewardable = False
    clean_fields = {
        requirement["name"]: _field_value(row, requirement["name"], requirement.get("fields") or ())
        for requirement in contract
    }
    return {
        "source_type": source_type,
        "source_label": _source_label(source_type),
        "record_id": _source_id(source_type, row, index),
        "symbol": _first_value(row, ("symbol",)),
        "timestamp": _first_value(row, ("timestamp", "prediction_created_at", "created_at")),
        "engine": _first_value(row, ("engine",)),
        "setup_type": _first_value(row, ("setup_type", "opportunity_type")),
        "regime": _first_value(row, ("regime", "market_regime")),
        "complete": complete,
        "rewardable": bool(rewardable and complete),
        "missing_fields": missing_fields,
        "reason": reason,
        "warnings": warnings,
        "fields": clean_fields,
    }


def _audit_records(records: Iterable[dict[str, Any]] | None, source_type: str) -> list[dict[str, Any]]:
    return [
        audit_record(row, source_type=source_type, index=index)
        for index, row in enumerate(records or [])
        if isinstance(row, dict)
    ]


def _rates(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    complete = sum(1 for row in records if row.get("complete"))
    rewardable = sum(1 for row in records if row.get("rewardable"))
    return {
        "total_records": total,
        "complete_records": complete,
        "incomplete_records": total - complete,
        "rewardable_records": rewardable,
        "non_rewardable_records": total - rewardable,
        "completion_rate": round(complete / total, 6) if total else 0.0,
        "rewardability_rate": round(rewardable / total, 6) if total else 0.0,
    }


def aggregate_completeness(records: list[dict[str, Any]]) -> dict[str, Any]:
    missing_counter: Counter[str] = Counter()
    missing_by_source: dict[str, Counter[str]] = defaultdict(Counter)
    missing_by_engine: dict[str, Counter[str]] = defaultdict(Counter)
    missing_by_setup_type: dict[str, Counter[str]] = defaultdict(Counter)
    missing_by_regime: dict[str, Counter[str]] = defaultdict(Counter)
    for row in records:
        source_type = str(row.get("source_type") or "unknown")
        engine = str(row.get("engine") or "unknown")
        setup_type = str(row.get("setup_type") or "unknown")
        regime = str(row.get("regime") or "unknown")
        for field in row.get("missing_fields") or []:
            missing_counter[field] += 1
            missing_by_source[source_type][field] += 1
            missing_by_engine[engine][field] += 1
            missing_by_setup_type[setup_type][field] += 1
            missing_by_regime[regime][field] += 1
    rates = _rates(records)
    return {
        **rates,
        "missing_field_counts": dict(missing_counter),
        "missing_by_source": {key: dict(value) for key, value in missing_by_source.items()},
        "missing_by_engine": {key: dict(value) for key, value in missing_by_engine.items()},
        "missing_by_setup_type": {key: dict(value) for key, value in missing_by_setup_type.items()},
        "missing_by_regime": {key: dict(value) for key, value in missing_by_regime.items()},
        "highest_priority_missing_fields": [
            {"field": field, "count": count}
            for field, count in missing_counter.most_common(10)
        ],
        "benchmark_blockers": [
            {"field": field, "count": count}
            for field, count in missing_counter.most_common(10)
            if field in {"actual_forward_return", "baseline_forward_return", "forecast_series", "actual_series", "slippage", "spread_at_signal", "prediction_horizon_minutes", "predicted_target_pct"}
        ],
    }


def _audited_field_value(row: dict[str, Any], fields: Iterable[str]) -> Any:
    field_payload = row.get("fields") if isinstance(row.get("fields"), dict) else {}
    for field in fields:
        if field in row and _has_value(row.get(field)):
            return row.get(field)
        if field in field_payload and _has_value(field_payload.get(field)):
            return field_payload.get(field)
    return None


def _proof_group_missing(row: dict[str, Any], field_group: dict[str, Any]) -> bool:
    return _audited_field_value(row, tuple(field_group.get("fields") or ())) is None


def build_proof_field_coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for requirement in PROOF_FIELD_REQUIREMENTS:
        source_types = set(requirement.get("source_types") or ())
        eligible = [row for row in records if row.get("source_type") in source_types]
        missing_group_counts: Counter[str] = Counter()
        missing_by_source: dict[str, Counter[str]] = defaultdict(Counter)
        pass_count = 0
        for row in eligible:
            missing_groups = [
                str(group["key"])
                for group in tuple(requirement.get("field_groups") or ())
                if _proof_group_missing(row, group)
            ]
            if not missing_groups:
                pass_count += 1
                continue
            source_type = str(row.get("source_type") or "unknown")
            for group_key in missing_groups:
                missing_group_counts[group_key] += 1
                missing_by_source[source_type][group_key] += 1
        total = len(eligible)
        coverage_rate = round(pass_count / total, 6) if total else 0.0
        threshold = float(requirement.get("threshold") or 1.0)
        if not total:
            status = "no_records"
        elif coverage_rate >= threshold:
            status = "ready"
        else:
            status = "needs_attention"
        rows.append(
            {
                "key": requirement["key"],
                "label": requirement["label"],
                "status": status,
                "source_types": sorted(source_types),
                "required_groups": [
                    {
                        "key": group["key"],
                        "label": group["label"],
                        "accepted_fields": list(group.get("fields") or ()),
                    }
                    for group in tuple(requirement.get("field_groups") or ())
                ],
                "eligible_records": total,
                "records_with_required_fields": pass_count,
                "records_missing_required_fields": total - pass_count,
                "coverage_rate": coverage_rate,
                "threshold": threshold,
                "missing_group_counts": dict(missing_group_counts),
                "missing_by_source": {key: dict(value) for key, value in missing_by_source.items()},
                "safe_next_action": requirement["safe_next_action"],
                "research_only": True,
                "manual_review_only": True,
                "changes_execution": False,
                "changes_ranking_weights": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
            }
        )
    ready_count = sum(1 for row in rows if row["status"] == "ready")
    missing_rows = [row for row in rows if row["status"] != "ready"]
    average_coverage = round(sum(float(row["coverage_rate"]) for row in rows) / len(rows), 6) if rows else 0.0
    return {
        "status": "ready" if not missing_rows else "needs_attention",
        "summary": {
            "requirement_count": len(rows),
            "ready_requirement_count": ready_count,
            "missing_requirement_count": len(rows) - ready_count,
            "average_coverage_rate": average_coverage,
            "proof_ready": not missing_rows,
            "claim_boundary": "Proof-field coverage is research-only and is not proof of alpha, investor performance, repeatability, or live-trading readiness.",
        },
        "records": rows,
        "safe_next_actions": [
            {
                "field": row["key"],
                "count": row["records_missing_required_fields"] or row["eligible_records"],
                "action": row["safe_next_action"],
                "manual_review_only": True,
                "changes_execution": False,
            }
            for row in missing_rows
        ],
        "research_only": True,
        **SAFETY_FLAGS,
    }


def _source_counts_for_fields(
    records: list[dict[str, Any]],
    *,
    source_types: set[str],
    fields: set[str],
) -> tuple[Counter[str], dict[str, Counter[str]], set[str]]:
    field_counts: Counter[str] = Counter()
    missing_by_source: dict[str, Counter[str]] = defaultdict(Counter)
    affected_record_ids: set[str] = set()
    for row in records:
        source_type = str(row.get("source_type") or "unknown")
        if source_types and source_type not in source_types:
            continue
        missing_fields = set(row.get("missing_fields") or [])
        matched_fields = sorted(missing_fields.intersection(fields))
        if not matched_fields:
            continue
        affected_record_ids.add(f"{source_type}:{row.get('record_id')}")
        for field in matched_fields:
            field_counts[field] += 1
            missing_by_source[source_type][field] += 1
    return field_counts, missing_by_source, affected_record_ids


def build_data_cleanup_plan(
    records: list[dict[str, Any]],
    proof_field_coverage: dict[str, Any],
) -> dict[str, Any]:
    proof_rows = {
        str(row.get("key")): row
        for row in proof_field_coverage.get("records") or []
        if isinstance(row, dict)
    }
    total_records = len(records)
    items: list[dict[str, Any]] = []
    for definition in CLEANUP_PLAN_DEFINITIONS:
        source_types = set(definition.get("source_types") or ())
        fields = set(definition.get("fields") or ())
        field_counts, missing_by_source, affected_record_ids = _source_counts_for_fields(
            records,
            source_types=source_types,
            fields=fields,
        )
        relevant_records = [
            row
            for row in records
            if not source_types or str(row.get("source_type") or "unknown") in source_types
        ]
        proof_gap_rows = [
            proof_rows[key]
            for key in tuple(definition.get("proof_requirements") or ())
            if isinstance(proof_rows.get(key), dict) and proof_rows[key].get("status") != "ready"
        ]
        proof_missing_records = sum(int(row.get("records_missing_required_fields") or 0) for row in proof_gap_rows)
        missing_field_count = sum(field_counts.values())
        if not total_records:
            status = "no_records"
        elif not relevant_records and proof_gap_rows:
            status = "needs_evidence_source"
        elif affected_record_ids or proof_gap_rows:
            status = "needs_cleanup"
        else:
            status = "ready"
        affected_sources = sorted(
            set(missing_by_source.keys())
            | {
                source_type
                for proof_row in proof_gap_rows
                for source_type in proof_row.get("source_types") or []
            }
        )
        items.append(
            {
                "key": definition["key"],
                "title": definition["title"],
                "priority": definition["priority"],
                "status": status,
                "source_types": sorted(source_types),
                "affected_sources": affected_sources,
                "accepted_fields": sorted(fields),
                "missing_field_count": missing_field_count,
                "affected_record_count": len(affected_record_ids),
                "proof_missing_record_count": proof_missing_records,
                "missing_field_counts": dict(field_counts),
                "missing_by_source": {key: dict(value) for key, value in missing_by_source.items()},
                "blocked_reports": list(definition.get("blocks") or ()),
                "safe_next_action": definition["safe_next_action"],
                "done_when": definition["done_when"],
                "safe_boundary": "Cleanup plan items are manual evidence tasks only. They do not infer returns, submit orders, change broker routes, bypass risk gates, or mutate ranking weights.",
                "manual_review_only": True,
                "research_only": True,
                "changes_execution": False,
                "changes_ranking_weights": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
            }
        )
    open_items = [row for row in items if row["status"] != "ready"]
    critical_open_items = [
        row
        for row in open_items
        if row.get("priority") == "critical"
    ]
    status = "ready" if not open_items else "needs_attention"
    if not total_records:
        status = "no_records"
    return {
        "status": status,
        "summary": {
            "item_count": len(items),
            "open_item_count": len(open_items),
            "critical_open_items": len(critical_open_items),
            "ready_item_count": len(items) - len(open_items),
            "top_cleanup_item": open_items[0]["title"] if open_items else None,
            "proof_first_rule": "Ambition is allowed. Proof decides priority.",
            "safe_boundary": "Data cleanup records proof gaps only. It does not fabricate market outcomes or authorize trading changes.",
        },
        "items": items,
        "safe_next_actions": [
            {
                "field": row["key"],
                "count": row["affected_record_count"] or row["proof_missing_record_count"] or row["missing_field_count"],
                "action": row["safe_next_action"],
                "manual_review_only": True,
                "changes_execution": False,
            }
            for row in open_items
        ],
        "research_only": True,
        **SAFETY_FLAGS,
    }


def _extract_forecast_records(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in report.get("records") or report.get("items") or []:
        if not isinstance(item, dict):
            continue
        evaluation = item.get("evaluation") if isinstance(item.get("evaluation"), dict) else item
        forecast = item.get("forecast") if isinstance(item.get("forecast"), dict) else item
        merged = {**forecast, **evaluation}
        if "forecast_series" not in merged and isinstance(forecast.get("series"), list):
            merged["forecast_series"] = forecast.get("series")
        rows.append(merged)
    return rows


def _extract_runtime_sources(db: Any = None, current_user: Any = None) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    warnings: list[str] = []
    sources: dict[str, list[dict[str, Any]]] = {
        "candidate": [],
        "forecast": [],
        "ai_review": [],
        "blocker": [],
        "missed_move": [],
        "paper_trade": [],
        "execution": [],
        "benchmark": [],
    }
    try:
        reward_report = get_evidence_reward_summary(db, current_user=current_user)
        candidate_rows = list(reward_report.get("records") or reward_report.get("candidate_rows") or [])
        sources["candidate"] = [row for row in candidate_rows if isinstance(row, dict)]
        sources["ai_review"] = [row for row in sources["candidate"] if row.get("ai_verdict")]
        sources["blocker"] = [row for row in sources["candidate"] if row.get("blockers") or row.get("blocker")]
        sources["missed_move"] = [
            row
            for row in sources["candidate"]
            if row.get("missed_move_outcome") or row.get("missed_move_magnitude") or row.get("move_magnitude")
        ]
        sources["paper_trade"] = [
            row
            for row in sources["candidate"]
            if row.get("trade_executed") or row.get("paper_trade_outcome") or row.get("order_id") or row.get("trade_id")
        ]
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Evidence Reward source unavailable: {exc.__class__.__name__}.")
    try:
        forecast_report = get_forecast_validation_summary()
        sources["forecast"] = _extract_forecast_records(forecast_report)
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Forecast Validation source unavailable: {exc.__class__.__name__}.")
    try:
        benchmark_report = get_professional_benchmark_summary(db, current_user=current_user)
        summary = dict(benchmark_report.get("summary") or {})
        sources["benchmark"] = [
            {
                "status": benchmark_report.get("status"),
                "generated_at": benchmark_report.get("generated_at"),
                "missing_fields": benchmark_report.get("missing_fields", {}),
                **summary,
            }
        ]
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Professional Benchmark source unavailable: {exc.__class__.__name__}.")
    if db is not None and current_user is not None:
        try:
            execution_report = execution_quality_summary(db, current_user=current_user)
            sources["execution"] = [row for row in execution_report.get("rows") or [] if isinstance(row, dict)]
        except Exception as exc:  # pragma: no cover - entitlement and empty-db guard
            warnings.append(f"Execution Quality source unavailable: {exc.__class__.__name__}.")
    return sources, warnings


def build_data_completeness_report(
    *,
    db: Any = None,
    current_user: Any = None,
    candidate_records: Iterable[dict[str, Any]] | None = None,
    forecast_records: Iterable[dict[str, Any]] | None = None,
    ai_records: Iterable[dict[str, Any]] | None = None,
    blocker_records: Iterable[dict[str, Any]] | None = None,
    missed_move_records: Iterable[dict[str, Any]] | None = None,
    paper_trade_records: Iterable[dict[str, Any]] | None = None,
    execution_records: Iterable[dict[str, Any]] | None = None,
    benchmark_records: Iterable[dict[str, Any]] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    explicit_sources = {
        "candidate": candidate_records,
        "forecast": forecast_records,
        "ai_review": ai_records,
        "blocker": blocker_records,
        "missed_move": missed_move_records,
        "paper_trade": paper_trade_records,
        "execution": execution_records,
        "benchmark": benchmark_records,
    }
    warnings: list[str] = []
    if all(value is None for value in explicit_sources.values()):
        sources, warnings = _extract_runtime_sources(db, current_user)
    else:
        sources = {key: list(value or []) for key, value in explicit_sources.items()}

    audited_by_source = {
        source_type: _audit_records(records, source_type)
        for source_type, records in sources.items()
    }
    all_records = [row for rows in audited_by_source.values() for row in rows]
    aggregations = aggregate_completeness(all_records)
    proof_field_coverage = build_proof_field_coverage(all_records)
    data_cleanup_plan = build_data_cleanup_plan(all_records, proof_field_coverage)
    source_summaries = {
        source_type: {
            "source_type": source_type,
            "source_label": _source_label(source_type),
            **_rates(rows),
        }
        for source_type, rows in audited_by_source.items()
    }
    missing_fields = aggregations["missing_field_counts"]
    if not all_records:
        status = "empty"
        warnings.append("No evidence records were found for completeness auditing.")
    elif aggregations["incomplete_records"]:
        status = "needs_attention"
    else:
        status = "ready"
    base_benchmark_ready = (
        source_summaries.get("candidate", {}).get("rewardable_records", 0) > 0
        and source_summaries.get("benchmark", {}).get("complete_records", 0) > 0
    )
    proof_field_summary = proof_field_coverage["summary"]
    summary = {
        "status": status,
        "total_records": aggregations["total_records"],
        "complete_records": aggregations["complete_records"],
        "incomplete_records": aggregations["incomplete_records"],
        "rewardable_records": aggregations["rewardable_records"],
        "non_rewardable_records": aggregations["non_rewardable_records"],
        "completion_rate": aggregations["completion_rate"],
        "rewardability_rate": aggregations["rewardability_rate"],
        "benchmark_ready": bool(base_benchmark_ready and proof_field_summary["proof_ready"]),
        "base_benchmark_ready": bool(base_benchmark_ready),
        "proof_field_ready": bool(proof_field_summary["proof_ready"]),
        "proof_field_coverage_rate": proof_field_summary["average_coverage_rate"],
        "proof_field_requirements_ready": proof_field_summary["ready_requirement_count"],
        "proof_field_requirements_total": proof_field_summary["requirement_count"],
        "cleanup_plan_status": data_cleanup_plan["status"],
        "cleanup_plan_open_items": data_cleanup_plan["summary"]["open_item_count"],
        "cleanup_plan_critical_open_items": data_cleanup_plan["summary"]["critical_open_items"],
        "top_cleanup_item": data_cleanup_plan["summary"]["top_cleanup_item"],
        "source_summaries": source_summaries,
        "highest_priority_missing_fields": aggregations["highest_priority_missing_fields"],
        "benchmark_blockers": aggregations["benchmark_blockers"],
        **SAFETY_FLAGS,
    }
    safe_next_actions = _safe_next_actions(aggregations["highest_priority_missing_fields"])
    safe_next_actions.extend(proof_field_coverage.get("safe_next_actions") or [])
    safe_next_actions.extend(data_cleanup_plan.get("safe_next_actions") or [])
    if safe_next_actions:
        warnings.append("Completeness gaps are diagnostics only; fixes must not change trading authority automatically.")
    if not proof_field_summary["proof_ready"]:
        warnings.append("Proof-field coverage is incomplete; benchmark readiness remains blocked until forward returns, baselines, forecast actuals, slippage, regime labels, and reward fields satisfy thresholds.")
    return serialize_value(
        {
            "status": status,
            "generated_at": generated_at or _utc_now(),
            "research_only": True,
            "mode": "research_only",
            "summary": summary,
            "records": all_records[:500],
            "records_by_source": audited_by_source,
            "aggregations": aggregations,
            "proof_field_coverage": proof_field_coverage,
            "data_cleanup_plan": data_cleanup_plan,
            "missing_fields": missing_fields,
            "warnings": warnings,
            "safe_next_actions": safe_next_actions,
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
            "finish_tracker": build_project_finish_tracker(report_name="data_completeness"),
        }
    )


def _safe_next_actions(priority_fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions = []
    action_map = {
        "actual_forward_return": "Stamp forward returns after each candidate window closes.",
        "baseline_forward_return": "Attach matched benchmark baseline returns at the same timestamp and horizon.",
        "forecast_series": "Store the immutable forecast path with each prediction contract.",
        "actual_series": "Attach post-prediction actual path data for forecast validation.",
        "spread_at_signal": "Capture spread at signal time for execution-adjusted reward.",
        "slippage": "Capture expected and realized slippage fields for execution quality.",
        "prediction_horizon_minutes": "Emit a prediction horizon before movement is measured.",
        "predicted_target_pct": "Emit an explicit target percentage before movement is measured.",
    }
    for row in priority_fields[:8]:
        field = row.get("field")
        if field in action_map:
            actions.append(
                {
                    "field": field,
                    "count": row.get("count", 0),
                    "action": action_map[field],
                    "manual_review_only": True,
                    "changes_execution": False,
                }
            )
    return actions


def _subset(report: dict[str, Any], source_type: str) -> dict[str, Any]:
    records = (report.get("records_by_source") or {}).get(source_type, [])
    return {
        "status": report.get("status", "unknown"),
        "generated_at": report.get("generated_at"),
        "research_only": True,
        "mode": "research_only",
        "summary": report.get("summary", {}),
        "records": records,
        "aggregations": {
            "source_summary": (report.get("summary", {}).get("source_summaries") or {}).get(source_type, {}),
            "missing_field_counts": {
                field: count
                for field, count in (report.get("missing_fields") or {}).items()
                if any(field in (row.get("missing_fields") or []) for row in records)
            },
        },
        "proof_field_coverage": report.get("proof_field_coverage", {}),
        "data_cleanup_plan": report.get("data_cleanup_plan", {}),
        "missing_fields": report.get("missing_fields", {}),
        "warnings": report.get("warnings", []),
        "safe_next_actions": report.get("safe_next_actions", []),
        "safety_notes": report.get("safety_notes", list(SAFETY_NOTES)),
        **SAFETY_FLAGS,
        "finish_tracker": report.get("finish_tracker") or build_project_finish_tracker(report_name=f"data_completeness_{source_type}"),
    }


def get_data_completeness_summary(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    return build_data_completeness_report(db=db, current_user=current_user)


def get_data_completeness_candidates(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    return _subset(get_data_completeness_summary(db, current_user=current_user), "candidate")


def get_data_completeness_forecasts(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    return _subset(get_data_completeness_summary(db, current_user=current_user), "forecast")


def get_data_completeness_ai(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    return _subset(get_data_completeness_summary(db, current_user=current_user), "ai_review")


def get_data_completeness_blockers(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = get_data_completeness_summary(db, current_user=current_user)
    blocker_rows = list((report.get("records_by_source") or {}).get("blocker", []))
    blocker_rows.extend((report.get("records_by_source") or {}).get("missed_move", []))
    subset = _subset(report, "blocker")
    subset["records"] = blocker_rows
    return subset


def get_data_completeness_execution(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = get_data_completeness_summary(db, current_user=current_user)
    execution_rows = list((report.get("records_by_source") or {}).get("execution", []))
    execution_rows.extend((report.get("records_by_source") or {}).get("paper_trade", []))
    subset = _subset(report, "execution")
    subset["records"] = execution_rows
    return subset


def get_data_completeness_benchmark_readiness(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    return _subset(get_data_completeness_summary(db, current_user=current_user), "benchmark")
