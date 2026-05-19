from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from backend.core.config import settings
from backend.services.exceptions import ConflictError, NotFoundError, ValidationServiceError
from backend.services.professional_benchmark_suite import get_professional_benchmark_summary
from backend.services.project_finish_tracker import build_project_finish_tracker
from backend.services.serialization import serialize_value
from backend.services.storage_utils import read_json_file, write_json_file

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
    "Does not change risk settings automatically.",
    "Does not grant AI order authority.",
)

EXPERIMENT_STATUSES: tuple[str, ...] = (
    "draft",
    "frozen",
    "running",
    "completed",
    "rejected",
    "needs_more_evidence",
)
IMMUTABLE_STATUSES = {"frozen", "running", "completed", "rejected", "needs_more_evidence"}

REQUIRED_CREATE_FIELDS = ("name", "train_window", "validation_window", "test_window")
WINDOW_FIELDS = ("train_window", "validation_window", "test_window", "paper_forward_window")
VERSION_FIELDS = (
    "strategy_config_version",
    "risk_config_version",
    "ranking_formula_version",
    "reward_formula_version",
    "forecast_model_version",
    "baseline_definition_version",
    "feature_version",
)
SNAPSHOT_FIELDS = (
    *WINDOW_FIELDS,
    *VERSION_FIELDS,
    "market_universe",
    "data_source",
    "code_version",
    "frozen_parameters",
    "allowed_change_policy",
)

DEFAULT_ALLOWED_CHANGE_POLICY = (
    "Draft experiments can be edited. Frozen, running, completed, rejected, and needs_more_evidence "
    "experiments cannot change parameters in place; clone the experiment to create a new version."
)
DEFAULT_VERSION_SNAPSHOT: dict[str, Any] = {
    "strategy_config_version": "strategy_config_v1",
    "risk_config_version": "risk_config_snapshot_v1",
    "ranking_formula_version": "ranked_entry_v1",
    "reward_formula_version": "evidence_reward_prediction_contract_v1",
    "forecast_model_version": "forecast_validation_contract_v1",
    "baseline_definition_version": "professional_benchmark_baselines_v1",
    "feature_version": "candidate_feature_snapshot_v1",
    "market_universe": "configured_trade_universe",
    "data_source": "local_evidence_artifacts",
}

MIN_WALK_FORWARD_PASS_RATE = 0.6
WALK_FORWARD_PROOF_REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "key": "frozen_snapshot",
        "label": "Frozen snapshot exists",
        "metric": "frozen_record_count",
        "threshold": 1,
        "comparison": "greater_or_equal",
        "safe_next_action": "Freeze at least one experiment before treating any forward evidence as repeatability proof.",
    },
    {
        "key": "no_lookahead_windows",
        "label": "No-lookahead windows",
        "metric": "no_lookahead_record_count",
        "threshold": 1,
        "comparison": "greater_or_equal",
        "safe_next_action": "Define train, validation, test, and paper-forward windows in chronological order before evaluation.",
    },
    {
        "key": "version_snapshot",
        "label": "Version snapshot complete",
        "metric": "version_complete_record_count",
        "threshold": 1,
        "comparison": "greater_or_equal",
        "safe_next_action": "Attach ranking, reward, forecast, baseline, feature, universe, data-source, and code-version snapshots.",
    },
    {
        "key": "out_of_sample_results",
        "label": "Out-of-sample result captured",
        "metric": "evaluated_record_count",
        "threshold": 1,
        "comparison": "greater_or_equal",
        "safe_next_action": "Link at least one frozen experiment to out-of-sample benchmark results before repeatability review.",
    },
    {
        "key": "walk_forward_pass_rate",
        "label": "Walk-forward pass rate",
        "metric": "pass_rate",
        "threshold": MIN_WALK_FORWARD_PASS_RATE,
        "comparison": "greater_or_equal",
        "safe_next_action": "Improve data quality, scoring, or setup selection until frozen experiments pass the configured rate.",
    },
    {
        "key": "after_cost_support",
        "label": "After-cost support",
        "metric": "after_cost_supported_record_count",
        "threshold": 1,
        "comparison": "greater_or_equal",
        "safe_next_action": "Add slippage or spread-adjusted reward evidence to frozen experiment results.",
    },
)

