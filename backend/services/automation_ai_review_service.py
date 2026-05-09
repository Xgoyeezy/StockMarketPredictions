from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta, timezone
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.services import notes_service, risk_control_service
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

MARKET_TIMEZONE = ZoneInfo("America/New_York")
AI_NOTE_OWNER = "automation-ai"
AI_REVIEW_HISTORY_LIMIT = 10
AI_DAILY_JOURNAL_LIMIT = 7
AI_DAILY_OBSERVATION_LIMIT = 80
AI_POST_CLOSE_BUFFER_MINUTES = 10
AI_PERSONAL_LIVE_PROFILE = "personal_live"

AI_SETTINGS_DEFAULTS: dict[str, Any] = {
    "ai_daily_review_enabled": True,
    "ai_auto_adjust_enabled": True,
    "ai_adjust_live_enabled": True,
    "ai_review_min_trades": 3,
    "ai_max_daily_setting_changes": 4,
    "ai_max_step_pct": 20.0,
    "ai_evidence_review_enabled": True,
    "ai_evidence_review_mode": "shadow_review",
    "ai_evidence_min_confidence": 0.70,
    "ai_evidence_max_candidates_per_cycle": 12,
}

AI_EVIDENCE_REVIEW_MODES = {"shadow_review", "paper_assist"}
AI_EVIDENCE_HARD_BLOCKERS = {
    "kill_switch",
    "daily_loss_budget_lock",
    "daily_objective_entry_lock",
    "target_reached_protect_streak",
    "cooldown_active",
    "stale_quote",
    "spread_too_wide",
    "broker_live_locked",
    "non_alpaca_route",
    "route_not_paper",
    "session_closed",
    "close_cleanup",
}

AI_EVIDENCE_REASON_CODE_MAP = {
    "qualified_opportunity_score": "opportunity_score_incomplete",
    "fresh_deep_or_rapid_confirmation": "confirmation_incomplete",
    "relative_volume": "volume_evidence_missing",
    "relative_volume_expansion": "volume_evidence_weak",
    "spread": "spread_evidence_missing",
    "valid_trade_decision": "trade_decision_incomplete",
    "routeable_ranking_tier": "ranking_tier_not_routeable",
    "ai_evidence_review_slot": "outside_review_limit",
    "kill_switch": "hard_safety_lock",
    "daily_loss_budget_lock": "hard_loss_lock",
    "daily_objective_entry_lock": "hard_objective_lock",
    "target_reached_protect_streak": "hard_objective_lock",
    "cooldown_active": "hard_cooldown_lock",
    "stale_quote": "hard_stale_data",
    "spread_too_wide": "hard_spread_lock",
    "broker_live_locked": "hard_live_lock",
    "non_alpaca_route": "hard_route_lock",
    "route_not_paper": "hard_route_lock",
    "session_closed": "hard_session_lock",
    "close_cleanup": "hard_close_cleanup",
}

AI_TUNABLE_LIMITS: dict[str, tuple[str, float | int, float | int]] = {
    "risk_percent": ("float", 0.05, 5.0),
    "max_notional_per_trade": ("float", 100.0, 5_000_000.0),
    "max_total_open_notional": ("float", 100.0, 5_000_000.0),
    "max_gross_leverage": ("float", 0.1, 10.0),
    "max_single_position_pct": ("float", 1.0, 100.0),
    "max_correlated_bucket_pct": ("float", 1.0, 100.0),
    "max_daily_loss_r": ("float", 0.25, 25.0),
    "max_consecutive_losses": ("int", 1, 25),
    "max_daily_entries": ("int", 1, 100),
    "max_daily_entries_per_symbol": ("int", 1, 25),
    "cooldown_minutes": ("int", 0, 1440),
    "cycle_entry_rank_limit": ("int", 1, 10),
    "min_edge_to_cost_ratio": ("float", 0.0, 25.0),
    "flatten_before_close_minutes": ("int", 1, 90),
    "order_type": ("enum", 0, 0),
    "require_liquidity_fields": ("bool", 0, 1),
    "require_edge_fields": ("bool", 0, 1),
    "market_slippage_bps": ("float", 0.0, 500.0),
    "limit_slippage_bps": ("float", 0.0, 500.0),
    "max_spread_bps": ("float", 0.0, 1000.0),
    "min_average_dollar_volume": ("float", 0.0, 1_000_000_000_000.0),
    "max_order_adv_pct": ("float", 0.001, 100.0),
    "max_intraday_volume_pct": ("float", 0.001, 100.0),
    "no_new_entries_first_minutes": ("int", 0, 120),
    "no_new_entries_before_close_minutes": ("int", 0, 240),
    "max_error_streak": ("int", 1, 25),
}

