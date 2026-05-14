from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median, pstdev
from typing import Any, Iterable

import pandas as pd

from backend import stock_direction_model as sdm
from backend.services import risk_control_service
from backend.services.desk_service import filter_frame_to_current_user
from backend.services.evidence_reward_engine import get_evidence_reward_summary
from backend.services.project_finish_tracker import build_project_finish_tracker
from backend.services.serialization import serialize_value

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
    "can_grant_ai_order_authority": False,
    "mutation": "none",
    "writes_execution_config": False,
    "writes_broker_config": False,
    "writes_risk_config": False,
    "writes_ranking_config": False,
    "writes_risk_limits": False,
}

SAFETY_NOTES: tuple[str, ...] = (
    "Research only. Does not affect trading.",
    "Paper-route evidence only.",
    "Risk visibility only. Does not enforce, loosen, or change risk gates.",
    "Does not place or block orders.",
    "Does not change broker routes.",
    "Does not change ranking weights automatically.",
    "Does not grant AI order authority.",
)

INVERSE_PROXY_SYMBOLS = {"SH", "PSQ", "DOG", "RWM", "VXX"}
SECRET_KEY_MARKERS = ("secret", "token", "password", "credential", "api_key", "apikey", "access_key", "private_key", "account_id")

DEFAULT_ACCOUNT_SIZE = 100000.0
DEFAULT_DAILY_RISK_BUDGET_PCT = 0.005
MIN_PORTFOLIO_RISK_SAMPLE_SIZE = 1
MIN_PORTFOLIO_RISK_COVERAGE = 0.80
MIN_STRESS_SCENARIO_COUNT = 9

PORTFOLIO_RISK_PROOF_REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "key": "portfolio_sample",
        "label": "Portfolio risk sample",
        "metric": "record_count",
        "threshold": MIN_PORTFOLIO_RISK_SAMPLE_SIZE,
        "comparison": ">=",
        "safe_next_action": "Collect paper-route position, pending order, or candidate exposure rows before treating portfolio risk as reviewable.",
    },
    {
        "key": "exposure_context",
        "label": "Exposure context coverage",
        "metric": "exposure_context_coverage",
        "threshold": MIN_PORTFOLIO_RISK_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Attach notional or quantity-plus-price evidence so gross, net, long, and proxy exposure are reviewable.",
    },
    {
        "key": "concentration_context",
        "label": "Concentration context coverage",
        "metric": "concentration_context_coverage",
        "threshold": MIN_PORTFOLIO_RISK_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Attach sector and correlation bucket context so crowding can be reviewed before promotion.",
    },
    {
        "key": "factor_context",
        "label": "Factor context coverage",
        "metric": "factor_context_coverage",
        "threshold": MIN_PORTFOLIO_RISK_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Attach SPY and QQQ beta evidence or a future factor model before stronger portfolio-risk claims.",
    },
    {
        "key": "liquidity_context",
        "label": "Liquidity context coverage",
        "metric": "liquidity_context_coverage",
        "threshold": MIN_PORTFOLIO_RISK_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Attach liquidity score, average dollar volume, or spread evidence to paper risk rows.",
    },
    {
        "key": "drawdown_budget_context",
        "label": "Drawdown and budget context",
        "metric": "drawdown_budget_context_coverage",
        "threshold": MIN_PORTFOLIO_RISK_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Attach max-risk dollars, daily budget, drawdown, or unrealized P&L evidence to risk rows.",
    },
    {
        "key": "candidate_strategy_context",
        "label": "Candidate and strategy context",
        "metric": "candidate_strategy_context_coverage",
        "threshold": MIN_PORTFOLIO_RISK_COVERAGE,
        "comparison": ">=",
        "safe_next_action": "Attach candidate, engine, setup, strategy, regime, and confidence context to each reviewable row.",
    },
    {
        "key": "stress_context",
        "label": "Stress scenario coverage",
        "metric": "stress_scenario_count",
        "threshold": MIN_STRESS_SCENARIO_COUNT,
        "comparison": ">=",
        "safe_next_action": "Keep transparent stress scenarios available for market, liquidity, sector, single-name, data, and broker shocks.",
    },
    {
        "key": "risk_visibility_safety_boundary",
        "label": "Risk visibility safety boundary",
        "metric": "risk_visibility_safety_boundary",
        "threshold": 1,
        "comparison": ">=",
        "safe_next_action": "Keep portfolio risk as read-only visibility; do not change risk limits, gates, routes, rankings, or orders.",
    },
)

