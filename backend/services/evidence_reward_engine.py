from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from typing import Any, Iterable

import pandas as pd

from backend import stock_direction_model as sdm
from backend.services.candidate_outcome_stamping_service import (
    candidate_outcome_files,
    load_outcome_index,
    merge_outcome_into_candidate,
)
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
    "Does not change broker routes.",
    "Does not bypass risk gates.",
    "Does not place orders.",
    "Does not grant AI order authority.",
)

DEFAULT_ROOT = Path(".")
FALSE_BLOCK_RETURN_THRESHOLD_PCT = 0.25
REQUIRED_PREDICTION_FIELDS = (
    "prediction_created_at",
    "predicted_direction",
    "prediction_horizon_minutes",
    "predicted_target_pct",
    "invalidation_level",
    "confidence",
    "actual_forward_return",
    "baseline_forward_return",
)


@dataclass(frozen=True)
class PredictionContract:
    prediction_created_at: str | None
    predicted_direction: str | None
    prediction_horizon_minutes: int | None
    predicted_target_pct: float | None
    invalidation_level: Any
    confidence: float | None
    actual_forward_return: float | None
    baseline_forward_return: float | None
    actual_forward_return_observed_at: str | None
    max_adverse_excursion: float | None
    hit_target: bool | None
    hit_invalidation: bool | None
    time_to_target_minutes: float | None
    direction_correct: bool | None
    timing_correct: bool | None
    confidence_error: float | None
    slippage_bps: float | None
    spread_bps: float | None
    risk_gate_breach: bool
    status: str
    missing_prediction_fields: tuple[str, ...]
    not_rewarded_reason: str | None


@dataclass(frozen=True)
class RewardRecord:
    candidate_lifecycle_id: str | None
    symbol: str | None
    timestamp: str | None
    engine: str
    setup_type: str
    score: float | None
    score_bucket: str
    blockers: tuple[str, ...]
    ai_verdict: str | None
    route: str | None
    regime: str | None
    allowed: bool
    blocked: bool
    trade_executed: bool
    prediction_contract: dict[str, Any]
    prediction_contract_status: str
    rewardable: bool
    reward_components: dict[str, float]
    total_reward: float | None
    missing_fields: tuple[str, ...]
    missing_prediction_fields: tuple[str, ...]
    not_rewarded_reason: str | None
    paper_trade_outcome: dict[str, Any] | None
    source: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return float(parsed)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on", "hit", "passed"}:
            return True
        if cleaned in {"0", "false", "no", "off", "miss", "failed"}:
            return False
    return bool(value)


def _clean_text(value: Any, default: str | None = None) -> str | None:
    text = str(value or "").strip()
    return text or default


