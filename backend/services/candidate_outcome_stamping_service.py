from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from backend.services.serialization import serialize_value

DEFAULT_ROOT = Path(".")
OUTCOME_VERSION = "candidate_outcome_baseline_v1"
BASELINE_DEFINITION_VERSION = "baseline_transparent_v1"
DEFAULT_HORIZONS_MINUTES = (5, 15, 30)
PRIMARY_BASELINE_ORDER = ("random_candidate_forward_return", "spy_forward_return")

SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "paper_only": True,
    "paper_route_only": True,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
    "can_change_broker_routes": False,
    "can_bypass_risk_gates": False,
    "can_clear_kill_switch": False,
    "can_change_ranking_weights": False,
    "mutation": "append_only_research_evidence",
}

SAFETY_NOTES: tuple[str, ...] = (
    "Research only. Does not affect trading.",
    "Paper-route evidence only.",
    "Does not place orders.",
    "Does not change broker routes.",
    "Does not bypass risk gates.",
    "Does not change ranking weights automatically.",
)

REQUIRED_PRE_MOVE_FIELDS = (
    "prediction_created_at",
    "predicted_direction",
    "prediction_horizon_minutes",
    "predicted_target_pct",
    "invalidation_level",
    "confidence",
    "engine",
    "setup_type",
    "score",
    "score_bucket",
    "regime",
    "spread_at_signal",
    "slippage_estimate_bps",
    "route",
    "experiment_version",
    "reward_formula_version",
    "baseline_definition_version",
    "feature_version",
    "sample_split",
)