WALK_FORWARD_VALIDATION_PLAN_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "create_frozen_experiment",
        "title": "Create and freeze an experiment snapshot",
        "priority": "critical",
        "proof_keys": ("frozen_snapshot",),
        "missing_fields": ("experiment_id", "frozen_at", "parameter_digest"),
        "blocked_claims": ("repeatability_review", "walk_forward_claim"),
        "safe_next_action": "Create a draft experiment with train, validation, test, and paper-forward windows, then freeze it before observing forward results.",
        "done_when": "At least one experiment is frozen or locked before the out-of-sample window is evaluated.",
    },
    {
        "key": "chronological_no_lookahead_windows",
        "title": "Chronological no-lookahead windows",
        "priority": "critical",
        "proof_keys": ("no_lookahead_windows",),
        "missing_fields": ("train_window", "validation_window", "test_window", "paper_forward_window"),
        "blocked_claims": ("repeatability_review", "no_lookahead_claim"),
        "safe_next_action": "Define train, validation, test, and paper-forward windows in chronological order before any evaluation.",
        "done_when": "A frozen experiment has complete chronological windows with no overlap or post-outcome parameter changes.",
    },
    {
        "key": "complete_version_snapshot",
        "title": "Complete version snapshot",
        "priority": "high",
        "proof_keys": ("version_snapshot",),
        "missing_fields": (
            "ranking_formula_version",
            "reward_formula_version",
            "forecast_model_version",
            "baseline_definition_version",
            "feature_version",
            "market_universe",
            "data_source",
            "code_version",
        ),
        "blocked_claims": ("auditability_claim", "repeatability_review"),
        "safe_next_action": "Attach ranking, reward, forecast, baseline, feature, universe, data-source, and code-version snapshots.",
        "done_when": "Version snapshot fields are complete for a frozen experiment and tied to a parameter digest.",
    },
    {
        "key": "out_of_sample_result",
        "title": "Out-of-sample result captured",
        "priority": "critical",
        "proof_keys": ("out_of_sample_results",),
        "missing_fields": ("verdict", "baseline_relative_edge", "score_bucket_lift", "rewardable_count"),
        "blocked_claims": ("repeatability_review", "walk_forward_claim"),
        "safe_next_action": "Link at least one frozen experiment to a completed out-of-sample benchmark result after the test window closes.",
        "done_when": "A frozen experiment has a pass, weak-pass, or fail verdict from forward-only evidence.",
    },
    {
        "key": "after_cost_support",
        "title": "After-cost support",
        "priority": "high",
        "proof_keys": ("after_cost_support",),
        "missing_fields": ("execution_adjusted_reward", "slippage_bps", "spread_bps"),
        "blocked_claims": ("tradability_review", "cost_adjusted_repeatability"),
        "safe_next_action": "Attach slippage, spread, fill, or execution-adjusted reward evidence to evaluated frozen experiments.",
        "done_when": "At least one evaluated frozen experiment includes after-cost reward evidence.",
    },
    {
        "key": "pass_rate_threshold",
        "title": "Walk-forward pass-rate threshold",
        "priority": "high",
        "proof_keys": ("walk_forward_pass_rate",),
        "missing_fields": ("passed_verdict_count", "evaluated_record_count"),
        "blocked_claims": ("repeatability_review", "strategy_stability_claim"),
        "safe_next_action": "Run enough frozen out-of-sample experiments to measure whether the pass rate meets the configured threshold.",
        "done_when": "Evaluated frozen experiments meet or exceed the v1 minimum pass-rate threshold.",
    },
)

SECRET_KEY_MARKERS = ("secret", "token", "password", "credential", "api_key", "apikey", "access_key", "private_key")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE_PATH = PROJECT_ROOT / "runtime-exports" / "walk-forward-experiments" / "experiments.json"


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


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromisoformat(f"{text}T00:00:00")
        except ValueError:
            return None


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
    if len(normalized) >= 3 and normalized[1:3] in {":\\", ":/"}:
        return True
    if normalized.startswith("\\\\"):
        return True
    return False


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


