from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from uuid import uuid4

from backend.services.evidence_reward_engine import get_evidence_reward_summary
from backend.services.forecast_validation_engine import get_forecast_validation_summary
from backend.services.project_finish_tracker import build_project_finish_tracker
from backend.services.serialization import serialize_value
from backend.services.storage_utils import read_json_file, write_json_file

SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "paper_route_only": True,
    "changes_execution": False,
    "changes_order_submission": False,
    "changes_broker_routes": False,
    "changes_risk_gates": False,
    "clears_kill_switch": False,
    "changes_ranking_weights": False,
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
    "writes_order_state": False,
}

SAFETY_NOTES: tuple[str, ...] = (
    "Research only. Does not affect trading.",
    "Does not place orders.",
    "Does not change broker routes.",
    "Does not bypass risk gates.",
    "Does not change ranking weights automatically.",
    "Does not grant AI order authority.",
)

REQUIRED_HUMAN_FIELDS: tuple[str, ...] = (
    "symbol",
    "human_direction",
    "human_confidence",
    "human_target_pct",
    "human_invalidation_level",
    "human_horizon_minutes",
)
REQUIRED_OUTCOME_FIELDS: tuple[str, ...] = ("actual_forward_return", "baseline_forward_return")
REQUIRED_SYSTEM_FIELDS: tuple[str, ...] = (
    "system_direction",
    "system_confidence",
    "system_target_pct",
    "system_invalidation_level",
    "system_horizon_minutes",
)
REQUIRED_COST_RISK_FIELDS: tuple[str, ...] = (
    "cost_model",
    "spread",
    "slippage",
    "fill_assumption",
    "risk_adjustment",
    "risk_gate_state",
    "kill_switch_state",
    "portfolio_exposure",
)
MIN_SHADOW_COMPARISON_COUNT = 3
MIN_SHADOW_PROOF_COVERAGE = 0.80

SHADOW_PROOF_REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "key": "same_opportunity_sample",
        "label": "Same-opportunity sample",
        "metric": "comparison_count",
        "threshold": MIN_SHADOW_COMPARISON_COUNT,
        "comparison": ">=",
        "safe_next_action": "Capture more human and system decisions on the same candidate set before making comparison claims.",
    },
    {
        "key": "same_opportunity_linkage",
        "label": "Same-opportunity linkage",
        "metric": "same_opportunity_coverage",
        "threshold": MIN_SHADOW_PROOF_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Link each human thesis to the exact candidate, system prediction, and matching horizon.",
    },
    {
        "key": "human_thesis_contract",
        "label": "Human thesis contract",
        "metric": "human_contract_coverage",
        "threshold": MIN_SHADOW_PROOF_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Require human direction, confidence, target, invalidation, horizon, and a specific thesis before scoring.",
    },
    {
        "key": "system_forecast_contract",
        "label": "System forecast contract",
        "metric": "system_contract_coverage",
        "threshold": MIN_SHADOW_PROOF_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Attach system direction, confidence, target, invalidation, and horizon to each comparison row.",
    },
    {
        "key": "outcome_contract",
        "label": "Outcome contract",
        "metric": "outcome_coverage",
        "threshold": MIN_SHADOW_PROOF_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Attach actual forward return, baseline return, target, invalidation, and outcome-window close evidence.",
    },
    {
        "key": "cost_risk_context",
        "label": "Cost and risk context",
        "metric": "cost_risk_context_coverage",
        "threshold": MIN_SHADOW_PROOF_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Attach spread, slippage, fill assumptions, risk adjustment, gate state, kill-switch state, and portfolio exposure.",
    },
    {
        "key": "decision_quality_metrics",
        "label": "Decision quality metrics",
        "metric": "decision_quality_metric_count",
        "threshold": 6,
        "comparison": ">=",
        "safe_next_action": "Report direction accuracy, target hit, false positives, false negatives, override quality, and missed winners for both sides.",
    },
    {
        "key": "system_after_cost_improvement",
        "label": "System after-cost improvement",
        "metric": "system_decision_quality_delta",
        "threshold": 0.0,
        "comparison": ">=",
        "safe_next_action": "Do not claim the system improves or beats a skilled trader until system net decision quality is at least as strong after costs and risk adjustment.",
    },
    {
        "key": "pre_outcome_capture",
        "label": "Pre-outcome capture",
        "metric": "pre_outcome_capture_coverage",
        "threshold": MIN_SHADOW_PROOF_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Timestamp human theses before the outcome window closes; do not hindsight-edit thesis records.",
    },
    {
        "key": "shadow_mode_safety_boundary",
        "label": "Shadow-mode safety boundary",
        "metric": "shadow_mode_safety_boundary",
        "threshold": 1,
        "comparison": ">=",
        "safe_next_action": "Keep Shadow Mode as research metadata only; do not place, route, approve, or configure trades.",
    },
)