def _parse_datetime(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _score_bucket(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 90:
        return "90_100"
    if score >= 80:
        return "80_89"
    if score >= 60:
        return "60_79"
    if score >= 40:
        return "40_59"
    return "0_39"


def _confidence_bucket(count: int) -> str:
    if count >= 50:
        return "high"
    if count >= 20:
        return "medium"
    if count >= 5:
        return "low"
    return "insufficient"


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _dispersion(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return 0.0
    return round(float(pstdev(values)), 6)


def _latest_files(files: Iterable[Path], *, limit: int = 60) -> list[Path]:
    rows: list[tuple[float, Path]] = []
    for path in files:
        try:
            rows.append((path.stat().st_mtime, path))
        except OSError:
            continue
    return [path for _, path in sorted(rows, key=lambda item: item[0], reverse=True)[:limit]]


def _read_jsonl(path: Path, *, max_rows: int = 10000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if len(rows) >= max_rows:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except OSError:
        return []
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _count_lines(path: Path, *, max_lines: int | None = None) -> int:
    try:
        with path.open("r", encoding="utf-8") as handle:
            count = 0
            for count, _ in enumerate(handle, start=1):
                if max_lines is not None and count >= max_lines:
                    return count
            return count
    except OSError:
        return 0


def _candidate_files(root: Path, tenant_slug: str) -> list[Path]:
    base = root / "runtime-exports" / "candidate-lifecycle"
    if not base.exists():
        return []
    files = list(base.glob(f"*/{tenant_slug}.jsonl"))
    if not files:
        files = list(base.glob("*/candidate-diagnostics.jsonl"))
    return _latest_files(files)


def _accelerator_files(root: Path, tenant_slug: str) -> list[Path]:
    base = root / "runtime-exports" / "evidence-accelerator"
    if not base.exists():
        return []
    return _latest_files(base.glob(f"*/{tenant_slug}.jsonl"), limit=20)


def _simulation_files(root: Path, tenant_slug: str) -> list[Path]:
    base = root / "runtime-exports" / "simulation-evidence"
    if not base.exists():
        return []
    return _latest_files(base.glob(f"*/{tenant_slug}.jsonl"), limit=20)


def _market_day_reports(root: Path) -> list[dict[str, Any]]:
    base = root / "runtime-exports" / "market-days"
    if not base.exists():
        return []
    return [_read_json(path) for path in _latest_files(base.glob("*/market-day-report.json"), limit=30)]


def _tenant_slug_from_user(current_user: Any) -> str:
    return (
        _clean_text(getattr(current_user, "tenant_slug", None))
        or _clean_text(getattr(current_user, "slug", None))
        or _clean_text(getattr(current_user, "tenant_id", None))
        or "systematic-equities"
    )


def _extract_blockers(payload: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    raw = payload.get("blockers")
    if isinstance(raw, list):
        values.extend(str(item).strip() for item in raw if str(item or "").strip())
    blocker = _clean_text(payload.get("blocker") or payload.get("diagnostic_blocker") or payload.get("reason"))
    if blocker and blocker.lower() not in {"none", "eligible", "allowed"}:
        values.append(blocker)
    return tuple(dict.fromkeys(values))


def _extract_score(payload: dict[str, Any]) -> float | None:
    for key in ("opportunity_score", "stage_one_score", "deep_score", "ranking_score", "setup_score", "score", "max_score"):
        parsed = _safe_float(payload.get(key))
        if parsed is not None:
            return parsed
    nested = payload.get("opportunity_capture")
    if isinstance(nested, dict):
        return _safe_float(nested.get("score"))
    return None


def _direction_sign(direction: str | None) -> int | None:
    cleaned = str(direction or "").strip().lower()
    if cleaned in {"bullish", "long", "buy", "up", "higher"}:
        return 1
    if cleaned in {"bearish", "short", "sell", "down", "lower"}:
        return -1
    return None


def compute_forward_return_score(actual_forward_return: Any, predicted_direction: str | None = None) -> float:
    """Return direction-adjusted forward return in percentage-point units."""
    actual = _safe_float(actual_forward_return)
    if actual is None:
        return 0.0
    sign = _direction_sign(predicted_direction) or 1
    return round(actual * sign, 6)


def compute_baseline_relative_score(
    actual_forward_return: Any,
    baseline_forward_return: Any,
    predicted_direction: str | None = None,
) -> float:
    """Return direction-adjusted excess return over the explicit baseline."""
    actual = _safe_float(actual_forward_return)
    baseline = _safe_float(baseline_forward_return)
    if actual is None or baseline is None:
        return 0.0
    sign = _direction_sign(predicted_direction) or 1
    return round((actual - baseline) * sign, 6)


def compute_drawdown_penalty(max_adverse_excursion_pct: Any) -> float:
    """Penalize adverse movement before the outcome; missing data is explicit elsewhere."""
    adverse = _safe_float(max_adverse_excursion_pct)
    if adverse is None:
        return 0.0
    return round(abs(adverse) * 0.50, 6)


def compute_slippage_penalty(slippage_bps: Any) -> float:
    """Convert slippage basis points into percentage-point style reward units."""
    slippage = _safe_float(slippage_bps)
    if slippage is None:
        return 0.0
    return round(abs(slippage) / 100.0, 6)


def compute_spread_penalty(spread_bps: Any) -> float:
    """Penalize only the spread above a 20 bps tolerance."""
    spread = _safe_float(spread_bps)
    if spread is None:
        return 0.0
    return round(max(0.0, spread - 20.0) / 100.0, 6)


def compute_risk_violation_penalty(risk_gate_breach: Any) -> float:
    """Apply a fixed penalty for any recorded risk gate breach."""
    return 1.0 if _safe_bool(risk_gate_breach) else 0.0


def compute_blocker_correctness_bonus(
    *,
    blocked: bool,
    actual_forward_return: Any,
    predicted_direction: str | None = None,
) -> float:
    """Reward a blocker only when it stopped a direction-adjusted loser."""
    if not blocked:
        return 0.0
    return 0.25 if compute_forward_return_score(actual_forward_return, predicted_direction) < 0 else 0.0


def compute_missed_move_penalty(
    *,
    blocked: bool,
    actual_forward_return: Any,
    predicted_direction: str | None = None,
) -> float:
    """Penalize a blocker when it stopped a direction-adjusted move of at least 0.25%."""
    if not blocked:
        return 0.0
    return 0.50 if compute_forward_return_score(actual_forward_return, predicted_direction) >= FALSE_BLOCK_RETURN_THRESHOLD_PCT else 0.0


def compute_total_reward(component_scores: dict[str, Any]) -> float:
    """Compose the v1 transparent research reward; it never mutates execution behavior."""
    reward = (
        float(component_scores.get("forward_return_score") or 0.0)
        + float(component_scores.get("baseline_relative_score") or 0.0)
        - float(component_scores.get("drawdown_penalty") or 0.0)
        - float(component_scores.get("slippage_penalty") or 0.0)
        - float(component_scores.get("spread_penalty") or 0.0)
        - float(component_scores.get("risk_violation_penalty") or 0.0)
        + float(component_scores.get("blocker_correctness_bonus") or 0.0)
        - float(component_scores.get("missed_move_penalty") or 0.0)
    )
    return round(reward, 6)


def _normalize_confidence(value: Any) -> float | None:
    parsed = _safe_float(value)
    if parsed is None:
        return None
    if parsed > 1.0:
        parsed = parsed / 100.0
    return round(max(0.0, min(parsed, 1.0)), 6)


def _first_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_float(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        parsed = _safe_float(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _paper_trade_return(row: dict[str, Any]) -> tuple[dict[str, Any] | None, float | None]:
    realized = _safe_float(row.get("realized_pnl") or row.get("pnl"))
    notional = _safe_float(row.get("position_cost") or row.get("broker_notional") or row.get("expected_notional"))
    entry = _safe_float(row.get("live_price_at_open") or row.get("actual_fill_price") or row.get("broker_filled_avg_price"))
    close = _safe_float(row.get("live_price_at_close") or row.get("close_price"))
    return_pct = None
    if realized is not None and notional not in (None, 0):
        return_pct = round((realized / abs(float(notional))) * 100.0, 6)
    elif entry not in (None, 0) and close is not None:
        return_pct = round(((float(close) - float(entry)) / float(entry)) * 100.0, 6)
    if realized is None and return_pct is None:
        return None, None
    return {"realized_pnl": realized, "return_pct": return_pct, "status": row.get("status") or row.get("broker_status")}, return_pct


def _index_trade_rows(frames: Iterable[pd.DataFrame]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    by_candidate: dict[str, dict[str, Any]] = {}
    all_rows: list[dict[str, Any]] = []
    for frame in frames:
        if frame is None or frame.empty:
            continue
        try:
            records = frame.to_dict(orient="records")
        except Exception:
            continue
        for row in records:
            if not isinstance(row, dict):
                continue
            all_rows.append(row)
            for key in ("candidate_lifecycle_id", "automation_candidate_id", "signal_id"):
                value = _clean_text(row.get(key))
                if value:
                    by_candidate[value] = row
    return by_candidate, all_rows


def _extract_prediction_contract(payload: dict[str, Any], paper_return: float | None = None) -> PredictionContract:
    created_at = _clean_text(_first_value(payload, "prediction_created_at", "prediction_timestamp", "scan_time", "observed_at"))
    direction = _clean_text(_first_value(payload, "predicted_direction", "accuracy_forecast_direction", "forecast_direction", "direction"))
    horizon = _safe_float(_first_value(payload, "prediction_horizon_minutes", "horizon_minutes", "forecast_horizon_minutes"))
    target_pct = _first_float(payload, "predicted_target_pct", "target_return_pct", "target_pct", "expected_move_pct")
    invalidation = _first_value(payload, "invalidation_level", "invalidation_price", "invalid_if")
    confidence = _normalize_confidence(_first_value(payload, "confidence", "ai_confidence", "forecast_confidence", "accuracy_forecast_confidence"))
    actual = _first_float(payload, "actual_forward_return", "actual_forward_return_pct", "forward_return_30m_pct", "return_30m_pct")
    if actual is None:
        actual = _first_float(payload, "forward_return_15m_pct", "return_15m_pct", "forward_return_5m_pct", "return_5m_pct")
    if actual is None:
        actual = _first_float(payload, "missed_move_pct", "current_move_pct")
    if actual is None:
        actual = paper_return
    baseline = _first_float(payload, "baseline_forward_return", "baseline_forward_return_pct", "benchmark_forward_return_pct")
    observed_at = _clean_text(_first_value(payload, "actual_forward_return_observed_at", "outcome_observed_at", "measured_at", "follow_up_measured_at"))
    max_adverse = _first_float(payload, "max_adverse_excursion", "max_adverse_excursion_pct", "mae_pct", "adverse_move_pct")
    slippage_bps = _first_float(payload, "slippage_bps", "slippage_estimate_bps", "estimated_slippage_bps")
    if slippage_bps is None:
        slippage_pct = _first_float(payload, "slippage", "slippage_pct", "slippage_estimate")
        slippage_bps = slippage_pct * 100.0 if slippage_pct is not None else None
    spread_bps = _first_float(payload, "spread_bps", "spread_basis_points", "quoted_spread_bps")
    risk_gate_breach = _safe_bool(
        _first_value(payload, "risk_gate_breach", "risk_violation", "risk_gate_failed", "risk_breach")
    )
    hit_target_raw = _first_value(payload, "hit_target", "target_reached")
    hit_invalidation_raw = _first_value(payload, "hit_invalidation", "invalidation_hit")
    time_to_target = _first_float(payload, "time_to_target", "time_to_target_minutes", "minutes_to_target")

    missing: list[str] = []
    fields = {
        "prediction_created_at": created_at,
        "predicted_direction": direction,
        "prediction_horizon_minutes": horizon,
        "predicted_target_pct": target_pct,
        "invalidation_level": invalidation,
        "confidence": confidence,
        "actual_forward_return": actual,
        "baseline_forward_return": baseline,
    }
    for key, value in fields.items():
        if value in (None, ""):
            missing.append(key)
    if direction and _direction_sign(direction) is None:
        missing.append("predicted_direction")
    created = _parse_datetime(created_at)
    observed = _parse_datetime(observed_at)
    post_move = bool(created and observed and created > observed)
    if post_move:
        status = "post_move"
        reason = "prediction_created_after_outcome_observation"
    elif missing == ["baseline_forward_return"] or ("baseline_forward_return" in missing and len(missing) == 1):
        status = "baseline_missing"
        reason = "baseline_forward_return_missing"
    elif missing:
        status = "incomplete"
        reason = "missing_prediction_fields"
    else:
        status = "rewardable"
        reason = None

    sign = _direction_sign(direction)
    signed_actual = actual * sign if actual is not None and sign is not None else None
    target_distance = abs(float(target_pct)) if target_pct is not None else None
    direction_correct = signed_actual is not None and signed_actual > 0
    hit_target = _safe_bool(hit_target_raw) if hit_target_raw is not None else (
        bool(target_distance is not None and signed_actual is not None and signed_actual >= target_distance)
    )
    hit_invalidation = _safe_bool(hit_invalidation_raw) if hit_invalidation_raw is not None else False
    timing_correct = bool(hit_target and horizon is not None and time_to_target is not None and time_to_target <= horizon)
    confidence_error = round(abs(float(confidence) - (1.0 if direction_correct else 0.0)), 6) if confidence is not None and direction_correct is not None else None

    return PredictionContract(
        prediction_created_at=created_at,
        predicted_direction=direction,
        prediction_horizon_minutes=int(horizon) if horizon is not None else None,
        predicted_target_pct=target_pct,
        invalidation_level=invalidation,
        confidence=confidence,
        actual_forward_return=actual,
        baseline_forward_return=baseline,
        actual_forward_return_observed_at=observed_at,
        max_adverse_excursion=max_adverse,
        hit_target=hit_target,
        hit_invalidation=hit_invalidation,
        time_to_target_minutes=time_to_target,
        direction_correct=direction_correct if sign is not None and actual is not None else None,
        timing_correct=timing_correct,
        confidence_error=confidence_error,
        slippage_bps=slippage_bps,
        spread_bps=spread_bps,
        risk_gate_breach=risk_gate_breach,
        status=status,
        missing_prediction_fields=tuple(dict.fromkeys(missing)),
        not_rewarded_reason=reason,
    )


def _prediction_reward_components(contract: PredictionContract, *, blocked: bool = False) -> tuple[dict[str, float], float | None]:
    base_components = {
        "forward_return_score": 0.0,
        "baseline_relative_score": 0.0,
        "drawdown_penalty": 0.0,
        "slippage_penalty": 0.0,
        "spread_penalty": 0.0,
        "risk_violation_penalty": 0.0,
        "blocker_correctness_bonus": 0.0,
        "missed_move_penalty": 0.0,
    }
    if contract.status != "rewardable":
        return {
            **base_components,
            "baseline_adjusted_return_score": 0.0,
            "direction_correct_bonus": 0.0,
            "target_hit_bonus": 0.0,
            "low_adverse_excursion_bonus": 0.0,
            "timing_bonus": 0.0,
            "confidence_calibration_bonus": 0.0,
            "drawdown_penalty": 0.0,
            "late_timing_penalty": 0.0,
            "high_confidence_wrong_penalty": 0.0,
            "target_missed_penalty": 0.0,
            "invalidation_hit_penalty": 0.0,
            "baseline_underperformance_penalty": 0.0,
        }, None
    sign = _direction_sign(contract.predicted_direction) or 1
    actual = float(contract.actual_forward_return or 0.0)
    baseline = float(contract.baseline_forward_return or 0.0)
    signed_actual = actual * sign
    signed_baseline = baseline * sign
    baseline_adjusted = signed_actual - signed_baseline
    confidence_error = float(contract.confidence_error or 0.0)
    adverse = abs(float(contract.max_adverse_excursion or 0.0))
    direction_correct = bool(contract.direction_correct)
    hit_target = bool(contract.hit_target)
    timing_correct = bool(contract.timing_correct)
    high_conf_wrong = max(0.0, float(contract.confidence or 0.0) - 0.70) * 2.0 if not direction_correct else 0.0
    late = direction_correct and (not timing_correct)
    required_components = {
        "forward_return_score": compute_forward_return_score(actual, contract.predicted_direction),
        "baseline_relative_score": compute_baseline_relative_score(actual, baseline, contract.predicted_direction),
        "drawdown_penalty": compute_drawdown_penalty(contract.max_adverse_excursion),
        "slippage_penalty": compute_slippage_penalty(contract.slippage_bps),
        "spread_penalty": compute_spread_penalty(contract.spread_bps),
        "risk_violation_penalty": compute_risk_violation_penalty(contract.risk_gate_breach),
        "blocker_correctness_bonus": compute_blocker_correctness_bonus(
            blocked=blocked,
            actual_forward_return=actual,
            predicted_direction=contract.predicted_direction,
        ),
        "missed_move_penalty": compute_missed_move_penalty(
            blocked=blocked,
            actual_forward_return=actual,
            predicted_direction=contract.predicted_direction,
        ),
    }
    components = {
        **required_components,
        "baseline_adjusted_return_score": round(baseline_adjusted, 6),
        "direction_correct_bonus": 0.5 if direction_correct else -0.5,
        "target_hit_bonus": 0.75 if hit_target else 0.0,
        "low_adverse_excursion_bonus": round(max(0.0, 0.25 - min(adverse, 2.5) * 0.1), 6),
        "timing_bonus": 0.4 if timing_correct else 0.0,
        "confidence_calibration_bonus": round(max(0.0, 0.35 - confidence_error) * 0.5, 6),
        "drawdown_penalty": round(adverse * 0.5, 6),
        "late_timing_penalty": 0.35 if late else 0.0,
        "high_confidence_wrong_penalty": round(high_conf_wrong, 6),
        "target_missed_penalty": 0.4 if not hit_target else 0.0,
        "invalidation_hit_penalty": 0.75 if contract.hit_invalidation else 0.0,
        "baseline_underperformance_penalty": round(abs(min(0.0, baseline_adjusted)), 6),
    }
    reward = compute_total_reward(required_components)
    return components, round(reward, 6)


def _normalize_payload(payload: dict[str, Any], trade_by_candidate: dict[str, dict[str, Any]]) -> RewardRecord | None:
    if _safe_bool(payload.get("simulation_evidence")) or str(payload.get("source") or "").lower() == "simulation_evidence":
        return None
    symbol = _clean_text(payload.get("ticker") or payload.get("symbol"))
    if symbol:
        symbol = symbol.upper()
    if not symbol or symbol in {"API_DETAIL", "UNKNOWN"}:
        return None
    lifecycle_id = _clean_text(payload.get("candidate_lifecycle_id") or payload.get("automation_candidate_id"))
    trade_row = trade_by_candidate.get(lifecycle_id or "")
    paper_outcome = None
    paper_return = None
    if trade_row:
        paper_outcome, paper_return = _paper_trade_return(trade_row)
    contract = _extract_prediction_contract(payload, paper_return)
    final_state = str(payload.get("final_state") or payload.get("status") or "").strip().lower()
    blockers = _extract_blockers(payload)
    allowed = bool(final_state == "eligible" or _safe_bool(payload.get("allowed")) or _safe_bool(payload.get("eligible")))
    blocked = bool(blockers or final_state in {"rejected_or_waiting", "blocked", "rejected", "waiting"})
    if allowed:
        blocked = False
    components, total_reward = _prediction_reward_components(contract, blocked=blocked)
    score = _extract_score(payload)
    missing_fields = list(contract.missing_prediction_fields)
    if score is None:
        missing_fields.append("score")
    regime = _clean_text(payload.get("regime") or payload.get("market_regime") or payload.get("regime_state"))
    if not regime:
        missing_fields.append("regime")
    return RewardRecord(
        candidate_lifecycle_id=lifecycle_id,
        symbol=symbol,
        timestamp=_clean_text(payload.get("scan_time") or payload.get("timestamp") or payload.get("observed_at")),
        engine=_clean_text(payload.get("desk_key") or payload.get("engine") or payload.get("strategy_desk_key"), "unknown") or "unknown",
        setup_type=_clean_text(payload.get("opportunity_type") or payload.get("setup_type") or payload.get("stage"), "unknown") or "unknown",
        score=score,
        score_bucket=_score_bucket(score),
        blockers=blockers,
        ai_verdict=_clean_text(payload.get("ai_verdict") or payload.get("ai_evidence_verdict")),
        route=_clean_text(payload.get("route") or payload.get("execution_route") or payload.get("automation_execution_intent")),
        regime=regime,
        allowed=allowed,
        blocked=blocked,
        trade_executed=bool(trade_row or _safe_bool(payload.get("trade_executed")) or _safe_bool(payload.get("order_submitted"))),
        prediction_contract=serialize_value(contract.__dict__),
        prediction_contract_status=contract.status,
        rewardable=contract.status == "rewardable",
        reward_components=components,
        total_reward=total_reward,
        missing_fields=tuple(dict.fromkeys(missing_fields)),
        missing_prediction_fields=contract.missing_prediction_fields,
        not_rewarded_reason=contract.not_rewarded_reason,
        paper_trade_outcome=paper_outcome,
        source=_clean_text(payload.get("source"), "candidate_lifecycle") or "candidate_lifecycle",
    )


def _trade_row_to_record(row: dict[str, Any]) -> RewardRecord | None:
    symbol = _clean_text(row.get("ticker") or row.get("symbol"))
    if not symbol:
        return None
    outcome, paper_return = _paper_trade_return(row)
    payload = dict(row)
    if paper_return is not None and "actual_forward_return" not in payload:
        payload["actual_forward_return"] = paper_return
    record = _normalize_payload(payload, {})
    if record is None:
        return None
    return RewardRecord(
        **{
            **record.__dict__,
            "paper_trade_outcome": outcome,
            "trade_executed": True,
            "source": "paper_trade_book",
        }
    )


def _market_day_missed_records(reports: list[dict[str, Any]], trade_by_candidate: dict[str, dict[str, Any]]) -> list[RewardRecord]:
    records: list[RewardRecord] = []
    for report in reports:
        containers = [
            report.get("missed_move_leaderboard"),
            (report.get("no_trade_report") or {}).get("missed_move_leaderboard")
            if isinstance(report.get("no_trade_report"), dict)
            else None,
        ]
        for container in containers:
            if not isinstance(container, dict):
                continue
            for item in list(container.get("items") or []):
                if not isinstance(item, dict):
                    continue
                payload = {
                    **item,
                    "ticker": item.get("ticker") or item.get("symbol"),
                    "blocker": item.get("blocker"),
                    "setup_type": item.get("setup_type"),
                    "opportunity_score": item.get("max_score"),
                    "final_state": "rejected_or_waiting",
                    "source": "market_day_missed_move",
                }
                record = _normalize_payload(payload, trade_by_candidate)
                if record is not None:
                    records.append(record)
    return records


def _rewardable(records: list[RewardRecord]) -> list[RewardRecord]:
    return [record for record in records if record.rewardable and record.total_reward is not None]


def _reward_distribution(records: list[RewardRecord]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in _rewardable(records):
        reward = float(record.total_reward or 0.0)
        if reward >= 1.0:
            bucket = "strong_positive"
        elif reward >= 0.25:
            bucket = "positive"
        elif reward > -0.25:
            bucket = "neutral"
        elif reward > -1.0:
            bucket = "negative"
        else:
            bucket = "strong_negative"
        counts[bucket] += 1
    for bucket in ("strong_positive", "positive", "neutral", "negative", "strong_negative"):
        counts.setdefault(bucket, 0)
    return dict(counts)


def _stats(records: list[RewardRecord], *, key: str, label: str) -> dict[str, Any]:
    rows = _rewardable(records)
    rewards = [float(row.total_reward or 0.0) for row in rows]
    wins = [reward for reward in rewards if reward > 0]
    return {
        key: label,
        "candidate_count": len(records),
        "rewardable_candidate_count": len(rows),
        "avg_reward": _average(rewards),
        "win_rate": round(len(wins) / len(rewards), 6) if rewards else None,
        "best_reward": round(max(rewards), 6) if rewards else None,
        "worst_reward": round(min(rewards), 6) if rewards else None,
        "dispersion": _dispersion(rewards),
        "confidence_bucket": _confidence_bucket(len(rows)),
        "data_status": "ready" if rows else "insufficient_data",
    }


def _group_stats(records: list[RewardRecord], attr: str, key_name: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[RewardRecord]] = defaultdict(list)
    for row in records:
        grouped[str(getattr(row, attr) or "unknown")].append(row)
    rows = [_stats(items, key=key_name, label=label) for label, items in grouped.items()]
    return sorted(rows, key=lambda item: (item["avg_reward"] is None, -(item["avg_reward"] or -9999), -item["candidate_count"]))


def _score_bucket_stats(records: list[RewardRecord]) -> list[dict[str, Any]]:
    order = {"90_100": 0, "80_89": 1, "60_79": 2, "40_59": 3, "0_39": 4, "unknown": 5}
    return sorted(_group_stats(records, "score_bucket", "score_bucket"), key=lambda item: order.get(item["score_bucket"], 99))


def _blocker_stats(records: list[RewardRecord]) -> list[dict[str, Any]]:
    grouped: dict[str, list[RewardRecord]] = defaultdict(list)
    for row in records:
        for blocker in row.blockers:
            grouped[blocker].append(row)
    rows: list[dict[str, Any]] = []
    for blocker, items in grouped.items():
        rewardable = _rewardable(items)
        rewards = [float(row.total_reward or 0.0) for row in rewardable]
        false_blocks = [
            row for row in rewardable
            if row.blocked and (row.prediction_contract.get("actual_forward_return") or 0) >= FALSE_BLOCK_RETURN_THRESHOLD_PCT
        ]
        rows.append(
            {
                "blocker": blocker,
                "times_seen": len(items),
                "rewardable_candidate_count": len(rewardable),
                "avg_reward_when_blocked": _average(rewards),
                "false_block_rate": round(len(false_blocks) / len(rewardable), 6) if rewardable else None,
                "false_block_count": len(false_blocks),
                "blocker_value_score": round(-(_average(rewards) or 0.0), 6) if rewards else None,
                "confidence_bucket": _confidence_bucket(len(rewardable)),
            }
        )
    return sorted(rows, key=lambda item: (item["blocker_value_score"] is None, -(item["blocker_value_score"] or -9999)))


def _setup_stats(records: list[RewardRecord]) -> list[dict[str, Any]]:
    rows = _group_stats(records, "setup_type", "setup_type")
    for row in rows:
        win_rate = row.get("win_rate") or 0.0
        dispersion = row.get("dispersion") or 0.0
        row["consistency_score"] = round(max(0.0, 1.0 - min(dispersion, 2.0) / 2.0) * win_rate, 6)
    return rows


def _engine_stats(records: list[RewardRecord]) -> list[dict[str, Any]]:
    rows = _group_stats(records, "engine", "engine")
    for row in rows:
        row["reward_by_regime"] = _group_stats([record for record in records if record.engine == row["engine"]], "regime", "regime")
    return rows


def _ai_stats(records: list[RewardRecord]) -> dict[str, Any]:
    with_verdict = [row for row in records if row.ai_verdict]
    by_verdict = _group_stats(with_verdict, "ai_verdict", "ai_verdict") if with_verdict else []
    approvals = _rewardable([row for row in with_verdict if row.ai_verdict == "approve_evidence"])
    rejects = _rewardable([row for row in with_verdict if row.ai_verdict in {"reject_evidence", "wait_for_confirmation", "size_down"}])
    false_positives = [row for row in approvals if (row.total_reward or 0.0) <= -FALSE_BLOCK_RETURN_THRESHOLD_PCT]
    false_negatives = [row for row in rejects if row.prediction_contract.get("direction_correct") is True]
    return {
        "verdict_count": len(with_verdict),
        "rewardable_verdict_count": len(_rewardable(with_verdict)),
        "approve_reward": _average([float(row.total_reward or 0.0) for row in approvals]),
        "reject_reward": _average([float(row.total_reward or 0.0) for row in rejects]),
        "false_positive_rate": round(len(false_positives) / len(approvals), 6) if approvals else None,
        "false_negative_rate": round(len(false_negatives) / len(rejects), 6) if rejects else None,
        "items": by_verdict,
        **SAFETY_FLAGS,
    }


def _safe_recommendations(
    *,
    blockers: list[dict[str, Any]],
    setups: list[dict[str, Any]],
    engines: list[dict[str, Any]],
    ai: dict[str, Any],
    score_buckets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for blocker in blockers:
        confidence = blocker.get("confidence_bucket")
        value = blocker.get("blocker_value_score")
        false_rate = blocker.get("false_block_rate")
        if confidence in {"medium", "high"} and value is not None and value > 0:
            recommendations.append(
                {
                    "type": "keep_blocker_strict",
                    "target": blocker.get("blocker"),
                    "reason": "Blocked candidates showed negative reward after the block.",
                    "manual_review_only": True,
                }
            )
        elif confidence in {"medium", "high"} and false_rate is not None and false_rate >= 0.35:
            recommendations.append(
                {
                    "type": "review_blocker",
                    "target": blocker.get("blocker"),
                    "reason": "Blocker stopped many rewardable forward winners.",
                    "manual_review_only": True,
                }
            )
    for setup in setups:
        avg = setup.get("avg_reward")
        if setup.get("confidence_bucket") in {"medium", "high"} and avg is not None:
            recommendations.append(
                {
                    "type": "increase_rank_weight" if avg > 0.5 else "decrease_rank_weight" if avg < -0.5 else "validate_score_bucket_separation",
                    "target": setup.get("setup_type"),
                    "reason": "Research-only setup reward review; no automatic ranking changes are made.",
                    "manual_review_only": True,
                }
            )
    for engine in engines:
        avg = engine.get("avg_reward")
        if engine.get("confidence_bucket") in {"medium", "high"} and avg is not None and avg < -0.5:
            recommendations.append(
                {
                    "type": "reduce_confidence",
                    "target": engine.get("engine"),
                    "reason": "Engine reward is negative in rewardable prediction contracts.",
                    "manual_review_only": True,
                }
            )
    if ai.get("false_negative_rate") is not None and ai["false_negative_rate"] >= 0.25:
        recommendations.append(
            {
                "type": "investigate_ai_reject_false_negatives",
                "target": "ai_referee",
                "reason": "AI reject/wait verdicts later aligned with positive prediction outcomes.",
                "manual_review_only": True,
            }
        )
    if not recommendations and not any(bucket.get("rewardable_candidate_count") for bucket in score_buckets):
        recommendations.append(
            {
                "type": "insufficient_data",
                "target": "prediction_contracts",
                "reason": "Emit more timestamped prediction contracts before changing ranking logic.",
                "manual_review_only": True,
            }
        )
    return recommendations[:12]


def _source_counts(
    *,
    candidate_files: list[Path],
    outcome_files: list[Path],
    accelerator_files: list[Path],
    simulation_files: list[Path],
    market_day_reports: list[dict[str, Any]],
    open_trades: pd.DataFrame,
    closed_trades: pd.DataFrame,
    pending_orders: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "candidate_lifecycle_files": len(candidate_files),
        "candidate_lifecycle_rows": sum(_count_lines(path) for path in candidate_files),
        "candidate_outcome_files": len(outcome_files),
        "candidate_outcome_rows": sum(_count_lines(path, max_lines=250000) for path in outcome_files),
        "evidence_accelerator_files": len(accelerator_files),
        "evidence_accelerator_rows": sum(_count_lines(path, max_lines=250000) for path in accelerator_files),
        "simulation_evidence_files": len(simulation_files),
        "simulation_evidence_rows_excluded": sum(_count_lines(path, max_lines=250000) for path in simulation_files),
        "market_day_reports": len(market_day_reports),
        "open_trade_rows": 0 if open_trades is None or open_trades.empty else int(len(open_trades)),
        "closed_trade_rows": 0 if closed_trades is None or closed_trades.empty else int(len(closed_trades)),
        "pending_order_rows": 0 if pending_orders is None or pending_orders.empty else int(len(pending_orders)),
    }


def _record_interpretation(record: RewardRecord) -> str:
    if not record.rewardable:
        return "Not rewarded; required prediction contract or forward outcome evidence is incomplete."
    reward = float(record.total_reward or 0.0)
    if reward >= 0.25:
        return "Positive research reward versus the explicit contract and baseline."
    if reward <= -0.25:
        return "Negative research reward; review prediction quality before changing any policy."
    return "Neutral research reward."


def _record_warnings(record: RewardRecord) -> list[str]:
    warnings: list[str] = []
    if not record.rewardable:
        warnings.append("Record is visible but excluded from reward averages.")
    if record.missing_prediction_fields:
        warnings.append("Missing required prediction contract fields.")
    if record.prediction_contract_status == "post_move":
        warnings.append("Prediction timestamp came after the measured outcome.")
    if record.prediction_contract_status == "baseline_missing":
        warnings.append("Baseline outcome is missing, so reward is not computed.")
    return warnings


def _record_to_row(record: RewardRecord) -> dict[str, Any]:
    row = serialize_value(record.__dict__)
    row["record_id"] = row.get("candidate_lifecycle_id")
    row["prediction_created_at"] = (row.get("prediction_contract") or {}).get("prediction_created_at")
    row["component_scores"] = row.get("reward_components") or {}
    row["reason"] = row.get("not_rewarded_reason") or ("rewardable_prediction_contract" if row.get("rewardable") else "not_rewardable")
    row["warnings"] = _record_warnings(record)
    row["interpretation"] = _record_interpretation(record)
    return row


def _records_to_rows(records: list[RewardRecord]) -> list[dict[str, Any]]:
    return [_record_to_row(record) for record in records]


def build_evidence_reward_report(
    *,
    tenant_slug: str,
    root: Path | str = DEFAULT_ROOT,
    open_trades: pd.DataFrame | None = None,
    closed_trades: pd.DataFrame | None = None,
    pending_orders: pd.DataFrame | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    candidate_files = _candidate_files(root_path, tenant_slug)
    outcome_files = candidate_outcome_files(root_path, tenant_slug)
    accelerator_files = _accelerator_files(root_path, tenant_slug)
    simulation_files = _simulation_files(root_path, tenant_slug)
    market_day_reports = _market_day_reports(root_path)
    open_frame = open_trades if open_trades is not None else sdm.read_open_trades()
    closed_frame = closed_trades if closed_trades is not None else sdm.read_closed_trades()
    pending_frame = pending_orders if pending_orders is not None else sdm.read_pending_orders()
    trade_by_candidate, trade_rows = _index_trade_rows([open_frame, closed_frame, pending_frame])
    outcomes_by_candidate = load_outcome_index(root_path, tenant_slug)

    records: list[RewardRecord] = []
    seen_keys: set[str] = set()
    for path in candidate_files:
        for payload in _read_jsonl(path, max_rows=10000):
            lifecycle_id = _clean_text(payload.get("candidate_lifecycle_id") or payload.get("automation_candidate_id"))
            enriched_payload = merge_outcome_into_candidate(payload, outcomes_by_candidate.get(lifecycle_id or ""))
            record = _normalize_payload(enriched_payload, trade_by_candidate)
            if record is None:
                continue
            key = record.candidate_lifecycle_id or f"{record.symbol}:{record.timestamp}:{record.engine}:{record.setup_type}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            records.append(record)
    records.extend(_market_day_missed_records(market_day_reports, trade_by_candidate))
    for row in trade_rows:
        candidate_id = _clean_text(row.get("candidate_lifecycle_id") or row.get("automation_candidate_id"))
        if candidate_id and candidate_id in seen_keys:
            continue
        record = _trade_row_to_record(row)
        if record is not None:
            records.append(record)

    rewardable_records = _rewardable(records)
    missing_counter: Counter[str] = Counter()
    missing_prediction_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    for row in records:
        missing_counter.update(row.missing_fields)
        missing_prediction_counter.update(row.missing_prediction_fields)
        status_counter[row.prediction_contract_status] += 1
    rewards = [float(row.total_reward or 0.0) for row in rewardable_records]
    blockers = _blocker_stats(records)
    setups = _setup_stats(records)
    engines = _engine_stats(records)
    regimes = _group_stats(records, "regime", "regime")
    score_buckets = _score_bucket_stats(records)
    ai = _ai_stats(records)
    prediction_contract_summary = {
        "rewardable": status_counter.get("rewardable", 0),
        "incomplete": status_counter.get("incomplete", 0),
        "baseline_missing": status_counter.get("baseline_missing", 0),
        "post_move": status_counter.get("post_move", 0),
        "missing_prediction_fields": dict(missing_prediction_counter),
    }
    non_rewardable_count = len(records) - len(rewardable_records)
    excluded_count = status_counter.get("baseline_missing", 0) + status_counter.get("post_move", 0)
    summary = {
        "tenant_slug": tenant_slug,
        "generated_at": _utc_now(),
        "candidate_count": len(records),
        "rewardable_count": len(rewardable_records),
        "non_rewardable_count": non_rewardable_count,
        "excluded_count": excluded_count,
        "rewardable_candidate_count": len(rewardable_records),
        "non_rewardable_candidate_count": non_rewardable_count,
        "incomplete_prediction_count": status_counter.get("incomplete", 0),
        "baseline_missing_count": status_counter.get("baseline_missing", 0),
        "post_move_excluded_count": status_counter.get("post_move", 0),
        "allowed_count": sum(1 for row in records if row.allowed),
        "blocked_count": sum(1 for row in records if row.blocked),
        "trade_executed_count": sum(1 for row in records if row.trade_executed),
        "missed_move_count": sum(1 for row in rewardable_records if row.reward_components.get("target_missed_penalty", 0.0) > 0),
        "avg_reward": _average(rewards),
        "reward_distribution": _reward_distribution(records),
        "reward_by_score_bucket": score_buckets,
        "prediction_contract_summary": prediction_contract_summary,
        "missing_fields": dict(missing_counter),
        "missing_field_counts": dict(missing_counter),
        "missing_prediction_field_counts": dict(missing_prediction_counter),
        "source_counts": _source_counts(
            candidate_files=candidate_files,
            outcome_files=outcome_files,
            accelerator_files=accelerator_files,
            simulation_files=simulation_files,
            market_day_reports=market_day_reports,
            open_trades=open_frame,
            closed_trades=closed_frame,
            pending_orders=pending_frame,
        ),
        "data_status": "ready" if rewardable_records else "no_rewardable_predictions" if records else "empty",
        "next_action": (
            "Review rewardable prediction contracts before making manual ranking-policy decisions."
            if rewardable_records
            else "Emit timestamped predictions with direction, horizon, target, invalidation, confidence, actual return, and baseline return."
            if records
            else "Collect candidate lifecycle rows before Evidence Reward can score predictions."
        ),
        **SAFETY_FLAGS,
    }
    rows = _records_to_rows(records[:250])
    rewardable_rows = _records_to_rows(rewardable_records[:250])
    incomplete_rows = _records_to_rows([row for row in records if not row.rewardable][:250])
    aggregations = {
        "reward_distribution": _reward_distribution(records),
        "reward_by_score_bucket": score_buckets,
        "reward_by_engine": engines,
        "reward_by_setup_type": setups,
        "reward_by_regime": regimes,
        "reward_by_ai_verdict": ai,
        "blocker_value_report": blockers,
        "missed_move_report": {
            "missed_move_count": summary["missed_move_count"],
            "penalized_rows": [
                row for row in rewardable_rows
                if (row.get("reward_components") or {}).get("missed_move_penalty", 0.0) > 0
            ][:25],
        },
        "best_positive_segments": sorted(
            setups + engines + regimes,
            key=lambda item: item.get("avg_reward") if item.get("avg_reward") is not None else -9999,
            reverse=True,
        )[:10],
        "worst_negative_segments": sorted(
            setups + engines + regimes,
            key=lambda item: item.get("avg_reward") if item.get("avg_reward") is not None else 9999,
        )[:10],
    }
    recommendations = _safe_recommendations(
        blockers=blockers,
        setups=setups,
        engines=engines,
        ai=ai,
        score_buckets=score_buckets,
    )
    warnings = []
    if not records:
        warnings.append("No candidate evidence has been found yet.")
    elif not rewardable_records:
        warnings.append("No rewardable prediction contracts were found; incomplete rows are excluded from averages.")
    if missing_counter:
        warnings.append("Some records are missing fields required for complete research attribution.")
    return serialize_value(
        {
            "status": summary["data_status"],
            "generated_at": summary["generated_at"],
            "research_only": True,
            "summary": summary,
            "records": rows,
            "aggregations": aggregations,
            "missing_fields": dict(missing_counter),
            "warnings": warnings,
            "safety_notes": list(SAFETY_NOTES),
            "candidate_rows": rows,
            "rewardable_predictions": rewardable_rows,
            "incomplete_evidence": incomplete_rows,
            "blocker_rewards": blockers,
            "engine_rewards": engines,
            "setup_rewards": setups,
            "ai_rewards": ai,
            "regime_rewards": regimes,
            "reward_by_score_bucket": score_buckets,
            "safe_recommendations": recommendations,
            **SAFETY_FLAGS,
        }
    )


def get_evidence_reward_summary(db: Any = None, *, current_user: Any) -> dict[str, Any]:
    return build_evidence_reward_report(tenant_slug=_tenant_slug_from_user(current_user))


def _report_subset(report: dict[str, Any], *, items: Any, aggregations: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": report.get("status", (report.get("summary") or {}).get("data_status", "unknown")),
        "generated_at": report.get("generated_at", (report.get("summary") or {}).get("generated_at")),
        "research_only": True,
        "summary": report["summary"],
        "records": items if isinstance(items, list) else [],
        "items": items,
        "aggregations": aggregations or {},
        "missing_fields": report.get("missing_fields", {}),
        "warnings": report.get("warnings", []),
        "safety_notes": report.get("safety_notes", list(SAFETY_NOTES)),
        **SAFETY_FLAGS,
    }


def get_evidence_reward_candidates(db: Any = None, *, current_user: Any) -> dict[str, Any]:
    report = get_evidence_reward_summary(db, current_user=current_user)
    return _report_subset(report, items=report["candidate_rows"])


def get_evidence_reward_blockers(db: Any = None, *, current_user: Any) -> dict[str, Any]:
    report = get_evidence_reward_summary(db, current_user=current_user)
    return _report_subset(report, items=report["blocker_rewards"], aggregations={"blocker_value_report": report["blocker_rewards"]})


def get_evidence_reward_engines(db: Any = None, *, current_user: Any) -> dict[str, Any]:
    report = get_evidence_reward_summary(db, current_user=current_user)
    return _report_subset(report, items=report["engine_rewards"], aggregations={"reward_by_engine": report["engine_rewards"]})


def get_evidence_reward_setups(db: Any = None, *, current_user: Any) -> dict[str, Any]:
    report = get_evidence_reward_summary(db, current_user=current_user)
    return _report_subset(report, items=report["setup_rewards"], aggregations={"reward_by_setup_type": report["setup_rewards"]})


def get_evidence_reward_ai(db: Any = None, *, current_user: Any) -> dict[str, Any]:
    report = get_evidence_reward_summary(db, current_user=current_user)
    return {**_report_subset(report, items=report["ai_rewards"].get("items", []), aggregations={"reward_by_ai_verdict": report["ai_rewards"]}), **report["ai_rewards"]}


def get_evidence_reward_regimes(db: Any = None, *, current_user: Any) -> dict[str, Any]:
    report = get_evidence_reward_summary(db, current_user=current_user)
    return _report_subset(report, items=report["regime_rewards"], aggregations={"reward_by_regime": report["regime_rewards"]})