def _digest(payload: dict[str, Any]) -> str:
    serialized = json.dumps(_sanitize_value(payload), sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _store_path(path: Path | str | None = None) -> Path:
    return Path(path) if path is not None else DEFAULT_STORE_PATH


def _read_records(store_path: Path | str | None = None) -> list[dict[str, Any]]:
    payload = read_json_file(_store_path(store_path), {"experiments": []})
    records = payload.get("experiments") if isinstance(payload, dict) else []
    return [row for row in records or [] if isinstance(row, dict)]


def _write_records(records: list[dict[str, Any]], store_path: Path | str | None = None) -> None:
    payload = {
        "schema_version": "walk_forward_experiment_registry_v1",
        "updated_at": _utc_now(),
        "experiments": records,
    }
    write_json_file(_store_path(store_path), serialize_value(payload))


def _current_code_version() -> str:
    return (
        str(os.getenv("GIT_COMMIT") or os.getenv("APP_COMMIT") or "").strip()
        or f"{settings.app_version}:{settings.app_phase}"
    )


def _created_by(current_user: Any = None) -> str | None:
    for field in ("user_id", "auth_subject", "name"):
        value = getattr(current_user, field, None)
        if value:
            return str(value)
    return None


def _missing_required(payload: dict[str, Any]) -> list[str]:
    missing = []
    for field in REQUIRED_CREATE_FIELDS:
        value = payload.get(field)
        if value is None or value == "" or value == {}:
            missing.append(field)
    return missing


def _validate_status(status: str) -> str:
    normalized = str(status or "draft").strip().lower()
    if normalized not in EXPERIMENT_STATUSES:
        raise ValidationServiceError(
            "Invalid walk-forward experiment status.",
            details={"status": normalized, "valid_statuses": list(EXPERIMENT_STATUSES)},
        )
    return normalized


def _snapshot_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_value(payload)
    snapshot: dict[str, Any] = {}
    for field in WINDOW_FIELDS:
        snapshot[field] = sanitized.get(field) or {}
    for field in VERSION_FIELDS:
        snapshot[field] = sanitized.get(field) or DEFAULT_VERSION_SNAPSHOT[field]
    snapshot["market_universe"] = sanitized.get("market_universe") or DEFAULT_VERSION_SNAPSHOT["market_universe"]
    snapshot["data_source"] = sanitized.get("data_source") or DEFAULT_VERSION_SNAPSHOT["data_source"]
    snapshot["code_version"] = sanitized.get("code_version") or _current_code_version()
    snapshot["frozen_parameters"] = sanitized.get("frozen_parameters") or {
        "strategy_config_version": snapshot["strategy_config_version"],
        "risk_config_version": snapshot["risk_config_version"],
        "ranking_formula_version": snapshot["ranking_formula_version"],
        "reward_formula_version": snapshot["reward_formula_version"],
        "forecast_model_version": snapshot["forecast_model_version"],
        "baseline_definition_version": snapshot["baseline_definition_version"],
        "feature_version": snapshot["feature_version"],
        "market_universe": snapshot["market_universe"],
    }
    snapshot["allowed_change_policy"] = sanitized.get("allowed_change_policy") or DEFAULT_ALLOWED_CHANGE_POLICY
    return snapshot


def _passes_threshold(value: Any, threshold: Any, comparison: str) -> bool:
    numeric = _safe_float(value)
    required = _safe_float(threshold)
    if numeric is None or required is None:
        return False
    if comparison == "greater_than":
        return numeric > required
    return numeric >= required


def _window_bounds(window: Any) -> tuple[datetime | None, datetime | None]:
    if not isinstance(window, dict):
        return None, None
    start = _safe_datetime(window.get("start") or window.get("from"))
    end = _safe_datetime(window.get("end") or window.get("to"))
    if start is not None and end is not None and start <= end:
        return start, end
    return None, None


def _has_chronological_windows(record: dict[str, Any]) -> bool:
    train_start, train_end = _window_bounds(record.get("train_window"))
    validation_start, validation_end = _window_bounds(record.get("validation_window"))
    test_start, test_end = _window_bounds(record.get("test_window"))
    paper_start, paper_end = _window_bounds(record.get("paper_forward_window"))
    if not all((train_start, train_end, validation_start, validation_end, test_start, test_end, paper_start, paper_end)):
        return False
    if not (train_end < validation_start and validation_end < test_start):
        return False
    if not (test_end < paper_start):
        return False
    return True


def _missing_version_snapshot_fields(record: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in VERSION_FIELDS:
        if not record.get(field):
            missing.append(field)
    for field in ("market_universe", "data_source", "code_version", "parameter_digest"):
        value = record.get(field)
        if value is None or value == "" or value == [] or value == {}:
            missing.append(field)
    return missing


def _is_evaluated_verdict(verdict: str) -> bool:
    return verdict in {"passed", "weak_pass", "failed"}


def _record_readiness(record: dict[str, Any]) -> dict[str, Any]:
    metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
    verdict = str(metrics.get("verdict") or "insufficient_evidence").strip().lower()
    status = str(record.get("status") or "draft").strip().lower()
    frozen_snapshot = status in IMMUTABLE_STATUSES
    no_lookahead = _has_chronological_windows(record)
    missing_versions = _missing_version_snapshot_fields(record)
    baseline_edge = _safe_float(metrics.get("baseline_relative_edge"))
    bucket_lift = _safe_float(metrics.get("score_bucket_lift"))
    after_cost_reward = _safe_float(metrics.get("execution_adjusted_reward"))
    benchmark_linked = baseline_edge is not None and bucket_lift is not None
    after_cost_supported = after_cost_reward is not None
    evaluated = _is_evaluated_verdict(verdict)
    passed = verdict == "passed"
    warnings: list[str] = []
    if not frozen_snapshot:
        warnings.append("Experiment is not frozen or locked.")
    if not no_lookahead:
        warnings.append("Train, validation, test, and paper-forward windows are incomplete or not chronological.")
    if missing_versions:
        warnings.append("Version snapshot is missing required fields.")
    if not benchmark_linked:
        warnings.append("Baseline-relative edge and score bucket lift are not both available.")
    if not after_cost_supported:
        warnings.append("Execution-adjusted reward is missing.")
    if not evaluated:
        warnings.append("Experiment does not yet have a pass, weak pass, or fail verdict.")
    return serialize_value(
        {
            "experiment_id": record.get("experiment_id"),
            "name": record.get("name"),
            "status": status,
            "verdict": verdict,
            "frozen_snapshot": frozen_snapshot,
            "no_lookahead_windows": no_lookahead,
            "version_snapshot_complete": not missing_versions,
            "missing_version_fields": missing_versions,
            "benchmark_linked": benchmark_linked,
            "after_cost_supported": after_cost_supported,
            "evaluated": evaluated,
            "passed": passed,
            "warnings": warnings,
            "research_only": True,
            "changes_execution": False,
            "changes_broker_routes": False,
            "changes_risk_gates": False,
            "changes_ranking_weights": False,
            "can_change_broker_routes": False,
            "can_bypass_risk_gates": False,
            "can_change_ranking_weights": False,
            "can_grant_ai_order_authority": False,
        }
    )


def build_walk_forward_proof_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    readiness = [_record_readiness(record) for record in records]
    frozen_count = sum(1 for row in readiness if row["frozen_snapshot"])
    no_lookahead_count = sum(1 for row in readiness if row["no_lookahead_windows"])
    version_complete_count = sum(1 for row in readiness if row["version_snapshot_complete"])
    evaluated_count = sum(1 for row in readiness if row["evaluated"])
    passed_count = sum(1 for row in readiness if row["passed"])
    after_cost_count = sum(1 for row in readiness if row["after_cost_supported"])
    pass_rate = round(passed_count / evaluated_count, 6) if evaluated_count else 0.0
    values = {
        "frozen_record_count": frozen_count,
        "no_lookahead_record_count": no_lookahead_count,
        "version_complete_record_count": version_complete_count,
        "evaluated_record_count": evaluated_count,
        "pass_rate": pass_rate,
        "after_cost_supported_record_count": after_cost_count,
    }
    rows: list[dict[str, Any]] = []
    for requirement in WALK_FORWARD_PROOF_REQUIREMENTS:
        value = values.get(str(requirement["metric"]))
        passed = _passes_threshold(value, requirement["threshold"], str(requirement["comparison"]))
        rows.append(
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
                "claim_boundary": "Walk-forward proof is for human research review only; it is not proof of alpha, investor performance, guaranteed returns, institutional readiness, or live-trading readiness.",
                "research_only": True,
                "changes_execution": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
                "changes_ranking_weights": False,
                "can_change_broker_routes": False,
                "can_bypass_risk_gates": False,
                "can_change_ranking_weights": False,
                "can_grant_ai_order_authority": False,
            }
        )
    proof_ready = bool(rows) and all(row["passed"] for row in rows)
    return serialize_value(
        {
            "status": "ready_for_human_review" if proof_ready else "needs_evidence",
            "proof_ready": proof_ready,
            "requirements": rows,
            "record_readiness": readiness,
            "summary": {
                "record_count": len(records),
                "frozen_record_count": frozen_count,
                "no_lookahead_record_count": no_lookahead_count,
                "version_complete_record_count": version_complete_count,
                "evaluated_record_count": evaluated_count,
                "passed_record_count": passed_count,
                "after_cost_supported_record_count": after_cost_count,
                "pass_rate": pass_rate,
                "minimum_pass_rate": MIN_WALK_FORWARD_PASS_RATE,
                "requirement_count": len(rows),
                "passed_requirement_count": sum(1 for row in rows if row["passed"]),
                "missing_requirement_count": sum(1 for row in rows if not row["passed"]),
            },
            "safe_next_actions": [row["safe_next_action"] for row in rows if not row["passed"]],
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def build_walk_forward_validation_plan(records: list[dict[str, Any]], proof_summary: dict[str, Any]) -> dict[str, Any]:
    proof_rows = {
        str(row.get("key")): row
        for row in proof_summary.get("requirements") or []
        if isinstance(row, dict)
    }
    items: list[dict[str, Any]] = []
    for definition in WALK_FORWARD_VALIDATION_PLAN_DEFINITIONS:
        proof_keys = tuple(definition.get("proof_keys") or ())
        related_rows = [
            proof_rows[key]
            for key in proof_keys
            if isinstance(proof_rows.get(key), dict)
        ]
        passed = bool(related_rows) and all(bool(row.get("passed")) for row in related_rows)
        if not records:
            status = "no_records"
        else:
            status = "ready" if passed else "needs_evidence"
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
                "claim_boundary": "Walk-forward validation plan items are internal research gates only; they do not prove alpha, guaranteed returns, investor performance, institutional readiness, or live-trading readiness.",
                "manual_review_only": True,
                "research_only": True,
                "changes_execution": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
                "changes_ranking_weights": False,
                "can_change_broker_routes": False,
                "can_bypass_risk_gates": False,
                "can_change_ranking_weights": False,
                "can_grant_ai_order_authority": False,
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
                "top_validation_item": open_items[0]["title"] if open_items else None,
                "proof_first_rule": "Ambition is allowed. Proof decides priority.",
                "claim_permissions": {
                    "cautious_internal_repeatability_review": proof_ready,
                    "public_repeatability_claim": False,
                    "public_alpha_claim": False,
                    "live_trading_readiness": False,
                    "institutional_readiness": False,
                },
                "blocked_claims": [
                    "proven_alpha",
                    "guaranteed_returns",
                    "public_repeatability",
                    "institutional_readiness",
                    "live_trading_readiness",
                ],
                "safe_boundary": "Walk-forward validation only records proof gaps and claim boundaries. It does not authorize orders, route changes, risk-gate changes, or ranking-weight mutation.",
            },
            "items": items,
            "safe_next_actions": [
                {
                    "field": row["key"],
                    "action": row["safe_next_action"],
                    "manual_review_only": True,
                    "changes_execution": False,
                    "changes_broker_routes": False,
                    "changes_risk_gates": False,
                    "changes_ranking_weights": False,
                    "can_change_broker_routes": False,
                    "can_bypass_risk_gates": False,
                    "can_change_ranking_weights": False,
                    "can_grant_ai_order_authority": False,
                }
                for row in open_items
            ],
            "research_only": True,
            **SAFETY_FLAGS,
        }
    )