PORTFOLIO_RISK_CLEANUP_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "portfolio_sample",
        "title": "Portfolio risk sample",
        "priority": "critical",
        "proof_keys": ("portfolio_sample",),
        "missing_fields": ("paper_position", "pending_paper_intent", "candidate_exposure"),
        "blocked_claims": ("portfolio_readiness_claim", "paper_to_live_review"),
        "safe_next_action": "Collect paper-route position, pending order, or candidate exposure rows before treating portfolio risk as reviewable.",
        "done_when": "Portfolio risk has at least one paper-route exposure row.",
    },
    {
        "key": "exposure_context",
        "title": "Exposure context",
        "priority": "critical",
        "proof_keys": ("exposure_context",),
        "missing_fields": ("notional", "quantity", "current_price", "side"),
        "blocked_claims": ("gross_exposure_review", "net_exposure_review", "portfolio_readiness_claim"),
        "safe_next_action": "Attach notional or quantity-plus-price evidence so gross, net, long, and proxy exposure are reviewable.",
        "done_when": "Exposure context coverage passes the portfolio-risk proof threshold.",
    },
    {
        "key": "concentration_context",
        "title": "Concentration context",
        "priority": "high",
        "proof_keys": ("concentration_context",),
        "missing_fields": ("sector", "correlation_bucket"),
        "blocked_claims": ("concentration_review", "crowding_review"),
        "safe_next_action": "Attach sector and correlation bucket context so crowding can be reviewed before promotion.",
        "done_when": "Concentration context coverage passes the portfolio-risk proof threshold.",
    },
    {
        "key": "factor_context",
        "title": "Factor context",
        "priority": "high",
        "proof_keys": ("factor_context",),
        "missing_fields": ("beta_to_SPY", "beta_to_QQQ"),
        "blocked_claims": ("factor_exposure_review", "market_beta_claim"),
        "safe_next_action": "Attach SPY and QQQ beta evidence or a future factor model before stronger portfolio-risk claims.",
        "done_when": "Factor context coverage passes the portfolio-risk proof threshold.",
    },
    {
        "key": "liquidity_context",
        "title": "Liquidity context",
        "priority": "high",
        "proof_keys": ("liquidity_context",),
        "missing_fields": ("liquidity_score", "average_dollar_volume", "spread_bps"),
        "blocked_claims": ("liquidity_risk_review", "execution_risk_review"),
        "safe_next_action": "Attach liquidity score, average dollar volume, or spread evidence to paper risk rows.",
        "done_when": "Liquidity context coverage passes the portfolio-risk proof threshold.",
    },
    {
        "key": "drawdown_budget_context",
        "title": "Drawdown and budget context",
        "priority": "high",
        "proof_keys": ("drawdown_budget_context",),
        "missing_fields": ("max_risk_dollars", "daily_risk_budget", "drawdown_pct", "unrealized_pnl"),
        "blocked_claims": ("drawdown_review", "risk_budget_review", "paper_to_live_review"),
        "safe_next_action": "Attach max-risk dollars, daily budget, drawdown, or unrealized P&L evidence to risk rows.",
        "done_when": "Drawdown and budget context coverage passes the portfolio-risk proof threshold.",
    },
    {
        "key": "candidate_strategy_context",
        "title": "Candidate and strategy context",
        "priority": "critical",
        "proof_keys": ("candidate_strategy_context",),
        "missing_fields": ("candidate_lifecycle_id", "engine", "setup_type", "strategy", "regime", "forecast_confidence"),
        "blocked_claims": ("candidate_specific_risk_review", "promotion_traceability", "portfolio_readiness_claim"),
        "safe_next_action": "Attach candidate, engine, setup, strategy, regime, and confidence context to each reviewable row.",
        "done_when": "Candidate and strategy context coverage passes the portfolio-risk proof threshold.",
    },
    {
        "key": "stress_context",
        "title": "Stress scenario context",
        "priority": "medium",
        "proof_keys": ("stress_context",),
        "missing_fields": ("stress_scenarios",),
        "blocked_claims": ("stress_readiness_claim", "portfolio_resilience_claim"),
        "safe_next_action": "Keep transparent stress scenarios available for market, liquidity, sector, single-name, data, and broker shocks.",
        "done_when": "Stress scenario coverage passes the portfolio-risk proof threshold.",
    },
    {
        "key": "risk_visibility_governance",
        "title": "Risk visibility governance",
        "priority": "critical",
        "proof_keys": ("risk_visibility_safety_boundary",),
        "missing_fields": (),
        "blocked_claims": ("risk_limit_change", "risk_gate_change", "broker_route_change", "order_submission", "ranking_mutation"),
        "safe_next_action": "Keep portfolio risk as read-only visibility; do not change risk limits, gates, routes, rankings, or orders.",
        "done_when": "Portfolio Risk remains read-only and all mutation flags remain false.",
    },
)

SECTOR_BY_SYMBOL: dict[str, str] = {
    "SPY": "broad_market",
    "QQQ": "technology",
    "IWM": "small_caps",
    "DIA": "industrials",
    "VTI": "broad_market",
    "SH": "inverse_proxy",
    "PSQ": "inverse_proxy",
    "DOG": "inverse_proxy",
    "RWM": "inverse_proxy",
    "VXX": "volatility_proxy",
    "XLK": "technology",
    "XLF": "financials",
    "XLE": "energy",
    "XLV": "healthcare",
    "XLI": "industrials",
    "XLY": "consumer_discretionary",
    "XLP": "consumer_staples",
    "XLU": "utilities",
    "XLC": "communications",
    "AAPL": "technology",
    "MSFT": "technology",
    "NVDA": "semiconductors",
    "AMD": "semiconductors",
    "AVGO": "semiconductors",
    "INTC": "semiconductors",
    "QCOM": "semiconductors",
    "AMZN": "consumer_discretionary",
    "META": "communications",
    "GOOGL": "communications",
    "GOOG": "communications",
    "TSLA": "consumer_discretionary",
    "NFLX": "communications",
    "JPM": "financials",
    "BAC": "financials",
    "GS": "financials",
    "XOM": "energy",
    "CVX": "energy",
    "LLY": "healthcare",
    "UNH": "healthcare",
    "JNJ": "healthcare",
    "PFE": "healthcare",
    "PG": "consumer_staples",
    "KO": "consumer_staples",
    "PEP": "consumer_staples",
    "WMT": "consumer_staples",
    "COST": "consumer_staples",
    "HD": "consumer_discretionary",
    "MCD": "consumer_discretionary",
    "CRM": "technology",
    "ORCL": "technology",
}


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


def _mean(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in (_safe_float(item) for item in values) if value is not None]
    return round(sum(clean) / len(clean), 6) if clean else None


def _median(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in (_safe_float(item) for item in values) if value is not None]
    return round(float(median(clean)), 6) if clean else None


def _dispersion(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in (_safe_float(item) for item in values) if value is not None]
    if not clean:
        return None
    if len(clean) == 1:
        return 0.0
    return round(float(pstdev(clean)), 6)


def _ratio(numerator: float, denominator: float) -> float | None:
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