SECTOR_ETF_BY_SECTOR = {
    "technology": "XLK",
    "tech": "XLK",
    "financials": "XLF",
    "financial": "XLF",
    "energy": "XLE",
    "healthcare": "XLV",
    "health_care": "XLV",
    "industrials": "XLI",
    "consumer_discretionary": "XLY",
    "consumer_staples": "XLP",
    "utilities": "XLU",
    "communication_services": "XLC",
    "materials": "XLB",
    "real_estate": "XLRE",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return float(parsed)


def safe_int(value: Any) -> int | None:
    parsed = safe_float(value)
    if parsed is None:
        return None
    return int(parsed)


def clean_text(value: Any, default: str | None = None) -> str | None:
    text = str(value or "").strip()
    return text or default


def first_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    for nested_key in (
        "opportunity_capture",
        "prediction_contract",
        "routeability",
        "quote_freshness",
        "market_possibility_engine",
        "realtime_alpha_ops",
        "adaptive_execution_intelligence",
        "scores",
        "risk",
    ):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            for key in keys:
                value = nested.get(key)
                if value is not None and value != "":
                    return value
    return None


def score_bucket(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 80:
        return "80_to_100"
    if score >= 60:
        return "60_to_80"
    if score >= 40:
        return "40_to_60"
    if score >= 20:
        return "20_to_40"
    return "0_to_20"


def reward_score_bucket(score: float | None) -> str:
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


def direction_sign(direction: Any) -> int | None:
    cleaned = str(direction or "").strip().lower()
    if cleaned in {"bullish", "long", "buy", "up", "higher", "call"}:
        return 1
    if cleaned in {"bearish", "short", "sell", "down", "lower", "put"}:
        return -1
    return None


def direction_from_payload(payload: dict[str, Any]) -> str | None:
    raw = first_value(payload, "predicted_direction", "forecast_direction", "accuracy_forecast_direction", "direction")
    if raw:
        sign = direction_sign(raw)
        if sign == 1:
            return "bullish"
        if sign == -1:
            return "bearish"
        return str(raw).strip()
    trade_decision = str(first_value(payload, "trade_decision", "action", "side") or "").strip().lower()
    if trade_decision in {"buy", "long", "call", "entry", "enter_long"}:
        return "bullish"
    if trade_decision in {"sell", "short", "put", "enter_short"}:
        return "bearish"
    return None


def normalize_confidence(value: Any) -> float | None:
    parsed = safe_float(value)
    if parsed is None:
        return None
    if parsed > 1.0:
        parsed = parsed / 100.0
    return round(max(0.0, min(1.0, parsed)), 6)


def normalize_horizon_minutes(value: Any) -> int | None:
    parsed = safe_int(value)
    if parsed and parsed > 0:
        return parsed
    text = str(value or "").strip().lower()
    if text.endswith("m") and text[:-1].isdigit():
        return int(text[:-1])
    return None


def candidate_declared_horizon(payload: dict[str, Any]) -> int | None:
    horizon = normalize_horizon_minutes(
        first_value(payload, "prediction_horizon_minutes", "horizon_minutes", "forecast_horizon_minutes")
    )
    if horizon:
        return horizon
    windows = payload.get("follow_up_windows")
    if isinstance(windows, list):
        parsed = [normalize_horizon_minutes(value) for value in windows]
        clean = [value for value in parsed if value]
        if 30 in clean:
            return 30
        if clean:
            return max(clean)
    return None


def candidate_horizons(payload: dict[str, Any]) -> list[int]:
    horizons = set(DEFAULT_HORIZONS_MINUTES)
    declared = candidate_declared_horizon(payload)
    if declared:
        horizons.add(declared)
    return sorted(horizons)


def candidate_symbol(payload: dict[str, Any]) -> str | None:
    symbol = clean_text(first_value(payload, "ticker", "symbol", "underlying_symbol"))
    return symbol.upper() if symbol else None


def candidate_timestamp(payload: dict[str, Any]) -> str | None:
    return clean_text(first_value(payload, "prediction_created_at", "scan_time", "timestamp", "observed_at", "created_at"))


def candidate_price(payload: dict[str, Any]) -> float | None:
    price = safe_float(
        first_value(
            payload,
            "price_at_signal",
            "reference_price",
            "trigger_price",
            "live_price",
            "current_price",
            "last_price",
            "close",
            "entry_price",
        )
    )
    return price if price and price > 0 else None


def quote_freshness_seconds(payload: dict[str, Any], created_at: datetime | None) -> float | None:
    explicit = safe_float(first_value(payload, "quote_freshness_seconds", "quote_age_seconds", "age_seconds"))
    if explicit is not None:
        return explicit
    quote_time = parse_datetime(first_value(payload, "quote_timestamp", "captured_at", "latest_bar_at", "bar_timestamp"))
    if created_at and quote_time:
        return round(max(0.0, (created_at - quote_time).total_seconds()), 3)
    return None


def enrich_candidate_lifecycle_row(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    row = dict(payload or {})
    created_at = candidate_timestamp(row)
    parsed_created = parse_datetime(created_at) or now
    score = safe_float(first_value(row, "score", "opportunity_score", "stage_one_score", "ranking_score", "setup_score"))
    opportunity = row.get("opportunity_capture") if isinstance(row.get("opportunity_capture"), dict) else {}
    routeability = row.get("routeability") if isinstance(row.get("routeability"), dict) else {}
    price = candidate_price(row)
    confidence = normalize_confidence(first_value(row, "confidence", "forecast_confidence", "ai_confidence"))
    if confidence is None and score is not None:
        confidence = normalize_confidence(score)
        row.setdefault("confidence_source", "score_proxy")

    row.setdefault("prediction_created_at", created_at)
    row.setdefault("predicted_direction", direction_from_payload(row))
    row.setdefault("prediction_horizon_minutes", candidate_declared_horizon(row))
    row.setdefault(
        "predicted_target_pct",
        safe_float(first_value(row, "predicted_target_pct", "target_return_pct", "target_pct", "expected_move_pct", "breakout_strength_pct")),
    )
    row.setdefault("invalidation_level", first_value(row, "invalidation_level", "invalidation_price", "invalid_if", "stop_price"))
    row.setdefault("confidence", confidence)
    row.setdefault("engine", clean_text(first_value(row, "engine", "desk_key", "strategy_desk_key"), "intraday_momentum"))
    row.setdefault("setup_type", clean_text(first_value(row, "setup_type", "opportunity_type", "stage", "type"), "unknown"))
    row.setdefault("score", score)
    row.setdefault("score_bucket", score_bucket(score))
    row.setdefault("reward_score_bucket", reward_score_bucket(score))
    row.setdefault("regime", clean_text(first_value(row, "regime", "market_regime", "regime_state", "session_regime", "session_phase")))
    row.setdefault("spread_at_signal", safe_float(first_value(row, "spread_at_signal", "spread_bps", "spread_estimate_bps", "quote_spread_bps")))
    row.setdefault("slippage_estimate_bps", safe_float(first_value(row, "slippage_estimate_bps", "estimated_slippage_bps", "slippage_bps")))
    row.setdefault("route", clean_text(first_value(row, "route", "execution_route", "automation_execution_intent"), None) or routeability.get("execution_route") or "broker_paper")
    row.setdefault("experiment_version", clean_text(first_value(row, "experiment_version"), "unassigned_live_observed"))
    row.setdefault("reward_formula_version", clean_text(first_value(row, "reward_formula_version"), "evidence_reward_prediction_contract_v1"))
    row.setdefault("baseline_definition_version", clean_text(first_value(row, "baseline_definition_version"), BASELINE_DEFINITION_VERSION))
    row.setdefault("feature_version", clean_text(first_value(row, "feature_version"), "candidate_lifecycle_v1"))
    row.setdefault("sample_split", clean_text(first_value(row, "sample_split"), "live_observed_unassigned"))
    row.setdefault("evidence_pool", clean_text(first_value(row, "evidence_pool"), "real_time_market_observed"))
    row.setdefault("paper_route_only", True)
    row.setdefault("paper_only", True)
    row.setdefault("price_at_signal", price)
    row.setdefault("reference_price", price)
    row.setdefault("quote_freshness_seconds", quote_freshness_seconds(row, parsed_created))
    if opportunity:
        row.setdefault("opportunity_type", opportunity.get("type") or opportunity.get("opportunity_type") or row.get("opportunity_type"))
        row.setdefault("predicted_target_pct", row.get("predicted_target_pct") or safe_float(opportunity.get("breakout_strength_pct")))
        row.setdefault("invalidation_level", row.get("invalidation_level") or opportunity.get("invalid_if"))

    missing = [field for field in REQUIRED_PRE_MOVE_FIELDS if row.get(field) in (None, "", [])]
    row["missing_pre_move_fields"] = sorted(set(missing))
    row["pre_move_contract_complete"] = not missing
    row["rewardable_candidate_contract"] = not missing
    return serialize_value(row)


def read_jsonl(path: Path, *, max_rows: int = 250000) -> list[dict[str, Any]]:
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


def append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    payloads = [serialize_value(row) for row in rows]
    if not payloads:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in payloads:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            written += 1
    return written


def latest_files(files: Iterable[Path], *, limit: int = 120) -> list[Path]:
    rows: list[tuple[float, Path]] = []
    for path in files:
        try:
            rows.append((path.stat().st_mtime, path))
        except OSError:
            continue
    return [path for _, path in sorted(rows, key=lambda item: item[0], reverse=True)[:limit]]


def candidate_lifecycle_files(root: Path, tenant_slug: str, *, limit: int = 120) -> list[Path]:
    base = root / "runtime-exports" / "candidate-lifecycle"
    if not base.exists():
        return []
    files = list(base.glob(f"*/{tenant_slug}.jsonl"))
    if not files:
        files = list(base.glob("*/candidate-diagnostics.jsonl"))
    return latest_files(files, limit=limit)


def candidate_outcome_files(root: Path, tenant_slug: str, *, limit: int = 120) -> list[Path]:
    base = root / "runtime-exports" / "candidate-outcomes"
    if not base.exists():
        return []
    files = list(base.glob(f"*/{tenant_slug}.jsonl"))
    if not files:
        files = list(base.glob("*/candidate-diagnostics.jsonl"))
    return latest_files(files, limit=limit)


def tenant_slug_from_user(current_user: Any) -> str:
    return (
        clean_text(getattr(current_user, "tenant_slug", None))
        or clean_text(getattr(current_user, "slug", None))
        or clean_text(getattr(current_user, "tenant_id", None))
        or "systematic-equities"
    )


def load_lifecycle_rows(root: Path, tenant_slug: str, *, max_rows_per_file: int = 250000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in reversed(candidate_lifecycle_files(root, tenant_slug)):
        for row in read_jsonl(path, max_rows=max_rows_per_file):
            if row.get("simulation_evidence") or str(row.get("evidence_pool") or "").lower() == "simulation_evidence":
                continue
            enriched = enrich_candidate_lifecycle_row(row)
            enriched["_source_file"] = str(path)
            rows.append(enriched)
    return rows


def load_outcome_rows(root: Path, tenant_slug: str, *, max_rows_per_file: int = 250000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in reversed(candidate_outcome_files(root, tenant_slug)):
        for row in read_jsonl(path, max_rows=max_rows_per_file):
            if row.get("simulation_evidence") or str(row.get("evidence_pool") or "").lower() == "simulation_evidence":
                continue
            row["_source_file"] = str(path)
            rows.append(row)
    return rows


def outcome_idempotency_key(candidate_lifecycle_id: str, horizon_minutes: int) -> str:
    return f"{candidate_lifecycle_id}|{int(horizon_minutes)}|{OUTCOME_VERSION}"


def outcome_record_id(candidate_lifecycle_id: str, horizon_minutes: int) -> str:
    digest = hashlib.sha1(outcome_idempotency_key(candidate_lifecycle_id, horizon_minutes).encode("utf-8")).hexdigest()[:16]
    return f"outcome_{digest}"


def candidate_day(row: dict[str, Any]) -> str:
    parsed = parse_datetime(candidate_timestamp(row))
    return (parsed or datetime.now(timezone.utc)).date().isoformat()


def build_price_timeline(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    timeline: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        symbol = candidate_symbol(row)
        timestamp = parse_datetime(candidate_timestamp(row))
        price = candidate_price(row)
        if not symbol or timestamp is None or price is None:
            continue
        timeline[symbol].append({"timestamp": timestamp, "price": price, "row": row})
    for symbol in list(timeline):
        timeline[symbol].sort(key=lambda item: item["timestamp"])
    return dict(timeline)


def find_price_at_or_after(timeline: list[dict[str, Any]], target_time: datetime) -> dict[str, Any] | None:
    for point in timeline:
        timestamp = point.get("timestamp")
        if isinstance(timestamp, datetime) and timestamp >= target_time:
            return point
    return None


def find_price_at_or_before(timeline: list[dict[str, Any]], target_time: datetime) -> dict[str, Any] | None:
    found = None
    for point in timeline:
        timestamp = point.get("timestamp")
        if isinstance(timestamp, datetime) and timestamp <= target_time:
            found = point
        if isinstance(timestamp, datetime) and timestamp > target_time:
            break
    return found


def return_pct(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start <= 0:
        return None
    return round(((float(end) - float(start)) / float(start)) * 100.0, 6)


def price_points_between(timeline: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    return [
        point
        for point in timeline
        if isinstance(point.get("timestamp"), datetime) and start <= point["timestamp"] <= end
    ]


def compute_candidate_forward_outcome(
    row: dict[str, Any],
    *,
    horizon_minutes: int,
    price_timeline: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    candidate_id = clean_text(row.get("candidate_lifecycle_id"))
    symbol = candidate_symbol(row)
    created_at = parse_datetime(candidate_timestamp(row))
    entry_price = candidate_price(row)
    missing_fields: list[str] = []
    if not candidate_id:
        missing_fields.append("candidate_lifecycle_id")
    if not symbol:
        missing_fields.append("symbol")
    if created_at is None:
        missing_fields.append("prediction_created_at")
    if entry_price is None:
        missing_fields.append("price_at_signal")
    if missing_fields:
        return {
            "available": False,
            "missing_fields": missing_fields,
            "reason": "Candidate cannot be stamped without an id, symbol, timestamp, and signal price.",
        }

    target_time = created_at + timedelta(minutes=int(horizon_minutes))
    timeline = price_timeline.get(symbol, [])
    observed = find_price_at_or_after(timeline, target_time)
    if observed is None:
        return {
            "available": False,
            "missing_fields": ["closed_horizon_price"],
            "reason": f"No observed {symbol} price exists at or after the {horizon_minutes} minute horizon; current price is not substituted.",
            "horizon_close_time": target_time.isoformat(),
        }

    future_price = safe_float(observed.get("price"))
    observed_at = observed.get("timestamp")
    actual = return_pct(entry_price, future_price)
    sign = direction_sign(row.get("predicted_direction")) or 1
    target_pct = safe_float(row.get("predicted_target_pct"))
    invalidation_level = safe_float(row.get("invalidation_level"))
    points = price_points_between(timeline, created_at, observed_at)
    signed_moves = [((safe_float(point.get("price")) or entry_price) - entry_price) / entry_price * 100.0 * sign for point in points if safe_float(point.get("price")) is not None]
    max_adverse = round(min(signed_moves), 6) if signed_moves else None
    hit_target = None
    time_to_target = None
    if target_pct is not None:
        hit_target = False
        for point in points:
            price = safe_float(point.get("price"))
            timestamp = point.get("timestamp")
            if price is None or not isinstance(timestamp, datetime):
                continue
            signed_move = ((price - entry_price) / entry_price) * 100.0 * sign
            if signed_move >= abs(target_pct):
                hit_target = True
                time_to_target = round((timestamp - created_at).total_seconds() / 60.0, 6)
                break
    hit_invalidation = None
    if invalidation_level is not None:
        hit_invalidation = any(
            (
                safe_float(point.get("price")) is not None
                and (
                    (sign == 1 and float(safe_float(point.get("price"))) <= invalidation_level)
                    or (sign == -1 and float(safe_float(point.get("price"))) >= invalidation_level)
                )
            )
            for point in points
        )

    return {
        "available": actual is not None,
        "missing_fields": [] if actual is not None else ["actual_forward_return"],
        "reason": "Forward return observed from later candidate lifecycle price evidence." if actual is not None else "Forward return could not be computed from available prices.",
        "actual_forward_return": actual,
        "actual_forward_return_observed_at": observed_at.isoformat() if isinstance(observed_at, datetime) else None,
        "actual_forward_price": future_price,
        "horizon_close_time": target_time.isoformat(),
        "max_adverse_excursion": max_adverse,
        "hit_target": hit_target,
        "hit_invalidation": hit_invalidation,
        "time_to_target_minutes": time_to_target,
    }


def compute_baseline_return(
    symbol: str,
    created_at: datetime,
    horizon_minutes: int,
    price_timeline: dict[str, list[dict[str, Any]]],
) -> tuple[float | None, list[str]]:
    timeline = price_timeline.get(symbol.upper(), [])
    start = find_price_at_or_before(timeline, created_at)
    end = find_price_at_or_after(timeline, created_at + timedelta(minutes=horizon_minutes))
    if start is None or end is None:
        return None, [f"{symbol.lower()}_price_series"]
    return return_pct(safe_float(start.get("price")), safe_float(end.get("price"))), []


def compute_random_candidate_baseline(
    row: dict[str, Any],
    *,
    horizon_minutes: int,
    price_timeline: dict[str, list[dict[str, Any]]],
    lifecycle_rows: list[dict[str, Any]],
) -> tuple[float | None, list[str]]:
    created_at = parse_datetime(candidate_timestamp(row))
    candidate_id = clean_text(row.get("candidate_lifecycle_id"))
    if created_at is None:
        return None, ["prediction_created_at"]
    values: list[float] = []
    for other in lifecycle_rows:
        if clean_text(other.get("candidate_lifecycle_id")) == candidate_id:
            continue
        other_created = parse_datetime(candidate_timestamp(other))
        if other_created is None:
            continue
        if abs((other_created - created_at).total_seconds()) > 30 * 60:
            continue
        outcome = compute_candidate_forward_outcome(
            other,
            horizon_minutes=horizon_minutes,
            price_timeline=price_timeline,
        )
        value = safe_float(outcome.get("actual_forward_return"))
        if value is not None:
            values.append(value)
    if not values:
        return None, ["random_candidate_peer_outcomes"]
    return round(mean(values), 6), []


def compute_strategy_baselines(
    row: dict[str, Any],
    *,
    horizon_minutes: int,
    actual_forward_return: float | None,
    price_timeline: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    created_at = parse_datetime(candidate_timestamp(row))
    symbol = candidate_symbol(row)
    missing: dict[str, list[str]] = {}
    baselines: dict[str, Any] = {
        "simple_momentum_forward_return": None,
        "simple_mean_reversion_forward_return": None,
        "simple_vwap_reclaim_forward_return": None,
        "opening_range_breakout_forward_return": None,
        "previous_close_drift_forward_return": None,
    }
    if not created_at or not symbol:
        for key in baselines:
            missing[key] = ["prediction_created_at", "symbol"]
        return {"values": baselines, "missing": missing}

    timeline = price_timeline.get(symbol, [])
    current_point = find_price_at_or_before(timeline, created_at)
    previous_point = find_price_at_or_before(timeline, created_at - timedelta(minutes=horizon_minutes))
    pre_return = return_pct(
        safe_float(previous_point.get("price")) if previous_point else None,
        safe_float(current_point.get("price")) if current_point else None,
    )
    if actual_forward_return is None:
        for key in ("simple_momentum_forward_return", "simple_mean_reversion_forward_return", "simple_vwap_reclaim_forward_return", "opening_range_breakout_forward_return"):
            missing[key] = ["actual_forward_return"]
    else:
        if pre_return is None:
            missing["simple_momentum_forward_return"] = ["pre_window_price"]
            missing["simple_mean_reversion_forward_return"] = ["pre_window_price"]
        else:
            baselines["simple_momentum_forward_return"] = actual_forward_return if pre_return >= 0 else 0.0
            baselines["simple_mean_reversion_forward_return"] = actual_forward_return if pre_return < 0 else 0.0
        setup = str(row.get("setup_type") or row.get("opportunity_type") or "").lower()
        stage = str(row.get("stage") or "").lower()
        if "vwap" in setup or "vwap" in stage:
            baselines["simple_vwap_reclaim_forward_return"] = actual_forward_return
        else:
            missing["simple_vwap_reclaim_forward_return"] = ["vwap_reclaim_setup"]
        if "breakout" in setup or "opening_range" in setup or "breakout" in stage:
            baselines["opening_range_breakout_forward_return"] = actual_forward_return
        else:
            missing["opening_range_breakout_forward_return"] = ["opening_range_breakout_setup"]

    previous_close = safe_float(first_value(row, "previous_close", "prior_close"))
    observed_price = None
    observed = find_price_at_or_after(timeline, created_at + timedelta(minutes=horizon_minutes))
    if observed:
        observed_price = safe_float(observed.get("price"))
    previous_close_return = return_pct(previous_close, observed_price)
    if previous_close_return is None:
        missing["previous_close_drift_forward_return"] = ["previous_close", "closed_horizon_price"]
    baselines["previous_close_drift_forward_return"] = previous_close_return
    return {"values": baselines, "missing": missing}


def compute_baselines(
    row: dict[str, Any],
    *,
    horizon_minutes: int,
    actual_forward_return: float | None,
    price_timeline: dict[str, list[dict[str, Any]]],
    lifecycle_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    created_at = parse_datetime(candidate_timestamp(row))
    baseline_values: dict[str, Any] = {}
    missing: dict[str, list[str]] = {}
    if created_at is None:
        return {
            "values": baseline_values,
            "baseline_forward_return": None,
            "available": False,
            "missing": {"all": ["prediction_created_at"]},
            "reason": "Baselines require a candidate timestamp.",
        }

    for symbol, key in (("SPY", "spy_forward_return"), ("QQQ", "qqq_forward_return")):
        value, missing_fields = compute_baseline_return(symbol, created_at, horizon_minutes, price_timeline)
        baseline_values[key] = value
        if missing_fields:
            missing[key] = missing_fields

    sector = str(first_value(row, "sector", "gics_sector", "market_sector") or "").strip().lower().replace(" ", "_")
    sector_symbol = SECTOR_ETF_BY_SECTOR.get(sector)
    if sector_symbol:
        value, missing_fields = compute_baseline_return(sector_symbol, created_at, horizon_minutes, price_timeline)
        baseline_values["sector_etf_forward_return"] = value
        baseline_values["sector_etf_symbol"] = sector_symbol
        if missing_fields:
            missing["sector_etf_forward_return"] = missing_fields
    else:
        baseline_values["sector_etf_forward_return"] = None
        missing["sector_etf_forward_return"] = ["sector"]

    random_value, random_missing = compute_random_candidate_baseline(
        row,
        horizon_minutes=horizon_minutes,
        price_timeline=price_timeline,
        lifecycle_rows=lifecycle_rows,
    )
    baseline_values["random_candidate_forward_return"] = random_value
    if random_missing:
        missing["random_candidate_forward_return"] = random_missing

    strategy = compute_strategy_baselines(
        row,
        horizon_minutes=horizon_minutes,
        actual_forward_return=actual_forward_return,
        price_timeline=price_timeline,
    )
    baseline_values.update(strategy["values"])
    missing.update(strategy["missing"])

    primary = None
    primary_source = None
    for key in PRIMARY_BASELINE_ORDER:
        value = safe_float(baseline_values.get(key))
        if value is not None:
            primary = value
            primary_source = key
            break
    return {
        "values": baseline_values,
        "baseline_forward_return": primary,
        "primary_baseline": primary_source,
        "available": primary is not None,
        "missing": missing,
        "reason": "Primary baseline is random candidate when available, otherwise SPY." if primary is not None else "No primary baseline is available; missing baselines are not fabricated.",
    }


def paper_execution_cost_fields(row: dict[str, Any], paper_trade_records: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    candidate_id = clean_text(row.get("candidate_lifecycle_id"))
    linked = None
    for trade in paper_trade_records or []:
        if not isinstance(trade, dict):
            continue
        if clean_text(trade.get("candidate_lifecycle_id") or trade.get("automation_candidate_id") or trade.get("signal_id")) == candidate_id:
            linked = trade
            break
    source = linked or row
    intended = safe_float(first_value(source, "intended_price", "expected_entry_price", "expected_price", "submitted_price", "price_at_signal"))
    fill = safe_float(first_value(source, "fill_price", "filled_price", "actual_fill_price", "broker_filled_avg_price"))
    slippage_bps = safe_float(first_value(source, "slippage_bps", "slippage_estimate_bps", "estimated_slippage_bps"))
    if slippage_bps is None and intended and fill:
        slippage_bps = round(((fill - intended) / intended) * 10000.0, 6)
    return {
        "spread_at_signal": safe_float(first_value(row, "spread_at_signal", "spread_bps", "spread_estimate_bps")),
        "quote_freshness_seconds": safe_float(first_value(row, "quote_freshness_seconds", "quote_age_seconds")),
        "expected_cost_estimate_bps": safe_float(first_value(row, "expected_cost_estimate_bps", "expected_cost_bps")),
        "order_id": clean_text(first_value(source, "order_id", "broker_order_id")),
        "trade_id": clean_text(first_value(source, "trade_id")),
        "intended_price": intended,
        "fill_price": fill,
        "slippage_bps": slippage_bps,
        "fill_delay_seconds": safe_float(first_value(source, "fill_delay_seconds", "fill_delay", "latency_seconds")),
        "partial_fill": bool(first_value(source, "partial_fill")) if first_value(source, "partial_fill") is not None else None,
        "paper_fill_status": clean_text(first_value(source, "paper_fill_status", "status", "route_state")),
    }


def build_outcome_record(
    row: dict[str, Any],
    *,
    horizon_minutes: int,
    price_timeline: dict[str, list[dict[str, Any]]],
    lifecycle_rows: list[dict[str, Any]],
    paper_trade_records: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    enriched = enrich_candidate_lifecycle_row(row)
    candidate_id = clean_text(enriched.get("candidate_lifecycle_id"))
    outcome = compute_candidate_forward_outcome(enriched, horizon_minutes=horizon_minutes, price_timeline=price_timeline)
    actual = safe_float(outcome.get("actual_forward_return"))
    baselines = compute_baselines(
        enriched,
        horizon_minutes=horizon_minutes,
        actual_forward_return=actual,
        price_timeline=price_timeline,
        lifecycle_rows=lifecycle_rows,
    )
    execution = paper_execution_cost_fields(enriched, paper_trade_records)
    missing = list(outcome.get("missing_fields") or [])
    for key, fields in baselines.get("missing", {}).items():
        if key == "sector_etf_forward_return":
            continue
        missing.extend(fields)
    if baselines.get("baseline_forward_return") is None:
        missing.append("baseline_forward_return")
    if actual is None:
        missing.append("actual_forward_return")
    missing = sorted(set(str(field) for field in missing if str(field).strip()))
    available = bool(outcome.get("available")) and baselines.get("baseline_forward_return") is not None
    record = {
        "outcome_record_id": outcome_record_id(candidate_id or "missing", horizon_minutes),
        "candidate_lifecycle_id": candidate_id,
        "idempotency_key": outcome_idempotency_key(candidate_id or "missing", horizon_minutes),
        "outcome_version": OUTCOME_VERSION,
        "baseline_definition_version": enriched.get("baseline_definition_version") or BASELINE_DEFINITION_VERSION,
        "tenant_slug": enriched.get("tenant_slug"),
        "symbol": candidate_symbol(enriched),
        "ticker": candidate_symbol(enriched),
        "prediction_created_at": enriched.get("prediction_created_at"),
        "horizon_minutes": int(horizon_minutes),
        "prediction_horizon_minutes": enriched.get("prediction_horizon_minutes"),
        "predicted_direction": enriched.get("predicted_direction"),
        "predicted_target_pct": enriched.get("predicted_target_pct"),
        "invalidation_level": enriched.get("invalidation_level"),
        "confidence": enriched.get("confidence"),
        "engine": enriched.get("engine"),
        "setup_type": enriched.get("setup_type"),
        "score": enriched.get("score"),
        "score_bucket": enriched.get("score_bucket"),
        "reward_score_bucket": enriched.get("reward_score_bucket"),
        "regime": enriched.get("regime"),
        "route": enriched.get("route"),
        "sample_split": enriched.get("sample_split"),
        "experiment_version": enriched.get("experiment_version"),
        "reward_formula_version": enriched.get("reward_formula_version"),
        "feature_version": enriched.get("feature_version"),
        "available": available,
        "outcome_available": bool(outcome.get("available")),
        "baseline_available": bool(baselines.get("available")),
        "reason": outcome.get("reason") if outcome.get("available") else outcome.get("reason") or baselines.get("reason"),
        **{key: value for key, value in outcome.items() if key not in {"available", "reason", "missing_fields"}},
        **baselines["values"],
        "baseline_forward_return": baselines.get("baseline_forward_return"),
        "primary_baseline": baselines.get("primary_baseline"),
        "baseline_missing_fields": baselines.get("missing", {}),
        **execution,
        "missing_fields": missing,
        "research_only": True,
        "paper_only": True,
        "paper_route_only": True,
        "simulation_evidence": False,
        "evidence_pool": "real_time_market_observed",
        "generated_at": utc_now(),
        **SAFETY_FLAGS,
    }
    return serialize_value(record)


def due_lifecycle_rows(
    lifecycle_rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    existing_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    existing = existing_keys or set()
    due: list[dict[str, Any]] = []
    for row in lifecycle_rows:
        candidate_id = clean_text(row.get("candidate_lifecycle_id"))
        created_at = parse_datetime(candidate_timestamp(row))
        if not candidate_id or created_at is None:
            continue
        for horizon in candidate_horizons(row):
            key = outcome_idempotency_key(candidate_id, horizon)
            if key in existing:
                continue
            matured_at = created_at + timedelta(minutes=horizon)
            if matured_at <= current:
                due.append({"candidate": row, "horizon_minutes": horizon, "matured_at": matured_at.isoformat(), "idempotency_key": key})
    return due


def load_outcome_index(root: Path | str = DEFAULT_ROOT, tenant_slug: str = "systematic-equities") -> dict[str, list[dict[str, Any]]]:
    root_path = Path(root)
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in load_outcome_rows(root_path, tenant_slug):
        candidate_id = clean_text(row.get("candidate_lifecycle_id"))
        if candidate_id:
            index[candidate_id].append(row)
    for rows in index.values():
        rows.sort(key=lambda item: (safe_int(item.get("horizon_minutes")) or 0, str(item.get("generated_at") or "")))
    return dict(index)


def select_best_outcome_for_candidate(candidate: dict[str, Any], outcomes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not outcomes:
        return None
    declared = candidate_declared_horizon(candidate)
    available = [row for row in outcomes if row.get("available") or row.get("outcome_available")]
    candidates = available or outcomes
    if declared:
        exact = [row for row in candidates if safe_int(row.get("horizon_minutes")) == declared]
        if exact:
            return exact[-1]
    for horizon in (30, 15, 5):
        exact = [row for row in candidates if safe_int(row.get("horizon_minutes")) == horizon]
        if exact:
            return exact[-1]
    return candidates[-1]


def merge_outcome_into_candidate(candidate: dict[str, Any], outcomes: list[dict[str, Any]] | None) -> dict[str, Any]:
    row = enrich_candidate_lifecycle_row(candidate)
    selected = select_best_outcome_for_candidate(row, list(outcomes or []))
    row["candidate_outcome_records"] = list(outcomes or [])
    row["candidate_outcome_count"] = len(outcomes or [])
    row["outcome_stamp_status"] = "stamped" if selected else "missing_outcome"
    if not selected:
        return row
    outcome_fields = {
        key: selected.get(key)
        for key in (
            "actual_forward_return",
            "actual_forward_return_observed_at",
            "max_adverse_excursion",
            "hit_target",
            "hit_invalidation",
            "time_to_target_minutes",
            "baseline_forward_return",
            "primary_baseline",
            "spy_forward_return",
            "qqq_forward_return",
            "sector_etf_forward_return",
            "sector_etf_symbol",
            "random_candidate_forward_return",
            "simple_momentum_forward_return",
            "simple_mean_reversion_forward_return",
            "simple_vwap_reclaim_forward_return",
            "opening_range_breakout_forward_return",
            "previous_close_drift_forward_return",
            "slippage_bps",
            "spread_at_signal",
            "spread_at_signal",
            "quote_freshness_seconds",
            "fill_delay_seconds",
            "partial_fill",
            "paper_fill_status",
            "order_id",
            "trade_id",
            "intended_price",
            "fill_price",
        )
        if selected.get(key) is not None
    }
    row.update(outcome_fields)
    if row.get("spread_bps") is None and row.get("spread_at_signal") is not None:
        row["spread_bps"] = row.get("spread_at_signal")
    if row.get("score_bucket") in {None, "", "unknown"} and row.get("score") is not None:
        row["score_bucket"] = reward_score_bucket(safe_float(row.get("score")))
    else:
        row["score_bucket"] = row.get("reward_score_bucket") or row.get("score_bucket")
    selected_missing = set(selected.get("missing_fields") or [])
    existing_missing = set(row.get("missing_pre_move_fields") or [])
    row["missing_outcome_fields"] = sorted(selected_missing)
    row["missing_pre_move_fields"] = sorted(existing_missing)
    return serialize_value(row)


def stamp_due_candidate_outcomes(
    *,
    tenant_slug: str,
    root: Path | str = DEFAULT_ROOT,
    now: datetime | None = None,
    paper_trade_records: Iterable[dict[str, Any]] | None = None,
    persist: bool = True,
    max_due: int = 500,
) -> dict[str, Any]:
    root_path = Path(root)
    current = now or datetime.now(timezone.utc)
    lifecycle_rows = load_lifecycle_rows(root_path, tenant_slug)
    outcome_rows = load_outcome_rows(root_path, tenant_slug)
    existing_keys = {str(row.get("idempotency_key")) for row in outcome_rows if row.get("idempotency_key")}
    due = due_lifecycle_rows(lifecycle_rows, now=current, existing_keys=existing_keys)
    price_timeline = build_price_timeline(lifecycle_rows)
    records: list[dict[str, Any]] = []
    for item in due[:max_due]:
        candidate = item["candidate"]
        record = build_outcome_record(
            candidate,
            horizon_minutes=int(item["horizon_minutes"]),
            price_timeline=price_timeline,
            lifecycle_rows=lifecycle_rows,
            paper_trade_records=paper_trade_records,
        )
        records.append(record)

    written = 0
    write_errors: list[str] = []
    if persist and records:
        rows_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            rows_by_day[candidate_day(record)].append(record)
        for day, rows in rows_by_day.items():
            target = root_path / "runtime-exports" / "candidate-outcomes" / day / f"{tenant_slug}.jsonl"
            try:
                written += append_jsonl(target, rows)
            except OSError as exc:
                write_errors.append(f"{target}: {exc}")
    missing_counter = Counter(field for record in records for field in record.get("missing_fields") or [])
    summary = {
        "tenant_slug": tenant_slug,
        "candidate_lifecycle_rows": len(lifecycle_rows),
        "existing_outcome_rows": len(outcome_rows),
        "due_count": len(due),
        "processed_count": len(records),
        "written_count": written,
        "available_count": sum(1 for row in records if row.get("available")),
        "unavailable_count": sum(1 for row in records if not row.get("available")),
        "baseline_coverage_count": sum(1 for row in records if row.get("baseline_available")),
        "execution_cost_coverage_count": sum(1 for row in records if safe_float(row.get("spread_at_signal")) is not None or safe_float(row.get("slippage_bps")) is not None),
        "last_run_at": current.isoformat(),
        **SAFETY_FLAGS,
    }
    warnings = []
    if write_errors:
        warnings.extend(write_errors)
    if records and summary["available_count"] < len(records):
        warnings.append("Some matured horizons were stamped as unavailable because required observed prices or baselines were missing.")
    return serialize_value(
        {
            "status": "ready" if records and not write_errors else "empty" if not records else "needs_attention",
            "generated_at": utc_now(),
            "research_only": True,
            "paper_only": True,
            "summary": summary,
            "records": records,
            "aggregations": {
                "missing_field_counts": dict(missing_counter),
                "processed_by_horizon": dict(Counter(str(row.get("horizon_minutes")) for row in records)),
            },
            "warnings": warnings,
            "missing_fields": dict(missing_counter),
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def build_evidence_outcomes_report(
    *,
    tenant_slug: str,
    root: Path | str = DEFAULT_ROOT,
    now: datetime | None = None,
    include_due_records: bool = True,
) -> dict[str, Any]:
    root_path = Path(root)
    current = now or datetime.now(timezone.utc)
    lifecycle_rows = load_lifecycle_rows(root_path, tenant_slug)
    outcome_rows = load_outcome_rows(root_path, tenant_slug)
    outcome_index = load_outcome_index(root_path, tenant_slug)
    existing_keys = {str(row.get("idempotency_key")) for row in outcome_rows if row.get("idempotency_key")}
    due = due_lifecycle_rows(lifecycle_rows, now=current, existing_keys=existing_keys)
    stamped_available = [row for row in outcome_rows if row.get("available")]
    baseline_available = [row for row in outcome_rows if row.get("baseline_available") or row.get("baseline_forward_return") is not None]
    execution_available = [
        row
        for row in outcome_rows
        if safe_float(row.get("spread_at_signal")) is not None
        or safe_float(row.get("slippage_bps")) is not None
        or row.get("paper_fill_status")
    ]
    missing_counter = Counter(field for row in outcome_rows for field in row.get("missing_fields") or [])
    horizons = Counter(str(row.get("horizon_minutes")) for row in outcome_rows if row.get("horizon_minutes") is not None)
    outcome_ids = {clean_text(row.get("candidate_lifecycle_id")) for row in outcome_rows if clean_text(row.get("candidate_lifecycle_id"))}
    lifecycle_ids = {clean_text(row.get("candidate_lifecycle_id")) for row in lifecycle_rows if clean_text(row.get("candidate_lifecycle_id"))}
    summary = {
        "tenant_slug": tenant_slug,
        "candidate_lifecycle_rows": len(lifecycle_rows),
        "stamped_outcome_rows": len(outcome_rows),
        "candidate_with_outcomes_count": len(outcome_ids),
        "candidate_without_outcomes_count": max(0, len(lifecycle_ids - outcome_ids)),
        "due_count": len(due),
        "available_outcome_count": len(stamped_available),
        "unavailable_outcome_count": len(outcome_rows) - len(stamped_available),
        "rewardability_lift_candidates": len(outcome_ids),
        "baseline_coverage_rate": round(len(baseline_available) / len(outcome_rows), 6) if outcome_rows else 0.0,
        "execution_cost_coverage_rate": round(len(execution_available) / len(outcome_rows), 6) if outcome_rows else 0.0,
        "last_run_at": max([str(row.get("generated_at") or "") for row in outcome_rows] or [""]) or None,
        "outcome_version": OUTCOME_VERSION,
        "primary_baseline_rule": "random_candidate_forward_return when available, otherwise SPY",
        **SAFETY_FLAGS,
    }
    due_records: list[dict[str, Any]] = []
    if include_due_records:
        price_timeline = build_price_timeline(lifecycle_rows)
        for item in due[:100]:
            preview = build_outcome_record(
                item["candidate"],
                horizon_minutes=int(item["horizon_minutes"]),
                price_timeline=price_timeline,
                lifecycle_rows=lifecycle_rows,
                paper_trade_records=None,
            )
            preview["preview_only"] = True
            due_records.append(preview)
    warnings: list[str] = []
    if not lifecycle_rows:
        warnings.append("No candidate lifecycle rows were found.")
    if due:
        warnings.append("Matured candidate horizons are due for append-only stamping.")
    if missing_counter:
        warnings.append("Some stamped outcomes are missing price, baseline, or execution-cost fields.")
    return serialize_value(
        {
            "status": "needs_attention" if due or missing_counter else "ready" if outcome_rows else "empty",
            "generated_at": utc_now(),
            "research_only": True,
            "paper_only": True,
            "summary": summary,
            "records": outcome_rows[-250:],
            "due_records": due_records,
            "due": due[:250],
            "aggregations": {
                "missing_field_counts": dict(missing_counter),
                "outcomes_by_horizon": dict(horizons),
                "outcomes_by_candidate": {key: len(value) for key, value in outcome_index.items()},
                "baseline_coverage": {
                    "rows_with_primary_baseline": len(baseline_available),
                    "coverage_rate": summary["baseline_coverage_rate"],
                },
                "execution_cost_coverage": {
                    "rows_with_execution_cost": len(execution_available),
                    "coverage_rate": summary["execution_cost_coverage_rate"],
                },
            },
            "warnings": warnings,
            "missing_fields": dict(missing_counter),
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def get_evidence_outcomes_summary(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    return build_evidence_outcomes_report(tenant_slug=tenant_slug_from_user(current_user))


def get_evidence_outcomes_due(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_evidence_outcomes_report(tenant_slug=tenant_slug_from_user(current_user), include_due_records=True)
    return {
        **report,
        "records": report.get("due_records", []),
        "summary": {
            **report.get("summary", {}),
            "due_count": len(report.get("due", [])),
        },
    }


def get_evidence_outcomes_records(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_evidence_outcomes_report(tenant_slug=tenant_slug_from_user(current_user), include_due_records=False)
    return {
        **report,
        "records": report.get("records", []),
    }


def post_evidence_outcomes_stamp_due(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    return stamp_due_candidate_outcomes(tenant_slug=tenant_slug_from_user(current_user), persist=True)


def research_source_paths(root: Path | str = DEFAULT_ROOT, tenant_slug: str = "systematic-equities") -> list[Path]:
    root_path = Path(root)
    return [
        *candidate_lifecycle_files(root_path, tenant_slug),
        *candidate_outcome_files(root_path, tenant_slug),
    ]