def _benchmark_report_from_inputs(
    *,
    benchmark_report: dict[str, Any] | None = None,
    db: Any = None,
    current_user: Any = None,
) -> dict[str, Any]:
    if benchmark_report is not None:
        return benchmark_report
    try:
        return get_professional_benchmark_summary(db, current_user=current_user)
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        return {
            "status": "insufficient_evidence",
            "summary": {"benchmark_verdict": "insufficient_evidence", "verdict_reason": f"Benchmark source unavailable: {exc.__class__.__name__}."},
            "sections": {},
            "warnings": [f"Benchmark source unavailable: {exc.__class__.__name__}."],
            "missing_fields": {"professional_benchmark": 1},
        }


def _first_item_value(section: dict[str, Any], key: str) -> Any:
    items = section.get("items") if isinstance(section, dict) else None
    if isinstance(items, list) and items:
        values = [_safe_float(row.get(key)) for row in items if isinstance(row, dict)]
        values = [value for value in values if value is not None]
        if values:
            return round(sum(values) / len(values), 6)
    return None


def _experiment_verdict_from_benchmark(benchmark_report: dict[str, Any]) -> str:
    status = str(benchmark_report.get("status") or "").strip().lower()
    summary = benchmark_report.get("summary") if isinstance(benchmark_report.get("summary"), dict) else {}
    benchmark_verdict = str(summary.get("benchmark_verdict") or status or "insufficient_evidence").strip().lower()
    data_quality = _safe_float(summary.get("data_quality_score"))
    rewardable_count = _safe_int(summary.get("rewardable_count")) or 0
    if benchmark_verdict == "data_quality_too_weak" or (data_quality is not None and data_quality < 50 and rewardable_count < 5):
        return "data_quality_too_weak"
    if benchmark_verdict == "insufficient_evidence" or rewardable_count < 5:
        return "insufficient_evidence"
    if benchmark_verdict == "edge_detected":
        return "passed"
    if benchmark_verdict == "weak_edge_detected":
        return "weak_pass"
    if benchmark_verdict == "no_edge_detected":
        return "failed"
    return "insufficient_evidence"