AI_NEVER_TUNE_FIELDS = {
    "enabled",
    "armed",
    "kill_switch",
    "execution_intent",
    "account_size",
    "effective_funds_multiplier",
    "tickers",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if pd.isna(parsed):
        return float(default)
    return float(parsed)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on"}:
            return True
        if cleaned in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _clamp_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    return max(float(minimum), min(float(maximum), _coerce_float(value, default)))


def _clamp_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    return max(int(minimum), min(int(maximum), _coerce_int(value, default)))


def normalize_ai_review_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    mode = str(
        state.get("ai_evidence_review_mode") or AI_SETTINGS_DEFAULTS["ai_evidence_review_mode"]
    ).strip().lower()
    if mode not in AI_EVIDENCE_REVIEW_MODES:
        mode = str(AI_SETTINGS_DEFAULTS["ai_evidence_review_mode"])
    return {
        "ai_daily_review_enabled": _coerce_bool(
            state.get("ai_daily_review_enabled"),
            bool(AI_SETTINGS_DEFAULTS["ai_daily_review_enabled"]),
        ),
        "ai_auto_adjust_enabled": _coerce_bool(
            state.get("ai_auto_adjust_enabled"),
            bool(AI_SETTINGS_DEFAULTS["ai_auto_adjust_enabled"]),
        ),
        "ai_adjust_live_enabled": _coerce_bool(
            state.get("ai_adjust_live_enabled"),
            bool(AI_SETTINGS_DEFAULTS["ai_adjust_live_enabled"]),
        ),
        "ai_review_min_trades": _clamp_int(
            state.get("ai_review_min_trades"),
            int(AI_SETTINGS_DEFAULTS["ai_review_min_trades"]),
            minimum=0,
            maximum=100,
        ),
        "ai_max_daily_setting_changes": _clamp_int(
            state.get("ai_max_daily_setting_changes"),
            int(AI_SETTINGS_DEFAULTS["ai_max_daily_setting_changes"]),
            minimum=0,
            maximum=12,
        ),
        "ai_max_step_pct": _clamp_float(
            state.get("ai_max_step_pct"),
            float(AI_SETTINGS_DEFAULTS["ai_max_step_pct"]),
            minimum=1.0,
            maximum=50.0,
        ),
        "ai_evidence_review_enabled": _coerce_bool(
            state.get("ai_evidence_review_enabled"),
            bool(AI_SETTINGS_DEFAULTS["ai_evidence_review_enabled"]),
        ),
        "ai_evidence_review_mode": mode,
        "ai_evidence_min_confidence": _clamp_float(
            state.get("ai_evidence_min_confidence"),
            float(AI_SETTINGS_DEFAULTS["ai_evidence_min_confidence"]),
            minimum=0.0,
            maximum=1.0,
        ),
        "ai_evidence_max_candidates_per_cycle": _clamp_int(
            state.get("ai_evidence_max_candidates_per_cycle"),
            int(AI_SETTINGS_DEFAULTS["ai_evidence_max_candidates_per_cycle"]),
            minimum=1,
            maximum=50,
        ),
    }


def _evidence_float(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(parsed):
        return default
    return float(parsed)


def _nested_evidence_float(payload: dict[str, Any], nested_key: str, key: str) -> float | None:
    nested = payload.get(nested_key)
    if isinstance(nested, dict):
        return _evidence_float(nested.get(key))
    return None


def _evidence_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "ready", "fresh"}
    return bool(value)


def _candidate_ai_value(candidate: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = candidate.get(key)
        if value not in (None, ""):
            return value
    for key in keys:
        for nested_key in ("opportunity_capture", "scores", "deep_analysis", "risk"):
            nested = candidate.get(nested_key)
            if isinstance(nested, dict):
                value = nested.get(key)
                if value not in (None, ""):
                    return value
    return None


def _ai_evidence_reason_code(reason: Any) -> str:
    normalized = str(reason or "").strip().lower()
    if not normalized:
        return "unspecified"
    return AI_EVIDENCE_REASON_CODE_MAP.get(normalized, normalized)


def _ai_evidence_review_truth_tag(
    review: dict[str, Any],
    candidate: dict[str, Any] | None = None,
) -> str:
    candidate = dict(candidate or {})
    verdict = str(review.get("verdict") or "").strip().lower()
    blocker = str(candidate.get("blocker") or candidate.get("diagnostic_blocker") or "").strip().lower()
    opportunity_score = _evidence_float(
        review.get("opportunity_score"),
        _evidence_float(candidate.get("opportunity_score"), 0.0) or 0.0,
    ) or 0.0
    rapid_confirmed = _evidence_bool(
        review.get("confirmation_ready")
        or candidate.get("rapid_confirmed")
        or candidate.get("confirmation_ready")
    )
    if verdict == "approve_evidence" and blocker:
        return "possible_false_positive_blocked_by_gate"
    if verdict == "reject_evidence" and opportunity_score >= 72.0 and rapid_confirmed:
        return "possible_false_negative_strong_event"
    if verdict == "wait_for_confirmation" and opportunity_score >= 72.0:
        return "incomplete_high_score_event"
    if verdict == "size_down":
        return "approved_with_risk_haircut"
    if verdict == "approve_evidence":
        return "shadow_approval_waiting_outcome"
    if verdict == "reject_evidence":
        return "shadow_rejection_waiting_outcome"
    return "shadow_review_waiting_outcome"


def build_ai_evidence_review_report(
    reviews: list[dict[str, Any]],
    *,
    candidates: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate intraday referee reviews without changing trading behavior."""

    now = now or _utc_now()
    candidate_rows = [dict(item or {}) for item in list(candidates or [])]
    review_rows = [dict(item or {}) for item in list(reviews or [])]
    verdict_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    reason_code_counts: Counter[str] = Counter()
    missing_buckets: Counter[str] = Counter()
    hard_blocker_counts: Counter[str] = Counter()
    ticker_approval_counts: Counter[str] = Counter()
    blocker_rejection_counts: Counter[str] = Counter()
    truth_tags: Counter[str] = Counter()
    confidence_values: list[float] = []
    latency_values: list[float] = []
    incomplete_rows: list[dict[str, Any]] = []

    for index, review in enumerate(review_rows):
        candidate = candidate_rows[index] if index < len(candidate_rows) else {}
        verdict = str(review.get("verdict") or "not_reviewed").strip().lower() or "not_reviewed"
        status = str(review.get("status") or "not_reviewed").strip().lower() or "not_reviewed"
        verdict_counts[verdict] += 1
        status_counts[status] += 1
        confidence = _evidence_float(review.get("confidence"))
        if confidence is not None:
            confidence_values.append(confidence)
        latency_ms = _evidence_float(review.get("review_latency_ms"))
        if latency_ms is not None:
            latency_values.append(latency_ms)
        ticker = str(review.get("ticker") or candidate.get("ticker") or "").strip().upper()
        if verdict == "approve_evidence" and ticker:
            ticker_approval_counts[ticker] += 1
        blocker = str(candidate.get("blocker") or candidate.get("diagnostic_blocker") or "").strip().lower()
        if verdict == "reject_evidence" and blocker:
            blocker_rejection_counts[blocker] += 1
        for missing in list(review.get("missing_evidence") or []):
            code = _ai_evidence_reason_code(missing)
            missing_buckets[code] += 1
            reason_code_counts[code] += 1
        for hard_blocker in list(review.get("hard_blockers") or []):
            code = _ai_evidence_reason_code(hard_blocker)
            hard_blocker_counts[code] += 1
            reason_code_counts[code] += 1
        tag = _ai_evidence_review_truth_tag(review, candidate)
        truth_tags[tag] += 1
        if review.get("missing_evidence"):
            incomplete_rows.append(
                {
                    "ticker": ticker or None,
                    "verdict": verdict,
                    "missing_evidence": list(review.get("missing_evidence") or [])[:6],
                    "reason_codes": [_ai_evidence_reason_code(item) for item in list(review.get("missing_evidence") or [])[:6]],
                    "next_action": review.get("next_action"),
                }
            )

    avg_confidence = round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else None
    return serialize_value(
        {
            "generated_at": _serialize_datetime(now),
            "reviewed_count": status_counts.get("reviewed", 0),
            "total_count": len(review_rows),
            "approved_count": verdict_counts.get("approve_evidence", 0),
            "wait_count": verdict_counts.get("wait_for_confirmation", 0),
            "size_down_count": verdict_counts.get("size_down", 0),
            "rejected_count": verdict_counts.get("reject_evidence", 0),
            "verdict_counts": dict(verdict_counts),
            "status_counts": dict(status_counts),
            "reason_code_counts": dict(reason_code_counts),
            "evidence_incomplete_buckets": dict(missing_buckets),
            "hard_blocker_counts": dict(hard_blocker_counts),
            "confidence": {
                "average": avg_confidence,
                "min": round(min(confidence_values), 4) if confidence_values else None,
                "max": round(max(confidence_values), 4) if confidence_values else None,
                "sample_count": len(confidence_values),
                "calibration": "shadow_pending_outcomes",
            },
            "confidence_drift": {
                "status": "collecting_shadow_sample",
                "baseline": avg_confidence,
                "current": avg_confidence,
                "drift": 0.0 if avg_confidence is not None else None,
            },
            "review_latency": {
                "average_ms": round(sum(latency_values) / len(latency_values), 4) if latency_values else 0.0,
                "max_ms": round(max(latency_values), 4) if latency_values else 0.0,
                "sample_count": len(latency_values),
            },
            "approval_trend_by_ticker": dict(ticker_approval_counts),
            "rejection_trend_by_blocker": dict(blocker_rejection_counts),
            "shadow_vs_outcome": {
                "mode": "shadow_pending_outcomes",
                "truth_tag_counts": dict(truth_tags),
                "false_positive_tags": {
                    key: value
                    for key, value in truth_tags.items()
                    if key.startswith("possible_false_positive")
                },
                "false_negative_tags": {
                    key: value
                    for key, value in truth_tags.items()
                    if key.startswith("possible_false_negative")
                },
            },
            "evidence_incomplete": incomplete_rows[:12],
            "export": {
                "available": bool(review_rows),
                "format": "json",
                "retention": {
                    "runtime_rows": len(review_rows),
                    "history_limit": AI_REVIEW_HISTORY_LIMIT,
                    "journal_limit_days": AI_DAILY_JOURNAL_LIMIT,
                },
            },
            "operator_override_notes": {
                "supported": True,
                "required_for_live": True,
                "detail": "Operator notes are evidence only; they do not bypass final risk gates.",
            },
            "paper_assist": {
                "dry_run_only": True,
                "can_approve_orders": False,
                "can_override_risk_gates": False,
            },
        }
    )


def build_missed_trade_ai_review(
    candidates: list[dict[str, Any]],
    *,
    limit: int = 12,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    rows: list[dict[str, Any]] = []
    for candidate in list(candidates or []):
        item = dict(candidate or {})
        if bool(item.get("eligible")):
            continue
        opportunity = dict(item.get("opportunity_capture") or {})
        score = _evidence_float(opportunity.get("score"), _evidence_float(item.get("opportunity_score"), 0.0) or 0.0) or 0.0
        stage_score = _evidence_float(item.get("stage_one_score"), 0.0) or 0.0
        deep_score = _evidence_float(item.get("deep_score"), 0.0) or 0.0
        review = dict(item.get("ai_evidence_review") or {})
        if score <= 0.0 and stage_score <= 0.0 and deep_score <= 0.0 and not review:
            continue
        rows.append(
            {
                "candidate_lifecycle_id": item.get("candidate_lifecycle_id"),
                "ticker": item.get("ticker"),
                "opportunity_score": round(score, 4),
                "stage_one_score": stage_score,
                "deep_score": deep_score,
                "blocker": item.get("blocker"),
                "blocker_at_move": item.get("blocker_at_move") or item.get("blocker"),
                "ai_verdict": review.get("verdict"),
                "ai_confidence": review.get("confidence"),
                "would_we_catch_it_now": (
                    "yes_needs_final_gates"
                    if review.get("verdict") in {"approve_evidence", "size_down"}
                    else "wait_for_confirmation"
                    if review.get("verdict") == "wait_for_confirmation" or score >= 72.0
                    else "no_current_evidence"
                ),
                "next_action": item.get("next_action") or review.get("next_action"),
            }
        )
    rows.sort(
        key=lambda item: (
            _evidence_float(item.get("opportunity_score"), 0.0) or 0.0,
            _evidence_float(item.get("stage_one_score"), 0.0) or 0.0,
            _evidence_float(item.get("deep_score"), 0.0) or 0.0,
        ),
        reverse=True,
    )
    catchable = sum(1 for item in rows if item["would_we_catch_it_now"] != "no_current_evidence")
    return serialize_value(
        {
            "generated_at": _serialize_datetime(now),
            "reviewed_count": len(rows),
            "catchable_now_count": catchable,
            "catch_rate": round(catchable / len(rows), 4) if rows else None,
            "rows": rows[: max(1, int(limit or 12))],
            "next_action": (
                "Review high-score blocked rows first; do not loosen risk gates."
                if rows
                else "No missed-trade candidates with enough evidence were found in the current diagnostics."
            ),
        }
    )


def review_trade_candidate_evidence(
    candidate: dict[str, Any],
    *,
    settings_state: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Rules-backed intraday evidence referee.

    This is intentionally deterministic for v1. It explains evidence quality
    and never creates a standalone route to execution.
    """

    started_at = perf_counter()
    now = now or _utc_now()
    settings_state = dict(settings_state or {})
    state = dict(state or {})
    ai_settings = normalize_ai_review_settings(settings_state)
    enabled = bool(ai_settings["ai_evidence_review_enabled"])
    mode = str(ai_settings["ai_evidence_review_mode"])
    min_confidence = float(ai_settings["ai_evidence_min_confidence"])
    ticker = str(candidate.get("ticker") or candidate.get("symbol") or "").strip().upper() or None
    if not enabled:
        return {
            "status": "disabled",
            "mode": mode,
            "verdict": "wait_for_confirmation",
            "confidence": 0.0,
            "reason_codes": ["ai_evidence_review_disabled"],
            "reasons": ["AI evidence review is disabled."],
            "missing_evidence": ["ai_evidence_review_enabled"],
            "evidence_incomplete_buckets": ["ai_evidence_review_disabled"],
            "next_action": "Use normal strategy, risk, and execution gates.",
            "reviewed_at": _serialize_datetime(now),
            "review_latency_ms": round((perf_counter() - started_at) * 1000.0, 4),
            "ticker": ticker,
        }

    opportunity_score = (
        _evidence_float(_candidate_ai_value(candidate, "opportunity_score", "score"))
        or _nested_evidence_float(candidate, "opportunity_capture", "score")
        or 0.0
    )
    min_opportunity_score = _evidence_float(settings_state.get("min_opportunity_score"), 72.0) or 72.0
    relative_volume = (
        _evidence_float(_candidate_ai_value(candidate, "relative_volume"))
        or _nested_evidence_float(candidate, "opportunity_capture", "relative_volume")
    )
    min_relative_volume = _evidence_float(settings_state.get("min_breakout_relative_volume"), 1.4) or 1.4
    spread_bps = _evidence_float(
        _candidate_ai_value(candidate, "spread_bps", "spread_estimate_bps", "daily_objective_spread_bps")
    )
    max_spread_bps = _evidence_float(
        settings_state.get("max_spread_bps_for_opportunity"),
        _evidence_float(settings_state.get("max_spread_bps"), 35.0) or 35.0,
    ) or 35.0
    execution_score = _evidence_float(_candidate_ai_value(candidate, "execution_score"), 0.0) or 0.0
    portfolio_score = _evidence_float(_candidate_ai_value(candidate, "portfolio_score"), 0.0) or 0.0
    deep_score = _evidence_float(_candidate_ai_value(candidate, "deep_score"), 0.0) or 0.0
    edge_to_cost = _evidence_float(_candidate_ai_value(candidate, "edge_to_cost_ratio"))
    opportunity_type = str(
        _candidate_ai_value(candidate, "opportunity_type", "type") or "none"
    ).strip().lower()
    rapid_confirmed = _evidence_bool(_candidate_ai_value(candidate, "rapid_confirmed"))
    deep_status = str(_candidate_ai_value(candidate, "deep_analysis_status", "status") or "").strip().lower()
    deep_cache_fresh = _evidence_bool(_candidate_ai_value(candidate, "deep_analysis_cache_fresh", "cache_fresh"))
    blocker = str(
        candidate.get("blocker")
        or candidate.get("diagnostic_blocker")
        or candidate.get("rejected_reason")
        or ""
    ).strip().lower()
    ranking_tier = str(candidate.get("ranking_tier") or "").strip().lower()
    trade_decision = str(candidate.get("trade_decision") or "").strip().upper()
    execution_intent = str(settings_state.get("execution_intent") or "").strip().lower()

    reasons: list[str] = []
    missing: list[str] = []
    hard_blockers: list[str] = []
    confirmation_ready = rapid_confirmed or (deep_status == "deep_analysis_ready" and deep_cache_fresh)
    if settings_state.get("kill_switch"):
        hard_blockers.append("kill_switch")
    if execution_intent and execution_intent != "broker_paper":
        hard_blockers.append("route_not_paper")
    for reason_key in (
        blocker,
        str((candidate.get("risk") or {}).get("reason") if isinstance(candidate.get("risk"), dict) else ""),
    ):
        normalized = str(reason_key or "").strip().lower()
        if normalized in AI_EVIDENCE_HARD_BLOCKERS:
            hard_blockers.append(normalized)

    if opportunity_score < min_opportunity_score:
        missing.append("qualified_opportunity_score")
    else:
        reasons.append(f"Opportunity score {opportunity_score:.1f} meets the {min_opportunity_score:.1f} threshold.")
    if not confirmation_ready:
        missing.append("fresh_deep_or_rapid_confirmation")
    else:
        reasons.append("Fresh deep-analysis or rapid confirmation is present.")
    if relative_volume is None:
        missing.append("relative_volume")
    elif relative_volume < min_relative_volume:
        missing.append("relative_volume_expansion")
    else:
        reasons.append(f"Relative volume {relative_volume:.2f} supports the setup.")
    if spread_bps is None:
        missing.append("spread")
    elif spread_bps > max_spread_bps:
        hard_blockers.append("spread_too_wide")
    else:
        reasons.append(f"Spread {spread_bps:.1f} bps is inside the {max_spread_bps:.1f} bps cap.")
    if trade_decision and trade_decision != "VALID TRADE":
        missing.append("valid_trade_decision")
    if ranking_tier == "stand_down":
        missing.append("routeable_ranking_tier")

    score_components = [
        opportunity_score,
        execution_score,
        portfolio_score,
        deep_score if deep_score else opportunity_score,
    ]
    score_confidence = sum(max(0.0, min(item, 100.0)) for item in score_components) / (len(score_components) * 100.0)
    confidence = score_confidence
    if confirmation_ready:
        confidence += 0.08
    if edge_to_cost is not None and edge_to_cost >= 3.0:
        confidence += 0.04
    if hard_blockers:
        confidence = min(confidence, 0.35)
    elif missing:
        confidence = min(confidence, 0.68)
    confidence = round(max(0.0, min(confidence, 0.99)), 2)

    if hard_blockers:
        verdict = "reject_evidence"
        status = "reviewed"
        next_action = "Do not route this candidate; hard safety or route evidence is blocking it."
        reasons.append("Hard blocker: " + ", ".join(sorted(set(hard_blockers))) + ".")
    elif "fresh_deep_or_rapid_confirmation" in missing or "relative_volume_expansion" in missing:
        verdict = "wait_for_confirmation"
        status = "reviewed"
        next_action = "Wait for confirmation, relative volume, and fresh quote evidence before considering a route."
    elif spread_bps is not None and spread_bps > max_spread_bps * 0.75:
        verdict = "size_down"
        status = "reviewed"
        next_action = "Evidence is usable, but price quality is thin; keep risk small if all final gates pass."
    elif not missing and confidence >= min_confidence:
        verdict = "approve_evidence"
        status = "reviewed"
        next_action = "Evidence supports consideration; final strategy and risk gates still decide routing."
    elif opportunity_score >= min_opportunity_score:
        verdict = "size_down"
        status = "reviewed"
        next_action = "Evidence is mixed; require final confirmation and smaller paper risk before routing."
    else:
        verdict = "reject_evidence"
        status = "reviewed"
        next_action = "Skip this setup until the opportunity score and confirmation improve."

    reason_codes = sorted(
        {
            _ai_evidence_reason_code(item)
            for item in [*missing, *hard_blockers]
            if str(item or "").strip()
        }
    )
    return {
        "status": status,
        "mode": mode,
        "verdict": verdict,
        "confidence": confidence,
        "min_confidence": min_confidence,
        "reason_codes": reason_codes,
        "reasons": reasons[:6] or ["No strong supporting evidence was found."],
        "missing_evidence": sorted(set(item for item in missing if item)),
        "evidence_incomplete_buckets": reason_codes,
        "hard_blockers": sorted(set(item for item in hard_blockers if item)),
        "opportunity_type": opportunity_type,
        "opportunity_score": round(float(opportunity_score), 2),
        "confirmation_ready": bool(confirmation_ready),
        "paper_route_only": True,
        "can_override_risk_gates": False,
        "paper_assist_dry_run_only": True,
        "operator_override_note_required": mode == "paper_assist",
        "next_action": next_action,
        "reviewed_at": _serialize_datetime(now),
        "review_latency_ms": round((perf_counter() - started_at) * 1000.0, 4),
        "ticker": ticker,
    }


def apply_ai_evidence_review_candidate_overlay(
    candidates: list[dict[str, Any]],
    *,
    state: dict[str, Any],
    current_equity: float | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    settings_state = dict((state or {}).get("settings") or {})
    ai_settings = normalize_ai_review_settings(settings_state)
    max_reviews = int(ai_settings["ai_evidence_max_candidates_per_cycle"])
    mode = str(ai_settings["ai_evidence_review_mode"])
    reviewed: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        next_candidate = dict(candidate)
        if index < max_reviews:
            review = review_trade_candidate_evidence(
                next_candidate,
                settings_state=settings_state,
                state=state,
                now=now,
            )
        else:
            review = {
                "status": "skipped",
                "mode": mode,
                "verdict": "wait_for_confirmation",
                "confidence": 0.0,
                "reason_codes": ["outside_review_limit"],
                "reasons": ["Candidate was outside the per-cycle AI evidence review limit."],
                "missing_evidence": ["ai_evidence_review_slot"],
                "evidence_incomplete_buckets": ["outside_review_limit"],
                "next_action": "Wait for a higher ranked candidate review slot.",
                "reviewed_at": _serialize_datetime(now or _utc_now()),
                "review_latency_ms": 0.0,
                "ticker": str(next_candidate.get("ticker") or "").strip().upper() or None,
                "paper_route_only": True,
                "can_override_risk_gates": False,
                "paper_assist_dry_run_only": True,
                "operator_override_note_required": mode == "paper_assist",
            }
        next_candidate["ai_evidence_review"] = serialize_value(review)
        if mode == "paper_assist" and review.get("verdict") in {"reject_evidence", "wait_for_confirmation"}:
            next_candidate["auto_entry_eligible"] = False
            next_candidate["ai_evidence_blocked"] = True
            next_candidate["ai_evidence_block_reason"] = review.get("verdict")
        elif mode == "paper_assist" and review.get("verdict") == "size_down":
            next_candidate["ai_evidence_size_down"] = True
            next_candidate["portfolio_score"] = max(_evidence_float(next_candidate.get("portfolio_score"), 0.0) or 0.0 - 8.0, 0.0)
            next_candidate["execution_score"] = max(_evidence_float(next_candidate.get("execution_score"), 0.0) or 0.0 - 4.0, 0.0)
        reviewed.append(next_candidate)
    return reviewed


def normalize_ai_review_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    journal = runtime.get("ai_daily_journal") if isinstance(runtime.get("ai_daily_journal"), dict) else {}
    normalized_journal: dict[str, dict[str, Any]] = {}
    for session_day in sorted(journal.keys())[-AI_DAILY_JOURNAL_LIMIT:]:
        value = journal.get(session_day)
        if not isinstance(value, dict):
            continue
        observations = [
            serialize_value(item)
            for item in list(value.get("observations") or [])[-AI_DAILY_OBSERVATION_LIMIT:]
            if isinstance(item, dict)
        ]
        normalized_journal[str(session_day)] = {
            **{str(key): serialize_value(raw) for key, raw in value.items() if key != "observations"},
            "observations": observations,
        }
    history = [
        serialize_value(item)
        for item in list(runtime.get("ai_review_history") or [])[-AI_REVIEW_HISTORY_LIMIT:]
        if isinstance(item, dict)
    ]
    return {
        "ai_daily_journal": normalized_journal,
        "ai_last_note_id": str(runtime.get("ai_last_note_id") or "").strip() or None,
        "ai_last_observation_at": _serialize_datetime(_parse_datetime(runtime.get("ai_last_observation_at"))),
        "ai_last_review_session_day": str(runtime.get("ai_last_review_session_day") or "").strip() or None,
        "ai_last_review_at": _serialize_datetime(_parse_datetime(runtime.get("ai_last_review_at"))),
        "ai_last_review": serialize_value(runtime.get("ai_last_review")),
        "ai_last_adjustment": serialize_value(runtime.get("ai_last_adjustment")),
        "ai_review_history": history,
    }


def session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def _previous_weekday(day: datetime) -> datetime:
    previous = day - timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= timedelta(days=1)
    return previous


def review_session_day_for(value: datetime | None = None, *, forced: bool = False) -> tuple[str, bool]:
    now = value or _utc_now()
    now_et = now.astimezone(MARKET_TIMEZONE)
    close_review_time = time(16, AI_POST_CLOSE_BUFFER_MINUTES)
    if forced:
        return now_et.strftime("%Y-%m-%d"), True
    if now_et.weekday() >= 5:
        return _previous_weekday(now_et).strftime("%Y-%m-%d"), True
    if now_et.time() >= close_review_time:
        return now_et.strftime("%Y-%m-%d"), True
    if now_et.time() < time(9, 30):
        return _previous_weekday(now_et).strftime("%Y-%m-%d"), True
    return now_et.strftime("%Y-%m-%d"), False


def _session_bounds_for_day(session_day: str) -> tuple[datetime, datetime]:
    day = datetime.strptime(session_day, "%Y-%m-%d").replace(tzinfo=MARKET_TIMEZONE)
    return day.astimezone(timezone.utc), (day + timedelta(days=1)).astimezone(timezone.utc)


def _profile_tag(profile_key: str) -> str:
    return "profile-" + str(profile_key or "personal_paper").strip().lower().replace(":", "-")


def _owned_automation_rows(frame: pd.DataFrame, *, tenant_id: str, profile_key: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    filtered = frame.copy()
    if "automation_origin" in filtered.columns:
        filtered = filtered.loc[
            filtered["automation_origin"].astype(str).str.strip().str.lower().eq("trade_automation")
        ]
    if "automation_tenant_id" in filtered.columns:
        filtered = filtered.loc[filtered["automation_tenant_id"].astype(str).str.strip().eq(str(tenant_id))]
    normalized_profile_key = str(profile_key or "personal_paper").strip().lower()
    if "automation_profile_key" in filtered.columns:
        profile = filtered["automation_profile_key"].astype(str).str.strip().str.lower()
        profile_matches = profile.eq(normalized_profile_key)
        if normalized_profile_key == "personal_paper":
            profile_matches = profile_matches | profile.eq("")
        filtered = filtered.loc[profile_matches]
    elif normalized_profile_key != "personal_paper":
        filtered = filtered.iloc[0:0]
    return filtered.reset_index(drop=True)


def _closed_rows_for_session(frame: pd.DataFrame, *, session_day: str) -> pd.DataFrame:
    if frame.empty or "closed_at" not in frame.columns:
        return pd.DataFrame()
    start_at, end_at = _session_bounds_for_day(session_day)
    closed = frame.copy()
    closed["__closed_at"] = pd.to_datetime(closed.get("closed_at"), errors="coerce", utc=True)
    mask = (
        closed["__closed_at"].notna()
        & (closed["__closed_at"] >= pd.Timestamp(start_at))
        & (closed["__closed_at"] < pd.Timestamp(end_at))
    )
    return closed.loc[mask].reset_index(drop=True)


def _count_recent_loss_streak(closed_rows: pd.DataFrame) -> int:
    if closed_rows.empty or "realized_pnl" not in closed_rows.columns:
        return 0
    rows = closed_rows.copy()
    rows["__closed_at"] = pd.to_datetime(rows.get("closed_at"), errors="coerce", utc=True)
    rows = rows.sort_values("__closed_at", ascending=False, na_position="last")
    streak = 0
    for pnl in pd.to_numeric(rows.get("realized_pnl"), errors="coerce").fillna(0.0).tolist():
        if pnl < 0:
            streak += 1
            continue
        if pnl > 0:
            break
    return streak


def _recent_automation_order_events(
    db: Session | None,
    *,
    tenant: Tenant,
    profile_key: str,
    session_day: str | None = None,
) -> list[dict[str, Any]]:
    if db is None:
        return []
    statement = (
        select(OrderEventRecord)
        .where(OrderEventRecord.tenant_id == tenant.id)
        .order_by(OrderEventRecord.created_at.desc())
        .limit(250)
    )
    rows = list(db.execute(statement).scalars().all())
    start_at = end_at = None
    if session_day:
        start_at, end_at = _session_bounds_for_day(session_day)
    events: list[dict[str, Any]] = []
    for row in rows:
        if start_at and row.created_at and row.created_at < start_at:
            continue
        if end_at and row.created_at and row.created_at >= end_at:
            continue
        payload = dict(row.payload_json or {})
        payload_trade = dict(payload.get("trade") or {})
        row_profile = str(
            payload.get("automation_profile_key")
            or payload_trade.get("automation_profile_key")
            or ""
        ).strip().lower()
        if row_profile and row_profile != str(profile_key).strip().lower():
            continue
        if (
            str(payload.get("automation_cycle_id") or "").strip()
            or str(payload_trade.get("automation_origin") or "").strip().lower() == "trade_automation"
            or row_profile
        ):
            events.append(
                {
                    "event_key": row.event_key,
                    "status": row.status,
                    "ticker": row.ticker,
                    "detail": row.detail,
                    "created_at": _serialize_datetime(row.created_at),
                    "slippage_bps": _coerce_float(payload.get("slippage_bps"), 0.0)
                    if payload.get("slippage_bps") is not None
                    else None,
                }
            )
    return events[:50]


def _safe_counter_increment(target: dict[str, Any], key: str, amount: int = 1) -> None:
    target[key] = _coerce_int(target.get(key), 0) + int(amount)


def _observation_from_state(
    state: dict[str, Any],
    *,
    now: datetime,
    cycle_id: str | None,
) -> dict[str, Any]:
    runtime = dict(state.get("runtime") or {})
    action = dict(runtime.get("last_action") or {})
    decision = dict(runtime.get("last_decision") or {})
    rejection = dict(runtime.get("last_rejection") or {})
    guardrail = dict(runtime.get("last_guardrail") or {})
    candidate = dict(runtime.get("last_candidate") or {})
    action_type = str(action.get("type") or decision.get("decision") or "cycle").strip().lower()
    tone = "neutral"
    category = "observation"
    if action_type in {"open_trade", "manage_positions", "flatten"} or decision.get("decision") == "opened":
        tone = "good"
        category = "worked"
    if action_type in {"stand_down", "blocked", "error"} or decision.get("decision") in {"stand_down", "blocked", "error"}:
        tone = "bad"
        category = "needs_attention"
    if guardrail.get("locked") or guardrail.get("reason"):
        tone = "bad"
        category = "risk_lock"
    return {
        "at": _serialize_datetime(now),
        "cycle_id": cycle_id,
        "tone": tone,
        "category": category,
        "action_type": action_type,
        "decision": serialize_value(decision),
        "action": serialize_value(action),
        "rejection": serialize_value(rejection) if rejection else None,
        "guardrail": serialize_value(guardrail) if guardrail else None,
        "candidate": {
            "ticker": candidate.get("ticker"),
            "instrument_type": candidate.get("automation_instrument_type") or candidate.get("instrument_type"),
            "rank": candidate.get("portfolio_rank") or candidate.get("board_rank"),
            "setup_score": candidate.get("setup_score"),
            "edge_to_cost_ratio": candidate.get("edge_to_cost_ratio"),
            "eligible": candidate.get("auto_entry_eligible"),
        }
        if candidate
        else None,
        "detail": str(
            decision.get("detail")
            or action.get("detail")
            or rejection.get("detail")
            or "Automation cycle completed."
        ).strip(),
    }


def _summarize_daily_journal_item(item: dict[str, Any]) -> dict[str, Any]:
    observations = [obs for obs in list(item.get("observations") or []) if isinstance(obs, dict)]
    tones = Counter(str(obs.get("tone") or "neutral") for obs in observations)
    actions = Counter(str(obs.get("action_type") or "cycle") for obs in observations)
    rejection_reasons = Counter(
        str((obs.get("rejection") or {}).get("reason") or (obs.get("decision") or {}).get("reason") or "")
        for obs in observations
    )
    rejection_reasons.pop("", None)
    return {
        "observation_count": len(observations),
        "good_count": int(tones.get("good", 0)),
        "bad_count": int(tones.get("bad", 0)),
        "neutral_count": int(tones.get("neutral", 0)),
        "opened_count": int(actions.get("open_trade", 0)),
        "managed_count": int(actions.get("manage_positions", 0)),
        "flatten_count": int(actions.get("flatten", 0)),
        "stand_down_count": int(actions.get("stand_down", 0)),
        "blocked_count": int(actions.get("blocked", 0)),
        "error_count": int(actions.get("error", 0)),
        "top_rejection_reasons": rejection_reasons.most_common(5),
    }


def _short_observation_line(obs: dict[str, Any]) -> str:
    when = _parse_datetime(obs.get("at"))
    stamp = when.astimezone(MARKET_TIMEZONE).strftime("%H:%M") if when else "--:--"
    action = str(obs.get("action_type") or "cycle").replace("_", " ")
    candidate = obs.get("candidate") or {}
    symbol = str(candidate.get("ticker") or "").strip()
    suffix = f" ({symbol})" if symbol else ""
    detail = str(obs.get("detail") or "").strip() or "Cycle recorded."
    return f"- {stamp} {action}{suffix}: {detail[:220]}"


def _score_line(score: Any) -> str:
    if not isinstance(score, dict):
        return "- Return -- | Risk -- | Accuracy -- | Overall --"
    return (
        f"- Return {float(score.get('return_score') or 0):.0f} | "
        f"Risk {float(score.get('risk_score') or 0):.0f} | "
        f"Accuracy {float(score.get('accuracy_score') or 0):.0f} | "
        f"Overall {float(score.get('overall_score') or 0):.0f}"
    )


def _format_change(change: dict[str, Any]) -> str:
    key = str(change.get("field") or "").replace("_", " ")
    before = change.get("before")
    after = change.get("after")
    reason = str(change.get("reason") or "Setting adjusted.").strip()
    return f"- {key}: {before} -> {after}. {reason}"


def _build_note_body(
    *,
    tenant: Tenant,
    profile_key: str,
    session_day: str,
    journal_item: dict[str, Any],
    review: dict[str, Any] | None = None,
    finalized: bool = False,
) -> str:
    summary = _summarize_daily_journal_item(journal_item)
    observations = [obs for obs in list(journal_item.get("observations") or []) if isinstance(obs, dict)]
    worked = [obs for obs in observations if obs.get("tone") == "good"][-8:]
    failed = [obs for obs in observations if obs.get("tone") == "bad"][-8:]
    neutral = [obs for obs in observations if obs.get("tone") not in {"good", "bad"}][-6:]
    review = dict(review or {})
    applied = list(review.get("applied_changes") or [])
    skipped = list(review.get("skipped_changes") or [])
    rationale = str(review.get("no_change_rationale") or "").strip()

    lines = [
        f"Automation AI daily review for {tenant.name} / {profile_key}",
        f"Session day: {session_day}",
        f"Status: {'Finalized after close' if finalized else 'Collecting intraday observations'}",
        "",
        "What worked",
    ]
    lines.extend(_short_observation_line(obs) for obs in worked)
    if not worked:
        lines.append("- No positive automation events have been observed yet.")
    lines.extend(["", "What needs attention"])
    lines.extend(_short_observation_line(obs) for obs in failed)
    if not failed:
        lines.append("- No hard weakness has been observed yet.")
    lines.extend(["", "Neutral observations"])
    lines.extend(_short_observation_line(obs) for obs in neutral)
    if not neutral:
        lines.append("- No neutral observations recorded.")
    lines.extend(
        [
            "",
            "Daily counts",
            f"- Observations {summary['observation_count']} | Good {summary['good_count']} | Bad {summary['bad_count']} | Neutral {summary['neutral_count']}",
            f"- Opened {summary['opened_count']} | Managed {summary['managed_count']} | Stood down {summary['stand_down_count']} | Errors {summary['error_count']}",
            "",
            "Objective score",
            _score_line(review.get("objective_scores")),
            "",
            "Applied setting changes",
        ]
    )
    lines.extend(_format_change(change) for change in applied)
    if not applied:
        lines.append(f"- {rationale or 'No setting change was warranted from the current evidence.'}")
    lines.extend(["", "Skipped changes"])
    lines.extend(_format_change(change) for change in skipped if isinstance(change, dict))
    if not skipped:
        lines.append("- None.")
    if summary["top_rejection_reasons"]:
        lines.extend(["", "Top rejection reasons"])
        lines.extend(f"- {reason}: {count}" for reason, count in summary["top_rejection_reasons"])
    return "\n".join(lines).strip()


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=AI_NOTE_OWNER,
            limit=250,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {"automation-ai", "daily-review", _profile_tag(profile_key), f"session-{session_day}"}
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _sync_daily_note(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    session_day: str,
    journal_item: dict[str, Any],
    review: dict[str, Any] | None = None,
    finalized: bool = False,
) -> str | None:
    runtime = state.setdefault("runtime", {})
    note_id = str(journal_item.get("note_id") or runtime.get("ai_last_note_id") or "").strip()
    title = f"Automation AI review - {profile_key} - {session_day}"
    tags = ["automation-ai", "daily-review", _profile_tag(profile_key), f"session-{session_day}"]
    body = _build_note_body(
        tenant=tenant,
        profile_key=profile_key,
        session_day=session_day,
        journal_item=journal_item,
        review=review,
        finalized=finalized,
    )
    if not note_id:
        note_id = _find_existing_note_id(profile_key, session_day) or ""
    if note_id:
        try:
            updated = notes_service.update_note(
                note_id,
                {
                    "title": title,
                    "body": body,
                    "tags": tags,
                    "owner": AI_NOTE_OWNER,
                    "priority": "high" if finalized and review and review.get("applied_changes") else "medium",
                    "note_type": "risk_review",
                    "completed": bool(finalized),
                },
            )
            journal_item["note_id"] = updated.get("id") or note_id
            runtime["ai_last_note_id"] = journal_item["note_id"]
            return str(journal_item["note_id"])
        except Exception:
            note_id = ""
    try:
        created = notes_service.create_note(
            title=title,
            body=body,
            tags=tags,
            owner=AI_NOTE_OWNER,
            priority="high" if finalized and review and review.get("applied_changes") else "medium",
            note_type="risk_review",
            completed=bool(finalized),
        )
    except Exception:
        return None
    journal_item["note_id"] = created.get("id")
    runtime["ai_last_note_id"] = created.get("id")
    return str(created.get("id") or "").strip() or None


def capture_trade_automation_ai_observation(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    now: datetime | None = None,
    cycle_id: str | None = None,
) -> dict[str, Any] | None:
    settings_state = dict(state.get("settings") or {})
    if not _coerce_bool(settings_state.get("ai_daily_review_enabled"), True):
        return None
    now = now or _utc_now()
    session_day = session_day_for(now)
    runtime = state.setdefault("runtime", {})
    journal = dict(runtime.get("ai_daily_journal") or {})
    item = dict(journal.get(session_day) or {})
    item.setdefault("session_day", session_day)
    item.setdefault("profile_key", profile_key)
    item.setdefault("linked_account_id", getattr(linked_account, "id", None))
    item.setdefault("started_at", _serialize_datetime(now))
    item["last_observation_at"] = _serialize_datetime(now)
    observations = [obs for obs in list(item.get("observations") or []) if isinstance(obs, dict)]
    observations.append(_observation_from_state(state, now=now, cycle_id=cycle_id))
    item["observations"] = observations[-AI_DAILY_OBSERVATION_LIMIT:]
    item["summary"] = _summarize_daily_journal_item(item)
    journal[session_day] = item
    for stale_day in sorted(journal.keys())[:-AI_DAILY_JOURNAL_LIMIT]:
        journal.pop(stale_day, None)
    runtime["ai_daily_journal"] = journal
    runtime["ai_last_observation_at"] = _serialize_datetime(now)
    _sync_daily_note(
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        session_day=session_day,
        journal_item=item,
        finalized=False,
    )
    return serialize_value(item)


def _calibration_summary(settings_state: dict[str, Any]) -> dict[str, Any]:
    tickers = [str(item or "").strip().upper() for item in list(settings_state.get("tickers") or []) if str(item or "").strip()]
    interval = str(settings_state.get("interval") or "5m").strip().lower() or "5m"
    items: list[dict[str, Any]] = []
    for ticker in tickers[:4]:
        try:
            summary = sdm.journal_probability_calibration_summary(ticker, interval)
        except Exception:
            continue
        items.append(
            {
                "ticker": ticker,
                "resolved_count": _coerce_int(summary.get("resolved_count"), 0),
                "empirical_hit_rate": summary.get("empirical_hit_rate"),
                "average_error": summary.get("average_error"),
                "calibration_scope": summary.get("calibration_scope"),
            }
        )
    resolved = sum(_coerce_int(item.get("resolved_count"), 0) for item in items)
    errors = [
        abs(_coerce_float(item.get("average_error"), 0.0))
        for item in items
        if item.get("average_error") is not None and _coerce_int(item.get("resolved_count"), 0) > 0
    ]
    return {
        "resolved_count": resolved,
        "average_abs_error": float(sum(errors) / len(errors)) if errors else None,
        "items": items,
    }


def _collect_review_evidence(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    session_day: str,
) -> dict[str, Any]:
    settings_state = dict(state.get("settings") or {})
    runtime = dict(state.get("runtime") or {})
    journal = dict(runtime.get("ai_daily_journal") or {})
    journal_item = dict(journal.get(session_day) or {"session_day": session_day, "observations": []})
    owned_closed = _owned_automation_rows(sdm.read_closed_trades(), tenant_id=str(tenant.id), profile_key=profile_key)
    day_closed = _closed_rows_for_session(owned_closed, session_day=session_day)
    analytics = sdm.performance_analytics(day_closed)
    events = _recent_automation_order_events(db, tenant=tenant, profile_key=profile_key, session_day=session_day)
    slippage_values = [abs(_coerce_float(item.get("slippage_bps"), 0.0)) for item in events if item.get("slippage_bps") is not None]
    observations = [item for item in list(journal_item.get("observations") or []) if isinstance(item, dict)]
    observation_summary = _summarize_daily_journal_item(journal_item)
    guardrail_reasons = Counter(
        str((obs.get("guardrail") or {}).get("reason") or (obs.get("decision") or {}).get("reason") or "")
        for obs in observations
    )
    guardrail_reasons.pop("", None)
    rejection_reasons = Counter(
        str((obs.get("rejection") or {}).get("reason") or (obs.get("decision") or {}).get("reason") or "")
        for obs in observations
    )
    rejection_reasons.pop("", None)
    closed_pnl = (
        float(pd.to_numeric(day_closed.get("realized_pnl"), errors="coerce").fillna(0.0).sum())
        if not day_closed.empty and "realized_pnl" in day_closed.columns
        else 0.0
    )
    return {
        "session_day": session_day,
        "settings": serialize_value(settings_state),
        "journal_item": serialize_value(journal_item),
        "observation_summary": observation_summary,
        "closed_trade_count": int(len(day_closed)),
        "realized_pnl": closed_pnl,
        "analytics": serialize_value(analytics),
        "loss_streak": _count_recent_loss_streak(day_closed),
        "recent_order_events": serialize_value(events[:20]),
        "slippage": {
            "sample_count": len(slippage_values),
            "average_abs_bps": float(sum(slippage_values) / len(slippage_values)) if slippage_values else None,
            "worst_abs_bps": max(slippage_values) if slippage_values else None,
        },
        "guardrail_reasons": guardrail_reasons.most_common(8),
        "rejection_reasons": rejection_reasons.most_common(8),
        "calibration": _calibration_summary(settings_state),
        "accuracy_calibration": serialize_value(runtime.get("accuracy_calibration_last_report") or {}),
    }


def _build_objective_scores(evidence: dict[str, Any]) -> dict[str, float]:
    analytics = dict(evidence.get("analytics") or {})
    slippage = dict(evidence.get("slippage") or {})
    observation_summary = dict(evidence.get("observation_summary") or {})
    calibration = dict(evidence.get("calibration") or {})
    accuracy_calibration = dict(evidence.get("accuracy_calibration") or {})
    closed_count = _coerce_int(evidence.get("closed_trade_count"), 0)
    realized_pnl = _coerce_float(evidence.get("realized_pnl"), 0.0)
    expectancy = _coerce_float(analytics.get("expectancy"), 0.0)
    win_rate = _coerce_float(analytics.get("win_rate"), 0.0)
    profit_factor = _coerce_float(analytics.get("profit_factor"), 0.0)
    if closed_count <= 0:
        return_score = 50.0
    else:
        return_score = 50.0
        return_score += max(min(expectancy / 2.0, 20.0), -20.0)
        return_score += max(min(realized_pnl / 20.0, 15.0), -15.0)
        return_score += max(min((win_rate - 0.5) * 40.0, 12.0), -12.0)
        return_score += 8.0 if profit_factor >= 1.5 else (-8.0 if profit_factor and profit_factor < 1.0 else 0.0)
    error_count = _coerce_int(observation_summary.get("error_count"), 0)
    bad_count = _coerce_int(observation_summary.get("bad_count"), 0)
    avg_slippage = slippage.get("average_abs_bps")
    worst_slippage = slippage.get("worst_abs_bps")
    risk_score = 100.0
    risk_score -= min(bad_count * 8.0, 32.0)
    risk_score -= min(error_count * 15.0, 30.0)
    risk_score -= min(max(_coerce_int(evidence.get("loss_streak"), 0) - 1, 0) * 12.0, 36.0)
    if realized_pnl < 0:
        risk_score -= min(abs(realized_pnl) / 20.0, 25.0)
    if avg_slippage is not None:
        risk_score -= min(max(_coerce_float(avg_slippage) - 10.0, 0.0) * 1.2, 25.0)
    if worst_slippage is not None:
        risk_score -= min(max(_coerce_float(worst_slippage) - 25.0, 0.0) * 0.6, 20.0)
    calibration_error = calibration.get("average_abs_error")
    decision_pnl_accuracy = accuracy_calibration.get("decision_pnl_accuracy")
    decision_confidence_error = accuracy_calibration.get("confidence_error")
    if calibration_error is None:
        accuracy_score = 50.0 if closed_count == 0 else max(35.0, min(90.0, 50.0 + ((win_rate - 0.5) * 60.0)))
    else:
        accuracy_score = max(0.0, min(100.0, 100.0 - (_coerce_float(calibration_error) * 100.0)))
        if closed_count:
            accuracy_score = (accuracy_score * 0.65) + (max(0.0, min(win_rate * 100.0, 100.0)) * 0.35)
    if decision_pnl_accuracy is not None:
        accuracy_score = (accuracy_score * 0.45) + (_coerce_float(decision_pnl_accuracy, 50.0) * 0.55)
    if decision_confidence_error is not None and _coerce_float(decision_confidence_error, 0.0) > 0.40:
        accuracy_score -= min((_coerce_float(decision_confidence_error) - 0.40) * 80.0, 20.0)
    return_score = max(0.0, min(100.0, return_score))
    risk_score = max(0.0, min(100.0, risk_score))
    accuracy_score = max(0.0, min(100.0, accuracy_score))
    return {
        "return_score": round(return_score, 2),
        "risk_score": round(risk_score, 2),
        "accuracy_score": round(accuracy_score, 2),
        "overall_score": round((return_score * 0.4) + (risk_score * 0.35) + (accuracy_score * 0.25), 2),
    }


def _is_same_value(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        return abs(_coerce_float(left) - _coerce_float(right)) < 0.000001
    return left == right


def _bounded_numeric_target(
    settings_state: dict[str, Any],
    key: str,
    target: float,
    *,
    max_step_pct: float,
) -> float | int:
    value_type, minimum, maximum = AI_TUNABLE_LIMITS[key]
    before = _coerce_float(settings_state.get(key), float(minimum))
    target = max(float(minimum), min(float(maximum), float(target)))
    if before <= 0:
        stepped = target
    else:
        max_delta = max(abs(before) * (max_step_pct / 100.0), 1.0 if value_type == "int" else 0.01)
        delta = max(-max_delta, min(max_delta, target - before))
        stepped = before + delta
    stepped = max(float(minimum), min(float(maximum), stepped))
    if value_type == "int":
        if stepped > before:
            return int(max(before + 1, round(stepped)))
        if stepped < before:
            return int(min(before - 1, round(stepped)))
        return int(round(stepped))
    return round(float(stepped), 6)


def _add_change(
    changes: list[dict[str, Any]],
    settings_state: dict[str, Any],
    key: str,
    target: Any,
    *,
    reason: str,
    evidence: str,
    confidence: float,
    max_step_pct: float,
) -> None:
    if key in AI_NEVER_TUNE_FIELDS or key not in AI_TUNABLE_LIMITS:
        return
    value_type = AI_TUNABLE_LIMITS[key][0]
    if value_type in {"float", "int"}:
        after = _bounded_numeric_target(settings_state, key, float(target), max_step_pct=max_step_pct)
    elif value_type == "bool":
        after = bool(target)
    elif value_type == "enum":
        after = str(target).strip().lower()
        if key == "order_type" and after not in {"market", "limit"}:
            return
    else:
        return
    before = settings_state.get(key)
    if _is_same_value(before, after):
        return
    if any(item.get("field") == key for item in changes):
        return
    changes.append(
        {
            "field": key,
            "before": serialize_value(before),
            "after": serialize_value(after),
            "reason": reason,
            "evidence": evidence,
            "confidence": round(max(0.0, min(float(confidence), 1.0)), 2),
        }
    )


def generate_ai_settings_patch(
    *,
    settings_state: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    settings_state = dict(settings_state or {})
    ai_settings = normalize_ai_review_settings(settings_state)
    max_changes = int(ai_settings["ai_max_daily_setting_changes"])
    max_step_pct = float(ai_settings["ai_max_step_pct"])
    min_trades = int(ai_settings["ai_review_min_trades"])
    analytics = dict(evidence.get("analytics") or {})
    slippage = dict(evidence.get("slippage") or {})
    observation_summary = dict(evidence.get("observation_summary") or {})
    closed_count = _coerce_int(evidence.get("closed_trade_count"), 0)
    realized_pnl = _coerce_float(evidence.get("realized_pnl"), 0.0)
    loss_streak = _coerce_int(evidence.get("loss_streak"), 0)
    win_rate = _coerce_float(analytics.get("win_rate"), 0.0)
    expectancy = _coerce_float(analytics.get("expectancy"), 0.0)
    profit_factor = _coerce_float(analytics.get("profit_factor"), 0.0)
    avg_slippage = slippage.get("average_abs_bps")
    worst_slippage = slippage.get("worst_abs_bps")
    accuracy_calibration = dict(evidence.get("accuracy_calibration") or {})
    decision_pnl_accuracy = accuracy_calibration.get("decision_pnl_accuracy")
    decision_confidence_error = accuracy_calibration.get("confidence_error")
    rejection_reasons = Counter(dict(evidence.get("rejection_reasons") or []))
    guardrail_reasons = Counter(dict(evidence.get("guardrail_reasons") or []))
    changes: list[dict[str, Any]] = []
    rationale = ""
    hard_safety_event = bool(
        realized_pnl < 0
        or loss_streak >= max(2, _coerce_int(settings_state.get("max_consecutive_losses"), 3) - 1)
        or observation_summary.get("error_count", 0)
        or guardrail_reasons
        or (avg_slippage is not None and _coerce_float(avg_slippage) > 20.0)
        or (worst_slippage is not None and _coerce_float(worst_slippage) > 40.0)
        or (decision_pnl_accuracy is not None and _coerce_float(decision_pnl_accuracy) < 45.0)
        or (decision_confidence_error is not None and _coerce_float(decision_confidence_error) > 0.45)
    )

    if max_changes <= 0:
        return {
            "changes": [],
            "no_change_rationale": "AI auto-adjustment is configured with zero allowed setting changes.",
        }

    if closed_count < min_trades and not hard_safety_event:
        return {
            "changes": [],
            "no_change_rationale": (
                f"Only {closed_count} closed automation trade(s) were available; "
                f"{min_trades} are required before changing non-safety settings."
            ),
        }

    if realized_pnl < 0 or loss_streak >= 2 or "daily_loss_lock" in guardrail_reasons:
        _add_change(
            changes,
            settings_state,
            "risk_percent",
            _coerce_float(settings_state.get("risk_percent"), 0.5) * 0.8,
            reason="Reduce per-trade risk after same-day losses.",
            evidence=f"Session PnL {realized_pnl:.2f}, loss streak {loss_streak}.",
            confidence=0.82,
            max_step_pct=max_step_pct,
        )
        _add_change(
            changes,
            settings_state,
            "max_daily_entries",
            _coerce_int(settings_state.get("max_daily_entries"), 3) - 1,
            reason="Lower the daily entry cap until outcomes stabilize.",
            evidence=f"Session PnL {realized_pnl:.2f}.",
            confidence=0.76,
            max_step_pct=max_step_pct,
        )
        _add_change(
            changes,
            settings_state,
            "cooldown_minutes",
            _coerce_int(settings_state.get("cooldown_minutes"), 20) * 1.2 + 5,
            reason="Increase cooldown to reduce repeat entries into the same weak tape.",
            evidence=f"Loss streak {loss_streak}.",
            confidence=0.72,
            max_step_pct=max_step_pct,
        )

    if observation_summary.get("error_count", 0):
        _add_change(
            changes,
            settings_state,
            "max_daily_entries",
            _coerce_int(settings_state.get("max_daily_entries"), 3) - 1,
            reason="Reduce entry load after worker errors.",
            evidence=f"{observation_summary.get('error_count')} cycle error(s) observed.",
            confidence=0.68,
            max_step_pct=max_step_pct,
        )
        _add_change(
            changes,
            settings_state,
            "cooldown_minutes",
            _coerce_int(settings_state.get("cooldown_minutes"), 20) * 1.2 + 5,
            reason="Give the worker more time between cycles after errors.",
            evidence=f"{observation_summary.get('error_count')} cycle error(s) observed.",
            confidence=0.64,
            max_step_pct=max_step_pct,
        )

    if (
        (decision_pnl_accuracy is not None and _coerce_float(decision_pnl_accuracy) < 45.0)
        or (decision_confidence_error is not None and _coerce_float(decision_confidence_error) > 0.45)
    ):
        _add_change(
            changes,
            settings_state,
            "min_edge_to_cost_ratio",
            _coerce_float(settings_state.get("min_edge_to_cost_ratio"), 2.5) * 1.15,
            reason="Tighten edge requirements after weak decision-PnL calibration.",
            evidence=f"Decision-PnL accuracy {decision_pnl_accuracy}, confidence error {decision_confidence_error}.",
            confidence=0.82,
            max_step_pct=max_step_pct,
        )
        _add_change(
            changes,
            settings_state,
            "cycle_entry_rank_limit",
            _coerce_int(settings_state.get("cycle_entry_rank_limit"), 2) - 1,
            reason="Concentrate entries until calibrated decision quality improves.",
            evidence=f"Decision-PnL accuracy {decision_pnl_accuracy}.",
            confidence=0.76,
            max_step_pct=max_step_pct,
        )

    if avg_slippage is not None and _coerce_float(avg_slippage) > 20.0 or worst_slippage is not None and _coerce_float(worst_slippage) > 40.0:
        if str(settings_state.get("order_type") or "").strip().lower() == "market":
            _add_change(
                changes,
                settings_state,
                "order_type",
                "limit",
                reason="Switch to limit routing after poor fill drift.",
                evidence=f"Average slippage {avg_slippage}, worst {worst_slippage}.",
                confidence=0.86,
                max_step_pct=max_step_pct,
            )
        _add_change(
            changes,
            settings_state,
            "min_edge_to_cost_ratio",
            _coerce_float(settings_state.get("min_edge_to_cost_ratio"), 2.5) * 1.15,
            reason="Require more edge to compensate for observed execution cost.",
            evidence=f"Average slippage {avg_slippage}, worst {worst_slippage}.",
            confidence=0.78,
            max_step_pct=max_step_pct,
        )
        _add_change(
            changes,
            settings_state,
            "max_spread_bps",
            _coerce_float(settings_state.get("max_spread_bps"), risk_control_service.DEFAULT_RISK_CONTROL_SETTINGS["max_spread_bps"]) * 0.85,
            reason="Tighten spread acceptance after high slippage.",
            evidence=f"Average slippage {avg_slippage}, worst {worst_slippage}.",
            confidence=0.72,
            max_step_pct=max_step_pct,
        )

    if rejection_reasons.get("missing_spread") or rejection_reasons.get("missing_average_dollar_volume"):
        _add_change(
            changes,
            settings_state,
            "require_liquidity_fields",
            True,
            reason="Keep liquidity telemetry mandatory when missing fields blocked candidates.",
            evidence="Missing spread or average-dollar-volume fields were observed.",
            confidence=0.7,
            max_step_pct=max_step_pct,
        )
    if rejection_reasons.get("edge_cost_ratio_too_low") or rejection_reasons.get("missing_edge"):
        _add_change(
            changes,
            settings_state,
            "require_edge_fields",
            True,
            reason="Require explicit edge telemetry when edge quality is the recurring blocker.",
            evidence="Edge-cost weakness was observed in candidate rejection telemetry.",
            confidence=0.68,
            max_step_pct=max_step_pct,
        )
        _add_change(
            changes,
            settings_state,
            "min_edge_to_cost_ratio",
            _coerce_float(settings_state.get("min_edge_to_cost_ratio"), 2.5) * 1.1,
            reason="Tighten minimum edge after repeated weak edge-cost signals.",
            evidence="Edge-cost weakness was observed in candidate rejection telemetry.",
            confidence=0.66,
            max_step_pct=max_step_pct,
        )

    if "open_notional_cap" in guardrail_reasons or "gross_exposure_cap" in rejection_reasons:
        _add_change(
            changes,
            settings_state,
            "max_gross_leverage",
            _coerce_float(settings_state.get("max_gross_leverage"), 1.5) * 0.9,
            reason="Reduce gross exposure after exposure cap pressure.",
            evidence="Open notional or gross exposure cap was reached.",
            confidence=0.74,
            max_step_pct=max_step_pct,
        )
        _add_change(
            changes,
            settings_state,
            "max_single_position_pct",
            _coerce_float(settings_state.get("max_single_position_pct"), 12.0) * 0.9,
            reason="Lower single-position concentration after exposure cap pressure.",
            evidence="Open notional or gross exposure cap was reached.",
            confidence=0.7,
            max_step_pct=max_step_pct,
        )

    strong_sample = (
        closed_count >= min_trades
        and closed_count > 0
        and realized_pnl > 0
        and expectancy > 0
        and win_rate >= 0.55
        and profit_factor >= 1.5
        and not hard_safety_event
        and (decision_pnl_accuracy is None or _coerce_float(decision_pnl_accuracy) >= 65.0)
        and (decision_confidence_error is None or _coerce_float(decision_confidence_error) <= 0.35)
        and (avg_slippage is None or _coerce_float(avg_slippage) <= 12.0)
        and (worst_slippage is None or _coerce_float(worst_slippage) <= 25.0)
    )
    if strong_sample:
        _add_change(
            changes,
            settings_state,
            "risk_percent",
            min(_coerce_float(settings_state.get("risk_percent"), 0.5) * 1.1, 1.0),
            reason="Cautiously increase risk after a clean positive sample.",
            evidence=f"{closed_count} closes, win rate {win_rate:.2f}, profit factor {profit_factor:.2f}.",
            confidence=0.7,
            max_step_pct=max_step_pct,
        )
        _add_change(
            changes,
            settings_state,
            "max_daily_entries",
            min(_coerce_int(settings_state.get("max_daily_entries"), 3) + 1, 5),
            reason="Allow one more daily entry after clean execution and positive expectancy.",
            evidence=f"{closed_count} closes, expectancy {expectancy:.2f}.",
            confidence=0.64,
            max_step_pct=max_step_pct,
        )

    changes = changes[:max_changes]
    if not changes:
        rationale = "The review found no setting change with enough evidence to improve return, risk, or accuracy."
    return {"changes": serialize_value(changes), "no_change_rationale": rationale}


def _apply_settings_changes(settings_state: dict[str, Any], changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    for change in changes:
        key = str(change.get("field") or "").strip()
        if key in AI_NEVER_TUNE_FIELDS or key not in AI_TUNABLE_LIMITS:
            continue
        settings_state[key] = change.get("after")
        applied.append(serialize_value(change))
    return applied


def _build_review_narrative(evidence: dict[str, Any], objective_scores: dict[str, float]) -> dict[str, Any]:
    observation_summary = dict(evidence.get("observation_summary") or {})
    analytics = dict(evidence.get("analytics") or {})
    slippage = dict(evidence.get("slippage") or {})
    accuracy_calibration = dict(evidence.get("accuracy_calibration") or {})
    worked: list[str] = []
    weaknesses: list[str] = []
    if _coerce_float(evidence.get("realized_pnl"), 0.0) > 0:
        worked.append(f"Positive realized PnL of {_coerce_float(evidence.get('realized_pnl'), 0.0):.2f}.")
    if _coerce_int(observation_summary.get("opened_count"), 0) > 0:
        worked.append(f"Automation opened {_coerce_int(observation_summary.get('opened_count'), 0)} trade(s).")
    if _coerce_int(observation_summary.get("managed_count"), 0) > 0:
        worked.append(f"Position management acted {_coerce_int(observation_summary.get('managed_count'), 0)} time(s).")
    if _coerce_int(observation_summary.get("error_count"), 0) == 0:
        worked.append("No worker errors were recorded.")
    if _coerce_float(accuracy_calibration.get("decision_pnl_accuracy"), 0.0) >= 65.0:
        worked.append(
            f"Decision-PnL accuracy is calibrated at {_coerce_float(accuracy_calibration.get('decision_pnl_accuracy')):.0f}."
        )
    if _coerce_float(evidence.get("realized_pnl"), 0.0) < 0:
        weaknesses.append(f"Negative realized PnL of {_coerce_float(evidence.get('realized_pnl'), 0.0):.2f}.")
    if _coerce_int(evidence.get("loss_streak"), 0) > 1:
        weaknesses.append(f"Loss streak reached {_coerce_int(evidence.get('loss_streak'), 0)}.")
    if _coerce_int(observation_summary.get("bad_count"), 0) > 0:
        weaknesses.append(f"{_coerce_int(observation_summary.get('bad_count'), 0)} negative cycle observation(s).")
    if slippage.get("average_abs_bps") is not None and _coerce_float(slippage.get("average_abs_bps")) > 20:
        weaknesses.append(f"Average slippage was {_coerce_float(slippage.get('average_abs_bps')):.1f} bps.")
    if accuracy_calibration.get("decision_pnl_accuracy") is not None and _coerce_float(
        accuracy_calibration.get("decision_pnl_accuracy")
    ) < 45.0:
        weaknesses.append(
            f"Decision-PnL accuracy is weak at {_coerce_float(accuracy_calibration.get('decision_pnl_accuracy')):.0f}."
        )
    if not worked:
        worked.append("No confirmed strength was recorded; the optimizer stayed conservative.")
    if not weaknesses:
        weaknesses.append("No hard weakness was found.")
    return {
        "what_worked": worked[:6],
        "weaknesses": weaknesses[:6],
        "summary": (
            f"Return {objective_scores['return_score']:.0f}, risk {objective_scores['risk_score']:.0f}, "
            f"accuracy {objective_scores['accuracy_score']:.0f}; "
            f"win rate {_coerce_float(analytics.get('win_rate'), 0.0) * 100:.0f}%."
        ),
    }


def build_ai_review_snapshot(state: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or _utc_now()
    runtime = dict(state.get("runtime") or {})
    settings_state = dict(state.get("settings") or {})
    session_day, review_window_open = review_session_day_for(now)
    journal = dict(runtime.get("ai_daily_journal") or {})
    current_journal = dict(journal.get(session_day) or journal.get(session_day_for(now)) or {})
    summary = _summarize_daily_journal_item(current_journal) if current_journal else {}
    last_review = runtime.get("ai_last_review") or {}
    last_adjustment = runtime.get("ai_last_adjustment") or {}
    return {
        "enabled": _coerce_bool(settings_state.get("ai_daily_review_enabled"), True),
        "auto_adjust_enabled": _coerce_bool(settings_state.get("ai_auto_adjust_enabled"), True),
        "adjust_live_enabled": _coerce_bool(settings_state.get("ai_adjust_live_enabled"), True),
        "review_session_day": session_day,
        "review_window_open": review_window_open,
        "last_observation_at": runtime.get("ai_last_observation_at"),
        "last_review_session_day": runtime.get("ai_last_review_session_day"),
        "last_review_at": runtime.get("ai_last_review_at"),
        "related_note_id": current_journal.get("note_id") or runtime.get("ai_last_note_id"),
        "current_journal_summary": serialize_value(summary),
        "last_review": serialize_value(last_review),
        "last_adjustment": serialize_value(last_adjustment),
        "history": serialize_value(list(runtime.get("ai_review_history") or [])[:AI_REVIEW_HISTORY_LIMIT]),
    }


def run_trade_automation_ai_review(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    forced: bool = False,
    actor: Any = None,
    now: datetime | None = None,
    live_route_allowed: bool = True,
) -> dict[str, Any]:
    now = now or _utc_now()
    settings_state = state.setdefault("settings", {})
    runtime = state.setdefault("runtime", {})
    if not _coerce_bool(settings_state.get("ai_daily_review_enabled"), True):
        return {"status": "skipped", "reason": "ai_review_disabled"}
    session_day, review_window_open = review_session_day_for(now, forced=forced)
    if not review_window_open:
        return {"status": "skipped", "reason": "review_window_not_open", "session_day": session_day}
    if not forced and runtime.get("ai_last_review_session_day") == session_day:
        return {"status": "skipped", "reason": "already_reviewed", "session_day": session_day}

    evidence = _collect_review_evidence(db, tenant=tenant, state=state, profile_key=profile_key, session_day=session_day)
    objective_scores = _build_objective_scores(evidence)
    patch = generate_ai_settings_patch(settings_state=settings_state, evidence=evidence)
    proposed_changes = list(patch.get("changes") or [])
    skipped_changes: list[dict[str, Any]] = []
    applied_changes: list[dict[str, Any]] = []
    live_profile = str(profile_key or "").strip().lower() == AI_PERSONAL_LIVE_PROFILE
    auto_adjust_enabled = _coerce_bool(settings_state.get("ai_auto_adjust_enabled"), True)
    live_adjust_enabled = _coerce_bool(settings_state.get("ai_adjust_live_enabled"), True)
    can_apply = auto_adjust_enabled
    skip_reason = ""
    if live_profile and not live_adjust_enabled:
        can_apply = False
        skip_reason = "Live AI adjustment is disabled."
    if live_profile and not live_route_allowed:
        can_apply = False
        skip_reason = "Live rollout gate is not cleared."
    if not auto_adjust_enabled:
        skip_reason = "AI auto-adjustment is disabled."
    if can_apply:
        applied_changes = _apply_settings_changes(settings_state, proposed_changes)
    else:
        skipped_changes = [
            {
                **dict(change),
                "skip_reason": skip_reason or "Auto-adjustment is not allowed for this profile.",
            }
            for change in proposed_changes
        ]

    narrative = _build_review_narrative(evidence, objective_scores)
    review = {
        "status": "reviewed",
        "session_day": session_day,
        "reviewed_at": _serialize_datetime(now),
        "profile_key": profile_key,
        "linked_account_id": getattr(linked_account, "id", None),
        "objective_scores": objective_scores,
        "evidence": serialize_value(evidence),
        "what_worked": narrative["what_worked"],
        "weaknesses": narrative["weaknesses"],
        "summary": narrative["summary"],
        "applied_changes": serialize_value(applied_changes),
        "skipped_changes": serialize_value(skipped_changes),
        "proposed_changes": serialize_value(proposed_changes),
        "no_change_rationale": patch.get("no_change_rationale") or "",
        "auto_adjust_enabled": auto_adjust_enabled,
        "live_adjust_enabled": live_adjust_enabled,
        "live_route_allowed": live_route_allowed,
    }
    journal = dict(runtime.get("ai_daily_journal") or {})
    journal_item = dict(evidence.get("journal_item") or journal.get(session_day) or {"session_day": session_day, "observations": []})
    note_id = _sync_daily_note(
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        session_day=session_day,
        journal_item=journal_item,
        review=review,
        finalized=True,
    )
    if note_id:
        review["note_id"] = note_id
        journal_item["note_id"] = note_id
    journal_item["finalized_at"] = _serialize_datetime(now)
    journal_item["review"] = serialize_value(review)
    journal[session_day] = journal_item
    for stale_day in sorted(journal.keys())[:-AI_DAILY_JOURNAL_LIMIT]:
        journal.pop(stale_day, None)
    runtime["ai_daily_journal"] = journal
    runtime["ai_last_review_session_day"] = session_day
    runtime["ai_last_review_at"] = _serialize_datetime(now)
    runtime["ai_last_review"] = serialize_value(review)
    adjustment = {
        "at": _serialize_datetime(now),
        "session_day": session_day,
        "applied_changes": serialize_value(applied_changes),
        "skipped_changes": serialize_value(skipped_changes),
        "no_change_rationale": review.get("no_change_rationale"),
    }
    runtime["ai_last_adjustment"] = adjustment
    history = list(runtime.get("ai_review_history") or [])
    history.insert(0, serialize_value(review))
    runtime["ai_review_history"] = history[:AI_REVIEW_HISTORY_LIMIT]
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.ai_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
                "objective_scores": objective_scores,
                "applied_change_count": len(applied_changes),
                "skipped_change_count": len(skipped_changes),
                "note_id": note_id,
            },
        )
        if applied_changes:
            record_audit_event(
                db,
                event_type="trade_automation.ai_adjusted",
                tenant=tenant,
                user=actor,
                payload={
                    "profile_key": profile_key,
                    "linked_account_id": getattr(linked_account, "id", None),
                    "session_day": session_day,
                    "applied_changes": serialize_value(applied_changes),
                    "objective_scores": objective_scores,
                },
            )
    return serialize_value(review)