SHADOW_MODE_VALIDATION_PLAN_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "same_opportunity_sample",
        "title": "Same-opportunity sample",
        "priority": "critical",
        "proof_keys": ("same_opportunity_sample",),
        "missing_fields": ("linked_candidate_id", "system_prediction_id", "same_horizon"),
        "blocked_claims": ("human_vs_system_comparison_claim", "system_beats_human_claim"),
        "safe_next_action": "Capture enough human and system decisions on the same candidate opportunity before judging either side.",
        "done_when": "At least the v1 minimum same-opportunity sample exists with linked human and system decisions.",
    },
    {
        "key": "decision_linkage",
        "title": "Decision linkage",
        "priority": "critical",
        "proof_keys": ("same_opportunity_linkage",),
        "missing_fields": ("linked_candidate_id", "system_prediction_id", "human_horizon_minutes", "system_horizon_minutes"),
        "blocked_claims": ("fair_comparison_claim", "repeatability_claim"),
        "safe_next_action": "Link every human thesis to the exact candidate, system prediction, and matching horizon.",
        "done_when": "Most comparison rows link human, system, candidate, and horizon fields before outcome scoring.",
    },
    {
        "key": "human_thesis_contract",
        "title": "Human thesis contract",
        "priority": "critical",
        "proof_keys": ("human_thesis_contract", "pre_outcome_capture"),
        "missing_fields": REQUIRED_HUMAN_FIELDS + ("human_reason", "created_at", "outcome_window_closed_at"),
        "blocked_claims": ("human_skill_claim", "override_quality_claim"),
        "safe_next_action": "Require a complete, timestamped human thesis before the outcome window closes.",
        "done_when": "Human records include direction, confidence, target, invalidation, horizon, thesis text, and pre-outcome timestamps.",
    },
    {
        "key": "system_forecast_contract",
        "title": "System forecast contract",
        "priority": "critical",
        "proof_keys": ("system_forecast_contract",),
        "missing_fields": REQUIRED_SYSTEM_FIELDS,
        "blocked_claims": ("system_quality_claim", "system_beats_human_claim"),
        "safe_next_action": "Attach the system forecast contract used at decision time to the same comparison row.",
        "done_when": "System records include direction, confidence, target, invalidation, and horizon for the matched opportunity.",
    },
    {
        "key": "outcome_contract",
        "title": "Outcome contract",
        "priority": "critical",
        "proof_keys": ("outcome_contract",),
        "missing_fields": REQUIRED_OUTCOME_FIELDS + ("outcome_window_closed_at", "target_hit", "invalidation_hit"),
        "blocked_claims": ("decision_quality_claim", "benchmark_relative_claim"),
        "safe_next_action": "Attach closed-window actual returns, baseline returns, target hits, invalidation hits, and outcome close evidence.",
        "done_when": "Comparison rows have closed-horizon outcomes and baseline returns before quality scoring.",
    },
    {
        "key": "cost_risk_context",
        "title": "Cost and risk context",
        "priority": "high",
        "proof_keys": ("cost_risk_context",),
        "missing_fields": REQUIRED_COST_RISK_FIELDS,
        "blocked_claims": ("after_cost_quality_claim", "paper_to_live_readiness"),
        "safe_next_action": "Attach spread, slippage, fill assumptions, risk adjustment, gate state, kill-switch state, and portfolio exposure.",
        "done_when": "Decision quality is measured after costs and with risk context visible for most comparison rows.",
    },
    {
        "key": "decision_quality_metrics",
        "title": "Decision quality metrics",
        "priority": "high",
        "proof_keys": ("decision_quality_metrics", "system_after_cost_improvement"),
        "missing_fields": ("direction_accuracy", "target_hit_rate", "false_positive_rate", "false_negative_rate", "override_quality", "missed_winner_comparison"),
        "blocked_claims": ("system_beats_human_claim", "human_override_quality_claim"),
        "safe_next_action": "Measure direction accuracy, targets, false positives, false negatives, overrides, and missed winners for both sides.",
        "done_when": "The report has enough after-cost metrics to compare human and system decision quality honestly.",
    },
    {
        "key": "shadow_safety_governance",
        "title": "Shadow safety governance",
        "priority": "critical",
        "proof_keys": ("shadow_mode_safety_boundary",),
        "missing_fields": (),
        "blocked_claims": ("automatic_ranking_mutation", "paper_to_live_readiness", "live_trading_readiness"),
        "safe_next_action": "Keep Shadow Mode as research metadata only; do not place, route, approve, or configure trades.",
        "done_when": "Shadow Mode remains read-only to execution state and cannot mutate execution, broker, risk, or ranking configuration.",
    },
)
SECRET_KEY_MARKERS = ("secret", "token", "password", "credential", "api_key", "apikey", "access_key", "private_key", "account_id")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE_PATH = PROJECT_ROOT / "runtime-exports" / "human-system-shadow-mode" / "human_theses.json"


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


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _mean(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in (_safe_float(item) for item in values) if value is not None]
    return round(sum(clean) / len(clean), 6) if clean else None


def _median(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in (_safe_float(item) for item in values) if value is not None]
    return round(float(median(clean)), 6) if clean else None


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 6)


def _passes_threshold(value: Any, threshold: float, comparison: str) -> bool:
    numeric = _safe_float(value)
    if numeric is None:
        return False
    if comparison == ">=":
        return numeric >= threshold
    if comparison == ">":
        return numeric > threshold
    if comparison == "<=":
        return numeric <= threshold
    if comparison == "<":
        return numeric < threshold
    return numeric == threshold


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and str(value).strip().lower() not in {"unknown", "nan", "none", "null"}


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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


def _direction_sign(value: Any) -> int | None:
    cleaned = str(value or "").strip().lower()
    if cleaned in {"up", "long", "buy", "bullish", "higher", "call"}:
        return 1
    if cleaned in {"down", "short", "sell", "bearish", "lower", "put"}:
        return -1
    if cleaned in {"flat", "neutral", "range"}:
        return 0
    return None


def _direction_label(value: Any) -> str:
    sign = _direction_sign(value)
    if sign == 1:
        return "up"
    if sign == -1:
        return "down"
    if sign == 0:
        return "flat"
    return "unknown"


def _looks_like_local_path(value: str) -> bool:
    cleaned = value.strip()
    return (len(cleaned) >= 3 and cleaned[1:3] in {":\\", ":/"}) or cleaned.startswith("\\\\")


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
    if isinstance(value, str) and _looks_like_local_path(value):
        return "[local_path_redacted]"
    return value


def _store_path(path: Path | str | None = None) -> Path:
    return Path(path) if path is not None else DEFAULT_STORE_PATH


def _read_human_records(store_path: Path | str | None = None) -> list[dict[str, Any]]:
    payload = read_json_file(_store_path(store_path), {"human_theses": []})
    records = payload.get("human_theses") if isinstance(payload, dict) else []
    return [row for row in records or [] if isinstance(row, dict)]


def _write_human_records(records: list[dict[str, Any]], store_path: Path | str | None = None) -> None:
    write_json_file(
        _store_path(store_path),
        {
            "schema_version": "human_system_shadow_mode_v1",
            "updated_at": _utc_now(),
            "human_theses": [_sanitize_value(row) for row in records],
        },
    )


def _created_by(current_user: Any = None) -> str | None:
    for field in ("user_id", "auth_subject", "name"):
        value = getattr(current_user, field, None)
        if value:
            return str(value)
    return None