def _nested_sources(row: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [row]
    for key in ("payload", "candidate", "position", "paper_trade_outcome", "execution", "risk", "forecast", "prediction_contract"):
        value = row.get(key)
        if isinstance(value, dict):
            sources.append(value)
    return sources


def _first_value(row: dict[str, Any], fields: Iterable[str]) -> Any:
    for source in _nested_sources(row):
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


def _first_text(row: dict[str, Any], fields: Iterable[str], fallback: str = "unknown") -> str:
    for field in fields:
        value = _first_value(row, (field,))
        if value is not None and str(value).strip():
            return str(value).strip()
    return fallback


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


def _is_paper_route(row: dict[str, Any]) -> bool:
    route_text = " ".join(
        str(_first_value(row, fields) or "")
        for fields in (
            ("route", "execution_route", "route_state", "broker", "broker_route", "paper_route", "mode"),
            ("source",),
        )
    ).lower()
    if "live" in route_text and "paper" not in route_text:
        return False
    if "paper" in route_text or "alpaca" in route_text or "broker_paper" in route_text or "internal" in route_text:
        return True
    return not route_text.strip()


def _is_simulation_evidence(row: dict[str, Any]) -> bool:
    for source in _nested_sources(row):
        evidence_pool = str(source.get("evidence_pool") or "").strip().lower()
        if source.get("simulation_evidence") or evidence_pool == "simulation_evidence":
            return True
    return False


def _symbol(row: dict[str, Any]) -> str:
    return _first_text(row, ("symbol", "ticker", "underlying_symbol"), "unknown").strip().upper() or "UNKNOWN"


def _side(row: dict[str, Any], symbol: str) -> str:
    side = _first_text(row, ("side", "broker_side", "direction", "position_side"), "long").strip().lower()
    if symbol in INVERSE_PROXY_SYMBOLS:
        return "inverse_proxy"
    if side in {"sell", "short", "put", "bearish", "inverse_proxy"}:
        return "short_or_proxy"
    return "long"


def _signed_direction(side: str) -> float:
    return -1.0 if side in {"short_or_proxy", "inverse_proxy"} else 1.0


def _units(row: dict[str, Any]) -> float | None:
    return _first_number(row, ("quantity", "qty", "shares", "units", "suggested_contracts", "filled_quantity", "filled_contracts", "broker_qty"))


def _price(row: dict[str, Any]) -> float | None:
    return _first_number(
        row,
        (
            "current_price",
            "mark_price",
            "live_price",
            "live_price_at_open",
            "entry_price",
            "actual_fill_price",
            "fill_price",
            "limit_price",
            "close",
        ),
    )


def compute_position_notional(row: dict[str, Any]) -> float | None:
    explicit = _first_number(
        row,
        (
            "notional",
            "position_notional",
            "position_cost",
            "projected_position_cost",
            "total_position_cost",
            "market_value",
            "broker_notional",
        ),
    )
    if explicit is not None:
        return round(abs(explicit), 6)
    units = _units(row)
    price = _price(row)
    if units is None or price is None or units <= 0 or price <= 0:
        return None
    instrument_type = _first_text(row, ("instrument_type", "asset_class"), "equity").lower()
    multiplier = 100.0 if instrument_type in {"listed_option", "option", "options"} else 1.0
    return round(abs(units * price * multiplier), 6)


def _sector_for(symbol: str, row: dict[str, Any]) -> tuple[str, bool]:
    explicit = _first_text(row, ("sector", "sector_name", "sector_key"), "").strip().lower()
    if explicit:
        return explicit.replace(" ", "_"), True
    mapped = SECTOR_BY_SYMBOL.get(symbol)
    if mapped:
        return mapped, True
    return "unknown", False


def _account_size(records: list[dict[str, Any]]) -> float:
    values = [_first_number(row, ("account_size", "equity", "portfolio_value", "buying_power_base")) for row in records]
    clean = [value for value in values if value is not None and value > 0]
    return round(float(clean[0]), 6) if clean else DEFAULT_ACCOUNT_SIZE


def normalize_portfolio_risk_record(row: dict[str, Any], index: int = 0) -> dict[str, Any] | None:
    if not isinstance(row, dict) or _is_simulation_evidence(row) or not _is_paper_route(row):
        return None
    symbol = _symbol(row)
    notional = compute_position_notional(row)
    side = _side(row, symbol)
    signed_exposure = round((notional or 0.0) * _signed_direction(side), 6) if notional is not None else None
    sector, sector_available = _sector_for(symbol, row)
    engine = _first_text(row, ("engine", "desk_key", "strategy_desk_key"), "unknown")
    setup_type = _first_text(row, ("setup_type", "opportunity_type"), "unknown")
    strategy = _first_text(row, ("strategy", "strategy_key", "strategy_id"), engine)
    regime = _first_text(row, ("regime", "market_regime", "regime_state"), "unknown")
    beta_spy = _first_number(row, ("beta_to_SPY", "beta_to_spy", "spy_beta", "beta"))
    beta_qqq = _first_number(row, ("beta_to_QQQ", "beta_to_qqq", "qqq_beta"))
    liquidity_score = _first_number(row, ("liquidity_score",))
    avg_dollar_volume = _first_number(row, ("average_dollar_volume", "avg_dollar_volume", "dollar_volume"))
    spread_bps = _first_number(row, ("spread_bps", "spread_at_signal", "bid_ask_spread_bps"))
    forecast_confidence = _first_number(row, ("forecast_confidence", "confidence", "ai_confidence"))
    max_risk_dollars = _first_number(row, ("max_risk_dollars", "risk_dollars", "planned_risk_dollars"))
    current_drawdown_pct = _first_number(row, ("drawdown_pct", "current_drawdown_pct", "max_drawdown_pct"))
    unrealized_pnl = _first_number(row, ("unrealized_pnl", "current_unrealized_pnl", "open_pnl", "floating_pnl"))
    daily_risk_budget = _first_number(row, ("daily_risk_budget", "daily_loss_budget", "loss_budget_dollars"))
    linked_candidate_id = _first_text(row, ("linked_candidate_id", "candidate_lifecycle_id", "automation_candidate_id"), "").strip() or None
    warnings: list[str] = []
    missing_fields: list[str] = []
    if notional is None:
        missing_fields.append("notional")
    if not sector_available:
        missing_fields.append("sector")
    if beta_spy is None:
        missing_fields.append("beta_to_SPY")
    if beta_qqq is None:
        missing_fields.append("beta_to_QQQ")
    if liquidity_score is None and avg_dollar_volume is None:
        missing_fields.append("liquidity")
    if forecast_confidence is None:
        missing_fields.append("forecast_confidence")
    if max_risk_dollars is None and current_drawdown_pct is None and unrealized_pnl is None and daily_risk_budget is None:
        missing_fields.append("drawdown_or_risk_budget")
    if liquidity_score is not None and liquidity_score < 0.4:
        warnings.append("Liquidity score is weak.")
    if avg_dollar_volume is not None and avg_dollar_volume < 1_000_000:
        warnings.append("Average dollar volume is below the visibility threshold.")
    if spread_bps is not None and spread_bps > 25:
        warnings.append("Spread is wide for portfolio-level risk visibility.")
    normalized = {
        "record_id": _first_text(row, ("record_id", "trade_id", "order_id", "candidate_lifecycle_id"), f"position-{index + 1}"),
        "linked_candidate_id": linked_candidate_id,
        "source_type": _first_text(row, ("source_type",), "paper_position"),
        "symbol": symbol,
        "timestamp": _first_text(row, ("timestamp", "created_at", "opened_at", "submitted_at"), ""),
        "engine": engine,
        "setup_type": setup_type,
        "strategy": strategy,
        "regime": regime,
        "sector": sector,
        "correlation_bucket": _first_text(row, ("correlation_bucket",), risk_control_service.bucket_for_symbol(symbol, None)),
        "route": _first_text(row, ("route", "execution_route", "broker", "route_state"), "broker_paper"),
        "paper_only": True,
        "side": side,
        "notional": notional,
        "signed_exposure": signed_exposure,
        "absolute_exposure": notional,
        "max_risk_dollars": max_risk_dollars,
        "liquidity_score": liquidity_score,
        "average_dollar_volume": avg_dollar_volume,
        "spread_bps": spread_bps,
        "beta_to_SPY": beta_spy,
        "beta_to_QQQ": beta_qqq,
        "forecast_confidence": forecast_confidence,
        "current_drawdown_pct": current_drawdown_pct,
        "unrealized_pnl": unrealized_pnl,
        "daily_risk_budget": daily_risk_budget,
        "warnings": warnings,
        "missing_fields": sorted(set(missing_fields + [str(item) for item in _listify(row.get("missing_fields"))])),
    }
    return _sanitize_value(normalized)


def normalize_portfolio_risk_records(records: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = []
    for index, row in enumerate(records or []):
        normalized = normalize_portfolio_risk_record(row, index)
        if normalized is not None:
            rows.append(normalized)
    return rows


def _group_exposure(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    gross = sum(float(row.get("absolute_exposure") or 0.0) for row in records)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[str(row.get(key) or "unknown")].append(row)
    items = []
    for label, rows in grouped.items():
        absolute = sum(float(row.get("absolute_exposure") or 0.0) for row in rows)
        signed = sum(float(row.get("signed_exposure") or 0.0) for row in rows)
        items.append(
            {
                key: label,
                "count": len(rows),
                "gross_exposure": round(absolute, 6),
                "net_exposure": round(signed, 6),
                "exposure_share": _ratio(absolute, gross),
                "average_beta_to_SPY": _mean(row.get("beta_to_SPY") for row in rows),
                "average_liquidity_score": _mean(row.get("liquidity_score") for row in rows),
                "average_forecast_confidence": _mean(row.get("forecast_confidence") for row in rows),
            }
        )
    return sorted(items, key=lambda item: (-float(item.get("gross_exposure") or 0.0), str(item.get(key) or "")))


def _weighted_average(records: list[dict[str, Any]], field: str) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for row in records:
        value = _safe_float(row.get(field))
        weight = _safe_float(row.get("absolute_exposure")) or 0.0
        if value is None or weight <= 0:
            continue
        numerator += value * weight
        denominator += weight
    return round(numerator / denominator, 6) if denominator > 0 else None


def compute_concentration(records: list[dict[str, Any]]) -> dict[str, Any]:
    gross = sum(float(row.get("absolute_exposure") or 0.0) for row in records)
    symbol_items = _group_exposure(records, "symbol")
    sector_items = _group_exposure(records, "sector")
    max_symbol_share = max((float(item.get("exposure_share") or 0.0) for item in symbol_items), default=0.0)
    max_sector_share = max((float(item.get("exposure_share") or 0.0) for item in sector_items), default=0.0)
    return {
        "symbol_concentration": round(max_symbol_share, 6) if records else None,
        "sector_concentration": round(max_sector_share, 6) if records else None,
        "top_symbols": symbol_items[:10],
        "top_sectors": sector_items[:10],
        "gross_exposure": round(gross, 6),
    }


def compute_correlation_heat(records: list[dict[str, Any]]) -> dict[str, Any]:
    gross = sum(float(row.get("absolute_exposure") or 0.0) for row in records)
    bucket_items = _group_exposure(records, "correlation_bucket")
    max_bucket_share = max((float(item.get("exposure_share") or 0.0) for item in bucket_items), default=0.0)
    crowded_bucket_count = sum(1 for item in bucket_items if float(item.get("exposure_share") or 0.0) >= 0.25)
    heat_score = None
    if records:
        heat_score = round(min(100.0, (max_bucket_share * 100.0) + max(0, crowded_bucket_count - 1) * 5.0), 2)
    return {
        "correlation_heat": heat_score,
        "max_bucket_share": round(max_bucket_share, 6) if records else None,
        "crowded_bucket_count": crowded_bucket_count,
        "buckets": bucket_items,
        "gross_exposure": round(gross, 6),
        "method": "correlation bucket concentration using configured/default symbol buckets",
    }


def compute_liquidity_exposure(records: list[dict[str, Any]]) -> dict[str, Any]:
    weak_rows = [
        row
        for row in records
        if (_safe_float(row.get("liquidity_score")) is not None and float(row.get("liquidity_score")) < 0.4)
        or (_safe_float(row.get("average_dollar_volume")) is not None and float(row.get("average_dollar_volume")) < 1_000_000)
        or (_safe_float(row.get("spread_bps")) is not None and float(row.get("spread_bps")) > 25)
    ]
    gross = sum(float(row.get("absolute_exposure") or 0.0) for row in records)
    weak_exposure = sum(float(row.get("absolute_exposure") or 0.0) for row in weak_rows)
    return {
        "liquidity_exposure": round(weak_exposure, 6),
        "liquidity_exposure_share": _ratio(weak_exposure, gross),
        "average_liquidity_score": _mean(row.get("liquidity_score") for row in records),
        "average_spread_bps": _mean(row.get("spread_bps") for row in records),
        "liquidity_warning_count": len(weak_rows),
        "warnings": [f"{row.get('symbol')} has weak liquidity/spread evidence." for row in weak_rows[:10]],
    }


def compute_drawdown_state(records: list[dict[str, Any]], account_size: float) -> dict[str, Any]:
    drawdown_values = [_first_number(row, ("drawdown_pct", "current_drawdown_pct", "max_drawdown_pct")) for row in records]
    clean = [value for value in drawdown_values if value is not None]
    current_drawdown = max(clean) if clean else None
    floating_pnl = sum(_first_number(row, ("unrealized_pnl", "current_unrealized_pnl", "open_pnl", "floating_pnl")) or 0.0 for row in records)
    if current_drawdown is None and floating_pnl < 0 and account_size > 0:
        current_drawdown = abs(floating_pnl) / account_size
    if current_drawdown is None:
        state = "unknown"
    elif current_drawdown >= 0.10:
        state = "stop_review"
    elif current_drawdown >= 0.05:
        state = "size_cut_review"
    elif current_drawdown >= 0.02:
        state = "elevated"
    else:
        state = "calm"
    return {
        "drawdown_state": state,
        "current_drawdown_pct": round(current_drawdown, 6) if current_drawdown is not None else None,
        "floating_pnl": round(floating_pnl, 6),
    }


def compute_daily_risk_budget_usage(records: list[dict[str, Any]], account_size: float) -> dict[str, Any]:
    explicit_budget = next((_first_number(row, ("daily_risk_budget", "daily_loss_budget", "loss_budget_dollars")) for row in records if _first_number(row, ("daily_risk_budget", "daily_loss_budget", "loss_budget_dollars")) is not None), None)
    daily_budget = explicit_budget if explicit_budget is not None and explicit_budget > 0 else account_size * DEFAULT_DAILY_RISK_BUDGET_PCT
    open_risk = sum(_safe_float(row.get("max_risk_dollars")) or 0.0 for row in records)
    if open_risk <= 0:
        open_risk = sum(float(row.get("absolute_exposure") or 0.0) * 0.01 for row in records)
    return {
        "daily_risk_budget": round(daily_budget, 6),
        "open_risk_estimate": round(open_risk, 6),
        "daily_risk_budget_usage": _ratio(open_risk, daily_budget),
    }


def compute_open_heat(records: list[dict[str, Any]], account_size: float) -> dict[str, Any]:
    gross = sum(float(row.get("absolute_exposure") or 0.0) for row in records)
    heat = _ratio(gross, account_size)
    if heat is None:
        state = "unknown"
    elif heat >= 1.0:
        state = "crowded"
    elif heat >= 0.5:
        state = "elevated"
    elif heat > 0:
        state = "controlled"
    else:
        state = "empty"
    return {
        "open_heat": heat,
        "open_heat_state": state,
        "open_position_count": len(records),
    }


def compute_forecast_confidence_exposure(records: list[dict[str, Any]]) -> dict[str, Any]:
    gross = sum(float(row.get("absolute_exposure") or 0.0) for row in records)
    buckets = {
        "high_confidence": [row for row in records if (_safe_float(row.get("forecast_confidence")) or 0.0) >= 0.70],
        "medium_confidence": [row for row in records if 0.40 <= (_safe_float(row.get("forecast_confidence")) or -1.0) < 0.70],
        "low_confidence": [row for row in records if (_safe_float(row.get("forecast_confidence")) is not None and (_safe_float(row.get("forecast_confidence")) or 0.0) < 0.40)],
        "missing_confidence": [row for row in records if _safe_float(row.get("forecast_confidence")) is None],
    }
    return {
        "average_forecast_confidence": _weighted_average(records, "forecast_confidence"),
        "buckets": [
            {
                "bucket": key,
                "count": len(rows),
                "gross_exposure": round(sum(float(row.get("absolute_exposure") or 0.0) for row in rows), 6),
                "exposure_share": _ratio(sum(float(row.get("absolute_exposure") or 0.0) for row in rows), gross),
            }
            for key, rows in buckets.items()
        ],
    }


def compute_portfolio_risk_aggregations(records: list[dict[str, Any]]) -> dict[str, Any]:
    account_size = _account_size(records)
    gross = sum(float(row.get("absolute_exposure") or 0.0) for row in records)
    net = sum(float(row.get("signed_exposure") or 0.0) for row in records)
    long_exposure = sum(float(row.get("absolute_exposure") or 0.0) for row in records if float(row.get("signed_exposure") or 0.0) > 0)
    short_or_proxy = sum(float(row.get("absolute_exposure") or 0.0) for row in records if float(row.get("signed_exposure") or 0.0) < 0)
    concentration = compute_concentration(records)
    correlation = compute_correlation_heat(records)
    liquidity = compute_liquidity_exposure(records)
    drawdown = compute_drawdown_state(records, account_size)
    daily_budget = compute_daily_risk_budget_usage(records, account_size)
    open_heat = compute_open_heat(records, account_size)
    forecast_confidence = compute_forecast_confidence_exposure(records)
    return {
        "gross_exposure": round(gross, 6),
        "net_exposure": round(net, 6),
        "long_exposure": round(long_exposure, 6),
        "short_or_proxy_exposure": round(short_or_proxy, 6),
        "account_size": account_size,
        "sector_exposure": _group_exposure(records, "sector"),
        "engine_exposure": _group_exposure(records, "engine"),
        "setup_exposure": _group_exposure(records, "setup_type"),
        "strategy_exposure": _group_exposure(records, "strategy"),
        "regime_exposure": _group_exposure(records, "regime"),
        "symbol_concentration": concentration.get("symbol_concentration"),
        "sector_concentration": concentration.get("sector_concentration"),
        "concentration": concentration,
        "correlation_heat": correlation,
        "liquidity_exposure": liquidity,
        "beta_to_SPY": _weighted_average(records, "beta_to_SPY"),
        "beta_to_QQQ": _weighted_average(records, "beta_to_QQQ"),
        "drawdown_state": drawdown,
        "daily_risk_budget_usage": daily_budget,
        "open_heat": open_heat,
        "forecast_confidence_exposure": forecast_confidence,
    }


def _stress_market_move(records: list[dict[str, Any]], move_pct: float, label: str) -> dict[str, Any]:
    pnl = 0.0
    missing_beta = 0
    for row in records:
        beta = _safe_float(row.get("beta_to_SPY"))
        if beta is None:
            missing_beta += 1
            beta = 1.0
        direction = _signed_direction(str(row.get("side") or "long"))
        pnl += float(row.get("absolute_exposure") or 0.0) * direction * beta * move_pct
    return {
        "scenario": label,
        "estimated_pnl": round(pnl, 6),
        "estimated_return_on_gross": _ratio(pnl, sum(float(row.get("absolute_exposure") or 0.0) for row in records)),
        "missing_beta_count": missing_beta,
        "analytics_only": True,
    }


def compute_stress_tests(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gross = sum(float(row.get("absolute_exposure") or 0.0) for row in records)
    largest_symbol = (compute_concentration(records).get("top_symbols") or [{}])[0]
    largest_sector = (compute_concentration(records).get("top_sectors") or [{}])[0]
    breakout_exposure = sum(float(row.get("absolute_exposure") or 0.0) for row in records if "breakout" in str(row.get("setup_type") or "").lower())
    liquidity = compute_liquidity_exposure(records)
    return [
        _stress_market_move(records, -0.02, "market_down_2_percent"),
        _stress_market_move(records, -0.05, "market_down_5_percent"),
        {
            "scenario": "volatility_expansion",
            "estimated_pnl": round(-gross * 0.01, 6),
            "estimated_return_on_gross": -0.01 if gross else None,
            "reason": "Simple 1 percent gross exposure shock for volatility expansion.",
            "analytics_only": True,
        },
        {
            "scenario": "liquidity_deterioration",
            "estimated_pnl": round(-gross * 0.0075 - float(liquidity.get("liquidity_exposure") or 0.0) * 0.01, 6),
            "estimated_return_on_gross": _ratio(-gross * 0.0075 - float(liquidity.get("liquidity_exposure") or 0.0) * 0.01, gross),
            "reason": "Simple spread/liquidity shock; does not change route behavior.",
            "analytics_only": True,
        },
        {
            "scenario": "sector_rotation",
            "estimated_pnl": round(-float(largest_sector.get("gross_exposure") or 0.0) * 0.03, 6),
            "affected_segment": largest_sector.get("sector"),
            "analytics_only": True,
        },
        {
            "scenario": "single_name_gap_down",
            "estimated_pnl": round(-float(largest_symbol.get("gross_exposure") or 0.0) * 0.05, 6),
            "affected_symbol": largest_symbol.get("symbol"),
            "analytics_only": True,
        },
        {
            "scenario": "failed_breakout_cluster",
            "estimated_pnl": round(-breakout_exposure * 0.02, 6),
            "affected_exposure": round(breakout_exposure, 6),
            "analytics_only": True,
        },
        {
            "scenario": "data_outage",
            "estimated_pnl": None,
            "operational_impact": "Data outage would make portfolio visibility stale; risk gates are not changed by this report.",
            "analytics_only": True,
        },
        {
            "scenario": "broker_outage",
            "estimated_pnl": None,
            "operational_impact": "Broker outage would require existing reconciliation/readiness checks; this report does not cancel or submit orders.",
            "analytics_only": True,
        },
    ]


def _known_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and text not in {"unknown", "nan", "none", "null"})


def _portfolio_row_readiness(row: dict[str, Any]) -> dict[str, Any]:
    exposure_context = row.get("absolute_exposure") is not None and row.get("signed_exposure") is not None
    concentration_context = _known_text(row.get("sector")) and _known_text(row.get("correlation_bucket"))
    factor_context = row.get("beta_to_SPY") is not None and row.get("beta_to_QQQ") is not None
    liquidity_context = row.get("liquidity_score") is not None or row.get("average_dollar_volume") is not None or row.get("spread_bps") is not None
    drawdown_budget_context = any(
        row.get(field) is not None
        for field in ("max_risk_dollars", "current_drawdown_pct", "unrealized_pnl", "daily_risk_budget")
    )
    candidate_strategy_context = bool(row.get("linked_candidate_id") or row.get("record_id")) and all(
        _known_text(row.get(field)) for field in ("engine", "setup_type", "strategy", "regime")
    ) and row.get("forecast_confidence") is not None
    warnings: list[str] = list(row.get("warnings") or [])
    missing = list(row.get("missing_fields") or [])
    if not exposure_context:
        warnings.append("Exposure context is incomplete.")
    if not concentration_context:
        warnings.append("Concentration context is incomplete.")
    if not factor_context:
        warnings.append("Factor exposure context is incomplete.")
    if not liquidity_context:
        warnings.append("Liquidity context is incomplete.")
    if not drawdown_budget_context:
        warnings.append("Drawdown or budget context is incomplete.")
    if not candidate_strategy_context:
        warnings.append("Candidate or strategy context is incomplete.")
    return {
        "record_id": row.get("record_id"),
        "linked_candidate_id": row.get("linked_candidate_id"),
        "symbol": row.get("symbol"),
        "route": row.get("route"),
        "exposure_context_complete": exposure_context,
        "concentration_context_complete": concentration_context,
        "factor_context_complete": factor_context,
        "liquidity_context_complete": liquidity_context,
        "drawdown_budget_context_complete": drawdown_budget_context,
        "candidate_strategy_context_complete": candidate_strategy_context,
        "warnings": list(dict.fromkeys(warnings)),
        "missing_fields": missing,
        "research_only": True,
        "paper_only": True,
        "changes_risk_limits": False,
        "changes_risk_gates": False,
        "changes_broker_routes": False,
        "changes_order_submission": False,
        "changes_ranking_weights": False,
    }


def build_portfolio_risk_proof_summary(
    records: list[dict[str, Any]],
    aggregations: dict[str, Any],
    stress_tests: list[dict[str, Any]],
) -> dict[str, Any]:
    record_count = len(records)
    readiness = [_portfolio_row_readiness(row) for row in records]

    def coverage(field: str) -> float:
        return _ratio(sum(1 for row in readiness if row.get(field)), record_count) or 0.0

    exposure_context_coverage = coverage("exposure_context_complete")
    concentration_context_coverage = coverage("concentration_context_complete")
    factor_context_coverage = coverage("factor_context_complete")
    liquidity_context_coverage = coverage("liquidity_context_complete")
    drawdown_budget_context_coverage = coverage("drawdown_budget_context_complete")
    candidate_strategy_context_coverage = coverage("candidate_strategy_context_complete")
    portfolio_risk_coverage = round(
        (
            exposure_context_coverage
            + concentration_context_coverage
            + factor_context_coverage
            + liquidity_context_coverage
            + drawdown_budget_context_coverage
            + candidate_strategy_context_coverage
        )
        / 6,
        6,
    )
    analytics_only_stress_count = sum(1 for row in stress_tests if isinstance(row, dict) and row.get("analytics_only") is True)
    safety_boundary = int(
        SAFETY_FLAGS["can_submit_orders"] is False
        and SAFETY_FLAGS["can_submit_live_orders"] is False
        and SAFETY_FLAGS["writes_risk_config"] is False
        and SAFETY_FLAGS["writes_risk_limits"] is False
        and SAFETY_FLAGS["writes_broker_config"] is False
        and SAFETY_FLAGS["writes_ranking_config"] is False
    )
    values = {
        "record_count": record_count,
        "exposure_context_coverage": exposure_context_coverage,
        "concentration_context_coverage": concentration_context_coverage,
        "factor_context_coverage": factor_context_coverage,
        "liquidity_context_coverage": liquidity_context_coverage,
        "drawdown_budget_context_coverage": drawdown_budget_context_coverage,
        "candidate_strategy_context_coverage": candidate_strategy_context_coverage,
        "stress_scenario_count": analytics_only_stress_count,
        "risk_visibility_safety_boundary": safety_boundary,
    }
    rows: list[dict[str, Any]] = []
    for requirement in PORTFOLIO_RISK_PROOF_REQUIREMENTS:
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
                "claim_boundary": "Portfolio risk proof is read-only research visibility; it is not risk approval, live-trading readiness, investor performance evidence, or permission to change risk gates.",
                "research_only": True,
                "paper_only": True,
                "changes_execution": False,
                "changes_order_submission": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
                "changes_risk_limits": False,
                "changes_ranking_weights": False,
            }
        )
    proof_ready = bool(rows) and all(row["passed"] for row in rows)
    return serialize_value(
        {
            "status": "ready_for_human_review" if proof_ready else "needs_evidence",
            "proof_ready": proof_ready,
            "requirements": rows,
            "summary": {
                "record_count": record_count,
                "portfolio_risk_coverage": portfolio_risk_coverage,
                "exposure_context_coverage": exposure_context_coverage,
                "concentration_context_coverage": concentration_context_coverage,
                "factor_context_coverage": factor_context_coverage,
                "liquidity_context_coverage": liquidity_context_coverage,
                "drawdown_budget_context_coverage": drawdown_budget_context_coverage,
                "candidate_strategy_context_coverage": candidate_strategy_context_coverage,
                "stress_scenario_count": analytics_only_stress_count,
                "symbol_concentration": aggregations.get("symbol_concentration"),
                "sector_concentration": aggregations.get("sector_concentration"),
                "open_heat": aggregations.get("open_heat", {}).get("open_heat"),
                "daily_risk_budget_usage": aggregations.get("daily_risk_budget_usage", {}).get("daily_risk_budget_usage"),
                "requirement_count": len(rows),
                "passed_requirement_count": sum(1 for row in rows if row["passed"]),
                "missing_requirement_count": sum(1 for row in rows if not row["passed"]),
            },
            "record_readiness": readiness[:100],
            "safe_next_actions": [row["safe_next_action"] for row in rows if not row["passed"]],
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def build_portfolio_risk_cleanup_plan(
    *,
    records: list[dict[str, Any]],
    proof_summary: dict[str, Any],
) -> dict[str, Any]:
    proof_rows = {
        str(row.get("key")): row
        for row in proof_summary.get("requirements") or []
        if isinstance(row, dict)
    }
    all_missing_fields: Counter[str] = Counter()
    for row in records:
        all_missing_fields.update(str(field) for field in _listify(row.get("missing_fields")))

    items: list[dict[str, Any]] = []
    for definition in PORTFOLIO_RISK_CLEANUP_DEFINITIONS:
        proof_keys = tuple(definition.get("proof_keys") or ())
        related_proof_rows = [
            proof_rows[key]
            for key in proof_keys
            if isinstance(proof_rows.get(key), dict)
        ]
        passed = bool(related_proof_rows) and all(bool(row.get("passed")) for row in related_proof_rows)
        status = "no_records" if not records and definition["key"] != "risk_visibility_governance" else "ready" if passed else "needs_evidence"
        values = {str(row.get("metric")): row.get("value") for row in related_proof_rows}
        missing_fields = sorted(
            {
                str(field)
                for row in related_proof_rows
                for field in _listify(row.get("missing_fields"))
            }
        )
        if not missing_fields and not passed:
            missing_fields = list(definition.get("missing_fields") or ())
        if not missing_fields and not passed and all_missing_fields:
            missing_fields = [field for field, _count in all_missing_fields.most_common(8)]
        safe_next_actions = [
            str(row.get("safe_next_action"))
            for row in related_proof_rows
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
                "values": values,
                "missing_fields": missing_fields,
                "blocked_claims": list(definition.get("blocked_claims") or ()),
                "safe_next_action": safe_next_actions[0],
                "safe_next_actions": safe_next_actions,
                "done_when": definition["done_when"],
                "claim_boundary": "Portfolio Risk cleanup is internal paper-route risk visibility only; it is not risk approval, portfolio safety proof, investor performance evidence, paper-to-live readiness, or permission to change limits.",
                "manual_review_only": True,
                "research_only": True,
                "paper_only": True,
                "changes_execution": False,
                "changes_order_submission": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
                "changes_risk_limits": False,
                "changes_ranking_weights": False,
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
                    "cautious_internal_portfolio_risk_review": proof_ready,
                    "portfolio_readiness_claim": False,
                    "risk_limit_change": False,
                    "risk_gate_change": False,
                    "broker_route_change": False,
                    "automatic_risk_mutation": False,
                    "paper_to_live_readiness": False,
                    "live_trading_readiness": False,
                },
                "blocked_claims": [
                    "portfolio_readiness_claim",
                    "risk_limit_change",
                    "risk_gate_change",
                    "broker_route_change",
                    "portfolio_safety_proof",
                    "paper_to_live_readiness",
                    "live_trading_readiness",
                ],
                "safe_boundary": "Portfolio Risk cleanup records missing risk visibility evidence and claim boundaries only. It does not authorize orders, risk-limit changes, risk-gate changes, broker-route changes, or ranking-weight mutation.",
            },
            "items": items,
            "safe_next_actions": [
                {
                    "field": row["key"],
                    "action": row["safe_next_action"],
                    "manual_review_only": True,
                    "changes_execution": False,
                    "changes_order_submission": False,
                    "changes_risk_limits": False,
                }
                for row in open_items
            ],
            "research_only": True,
            "paper_only": True,
            **SAFETY_FLAGS,
        }
    )


