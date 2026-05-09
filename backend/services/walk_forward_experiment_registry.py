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
from backend.services.serialization import serialize_value
from backend.services.storage_utils import read_json_file, write_json_file

SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "paper_route_only": True,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
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
    return {
        "experiment_count": len(records),
        "draft_count": status_counts.get("draft", 0),
        "frozen_or_locked_count": frozen_count,
        "status_counts": status_counts,
        "verdict_counts": verdict_counts,
        "latest_experiment_id": latest.get("experiment_id") if latest else None,
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
    summary = _summary(record_list if records is not None else _read_records())
    return serialize_value(
        {
            "status": status,
            "generated_at": generated_at or _utc_now(),
            "research_only": True,
            "summary": summary,
            "record": record,
            "records": record_list,
            "warnings": [str(item) for item in warnings or [] if item],
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def list_experiment_records(*, store_path: Path | str | None = None) -> list[dict[str, Any]]:
    return _read_records(store_path)


def get_walk_forward_summary(*, store_path: Path | str | None = None) -> dict[str, Any]:
    records = list_experiment_records(store_path=store_path)
    return serialize_value(
        {
            "status": "ready" if records else "empty",
            "generated_at": _utc_now(),
            "research_only": True,
            "summary": _summary(records),
            "records": records[:100],
            "warnings": [] if records else ["No walk-forward experiments have been created yet."],
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
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