def _record_digest(payload: dict[str, Any]) -> str:
    serialized = json.dumps(_sanitize_value(payload), sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _first_value(row: dict[str, Any], fields: Iterable[str]) -> Any:
    nested = [row]
    for key in ("prediction_contract", "system_prediction", "reward_components", "component_scores", "evaluation"):
        value = row.get(key)
        if isinstance(value, dict):
            nested.append(value)
    for source in nested:
        for field in fields:
            value = source.get(field)
            if value is not None and value != "":
                return value
    return None


def _first_number(row: dict[str, Any], fields: Iterable[str]) -> float | None:
    for field in fields:
        value = _safe_float(_first_value(row, (field,)))
        if value is not None:
            return value
    return None


def _first_text(row: dict[str, Any], fields: Iterable[str], fallback: str = "") -> str:
    for field in fields:
        value = _first_value(row, (field,))
        if value is not None and str(value).strip():
            return str(value).strip()
    return fallback


def _is_simulation_evidence(row: dict[str, Any]) -> bool:
    nested = [row]
    for key in ("payload", "candidate", "paper_trade_outcome", "prediction_contract", "system_prediction", "reward_components", "component_scores", "evaluation"):
        value = row.get(key)
        if isinstance(value, dict):
            nested.append(value)
    for source in nested:
        evidence_pool = str(source.get("evidence_pool") or "").strip().lower()
        if source.get("simulation_evidence") or evidence_pool == "simulation_evidence":
            return True
    return False


def _field_missing(row: dict[str, Any], field: str) -> bool:
    value = row.get(field)
    return value is None or value == ""


def human_missing_fields(row: dict[str, Any]) -> list[str]:
    missing = [field for field in REQUIRED_HUMAN_FIELDS if _field_missing(row, field)]
    if _direction_sign(row.get("human_direction")) is None:
        missing.append("human_direction")
    confidence = _safe_float(row.get("human_confidence"))
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        missing.append("human_confidence")
    return list(dict.fromkeys(missing))


def outcome_missing_fields(row: dict[str, Any]) -> list[str]:
    return [field for field in REQUIRED_OUTCOME_FIELDS if _safe_float(row.get(field)) is None]


def _direction_correct(direction: Any, actual_forward_return: Any) -> bool | None:
    sign = _direction_sign(direction)
    actual = _safe_float(actual_forward_return)
    if sign is None or actual is None:
        return None
    if sign == 0:
        return abs(actual) < 0.05
    return (actual * sign) > 0


def _target_hit(direction: Any, target_pct: Any, actual_forward_return: Any, explicit: Any = None) -> bool | None:
    if explicit is not None and explicit != "":
        return bool(explicit)
    sign = _direction_sign(direction)
    target = _safe_float(target_pct)
    actual = _safe_float(actual_forward_return)
    if sign is None or target is None or actual is None:
        return None
    return actual * sign >= abs(target)


def _invalidation_hit(explicit: Any = None) -> bool:
    if isinstance(explicit, str):
        return explicit.strip().lower() in {"1", "true", "yes", "hit", "invalidated"}
    return bool(explicit)


def compute_shadow_reward(
    *,
    direction: Any,
    confidence: Any,
    target_pct: Any,
    actual_forward_return: Any,
    baseline_forward_return: Any,
    max_adverse_excursion: Any = None,
    hit_target: Any = None,
    hit_invalidation: Any = None,
    time_to_target: Any = None,
    horizon_minutes: Any = None,
) -> dict[str, Any]:
    actual = _safe_float(actual_forward_return)
    baseline = _safe_float(baseline_forward_return)
    conf = _safe_float(confidence)
    target = _safe_float(target_pct)
    adverse = abs(_safe_float(max_adverse_excursion) or 0.0)
    horizon = _safe_float(horizon_minutes)
    time_to_target_value = _safe_float(time_to_target)
    direction_correct = _direction_correct(direction, actual)
    target_hit = _target_hit(direction, target, actual, hit_target)
    invalidation_hit = _invalidation_hit(hit_invalidation)
    if actual is None or baseline is None or direction_correct is None or conf is None or target is None:
        return {
            "rewardable": False,
            "total_reward": None,
            "direction_correct": direction_correct,
            "target_hit": target_hit,
            "invalidation_hit": invalidation_hit,
            "components": {},
        }
    direction_score = 1.0 if direction_correct else -1.0
    target_score = 0.5 if target_hit else -0.25
    baseline_relative_score = actual - baseline
    adverse_penalty = adverse * 0.5
    invalidation_penalty = 0.75 if invalidation_hit else 0.0
    late_penalty = 0.0
    if target_hit and horizon is not None and time_to_target_value is not None and time_to_target_value > horizon:
        late_penalty = 0.25
    confidence_error = abs(conf - (1.0 if direction_correct else 0.0))
    confidence_penalty = confidence_error * 0.5
    total = direction_score + target_score + baseline_relative_score - adverse_penalty - invalidation_penalty - late_penalty - confidence_penalty
    return {
        "rewardable": True,
        "total_reward": round(total, 6),
        "direction_correct": direction_correct,
        "target_hit": bool(target_hit),
        "invalidation_hit": invalidation_hit,
        "components": {
            "direction_score": round(direction_score, 6),
            "target_score": round(target_score, 6),
            "baseline_relative_score": round(baseline_relative_score, 6),
            "adverse_penalty": round(adverse_penalty, 6),
            "invalidation_penalty": round(invalidation_penalty, 6),
            "late_penalty": round(late_penalty, 6),
            "confidence_penalty": round(confidence_penalty, 6),
            "confidence_error": round(confidence_error, 6),
        },
    }


def _normalize_system_record(row: dict[str, Any]) -> dict[str, Any]:
    evaluation = row.get("evaluation") if isinstance(row.get("evaluation"), dict) else row
    return {
        "system_prediction_id": _first_text(row, ("prediction_id", "record_id", "candidate_lifecycle_id"), ""),
        "linked_candidate_id": _first_text(row, ("linked_candidate_id", "candidate_lifecycle_id", "record_id"), ""),
        "symbol": _first_text(row, ("symbol",), "").upper(),
        "system_direction": _direction_label(_first_value(evaluation, ("predicted_direction", "direction", "system_direction")) or _first_value(row, ("predicted_direction", "direction", "system_direction"))),
        "system_confidence": _first_number(row, ("confidence", "system_confidence")),
        "system_target_pct": _first_number(row, ("predicted_target_pct", "system_target_pct", "target_pct")),
        "system_invalidation_level": _first_number(row, ("invalidation_level", "system_invalidation_level")),
        "system_horizon_minutes": _safe_int(_first_value(row, ("horizon_minutes", "prediction_horizon_minutes", "system_horizon_minutes"))),
        "system_forecast_reward": _first_number(row, ("forecast_total_reward", "total_reward")),
        "system_candidate_reward": _first_number(row, ("candidate_reward", "total_reward")),
        "engine": _first_text(row, ("engine", "model_name", "source"), ""),
        "setup_type": _first_text(row, ("setup_type",), ""),
        "regime": _first_text(row, ("regime",), ""),
    }


def _load_system_records(db: Any = None, current_user: Any = None) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    records: list[dict[str, Any]] = []
    try:
        forecast_report = get_forecast_validation_summary()
        for item in list(forecast_report.get("records") or forecast_report.get("items") or []):
            if isinstance(item, dict):
                records.append(_normalize_system_record(item))
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Forecast Validation source unavailable: {exc.__class__.__name__}.")
    try:
        reward_report = get_evidence_reward_summary(db, current_user=current_user)
        for item in list(reward_report.get("records") or reward_report.get("candidate_rows") or []):
            if isinstance(item, dict):
                records.append(_normalize_system_record(item))
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Evidence Reward source unavailable: {exc.__class__.__name__}.")
    return records, warnings


def _match_system_record(human: dict[str, Any], system_records: list[dict[str, Any]]) -> dict[str, Any] | None:
    linked = str(human.get("linked_candidate_id") or "").strip()
    symbol = str(human.get("symbol") or "").strip().upper()
    if linked:
        for row in system_records:
            if _is_simulation_evidence(row):
                continue
            if linked in {str(row.get("linked_candidate_id") or ""), str(row.get("system_prediction_id") or "")}:
                return row
        return None
    for row in system_records:
        if _is_simulation_evidence(row):
            continue
        if symbol and str(row.get("symbol") or "").strip().upper() == symbol:
            return row
    return None


def create_human_thesis(payload: dict[str, Any], *, current_user: Any = None, store_path: Path | str | None = None) -> dict[str, Any]:
    now = _utc_now()
    sanitized = _sanitize_value(dict(payload or {}))
    record = {
        "human_thesis_id": str(sanitized.get("human_thesis_id") or f"human-shadow-{uuid4().hex[:12]}"),
        "created_at": str(sanitized.get("created_at") or now),
        "created_by": _created_by(current_user),
        "symbol": str(sanitized.get("symbol") or "").strip().upper(),
        "linked_candidate_id": str(sanitized.get("linked_candidate_id") or "").strip() or None,
        "human_direction": _direction_label(sanitized.get("human_direction")),
        "human_confidence": _safe_float(sanitized.get("human_confidence")),
        "human_target_pct": _safe_float(sanitized.get("human_target_pct")),
        "human_invalidation_level": _safe_float(sanitized.get("human_invalidation_level")),
        "human_horizon_minutes": _safe_int(sanitized.get("human_horizon_minutes")),
        "human_reason": str(sanitized.get("human_reason") or "").strip()[:1000],
        "setup_type": str(sanitized.get("setup_type") or "").strip() or None,
        "engine": str(sanitized.get("engine") or "").strip() or None,
        "regime": str(sanitized.get("regime") or "").strip() or None,
        "system_prediction_id": str(sanitized.get("system_prediction_id") or "").strip() or None,
        "system_direction": _direction_label(sanitized.get("system_direction")),
        "system_confidence": _safe_float(sanitized.get("system_confidence")),
        "system_target_pct": _safe_float(sanitized.get("system_target_pct")),
        "system_invalidation_level": _safe_float(sanitized.get("system_invalidation_level")),
        "system_horizon_minutes": _safe_int(sanitized.get("system_horizon_minutes")),
        "system_forecast_reward": _safe_float(sanitized.get("system_forecast_reward")),
        "system_candidate_reward": _safe_float(sanitized.get("system_candidate_reward")),
        "cost_model": str(sanitized.get("cost_model") or "").strip() or None,
        "human_reward_after_costs": _safe_float(sanitized.get("human_reward_after_costs")),
        "system_reward_after_costs": _safe_float(sanitized.get("system_reward_after_costs")),
        "spread": _safe_float(sanitized.get("spread")),
        "slippage": _safe_float(sanitized.get("slippage")),
        "fill_assumption": str(sanitized.get("fill_assumption") or "").strip() or None,
        "risk_adjustment": _safe_float(sanitized.get("risk_adjustment")),
        "risk_gate_state": str(sanitized.get("risk_gate_state") or "").strip() or None,
        "kill_switch_state": str(sanitized.get("kill_switch_state") or "").strip() or None,
        "portfolio_exposure": _safe_float(sanitized.get("portfolio_exposure")),
        "actual_forward_return": _safe_float(sanitized.get("actual_forward_return")),
        "baseline_forward_return": _safe_float(sanitized.get("baseline_forward_return")),
        "outcome_window_closed_at": str(sanitized.get("outcome_window_closed_at") or sanitized.get("outcome_at") or "").strip() or None,
        "target_hit": sanitized.get("target_hit"),
        "invalidation_hit": sanitized.get("invalidation_hit"),
        "max_adverse_excursion": _safe_float(sanitized.get("max_adverse_excursion")),
        "time_to_target": _safe_float(sanitized.get("time_to_target")),
        "blockers": [str(item).strip() for item in _listify(sanitized.get("blockers")) if str(item).strip()],
        "metadata": _sanitize_value(sanitized.get("metadata") or {}),
        "immutable_after_outcome_close": bool(sanitized.get("immutable_after_outcome_close", False)),
        "research_only": True,
        "paper_route_only": True,
    }
    record["record_digest"] = _record_digest(record)
    records = _read_human_records(store_path)
    records.append(record)
    _write_human_records(records, store_path)
    normalized = build_shadow_comparison_row(record, system_records=[])
    return serialize_value(
        {
            "status": "created",
            "generated_at": now,
            "research_only": True,
            "record": normalized,
            "summary": {"human_thesis_id": record["human_thesis_id"], "rewardable": normalized.get("human_rewardable")},
            "warnings": normalized.get("warnings", []),
            "missing_fields": {field: 1 for field in normalized.get("missing_fields", [])},
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def build_shadow_comparison_row(human: dict[str, Any], *, system_records: list[dict[str, Any]]) -> dict[str, Any]:
    matched_system = _match_system_record(human, system_records) or {}
    system_direction = human.get("system_direction") if human.get("system_direction") != "unknown" else matched_system.get("system_direction")
    system_confidence = _safe_float(human.get("system_confidence"))
    if system_confidence is None:
        system_confidence = _safe_float(matched_system.get("system_confidence"))
    system_target_pct = _safe_float(human.get("system_target_pct"))
    if system_target_pct is None:
        system_target_pct = _safe_float(matched_system.get("system_target_pct"))
    system_invalidation = _safe_float(human.get("system_invalidation_level"))
    if system_invalidation is None:
        system_invalidation = _safe_float(matched_system.get("system_invalidation_level"))
    system_horizon = _safe_int(human.get("system_horizon_minutes"))
    if system_horizon is None:
        system_horizon = _safe_int(matched_system.get("system_horizon_minutes"))
    human_reward = compute_shadow_reward(
        direction=human.get("human_direction"),
        confidence=human.get("human_confidence"),
        target_pct=human.get("human_target_pct"),
        actual_forward_return=human.get("actual_forward_return"),
        baseline_forward_return=human.get("baseline_forward_return"),
        max_adverse_excursion=human.get("max_adverse_excursion"),
        hit_target=human.get("target_hit"),
        hit_invalidation=human.get("invalidation_hit"),
        time_to_target=human.get("time_to_target"),
        horizon_minutes=human.get("human_horizon_minutes"),
    )
    explicit_system_reward = _safe_float(human.get("system_forecast_reward"))
    if explicit_system_reward is None:
        explicit_system_reward = _safe_float(human.get("system_candidate_reward"))
    if explicit_system_reward is None:
        explicit_system_reward = _safe_float(matched_system.get("system_forecast_reward"))
    if explicit_system_reward is None:
        explicit_system_reward = _safe_float(matched_system.get("system_candidate_reward"))
    system_reward = compute_shadow_reward(
        direction=system_direction,
        confidence=system_confidence,
        target_pct=system_target_pct,
        actual_forward_return=human.get("actual_forward_return"),
        baseline_forward_return=human.get("baseline_forward_return"),
        max_adverse_excursion=human.get("max_adverse_excursion"),
        hit_target=human.get("target_hit"),
        hit_invalidation=human.get("invalidation_hit"),
        time_to_target=human.get("time_to_target"),
        horizon_minutes=system_horizon,
    )
    if explicit_system_reward is not None and system_reward.get("rewardable"):
        system_reward["total_reward"] = round(explicit_system_reward, 6)
    missing = human_missing_fields(human) + outcome_missing_fields(human)
    if system_direction in (None, "", "unknown"):
        missing.append("system_direction")
    if system_confidence is None:
        missing.append("system_confidence")
    if system_target_pct is None:
        missing.append("system_target_pct")
    if system_invalidation is None:
        missing.append("system_invalidation_level")
    if system_horizon is None:
        missing.append("system_horizon_minutes")
    human_total = _safe_float(human_reward.get("total_reward"))
    system_total = _safe_float(system_reward.get("total_reward"))
    human_after_costs = _safe_float(human.get("human_reward_after_costs"))
    system_after_costs = _safe_float(human.get("system_reward_after_costs"))
    human_net_after_costs = human_after_costs if human_after_costs is not None else human_total
    system_net_after_costs = system_after_costs if system_after_costs is not None else system_total
    if human_total is None and system_total is None:
        winner = "insufficient_data"
    elif system_total is None or (human_total is not None and human_total > system_total):
        winner = "human"
    elif human_total is None or system_total > human_total:
        winner = "system"
    else:
        winner = "tie"
    warnings: list[str] = []
    if missing:
        warnings.append("Missing fields prevent complete human-vs-system shadow scoring.")
    if not human.get("human_reason"):
        warnings.append("Human reason is empty; vague labels do not count as a complete thesis.")
    return _sanitize_value(
        {
            "human_thesis_id": human.get("human_thesis_id"),
            "created_at": human.get("created_at"),
            "symbol": str(human.get("symbol") or "").upper(),
            "linked_candidate_id": human.get("linked_candidate_id"),
            "human_direction": human.get("human_direction"),
            "human_confidence": human.get("human_confidence"),
            "human_target_pct": human.get("human_target_pct"),
            "human_invalidation_level": human.get("human_invalidation_level"),
            "human_horizon_minutes": human.get("human_horizon_minutes"),
            "human_reason": human.get("human_reason"),
            "setup_type": human.get("setup_type") or matched_system.get("setup_type"),
            "engine": human.get("engine") or matched_system.get("engine"),
            "regime": human.get("regime") or matched_system.get("regime"),
            "system_prediction_id": human.get("system_prediction_id") or matched_system.get("system_prediction_id"),
            "system_direction": system_direction or "unknown",
            "system_confidence": system_confidence,
            "system_target_pct": system_target_pct,
            "system_invalidation_level": system_invalidation,
            "system_horizon_minutes": system_horizon,
            "cost_model": human.get("cost_model"),
            "human_reward_after_costs": human_after_costs,
            "system_reward_after_costs": system_after_costs,
            "human_net_decision_quality_after_costs": human_net_after_costs,
            "system_net_decision_quality_after_costs": system_net_after_costs,
            "spread": _safe_float(human.get("spread")),
            "slippage": _safe_float(human.get("slippage")),
            "fill_assumption": human.get("fill_assumption"),
            "risk_adjustment": _safe_float(human.get("risk_adjustment")),
            "risk_gate_state": human.get("risk_gate_state"),
            "kill_switch_state": human.get("kill_switch_state"),
            "portfolio_exposure": _safe_float(human.get("portfolio_exposure")),
            "actual_forward_return": human.get("actual_forward_return"),
            "baseline_forward_return": human.get("baseline_forward_return"),
            "outcome_window_closed_at": human.get("outcome_window_closed_at") or human.get("outcome_at"),
            "target_hit": human_reward.get("target_hit"),
            "invalidation_hit": human_reward.get("invalidation_hit"),
            "max_adverse_excursion": human.get("max_adverse_excursion"),
            "time_to_target": human.get("time_to_target"),
            "blockers": [str(item) for item in _listify(human.get("blockers"))],
            "record_digest": human.get("record_digest"),
            "immutable_after_outcome_close": bool(human.get("immutable_after_outcome_close", False)),
            "human_reward": human_total,
            "system_reward": system_total,
            "human_reward_components": human_reward.get("components", {}),
            "system_reward_components": system_reward.get("components", {}),
            "human_rewardable": bool(human_reward.get("rewardable")) and not human_missing_fields(human) and not outcome_missing_fields(human),
            "system_rewardable": bool(system_reward.get("rewardable")),
            "human_direction_correct": human_reward.get("direction_correct"),
            "system_direction_correct": system_reward.get("direction_correct"),
            "winner": winner,
            "warnings": warnings,
            "missing_fields": sorted(set(str(field) for field in missing if field)),
            "research_only": True,
        }
    )


def _bias_flags(row: dict[str, Any]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    human_conf = _safe_float(row.get("human_confidence")) or 0.0
    human_reward = _safe_float(row.get("human_reward"))
    system_reward = _safe_float(row.get("system_reward"))
    actual = _safe_float(row.get("actual_forward_return"))
    adverse = abs(_safe_float(row.get("max_adverse_excursion")) or 0.0)
    blockers = [str(item).lower() for item in _listify(row.get("blockers"))]
    if human_conf >= 0.70 and row.get("human_direction_correct") is False:
        flags.append({"bias": "high_confidence_wrong_calls", "detail": "Human confidence was high while direction was wrong."})
    if adverse >= 0.50:
        flags.append({"bias": "chasing_extended_moves", "detail": "The idea suffered large adverse excursion before outcome measurement."})
    if row.get("target_hit") and _safe_float(row.get("time_to_target")) is not None and _safe_float(row.get("human_horizon_minutes")) is not None and float(row.get("time_to_target")) > float(row.get("human_horizon_minutes")):
        flags.append({"bias": "late_entries", "detail": "Target eventually hit, but later than the human horizon."})
    if row.get("invalidation_hit"):
        flags.append({"bias": "holding_invalidated_ideas", "detail": "Invalidation was hit during the outcome window."})
    if blockers and actual is not None and actual < 0 and row.get("human_direction_correct") is False:
        flags.append({"bias": "ignoring_good_blockers", "detail": "Human thesis fought blockers that later aligned with a poor outcome."})
    if row.get("human_direction") != row.get("system_direction") and (_safe_float(row.get("system_confidence")) or 0.0) >= 0.70 and system_reward is not None and human_reward is not None and system_reward > human_reward:
        flags.append({"bias": "overriding_strong_system_evidence", "detail": "Human direction overrode high-confidence system evidence and underperformed it."})
    if human_conf < 0.45 and row.get("human_direction_correct") and human_reward is not None and human_reward > 0:
        flags.append({"bias": "underconfidence_on_good_calls", "detail": "Human thesis was correct with positive reward but low confidence."})
    return flags


def compute_shadow_analytics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    human_rewardable = [row for row in rows if row.get("human_rewardable") and row.get("human_reward") is not None]
    system_rewardable = [row for row in rows if row.get("system_rewardable") and row.get("system_reward") is not None]
    comparable = [row for row in rows if row.get("human_reward") is not None and row.get("system_reward") is not None]
    human_correct = sum(1 for row in human_rewardable if row.get("human_direction_correct"))
    system_correct = sum(1 for row in system_rewardable if row.get("system_direction_correct"))
    human_target_hits = sum(1 for row in human_rewardable if row.get("target_hit"))
    system_target_hits = sum(1 for row in system_rewardable if row.get("target_hit") and row.get("system_direction_correct"))
    human_invalidation_hits = sum(1 for row in human_rewardable if row.get("invalidation_hit"))
    system_invalidation_hits = sum(1 for row in system_rewardable if row.get("invalidation_hit"))
    human_false_positive = sum(1 for row in human_rewardable if row.get("human_direction_correct") is False)
    system_false_positive = sum(1 for row in system_rewardable if row.get("system_direction_correct") is False)
    human_false_negative = sum(1 for row in human_rewardable if (_safe_float(row.get("actual_forward_return")) or 0.0) >= 0.25 and row.get("human_direction_correct") is False)
    system_false_negative = sum(1 for row in system_rewardable if (_safe_float(row.get("actual_forward_return")) or 0.0) >= 0.25 and row.get("system_direction_correct") is False)
    override_rows = [row for row in comparable if row.get("human_direction") != row.get("system_direction")]
    human_override_wins = sum(1 for row in override_rows if row.get("winner") == "human")
    missed_winners = [row for row in rows if (_safe_float(row.get("actual_forward_return")) or 0.0) >= 0.25]
    bias_counter: Counter[str] = Counter()
    bias_items: list[dict[str, Any]] = []
    for row in rows:
        for flag in _bias_flags(row):
            bias_counter[str(flag["bias"])] += 1
            bias_items.append({**flag, "human_thesis_id": row.get("human_thesis_id"), "symbol": row.get("symbol")})
    return {
        "human_direction_accuracy": _ratio(human_correct, len(human_rewardable)),
        "system_direction_accuracy": _ratio(system_correct, len(system_rewardable)),
        "human_target_hit_rate": _ratio(human_target_hits, len(human_rewardable)),
        "system_target_hit_rate": _ratio(system_target_hits, len(system_rewardable)),
        "human_invalidation_hit_rate": _ratio(human_invalidation_hits, len(human_rewardable)),
        "system_invalidation_hit_rate": _ratio(system_invalidation_hits, len(system_rewardable)),
        "human_avg_reward": _mean(row.get("human_reward") for row in human_rewardable),
        "system_avg_reward": _mean(row.get("system_reward") for row in system_rewardable),
        "human_median_reward": _median(row.get("human_reward") for row in human_rewardable),
        "system_median_reward": _median(row.get("system_reward") for row in system_rewardable),
        "human_vs_system_edge": None if not comparable else round((_mean(row.get("human_reward") for row in comparable) or 0.0) - (_mean(row.get("system_reward") for row in comparable) or 0.0), 6),
        "human_false_positive_rate": _ratio(human_false_positive, len(human_rewardable)),
        "system_false_positive_rate": _ratio(system_false_positive, len(system_rewardable)),
        "human_false_negative_rate": _ratio(human_false_negative, len(human_rewardable)),
        "system_false_negative_rate": _ratio(system_false_negative, len(system_rewardable)),
        "override_quality": {
            "override_count": len(override_rows),
            "human_override_win_rate": _ratio(human_override_wins, len(override_rows)),
            "system_override_win_rate": _ratio(sum(1 for row in override_rows if row.get("winner") == "system"), len(override_rows)),
        },
        "missed_winner_comparison": {
            "missed_winner_count": len(missed_winners),
            "human_caught_count": sum(1 for row in missed_winners if row.get("human_direction_correct")),
            "system_caught_count": sum(1 for row in missed_winners if row.get("system_direction_correct")),
            "human_caught_rate": _ratio(sum(1 for row in missed_winners if row.get("human_direction_correct")), len(missed_winners)),
            "system_caught_rate": _ratio(sum(1 for row in missed_winners if row.get("system_direction_correct")), len(missed_winners)),
        },
        "bias_diagnostics": {
            "items": bias_items,
            "counts": dict(bias_counter),
        },
    }


def _pre_outcome_captured(row: dict[str, Any]) -> bool:
    created_at = _parse_time(row.get("created_at"))
    closed_at = _parse_time(row.get("outcome_window_closed_at"))
    return created_at is not None and closed_at is not None and created_at < closed_at


def _shadow_row_readiness(row: dict[str, Any]) -> dict[str, Any]:
    same_horizon = row.get("human_horizon_minutes") == row.get("system_horizon_minutes")
    same_opportunity = bool(row.get("linked_candidate_id")) and bool(row.get("system_prediction_id")) and same_horizon
    human_contract = not human_missing_fields(row) and bool(str(row.get("human_reason") or "").strip())
    system_contract = all(_has_value(row.get(field)) for field in REQUIRED_SYSTEM_FIELDS)
    outcome_contract = all(_has_value(row.get(field)) for field in REQUIRED_OUTCOME_FIELDS) and row.get("target_hit") is not None and row.get("invalidation_hit") is not None
    cost_risk_context = all(_has_value(row.get(field)) for field in REQUIRED_COST_RISK_FIELDS)
    reward_comparable = row.get("human_net_decision_quality_after_costs") is not None and row.get("system_net_decision_quality_after_costs") is not None
    pre_outcome = _pre_outcome_captured(row)
    warnings: list[str] = list(row.get("warnings") or [])
    if not same_opportunity:
        warnings.append("Same-opportunity linkage is incomplete.")
    if not human_contract:
        warnings.append("Human thesis contract is incomplete.")
    if not system_contract:
        warnings.append("System forecast contract is incomplete.")
    if not outcome_contract:
        warnings.append("Outcome contract is incomplete.")
    if not cost_risk_context:
        warnings.append("Cost or risk context is incomplete.")
    if not pre_outcome:
        warnings.append("Pre-outcome capture cannot be proven.")
    return {
        "human_thesis_id": row.get("human_thesis_id"),
        "symbol": row.get("symbol"),
        "linked_candidate_id": row.get("linked_candidate_id"),
        "system_prediction_id": row.get("system_prediction_id"),
        "same_opportunity_complete": same_opportunity,
        "human_contract_complete": human_contract,
        "system_contract_complete": system_contract,
        "outcome_contract_complete": outcome_contract,
        "cost_risk_context_complete": cost_risk_context,
        "reward_comparable": reward_comparable,
        "pre_outcome_capture_proven": pre_outcome,
        "same_horizon": same_horizon,
        "human_net_decision_quality_after_costs": row.get("human_net_decision_quality_after_costs"),
        "system_net_decision_quality_after_costs": row.get("system_net_decision_quality_after_costs"),
        "winner": row.get("winner"),
        "warnings": list(dict.fromkeys(warnings)),
        "missing_fields": row.get("missing_fields") or [],
        "research_only": True,
        "changes_execution": False,
        "changes_order_submission": False,
        "changes_broker_routes": False,
        "changes_risk_gates": False,
        "changes_ranking_weights": False,
        "can_change_broker_routes": False,
        "can_bypass_risk_gates": False,
        "can_change_ranking_weights": False,
        "can_grant_ai_order_authority": False,
    }


def build_shadow_mode_proof_summary(rows: list[dict[str, Any]], aggregations: dict[str, Any]) -> dict[str, Any]:
    comparison_count = len(rows)
    readiness = [_shadow_row_readiness(row) for row in rows]

    def coverage(field: str) -> float:
        return _ratio(sum(1 for row in readiness if row.get(field)), comparison_count) or 0.0

    comparable_rows = [row for row in readiness if row.get("reward_comparable")]
    system_quality_delta = None
    if comparable_rows:
        system_quality_delta = round(
            (_mean(row.get("system_net_decision_quality_after_costs") for row in comparable_rows) or 0.0)
            - (_mean(row.get("human_net_decision_quality_after_costs") for row in comparable_rows) or 0.0),
            6,
        )
    metric_values = [
        aggregations.get("human_direction_accuracy"),
        aggregations.get("system_direction_accuracy"),
        aggregations.get("human_target_hit_rate"),
        aggregations.get("system_target_hit_rate"),
        aggregations.get("human_false_positive_rate"),
        aggregations.get("system_false_positive_rate"),
        aggregations.get("human_false_negative_rate"),
        aggregations.get("system_false_negative_rate"),
        (aggregations.get("override_quality") or {}).get("human_override_win_rate"),
        (aggregations.get("missed_winner_comparison") or {}).get("human_caught_rate"),
        (aggregations.get("missed_winner_comparison") or {}).get("system_caught_rate"),
    ]
    decision_quality_metric_count = sum(1 for value in metric_values if value is not None)
    safety_boundary = int(
        SAFETY_FLAGS["can_submit_orders"] is False
        and SAFETY_FLAGS["can_submit_live_orders"] is False
        and SAFETY_FLAGS["writes_execution_config"] is False
        and SAFETY_FLAGS["writes_broker_config"] is False
        and SAFETY_FLAGS["writes_risk_config"] is False
        and SAFETY_FLAGS["writes_ranking_config"] is False
        and SAFETY_FLAGS["writes_order_state"] is False
    )
    values = {
        "comparison_count": comparison_count,
        "same_opportunity_coverage": coverage("same_opportunity_complete"),
        "human_contract_coverage": coverage("human_contract_complete"),
        "system_contract_coverage": coverage("system_contract_complete"),
        "outcome_coverage": coverage("outcome_contract_complete"),
        "cost_risk_context_coverage": coverage("cost_risk_context_complete"),
        "decision_quality_metric_count": decision_quality_metric_count,
        "system_decision_quality_delta": system_quality_delta,
        "pre_outcome_capture_coverage": coverage("pre_outcome_capture_proven"),
        "shadow_mode_safety_boundary": safety_boundary,
    }
    requirement_rows: list[dict[str, Any]] = []
    for requirement in SHADOW_PROOF_REQUIREMENTS:
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
                "claim_boundary": "Shadow-mode proof is same-opportunity research review only; it is not proof of alpha, guaranteed returns, investment advice, live-trading readiness, or order approval.",
                "research_only": True,
                "changes_execution": False,
                "changes_order_submission": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
                "changes_ranking_weights": False,
                "can_change_broker_routes": False,
                "can_bypass_risk_gates": False,
                "can_change_ranking_weights": False,
                "can_grant_ai_order_authority": False,
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
                "reward_comparable_count": len(comparable_rows),
                "requirement_count": len(requirement_rows),
                "passed_requirement_count": sum(1 for row in requirement_rows if row["passed"]),
                "missing_requirement_count": sum(1 for row in requirement_rows if not row["passed"]),
            },
            "record_readiness": readiness[:100],
            "safe_next_actions": [
                {
                    "field": row["key"],
                    "action": row["safe_next_action"],
                    "manual_review_only": True,
                    "changes_execution": False,
                    "changes_order_submission": False,
                    "changes_broker_routes": False,
                    "changes_risk_gates": False,
                    "changes_ranking_weights": False,
                    "can_change_broker_routes": False,
                    "can_bypass_risk_gates": False,
                    "can_change_ranking_weights": False,
                    "can_grant_ai_order_authority": False,
                }
                for row in requirement_rows
                if not row["passed"]
            ],
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def build_shadow_mode_validation_plan(
    *,
    rows: list[dict[str, Any]],
    proof_summary: dict[str, Any],
) -> dict[str, Any]:
    proof_rows = {
        str(row.get("key")): row
        for row in proof_summary.get("requirements") or []
        if isinstance(row, dict)
    }
    all_missing_fields: Counter[str] = Counter()
    for row in rows:
        all_missing_fields.update(str(field) for field in _listify(row.get("missing_fields")))

    items: list[dict[str, Any]] = []
    for definition in SHADOW_MODE_VALIDATION_PLAN_DEFINITIONS:
        proof_keys = tuple(definition.get("proof_keys") or ())
        related_rows = [
            proof_rows[key]
            for key in proof_keys
            if isinstance(proof_rows.get(key), dict)
        ]
        passed = bool(related_rows) and all(bool(row.get("passed")) for row in related_rows)
        status = "no_records" if not rows and definition["key"] != "shadow_safety_governance" else "ready" if passed else "needs_evidence"
        missing_fields = list(definition.get("missing_fields") or ())
        if not missing_fields and not passed and all_missing_fields:
            missing_fields = [field for field, _count in all_missing_fields.most_common(8)]
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
                "missing_fields": missing_fields,
                "blocked_claims": list(definition.get("blocked_claims") or ()),
                "safe_next_action": safe_next_actions[0],
                "safe_next_actions": safe_next_actions,
                "done_when": definition["done_when"],
                "claim_boundary": "Human vs System validation is same-opportunity research review only; it is not proof of alpha, investor performance, live-trading readiness, or permission for AI/order/ranking authority.",
                "manual_review_only": True,
                "research_only": True,
                "changes_execution": False,
                "changes_order_submission": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
                "changes_ranking_weights": False,
                "can_submit_orders": False,
                "can_submit_live_orders": False,
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
                    "cautious_internal_shadow_review": proof_ready,
                    "system_beats_human_claim": False,
                    "human_override_quality_claim": False,
                    "public_alpha_claim": False,
                    "automatic_ranking_mutation": False,
                    "paper_to_live_readiness": False,
                    "live_trading_readiness": False,
                },
                "blocked_claims": [
                    "system_beats_human_claim",
                    "human_override_quality_claim",
                    "public_alpha_claim",
                    "repeatability_claim",
                    "paper_to_live_readiness",
                    "live_trading_readiness",
                ],
                "safe_boundary": "Human vs System validation records proof gaps and claim boundaries only. It does not authorize orders, broker-route changes, risk-gate changes, kill-switch changes, ranking-weight mutation, AI order authority, or live trading.",
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


def build_shadow_mode_report(
    *,
    records: Iterable[dict[str, Any]] | None = None,
    db: Any = None,
    current_user: Any = None,
    store_path: Path | str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    source_warnings: list[str] = []
    human_records = list(records) if records is not None else _read_human_records(store_path)
    system_records, warnings = _load_system_records(db, current_user)
    source_warnings.extend(warnings)
    rows = [
        build_shadow_comparison_row(record, system_records=system_records)
        for record in human_records
        if isinstance(record, dict) and not _is_simulation_evidence(record)
    ]
    aggregations = compute_shadow_analytics(rows)
    proof_summary = build_shadow_mode_proof_summary(rows, aggregations)
    validation_plan = build_shadow_mode_validation_plan(rows=rows, proof_summary=proof_summary)
    missing_counter: Counter[str] = Counter()
    for row in rows:
        missing_counter.update(row.get("missing_fields") or [])
    warnings = [*source_warnings]
    if missing_counter:
        warnings.append("Some shadow records are missing fields needed for complete human-vs-system scoring.")
    status = "ready" if rows else "empty"
    summary = {
        "status": status,
        "record_count": len(rows),
        "human_rewardable_count": sum(1 for row in rows if row.get("human_rewardable")),
        "system_rewardable_count": sum(1 for row in rows if row.get("system_rewardable")),
        "comparison_count": sum(1 for row in rows if row.get("human_reward") is not None and row.get("system_reward") is not None),
        "human_win_count": sum(1 for row in rows if row.get("winner") == "human"),
        "system_win_count": sum(1 for row in rows if row.get("winner") == "system"),
        "tie_count": sum(1 for row in rows if row.get("winner") == "tie"),
        "human_direction_accuracy": aggregations.get("human_direction_accuracy"),
        "system_direction_accuracy": aggregations.get("system_direction_accuracy"),
        "human_avg_reward": aggregations.get("human_avg_reward"),
        "system_avg_reward": aggregations.get("system_avg_reward"),
        "human_vs_system_edge": aggregations.get("human_vs_system_edge"),
        "shadow_proof_ready": proof_summary["proof_ready"],
        "shadow_proof_status": proof_summary["status"],
        "shadow_requirements_passed": proof_summary["summary"]["passed_requirement_count"],
        "shadow_requirements_total": proof_summary["summary"]["requirement_count"],
        "same_opportunity_coverage": proof_summary["summary"]["same_opportunity_coverage"],
        "human_contract_coverage": proof_summary["summary"]["human_contract_coverage"],
        "system_contract_coverage": proof_summary["summary"]["system_contract_coverage"],
        "outcome_coverage": proof_summary["summary"]["outcome_coverage"],
        "cost_risk_context_coverage": proof_summary["summary"]["cost_risk_context_coverage"],
        "pre_outcome_capture_coverage": proof_summary["summary"]["pre_outcome_capture_coverage"],
        "system_decision_quality_delta": proof_summary["summary"]["system_decision_quality_delta"],
        "shadow_validation_status": validation_plan["status"],
        "shadow_validation_open_items": validation_plan["summary"]["open_item_count"],
        "shadow_validation_critical_open_items": validation_plan["summary"]["critical_open_items"],
        "top_validation_item": validation_plan["summary"]["top_validation_item"],
        "claim_permissions": validation_plan["summary"]["claim_permissions"],
        **SAFETY_FLAGS,
    }
    aggregations["shadow_proof"] = proof_summary
    aggregations["shadow_validation_plan"] = validation_plan
    return serialize_value(
        {
            "status": status,
            "generated_at": generated_at or _utc_now(),
            "research_only": True,
            "summary": summary,
            "records": rows[:250],
            "comparisons": rows[:250],
            "proof_summary": proof_summary,
            "shadow_validation_plan": validation_plan,
            "aggregations": aggregations,
            "warnings": list(dict.fromkeys(warnings)),
            "missing_fields": dict(missing_counter),
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
            "finish_tracker": build_project_finish_tracker(report_name="shadow_mode"),
        }
    )


def _subset(report: dict[str, Any], *, records: list[dict[str, Any]], aggregations: dict[str, Any]) -> dict[str, Any]:
    return serialize_value({**report, "records": records, "aggregations": aggregations, "research_only": True, "safety_notes": list(SAFETY_NOTES), **SAFETY_FLAGS})


def get_shadow_mode_summary(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    return build_shadow_mode_report(db=db, current_user=current_user)


def get_shadow_mode_records(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_shadow_mode_report(db=db, current_user=current_user)
    return _subset(report, records=report.get("records", []), aggregations=report.get("aggregations", {}))


def get_shadow_mode_comparisons(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_shadow_mode_report(db=db, current_user=current_user)
    comparable = [row for row in report.get("records", []) if row.get("human_reward") is not None or row.get("system_reward") is not None]
    return _subset(report, records=comparable, aggregations={"comparison_count": len(comparable), **report.get("aggregations", {})})


def get_shadow_mode_bias(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_shadow_mode_report(db=db, current_user=current_user)
    bias = report.get("aggregations", {}).get("bias_diagnostics", {})
    return _subset(report, records=bias.get("items", []), aggregations={"bias_diagnostics": bias})