def _records_from_frame(frame: pd.DataFrame, source_type: str) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    records = []
    for row in frame.to_dict(orient="records"):
        if isinstance(row, dict):
            records.append({**row, "source_type": source_type})
    return records


def _load_runtime_rows(db: Any = None, current_user: Any = None) -> tuple[list[dict[str, Any]], list[str]]:
    del db
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        rows.extend(_records_from_frame(filter_frame_to_current_user(sdm.read_paper_open_trades(), current_user), "paper_open_trade"))
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Paper open trade source unavailable: {exc.__class__.__name__}.")
    try:
        rows.extend(_records_from_frame(filter_frame_to_current_user(sdm.read_open_trades(), current_user), "open_trade"))
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Open trade source unavailable: {exc.__class__.__name__}.")
    try:
        rows.extend(_records_from_frame(filter_frame_to_current_user(sdm.read_pending_orders(), current_user), "pending_order"))
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Pending order source unavailable: {exc.__class__.__name__}.")
    try:
        reward_report = get_evidence_reward_summary(current_user=current_user)
        for row in list(reward_report.get("records") or reward_report.get("candidate_rows") or []):
            if not isinstance(row, dict):
                continue
            if any(_first_value(row, fields) is not None for fields in (("position_notional", "notional", "projected_position_cost"), ("paper_trade_outcome",))):
                rows.append({**row, "source_type": "evidence_candidate"})
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Evidence Reward portfolio source unavailable: {exc.__class__.__name__}.")
    return rows, warnings