def evaluate_experiment_from_benchmark(benchmark_report: dict[str, Any]) -> dict[str, Any]:
    summary = benchmark_report.get("summary") if isinstance(benchmark_report.get("summary"), dict) else {}
    sections = benchmark_report.get("sections") if isinstance(benchmark_report.get("sections"), dict) else {}
    core = sections.get("core_metrics") if isinstance(sections.get("core_metrics"), dict) else {}
    forecast = sections.get("forecast_accuracy") if isinstance(sections.get("forecast_accuracy"), dict) else {}
    blockers = sections.get("blocker_value") if isinstance(sections.get("blocker_value"), dict) else {}
    ai = sections.get("ai_verdict_accuracy") if isinstance(sections.get("ai_verdict_accuracy"), dict) else {}
    execution = sections.get("execution_quality") if isinstance(sections.get("execution_quality"), dict) else {}
    candidate_count = _safe_int(summary.get("candidate_count")) or _safe_int(summary.get("sample_size")) or 0
    rewardable_count = _safe_int(summary.get("rewardable_count")) or 0
    verdict = _experiment_verdict_from_benchmark(benchmark_report)
    warnings = _listify(benchmark_report.get("warnings"))
    if verdict in {"insufficient_evidence", "data_quality_too_weak"}:
        warnings.append(str(summary.get("verdict_reason") or "Walk-forward experiment needs more rewardable forward-only evidence."))
    return serialize_value(
        {
            "sample_size": candidate_count,
            "rewardable_count": rewardable_count,
            "non_rewardable_count": max(0, candidate_count - rewardable_count),
            "baseline_relative_edge": _safe_float(summary.get("baseline_relative_edge")),
            "score_bucket_lift": _safe_float(summary.get("score_bucket_lift")),
            "forecast_accuracy": _safe_float(forecast.get("direction_accuracy")),
            "blocker_value": _first_item_value(blockers, "estimated_blocker_value"),
            "ai_verdict_accuracy": {
                "verdict_count": _safe_int(ai.get("verdict_count")) or 0,
                "false_positive_rate": _safe_float(ai.get("false_positive_rate")),
                "false_negative_rate": _safe_float(ai.get("false_negative_rate")),
            },
            "execution_adjusted_reward": _safe_float(execution.get("slippage_adjusted_reward")),
            "regime_stability": summary.get("regime_stability") or core.get("regime_stability"),
            "max_drawdown": _safe_float(summary.get("max_drawdown") or core.get("max_drawdown")),
            "profit_factor": _safe_float(summary.get("profit_factor") or core.get("profit_factor")),
            "warnings": [str(item) for item in warnings if item],
            "verdict": verdict,
            "benchmark_verdict": summary.get("benchmark_verdict") or benchmark_report.get("status"),
            "verdict_reason": summary.get("verdict_reason") or "Mapped from Professional Benchmark Suite v1.",
        }
    )