def build_portfolio_risk_report(
    *,
    records: Iterable[dict[str, Any]] | None = None,
    db: Any = None,
    current_user: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    source_warnings: list[str] = []
    if records is None:
        records, source_warnings = _load_runtime_rows(db, current_user)
    normalized = normalize_portfolio_risk_records(records)
    aggregations = compute_portfolio_risk_aggregations(normalized)
    stress_tests = compute_stress_tests(normalized)
    proof_summary = build_portfolio_risk_proof_summary(normalized, aggregations, stress_tests)
    cleanup_plan = build_portfolio_risk_cleanup_plan(records=normalized, proof_summary=proof_summary)
    missing_counter: Counter[str] = Counter()
    for row in normalized:
        missing_counter.update(row.get("missing_fields") or [])
    warnings = [*source_warnings]
    if missing_counter:
        warnings.append("Some paper portfolio rows are missing fields required for complete portfolio risk analytics.")
    if aggregations.get("liquidity_exposure", {}).get("liquidity_warning_count"):
        warnings.append("Liquidity, spread, or average-dollar-volume warnings were observed.")
    if cleanup_plan["summary"]["open_item_count"]:
        warnings.append("Portfolio Risk cleanup still has open proof-visibility items.")
    status = "empty" if not normalized else "ready_for_human_review" if cleanup_plan["status"] == "ready_for_human_review" else "needs_evidence"
    summary = {
        "status": status,
        "position_count": len(normalized),
        "gross_exposure": aggregations.get("gross_exposure"),
        "net_exposure": aggregations.get("net_exposure"),
        "long_exposure": aggregations.get("long_exposure"),
        "short_or_proxy_exposure": aggregations.get("short_or_proxy_exposure"),
        "symbol_concentration": aggregations.get("symbol_concentration"),
        "sector_concentration": aggregations.get("sector_concentration"),
        "correlation_heat": aggregations.get("correlation_heat", {}).get("correlation_heat"),
        "liquidity_exposure": aggregations.get("liquidity_exposure", {}).get("liquidity_exposure"),
        "beta_to_SPY": aggregations.get("beta_to_SPY"),
        "beta_to_QQQ": aggregations.get("beta_to_QQQ"),
        "drawdown_state": aggregations.get("drawdown_state", {}).get("drawdown_state"),
        "daily_risk_budget_usage": aggregations.get("daily_risk_budget_usage", {}).get("daily_risk_budget_usage"),
        "open_heat": aggregations.get("open_heat", {}).get("open_heat"),
        "portfolio_risk_proof_ready": proof_summary["proof_ready"],
        "portfolio_risk_proof_status": proof_summary["status"],
        "portfolio_risk_requirements_passed": proof_summary["summary"]["passed_requirement_count"],
        "portfolio_risk_requirements_total": proof_summary["summary"]["requirement_count"],
        "portfolio_risk_coverage": proof_summary["summary"]["portfolio_risk_coverage"],
        "exposure_context_coverage": proof_summary["summary"]["exposure_context_coverage"],
        "factor_context_coverage": proof_summary["summary"]["factor_context_coverage"],
        "liquidity_context_coverage": proof_summary["summary"]["liquidity_context_coverage"],
        "drawdown_budget_context_coverage": proof_summary["summary"]["drawdown_budget_context_coverage"],
        "candidate_strategy_context_coverage": proof_summary["summary"]["candidate_strategy_context_coverage"],
        "portfolio_risk_cleanup_status": cleanup_plan["status"],
        "portfolio_risk_cleanup_open_items": cleanup_plan["summary"]["open_item_count"],
        "portfolio_risk_cleanup_critical_open_items": cleanup_plan["summary"]["critical_open_items"],
        "top_cleanup_item": cleanup_plan["summary"]["top_cleanup_item"],
        "claim_permissions": cleanup_plan["summary"]["claim_permissions"],
        **SAFETY_FLAGS,
    }
    aggregations["portfolio_risk_proof"] = proof_summary
    aggregations["portfolio_risk_cleanup_plan"] = cleanup_plan
    return serialize_value(
        {
            "status": status,
            "generated_at": generated_at or _utc_now(),
            "research_only": True,
            "paper_only": True,
            "summary": summary,
            "records": normalized[:250],
            "proof_summary": proof_summary,
            "portfolio_risk_cleanup_plan": cleanup_plan,
            "aggregations": aggregations,
            "stress_tests": stress_tests,
            "warnings": list(dict.fromkeys(warnings)),
            "missing_fields": dict(missing_counter),
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
            "finish_tracker": build_project_finish_tracker(report_name="portfolio_risk"),
        }
    )


def _subset(report: dict[str, Any], *, records: list[dict[str, Any]], aggregations: dict[str, Any]) -> dict[str, Any]:
    return serialize_value({**report, "records": records, "aggregations": aggregations, "research_only": True, "paper_only": True, "safety_notes": list(SAFETY_NOTES), **SAFETY_FLAGS})


def get_portfolio_risk_summary(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    return build_portfolio_risk_report(db=db, current_user=current_user)


def get_portfolio_risk_exposures(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_portfolio_risk_report(db=db, current_user=current_user)
    return _subset(
        report,
        records=report.get("records", []),
        aggregations={
            "sector_exposure": report.get("aggregations", {}).get("sector_exposure", []),
            "engine_exposure": report.get("aggregations", {}).get("engine_exposure", []),
            "setup_exposure": report.get("aggregations", {}).get("setup_exposure", []),
            "strategy_exposure": report.get("aggregations", {}).get("strategy_exposure", []),
            "regime_exposure": report.get("aggregations", {}).get("regime_exposure", []),
            "forecast_confidence_exposure": report.get("aggregations", {}).get("forecast_confidence_exposure", {}),
        },
    )


def get_portfolio_risk_concentration(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_portfolio_risk_report(db=db, current_user=current_user)
    concentration = report.get("aggregations", {}).get("concentration", {})
    return _subset(report, records=concentration.get("top_symbols", []), aggregations=concentration)


def get_portfolio_risk_correlation(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_portfolio_risk_report(db=db, current_user=current_user)
    correlation = report.get("aggregations", {}).get("correlation_heat", {})
    return _subset(report, records=correlation.get("buckets", []), aggregations=correlation)


def get_portfolio_risk_stress_tests(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_portfolio_risk_report(db=db, current_user=current_user)
    return _subset(report, records=report.get("stress_tests", []), aggregations={"stress_tests": report.get("stress_tests", [])})


def get_portfolio_risk_regimes(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_portfolio_risk_report(db=db, current_user=current_user)
    regime = report.get("aggregations", {}).get("regime_exposure", [])
    return _subset(report, records=regime, aggregations={"regime_exposure": regime})