def _normalize_metrics(metrics: dict[str, Any] | None, benchmark_report: dict[str, Any] | None = None) -> dict[str, Any]:
    base = evaluate_experiment_from_benchmark(benchmark_report or {"status": "insufficient_evidence", "summary": {}, "sections": {}})
    if isinstance(metrics, dict):
        sanitized = _sanitize_value(metrics)
        base.update(sanitized)
        if not base.get("verdict"):
            base["verdict"] = "insufficient_evidence"
    return serialize_value(base)


def _record_from_payload(
    payload: dict[str, Any],
    *,
    current_user: Any = None,
    benchmark_report: dict[str, Any] | None = None,
    now: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    missing = _missing_required(payload)
    if missing:
        raise ValidationServiceError(
            "Walk-forward experiment is missing required fields.",
            details={"missing_fields": missing},
        )
    timestamp = now or _utc_now()
    sanitized = _sanitize_value(payload)
    status = _validate_status(str(sanitized.get("status") or "draft"))
    snapshot = _snapshot_from_payload(sanitized)
    record_id = experiment_id or str(sanitized.get("experiment_id") or f"wf-{uuid4().hex[:12]}")
    benchmark = benchmark_report if benchmark_report is not None else None
    metrics = _normalize_metrics(sanitized.get("metrics") if isinstance(sanitized.get("metrics"), dict) else None, benchmark)
    warnings = [str(item) for item in _listify(sanitized.get("warnings")) if item]
    if status in IMMUTABLE_STATUSES:
        warnings.append("Experiment parameters are immutable in this status; clone to change parameters.")
    return serialize_value(
        {
            "experiment_id": record_id,
            "name": str(sanitized.get("name") or record_id),
            "description": str(sanitized.get("description") or ""),
            "created_at": timestamp,
            "created_by": _created_by(current_user),
            "status": status,
            **snapshot,
            "parameter_digest": _digest({field: snapshot.get(field) for field in SNAPSHOT_FIELDS}),
            "metrics": metrics,
            "warnings": warnings,
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def _find_record(records: list[dict[str, Any]], experiment_id: str) -> tuple[int, dict[str, Any]]:
    for index, record in enumerate(records):
        if str(record.get("experiment_id")) == str(experiment_id):
            return index, record
    raise NotFoundError("Walk-forward experiment was not found.", details={"experiment_id": experiment_id})


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {status: 0 for status in EXPERIMENT_STATUSES}
    verdict_counts: dict[str, int] = {}
    frozen_count = 0
    for record in records:
        status = str(record.get("status") or "draft")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in IMMUTABLE_STATUSES:
            frozen_count += 1
        verdict = str((record.get("metrics") or {}).get("verdict") or "insufficient_evidence")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
    latest = records[-1] if records else None
    proof_summary = build_walk_forward_proof_summary(records)
    validation_plan = build_walk_forward_validation_plan(records, proof_summary)
    return {
        "experiment_count": len(records),
        "draft_count": status_counts.get("draft", 0),
        "frozen_or_locked_count": frozen_count,
        "status_counts": status_counts,
        "verdict_counts": verdict_counts,
        "latest_experiment_id": latest.get("experiment_id") if latest else None,
        "walk_forward_proof_ready": proof_summary["proof_ready"],
        "walk_forward_proof_status": proof_summary["status"],
        "walk_forward_pass_rate": proof_summary["summary"]["pass_rate"],
        "walk_forward_requirements_passed": proof_summary["summary"]["passed_requirement_count"],
        "walk_forward_requirements_total": proof_summary["summary"]["requirement_count"],
        "walk_forward_validation_status": validation_plan["status"],
        "walk_forward_validation_open_items": validation_plan["summary"]["open_item_count"],
        "walk_forward_validation_critical_open_items": validation_plan["summary"]["critical_open_items"],
        "top_validation_item": validation_plan["summary"]["top_validation_item"],
        "claim_permissions": validation_plan["summary"]["claim_permissions"],
        "research_only": True,
        "storage": "sanitized_runtime_metadata",
        **SAFETY_FLAGS,
    }


def _response(
    *,
    status: str,
    record: dict[str, Any] | None = None,
    records: list[dict[str, Any]] | None = None,
    warnings: Iterable[Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    record_list = list(records or ([] if record is None else [record]))
    summary_records = record_list if records is not None else _read_records()
    summary = _summary(summary_records)
    proof_summary = build_walk_forward_proof_summary(summary_records)
    validation_plan = build_walk_forward_validation_plan(summary_records, proof_summary)
    return serialize_value(
        {
            "status": status,
            "generated_at": generated_at or _utc_now(),
            "research_only": True,
            "summary": summary,
            "proof_summary": proof_summary,
            "walk_forward_validation_plan": validation_plan,
            "record": record,
            "records": record_list,
            "warnings": [str(item) for item in warnings or [] if item],
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
            "finish_tracker": build_project_finish_tracker(report_name="walk_forward"),
        }
    )


def list_experiment_records(*, store_path: Path | str | None = None) -> list[dict[str, Any]]:
    return _read_records(store_path)


def get_walk_forward_summary(*, store_path: Path | str | None = None) -> dict[str, Any]:
    records = list_experiment_records(store_path=store_path)
    proof_summary = build_walk_forward_proof_summary(records)
    validation_plan = build_walk_forward_validation_plan(records, proof_summary)
    return serialize_value(
        {
            "status": "ready" if records else "empty",
            "generated_at": _utc_now(),
            "research_only": True,
            "summary": _summary(records),
            "proof_summary": proof_summary,
            "walk_forward_validation_plan": validation_plan,
            "records": records[:100],
            "warnings": [] if records else ["No walk-forward experiments have been created yet."],
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
            "finish_tracker": build_project_finish_tracker(report_name="walk_forward_summary"),
        }
    )


def get_walk_forward_experiments(*, store_path: Path | str | None = None) -> dict[str, Any]:
    records = list_experiment_records(store_path=store_path)
    return _response(status="ready" if records else "empty", records=records, warnings=[] if records else ["No experiment records found."])


def get_walk_forward_experiment(experiment_id: str, *, store_path: Path | str | None = None) -> dict[str, Any]:
    records = list_experiment_records(store_path=store_path)
    _, record = _find_record(records, experiment_id)
    return _response(status="ready", record=record, records=records)


def create_walk_forward_experiment(
    payload: dict[str, Any],
    *,
    current_user: Any = None,
    benchmark_report: dict[str, Any] | None = None,
    store_path: Path | str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    record = _record_from_payload(payload, current_user=current_user, benchmark_report=benchmark_report, now=now)
    records = list_experiment_records(store_path=store_path)
    if any(str(row.get("experiment_id")) == str(record["experiment_id"]) for row in records):
        raise ConflictError("Walk-forward experiment id already exists.", details={"experiment_id": record["experiment_id"]})
    records.append(record)
    _write_records(records, store_path)
    return _response(status="created", record=record, records=records, warnings=record.get("warnings"))


def update_walk_forward_experiment(
    experiment_id: str,
    changes: dict[str, Any],
    *,
    store_path: Path | str | None = None,
) -> dict[str, Any]:
    records = list_experiment_records(store_path=store_path)
    index, record = _find_record(records, experiment_id)
    if str(record.get("status")) in IMMUTABLE_STATUSES:
        raise ConflictError(
            "Frozen walk-forward experiment parameters cannot be edited in place; clone the experiment.",
            details={"experiment_id": experiment_id, "status": record.get("status")},
        )
    mutable = deepcopy(record)
    sanitized = _sanitize_value(changes or {})
    parameter_fields = set(SNAPSHOT_FIELDS) | {"name", "description", "status"}
    for key, value in sanitized.items():
        if key in parameter_fields:
            mutable[key] = value
    if "status" in mutable:
        mutable["status"] = _validate_status(str(mutable["status"]))
    snapshot = {field: mutable.get(field) for field in SNAPSHOT_FIELDS}
    mutable["parameter_digest"] = _digest(snapshot)
    records[index] = serialize_value(mutable)
    _write_records(records, store_path)
    return _response(status="updated", record=records[index], records=records)


def freeze_walk_forward_experiment(
    experiment_id: str,
    *,
    current_user: Any = None,
    store_path: Path | str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    records = list_experiment_records(store_path=store_path)
    index, record = _find_record(records, experiment_id)
    status = str(record.get("status") or "draft")
    if status != "draft":
        raise ConflictError(
            "Only draft walk-forward experiments can be frozen.",
            details={"experiment_id": experiment_id, "status": status},
        )
    frozen = deepcopy(record)
    frozen["status"] = "frozen"
    frozen["frozen_at"] = now or _utc_now()
    frozen["frozen_by"] = _created_by(current_user)
    warnings = [str(item) for item in _listify(frozen.get("warnings")) if item]
    warnings.append("Experiment parameters are now frozen; clone to make changes.")
    frozen["warnings"] = warnings
    records[index] = serialize_value(frozen)
    _write_records(records, store_path)
    return _response(status="frozen", record=records[index], records=records, warnings=warnings)


def clone_walk_forward_experiment(
    experiment_id: str,
    *,
    current_user: Any = None,
    store_path: Path | str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    records = list_experiment_records(store_path=store_path)
    _, source = _find_record(records, experiment_id)
    timestamp = now or _utc_now()
    clone = deepcopy(source)
    clone["experiment_id"] = f"wf-{uuid4().hex[:12]}"
    clone["name"] = f"{source.get('name') or experiment_id} clone"
    clone["description"] = str(source.get("description") or "")
    clone["created_at"] = timestamp
    clone["created_by"] = _created_by(current_user)
    clone["status"] = "draft"
    clone["cloned_from"] = experiment_id
    clone.pop("frozen_at", None)
    clone.pop("frozen_by", None)
    clone["warnings"] = ["Cloned from immutable experiment; review parameters before freezing."]
    clone["parameter_digest"] = _digest({field: clone.get(field) for field in SNAPSHOT_FIELDS})
    records.append(serialize_value(clone))
    _write_records(records, store_path)
    return _response(status="cloned", record=records[-1], records=records, warnings=clone["warnings"])


def create_walk_forward_experiment_from_runtime(
    payload: dict[str, Any],
    *,
    db: Any = None,
    current_user: Any = None,
    store_path: Path | str | None = None,
) -> dict[str, Any]:
    benchmark_report = _benchmark_report_from_inputs(db=db, current_user=current_user)
    return create_walk_forward_experiment(
        payload,
        current_user=current_user,
        benchmark_report=benchmark_report,
        store_path=store_path,
    )
