from __future__ import annotations

import ast
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypedDict, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from backend.core.config import settings
from backend.services.market_data import (
    EventRevisionSnapshot,
    MarketDataProvider,
    MarketStateSnapshot,
    OptionContractQuote,
    OptionsFlowSnapshot,
    RelativeStrengthSnapshot,
    get_market_data_provider,
    latest_close_from_ohlcv_frame,
    normalize_model_ohlcv_frame,
    resample_ohlcv_to_4h,
)
from backend.services.market_data.yfinance_adapter import YFinanceMarketDataProvider
from backend.services.serialization import serialize_value
from backend.services.storage_utils import write_dataframe_csv


UTC_TZ = ZoneInfo("UTC")
NEW_YORK_TZ = ZoneInfo("America/New_York")


class Metrics(TypedDict):
    accuracy: float
    precision: float
    recall: float
    roc_auc: float


class OptionContract(TypedDict):
    contract_symbol: str
    expiration: str
    strike: float
    bid: float
    ask: float
    mid: float
    last_price: float
    implied_volatility: float
    volume: int
    open_interest: int
    in_the_money: bool
    spread_pct: float
    quote_timestamp: str


class OptionPlan(TypedDict):
    action: str
    option_side: str
    strike_style: str
    days_to_expiration: str
    entry_signal: str
    entry_low_price: float
    entry_high_price: float
    sell_signal: str
    take_profit_1: float
    take_profit_2: float
    stop_loss: float
    invalidation_price: float
    expected_underlying_target: float
    recommended_contract: OptionContract | None


class ExitPlan(TypedDict):
    entry_reference_price: float
    entry_reference_source: str
    direction: str
    initial_stop_price: float
    risk_unit: float
    tp1_price: float
    tp2_price: float
    stop_after_tp1: float
    stop_after_tp2: float
    time_stop_bars: int
    scale_out: dict[str, float]


class ExitEvaluation(TypedDict):
    monitor_action: str
    exit_reason: str
    current_exit_stage: str
    active_stop_price: float
    next_target_price: float
    entry_reference_price: float
    risk_unit: float
    tp1_price: float
    tp2_price: float
    stop_after_tp1: float
    stop_after_tp2: float
    time_stop_bars: int
    bars_held: int
    trade_age_days: float
    tp1_taken: bool
    tp2_taken: bool
    tp1_hit: bool
    tp2_hit: bool
    stop_hit: bool
    time_stop_hit: bool
    data_issue: bool


class PositionSizing(TypedDict):
    account_size: float
    risk_percent: float
    max_risk_dollars: float
    effective_max_risk_dollars: float
    risk_budget_multiplier: float
    contract_mid: float
    estimated_cost_per_contract: float
    max_loss_per_contract: float
    suggested_contracts: float
    total_position_cost: float
    total_max_loss: float
    affordable: bool
    status: str
    reason: str


class EventMarker(TypedDict):
    event_name: str
    event_date: str
    event_class: str
    source: str
    days_until: int | None
    severity: str


class EventRiskInfo(TypedDict):
    event_risk: bool
    event_label: str
    event_reason: str
    next_event_name: str
    next_event_date: str
    next_event_days: int | None
    next_earnings_name: str
    next_earnings_date: str
    next_earnings_days: int | None
    next_macro_name: str
    next_macro_date: str
    next_macro_days: int | None
    next_corporate_name: str
    next_corporate_date: str
    next_corporate_days: int | None
    event_class: str
    event_severity: str
    event_window_label: str
    session_label: str
    trade_posture: str
    primary_event_label: str
    summary: str
    upcoming_events: List[EventMarker]


class NewsHeadline(TypedDict):
    title: str
    publisher: str
    published_at: str
    url: str
    sentiment_score: float
    relevance_weight: float
    source_type: str
    relevance_score: float
    mentioned_tickers: List[str]


class NewsSentimentInfo(TypedDict):
    sentiment_score: float
    confidence: float
    article_count: int
    weighted_article_count: float
    label: str
    lookback_days: int
    updated_at: str
    source: str
    headlines: List[NewsHeadline]


class MarketStateInfo(TypedDict):
    breadth_score: float
    breadth_momentum: float
    market_trend_score: float
    dispersion_score: float
    vix_level: float | None
    vix_change_pct: float
    vix_term_structure: float
    realized_volatility: float
    realized_volatility_percentile: float
    opening_range_bias: float
    opening_range_breakout: float
    time_of_day_bucket: str
    time_of_day_progress: float
    source: str
    source_detail: str
    fetched_at: str
    freshness_seconds: float | None
    freshness_ttl_seconds: int
    freshness_status: str
    cache_status: str
    degraded: bool


class RelativeStrengthInfo(TypedDict):
    benchmark_symbol: str
    sector_symbol: str
    benchmark_relative_return: float
    sector_relative_return: float
    residual_return: float
    peer_momentum_rank: float
    peer_count: int
    relative_strength_score: float
    source: str
    source_detail: str
    fetched_at: str
    freshness_seconds: float | None
    freshness_ttl_seconds: int
    freshness_status: str
    cache_status: str
    degraded: bool


class OptionsFlowInfo(TypedDict):
    implied_volatility: float
    iv_rank: float
    iv_percentile: float
    iv_realized_vol_spread: float
    put_call_open_interest_ratio: float
    call_volume_pressure: float
    put_volume_pressure: float
    net_flow_pressure: float
    skew_score: float
    unusual_volume_score: float
    source: str
    source_detail: str
    fetched_at: str
    freshness_seconds: float | None
    freshness_ttl_seconds: int
    freshness_status: str
    cache_status: str
    degraded: bool


class EventRevisionInfo(TypedDict):
    days_to_earnings: int | None
    event_pressure: float
    analyst_revision_score: float
    target_revision_score: float
    estimate_revision_score: float
    macro_sensitivity: float
    revision_confidence: float
    source: str
    source_detail: str
    fetched_at: str
    freshness_seconds: float | None
    freshness_ttl_seconds: int
    freshness_status: str
    cache_status: str
    degraded: bool


class PredictionEnsembleSummary(TypedDict):
    base_probability_up: float
    technical_probability_up: float
    microstructure_probability_up: float
    relative_strength_probability_up: float
    uncertainty_score: float
    driver_weights: dict[str, float]
    driver_scores: dict[str, float]
    available_drivers: List[str]
    calibration_sample_size: int
    empirical_hit_rate: float | None
    purged_split_count: int
    split_embargo_bars: int


class PriceForecast(TypedDict):
    forecast_horizon_bars: int
    base_probability_up: float
    state_adjusted_probability_up: float
    adjusted_probability_up: float
    technical_probability_up: float
    journal_adjusted_probability_up: float
    adjusted_expected_move: float
    technical_expected_move: float
    expected_price: float
    upper_price: float
    lower_price: float
    confidence_score: float
    uncertainty_score: float
    prediction_data_quality: str
    degraded_prediction: bool
    state_source_map: dict[str, str]
    state_freshness: dict[str, dict[str, object]]
    driver_scores: dict[str, float]
    driver_agreement_score: float
    volatility_regime: str
    relative_strength_score: float
    iv_context: dict[str, float | str | None]
    market_state: MarketStateInfo
    relative_strength: RelativeStrengthInfo
    options_flow: OptionsFlowInfo
    event_revision: EventRevisionInfo
    ensemble_summary: PredictionEnsembleSummary
    label: str
    market_regime: str
    regime_strength_score: float
    contribution_breakdown: dict[str, float | str]
    news_sentiment: NewsSentimentInfo
    journal_calibration: dict[str, object]


class InstitutionalFlowProfile(TypedDict):
    score: float
    label: str
    avg_dollar_volume: float
    median_dollar_volume: float
    controlled_universe: bool
    option_liquidity_score: float
    trade_posture: str
    event_window_label: str
    notes: List[str]


class OptionExecutionProfile(TypedDict):
    execution_score: float
    contract_quality_tier: str
    liquidity_tier: str
    quote_age_seconds: int | None
    dte_bucket: str
    moneyness_bucket: str
    vehicle_recommendation: str
    vehicle_reason: str
    reject_reasons: List[str]


class AnalysisReport(TypedDict):
    ticker: str
    close: float
    base_probability_up: float
    state_adjusted_probability_up: float
    probability_up: float
    probability_not_up: float
    technical_probability_up: float
    verdict: str
    expected_move: float
    technical_expected_move: float
    atr_pct: float
    interval: str
    metrics: Metrics
    notes: List[str]
    feature_importance: None
    option_plan: OptionPlan
    exit_plan: ExitPlan
    alignment_label: str
    alignment_score: float
    conviction_label: str
    is_high_conviction: bool
    setup_score: float
    uncertainty_score: float
    prediction_data_quality: str
    degraded_prediction: bool
    state_source_map: dict[str, str]
    state_freshness: dict[str, dict[str, object]]
    driver_scores: dict[str, float]
    driver_agreement_score: float
    volatility_regime: str
    relative_strength_score: float
    iv_context: dict[str, float | str | None]
    market_state: MarketStateInfo
    relative_strength: RelativeStrengthInfo
    options_flow: OptionsFlowInfo
    event_revision: EventRevisionInfo
    ensemble_summary: PredictionEnsembleSummary
    alpha_score: float
    execution_score: float
    portfolio_score: float
    edge_to_cost_ratio: float
    expected_edge_bps: float
    estimated_cost_bps: float
    spread_bps: float
    average_dollar_volume: float
    average_1m_dollar_volume: float
    quote_age_seconds: float
    proxy_correlation_bucket: str
    portfolio_rank: int | None
    auto_entry_eligible: bool
    setup_grade: str
    trade_decision: str
    reject_reason: str
    event_risk: bool
    event_label: str
    event_reason: str
    next_event_name: str
    next_event_date: str
    event_context: EventRiskInfo
    news_sentiment: NewsSentimentInfo
    forecast: PriceForecast
    market_regime: str
    institutional_flow: InstitutionalFlowProfile
    option_execution_profile: OptionExecutionProfile
    vehicle_recommendation: str
    vehicle_reason: str


class AlertRecord(TypedDict):
    timestamp: str
    ticker: str
    interval: str
    trade_status: str
    alert_type: str
    message: str
    live_price: float
    entry_low: float
    entry_high: float
    target_price: float
    invalidation_price: float
    contract_symbol: str
    verdict: str


class PaperTradeRecord(TypedDict):
    trade_id: str
    opened_at: str
    ticker: str
    interval: str
    verdict: str
    alignment_label: str
    conviction_label: str
    setup_score: float
    alpha_score: float
    execution_score: float
    portfolio_score: float
    edge_to_cost_ratio: float
    proxy_correlation_bucket: str
    portfolio_rank: int | None
    auto_entry_eligible: bool
    setup_grade: str
    trade_decision: str
    reject_reason: str
    event_risk: bool
    event_label: str
    event_reason: str
    next_event_name: str
    next_event_date: str
    live_price_at_open: float
    contract_symbol: str
    contract_mid_at_open: float
    suggested_contracts: float
    position_cost: float
    max_risk_dollars: float
    target_price: float
    invalidation_price: float
    tp1_pct: float
    tp2_pct: float
    status: str


DEFAULT_SCAN_TICKERS: List[str] = [
    "SPY",
    "QQQ",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "AMD",
    "NFLX",
    "AVGO",
    "JPM",
    "BAC",
    "WMT",
    "COST",
    "HD",
    "DIS",
    "KO",
    "PEP",
    "XOM",
    "CVX",
    "PLTR",
    "UBER",
    "CRM",
    "INTC",
    "QCOM",
    "MU",
    "ADBE",
    "ORCL",
]

CONTROLLED_LIQUID_UNIVERSE: List[str] = list(
    dict.fromkeys(
        [
            "SPY",
            "QQQ",
            "IWM",
            "DIA",
            *DEFAULT_SCAN_TICKERS,
        ]
    )
)

_PROXY_CORRELATION_BUCKETS: dict[str, set[str]] = {
    "broad_market": {"SPY", "VOO", "IVV", "QQQ", "IWM", "DIA", "TQQQ", "SQQQ", "UPRO", "SPXL"},
    "semiconductors": {"NVDA", "AMD", "AVGO", "INTC", "TSM", "ASML", "MU", "SMH", "SOXX"},
    "mega_cap_tech": {"AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN", "TSLA", "NFLX"},
    "crypto_proxy": {"COIN", "MSTR", "MARA", "RIOT", "CLSK", "IBIT", "FBTC", "BITO"},
    "banks": {"JPM", "BAC", "GS", "MS", "C", "WFC", "XLF"},
    "energy": {"XOM", "CVX", "COP", "OXY", "SLB", "XLE"},
}

_SECTOR_PROXY_BY_BUCKET: dict[str, str] = {
    "broad_market": "SPY",
    "semiconductors": "SMH",
    "mega_cap_tech": "XLK",
    "crypto_proxy": "IBIT",
    "banks": "XLF",
    "energy": "XLE",
}

_INSTITUTIONAL_DOLLAR_VOLUME_THRESHOLDS: dict[str, tuple[float, float]] = {
    "1m": (15_000_000.0, 4_000_000.0),
    "5m": (45_000_000.0, 12_000_000.0),
    "15m": (110_000_000.0, 30_000_000.0),
    "30m": (220_000_000.0, 60_000_000.0),
    "1h": (350_000_000.0, 90_000_000.0),
    "4h": (700_000_000.0, 180_000_000.0),
    "1d": (2_500_000_000.0, 600_000_000.0),
}


def get_controlled_liquid_universe(limit: int | None = None) -> List[str]:
    universe = list(CONTROLLED_LIQUID_UNIVERSE)
    if limit is None:
        return universe
    return universe[: max(0, int(limit))]


INDEX_EVENT_TICKERS = {"SPY", "QQQ", "IWM", "DIA"}
CBOE_TIER_ONE_OPTION_TICKERS = {"SPY", "QQQ", "IWM"}
CBOE_TIER_TWO_OPTION_TICKERS = {"AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA"}
OPTION_EXECUTION_STRONG_SCORE = 75.0
OPTION_EXECUTION_ACCEPTABLE_SCORE = 60.0
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STORAGE_DIR = Path(settings.storage_dir)

MACRO_EVENTS_PATH = DATA_DIR / "macro_events.csv"

_DOWNLOAD_CACHE: Dict[Tuple[str, str, str], pd.DataFrame] = {}
_DOWNLOAD_CACHE_TS: Dict[Tuple[str, str, str], float] = {}
_BATCH_CACHE: Dict[Tuple[Tuple[str, ...], str, str], Dict[str, pd.DataFrame]] = {}
_BATCH_CACHE_TS: Dict[Tuple[Tuple[str, ...], str, str], float] = {}
_EARNINGS_CACHE: Dict[str, tuple[str, str]] = {}
_EARNINGS_UNSUPPORTED_TICKERS = {
    "SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "SLV", "XLE", "XLF", "XLK",
    "XLI", "XLY", "XLP", "XLV", "XLU", "XLB", "XLRE", "SMH", "ARKK", "VTI",
    "VOO", "VEA", "EEM", "UVXY", "SOXL", "SOXS", "TQQQ", "SQQQ",
}
_CORPORATE_EVENT_CACHE: Dict[str, tuple[str, str]] = {}
_MACRO_EVENTS_CACHE: Optional[pd.DataFrame] = None
_NEWS_SENTIMENT_CACHE: Dict[str, NewsSentimentInfo] = {}
_NEWS_SENTIMENT_CACHE_TS: Dict[str, float] = {}
_NEWS_SENTIMENT_FUTURES: Dict[str, Future[NewsSentimentInfo]] = {}
_NEWS_SENTIMENT_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="news-sentiment")
_LIVE_PRICE_CACHE: Dict[str, float] = {}
_LIVE_PRICE_CACHE_TS: Dict[str, float] = {}
_CONTRACT_MID_CACHE: Dict[str, float] = {}
_CONTRACT_MID_CACHE_TS: Dict[str, float] = {}
_ALIGNMENT_CACHE: Dict[Tuple[str, int, str], tuple[str, float]] = {}
_ALIGNMENT_CACHE_TS: Dict[Tuple[str, int, str], float] = {}
_FILE_READ_CACHE: Dict[str, pd.DataFrame] = {}
_FILE_READ_CACHE_META: Dict[str, tuple[int, int, float]] = {}

LIVE_PRICE_CACHE_TTL_SECONDS = 4.0
CONTRACT_MID_CACHE_TTL_SECONDS = 10.0
ALIGNMENT_CACHE_TTL_SECONDS = 20.0
NEWS_SENTIMENT_RESOLUTION_TIMEOUT_SECONDS = 2.0

TRADE_JOURNAL_PATH = STORAGE_DIR / "trade_journal.csv"
OPEN_TRADES_PATH = STORAGE_DIR / "open_trades.csv"
CLOSED_TRADES_PATH = STORAGE_DIR / "closed_trades.csv"
PENDING_ORDERS_PATH = STORAGE_DIR / "pending_orders.csv"
PAPER_OPEN_TRADES_PATH = STORAGE_DIR / "paper_open_trades.csv"
PAPER_CLOSED_TRADES_PATH = STORAGE_DIR / "paper_closed_trades.csv"
FORECAST_JOURNAL_PATH = STORAGE_DIR / "forecast_journal.csv"
INTRADAY_PREDICTION_STACK_VERSION = "intraday_hybrid_v1"


@dataclass
class ModelConfig:
    ticker: str = "SPY"
    period: str = "60d"
    interval: str = "5m"
    horizon: int = 5
    threshold_up: float = 0.002
    threshold_down: float = -0.002
    min_rows: int = 250
    chart_days: int = 180
    scan_top_n: int = 10
    scan_output_file: str = str(STORAGE_DIR / "scan_results.csv")


def utc_now() -> pd.Timestamp:
    ts = pd.Timestamp.utcnow()
    return ts.tz_localize(None) if ts.tzinfo is not None else ts


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _safe_float(value: object, default: float = float("nan")) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, (int, float, np.integer, np.floating)):
            converted = float(value)
        elif isinstance(value, str):
            converted = float(value.strip())
        else:
            converted = float(str(value))
        if math.isnan(converted):
            return default
        return converted
    except Exception:
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, (int, float, np.integer, np.floating)):
            return int(float(value))
        if isinstance(value, str):
            return int(float(value.strip()))
        return int(float(str(value)))
    except Exception:
        return default


def _institutional_dollar_volume_thresholds(interval: str) -> tuple[float, float]:
    normalized_interval = str(interval or "").strip().lower()
    return _INSTITUTIONAL_DOLLAR_VOLUME_THRESHOLDS.get(normalized_interval, (250_000_000.0, 60_000_000.0))


def build_institutional_flow_profile(
    ticker: str,
    interval: str,
    price: pd.DataFrame,
    contract: OptionContract | None,
    event_info: EventRiskInfo,
) -> InstitutionalFlowProfile:
    normalized_ticker = _normalize_symbol(ticker)
    controlled_universe = normalized_ticker in set(CONTROLLED_LIQUID_UNIVERSE)

    recent_price = normalize_model_ohlcv_frame(price.copy(), normalized_ticker).tail(40)
    dollar_volume = pd.Series(dtype=float)
    if not recent_price.empty:
        close_series = pd.to_numeric(recent_price.get("Close"), errors="coerce")
        volume_series = pd.to_numeric(recent_price.get("Volume"), errors="coerce")
        dollar_volume = (close_series * volume_series).dropna()

    avg_dollar_volume = float(dollar_volume.mean()) if not dollar_volume.empty else float("nan")
    median_dollar_volume = float(dollar_volume.median()) if not dollar_volume.empty else float("nan")
    strong_threshold, acceptable_threshold = _institutional_dollar_volume_thresholds(interval)

    score = 0.45
    notes: List[str] = []

    if controlled_universe:
        score += 0.12
        notes.append("Controlled liquid universe member.")

    if not math.isnan(avg_dollar_volume):
        if avg_dollar_volume >= strong_threshold:
            score += 0.18
            notes.append("Bar dollar volume is strong enough for institutional flow.")
        elif avg_dollar_volume >= acceptable_threshold:
            score += 0.09
            notes.append("Bar dollar volume is acceptable for client-flow execution.")
        elif avg_dollar_volume < acceptable_threshold * 0.35:
            score -= 0.14
            notes.append("Bar dollar volume is thin for institutional-style execution.")

    option_liquidity_score = 0.5
    if contract is not None:
        spread_pct = _safe_float(contract.get("spread_pct"))
        volume = _safe_int(contract.get("volume"))
        open_interest = _safe_int(contract.get("open_interest"))
        if not math.isnan(spread_pct) and spread_pct <= 0.08 and volume >= 100 and open_interest >= 500:
            option_liquidity_score = 0.92
            score += 0.18
            notes.append("Option contract liquidity is strong.")
        elif not math.isnan(spread_pct) and spread_pct <= 0.15 and volume >= 25 and open_interest >= 100:
            option_liquidity_score = 0.7
            score += 0.08
            notes.append("Option contract liquidity is acceptable.")
        else:
            option_liquidity_score = 0.24
            score -= 0.12
            notes.append("Option contract liquidity is weak.")

    trade_posture = str(event_info.get("trade_posture") or "").strip().lower()
    event_window_label = str(event_info.get("event_window_label") or "").strip().lower()
    if bool(event_info.get("event_risk")):
        score -= 0.18
        notes.append("Event risk weakens client-flow quality.")
    elif trade_posture == "clear" and event_window_label in {"quiet_window", "post_event_window", "none"}:
        score += 0.08
        notes.append("Event posture is clean enough for flow execution.")

    score = float(np.clip(score, 0.0, 1.0))
    if score >= 0.78:
        label = "INSTITUTIONAL FLOW STRONG"
    elif score >= 0.6:
        label = "INSTITUTIONAL FLOW ACCEPTABLE"
    elif score >= 0.42:
        label = "FLOW QUALITY MIXED"
    else:
        label = "FLOW QUALITY WEAK"

    return {
        "score": round(score, 4),
        "label": label,
        "avg_dollar_volume": round(avg_dollar_volume, 2) if not math.isnan(avg_dollar_volume) else float("nan"),
        "median_dollar_volume": round(median_dollar_volume, 2) if not math.isnan(median_dollar_volume) else float("nan"),
        "controlled_universe": controlled_universe,
        "option_liquidity_score": round(float(option_liquidity_score), 4),
        "trade_posture": trade_posture,
        "event_window_label": event_window_label,
        "notes": notes,
    }


def _get_option_liquidity_tier(ticker: str) -> str:
    normalized_ticker = _normalize_symbol(ticker)
    if normalized_ticker in CBOE_TIER_ONE_OPTION_TICKERS:
        return "index"
    if normalized_ticker in CBOE_TIER_TWO_OPTION_TICKERS:
        return "mega_cap"
    return "other"


def _get_option_tier_bias(ticker: str) -> float:
    liquidity_tier = _get_option_liquidity_tier(ticker)
    if liquidity_tier == "index":
        return -0.18
    if liquidity_tier == "mega_cap":
        return -0.08
    return 0.0


def _parse_contract_quote_timestamp(value: object) -> pd.Timestamp | None:
    if value in (None, "", "nan"):
        return None
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        if parsed.tzinfo is None:
            return parsed.tz_localize("UTC")
        return parsed.tz_convert("UTC")
    except Exception:
        return None


def _contract_quote_age_seconds(contract: OptionContract | None) -> int | None:
    timestamp = _parse_contract_quote_timestamp(contract.get("quote_timestamp") if contract else None)
    if timestamp is None:
        return None
    now = pd.Timestamp.now(tz="UTC")
    age_seconds = (now - timestamp).total_seconds()
    return max(0, int(age_seconds))


def _contract_days_to_expiration(contract: OptionContract | None) -> int | None:
    if contract is None:
        return None
    expiration_value = str(contract.get("expiration") or "").strip()
    if not expiration_value:
        return None
    try:
        expiration_ts = pd.Timestamp(expiration_value)
        if expiration_ts.tzinfo is None:
            expiration_ts = expiration_ts.tz_localize("UTC")
        else:
            expiration_ts = expiration_ts.tz_convert("UTC")
        today = pd.Timestamp.now(tz="UTC").normalize()
        return int((expiration_ts.normalize() - today).days)
    except Exception:
        return None


def _contract_dte_bucket(contract: OptionContract | None, fallback_label: str = "") -> str:
    dte_days = _contract_days_to_expiration(contract)
    if dte_days is None:
        fallback = str(fallback_label or "").strip()
        return fallback.lower().replace(" ", "_") if fallback else "unknown"
    if dte_days == 0:
        return "0dte"
    if dte_days <= 6:
        return "1_6dte"
    if dte_days <= 14:
        return "7_14dte"
    if dte_days <= 45:
        return "15_45dte"
    if dte_days <= 90:
        return "46_90dte"
    return "90dte_plus"


def _contract_moneyness_bucket(
    option_side: str,
    close_price: float,
    strike: float,
) -> str:
    if close_price <= 0 or strike <= 0 or option_side not in {"CALL", "PUT"}:
        return "unknown"

    if option_side == "CALL":
        relative_gap = (strike - close_price) / close_price
    else:
        relative_gap = (close_price - strike) / close_price

    absolute_gap = abs(relative_gap)
    if absolute_gap <= 0.015:
        return "near_atm"
    if -0.05 <= relative_gap < -0.015:
        return "slightly_itm"
    if 0.015 < relative_gap <= 0.05:
        return "slightly_otm"
    if relative_gap < -0.05:
        return "deep_itm"
    return "deep_otm"


def build_option_execution_profile(
    *,
    ticker: str,
    interval: str,
    close_price: float,
    option_plan: OptionPlan,
    institutional_flow: InstitutionalFlowProfile,
    event_info: EventRiskInfo,
) -> OptionExecutionProfile:
    normalized_ticker = _normalize_symbol(ticker)
    contract = option_plan.get("recommended_contract")
    liquidity_tier = _get_option_liquidity_tier(normalized_ticker)
    option_side = str(option_plan.get("option_side") or "").strip().upper()
    quote_age_seconds = _contract_quote_age_seconds(contract)
    dte_bucket = _contract_dte_bucket(contract, fallback_label=str(option_plan.get("days_to_expiration") or ""))
    moneyness_bucket = _contract_moneyness_bucket(
        option_side,
        close_price,
        _safe_float(contract.get("strike")) if contract else float("nan"),
    )
    spread_pct = _safe_float(contract.get("spread_pct")) if contract else float("nan")
    volume = _safe_int(contract.get("volume")) if contract else 0
    open_interest = _safe_int(contract.get("open_interest")) if contract else 0
    avg_dollar_volume = _safe_float(institutional_flow.get("avg_dollar_volume"))
    strong_dollar_volume, acceptable_dollar_volume = _institutional_dollar_volume_thresholds(interval)

    execution_score = 35.0
    reject_reasons: list[str] = []

    if liquidity_tier == "index":
        execution_score += 12.0
    elif liquidity_tier == "mega_cap":
        execution_score += 7.0

    if contract is None:
        execution_score -= 30.0
        reject_reasons.append("No clean listed option contract found.")
    else:
        if math.isnan(spread_pct):
            execution_score -= 18.0
            reject_reasons.append("Spread is unavailable.")
        elif spread_pct <= 0.08:
            execution_score += 18.0
        elif spread_pct <= 0.15:
            execution_score += 9.0
        else:
            execution_score -= 22.0
            reject_reasons.append("Spread wider than 15%.")

        if volume >= 100 and open_interest >= 500:
            execution_score += 18.0
        elif volume >= 25 and open_interest >= 100:
            execution_score += 8.0
        else:
            execution_score -= 18.0
            if volume < 25:
                reject_reasons.append("Volume below 25 contracts.")
            if open_interest < 100:
                reject_reasons.append("Open interest below 100 contracts.")

        if quote_age_seconds is None:
            execution_score -= 12.0
            reject_reasons.append("Quote timestamp is unavailable.")
        elif quote_age_seconds <= 3:
            execution_score += 12.0
        elif quote_age_seconds <= 10:
            execution_score += 6.0
        else:
            execution_score -= 18.0
            reject_reasons.append("Quote is stale.")

        if dte_bucket in {"7_14dte", "15_45dte"}:
            execution_score += 10.0
        elif dte_bucket == "0dte":
            if normalized_ticker in {"SPY", "QQQ"}:
                execution_score -= 2.0
            else:
                execution_score -= 28.0
                reject_reasons.append("0DTE blocked outside SPY/QQQ.")
        elif dte_bucket == "1_6dte":
            execution_score -= 10.0
            reject_reasons.append("Sub-7DTE contract is outside the default window.")
        elif dte_bucket not in {"unknown"}:
            execution_score -= 6.0

        if moneyness_bucket == "near_atm":
            execution_score += 10.0
        elif moneyness_bucket == "slightly_itm":
            execution_score += 8.0
        elif moneyness_bucket == "slightly_otm":
            execution_score += 2.0
        elif moneyness_bucket == "deep_itm":
            execution_score -= 4.0
        elif moneyness_bucket == "deep_otm":
            execution_score -= 14.0
            reject_reasons.append("Contract is too far OTM.")

    if not math.isnan(avg_dollar_volume):
        if avg_dollar_volume >= strong_dollar_volume:
            execution_score += 10.0
        elif avg_dollar_volume >= acceptable_dollar_volume:
            execution_score += 5.0
        else:
            execution_score -= 12.0
            reject_reasons.append("Underlying dollar volume is too thin.")

    if bool(event_info.get("event_risk")):
        execution_score -= 8.0
        reject_reasons.append("Event posture does not support clean option execution.")

    execution_score = float(np.clip(execution_score, 0.0, 100.0))
    if not reject_reasons and execution_score >= OPTION_EXECUTION_STRONG_SCORE:
        contract_quality_tier = "strong"
    elif execution_score >= OPTION_EXECUTION_ACCEPTABLE_SCORE and not any(
        reason in {
            "Spread wider than 15%.",
            "Open interest below 100 contracts.",
            "Volume below 25 contracts.",
            "Quote is stale.",
            "0DTE blocked outside SPY/QQQ.",
        }
        for reason in reject_reasons
    ):
        contract_quality_tier = "acceptable"
    else:
        contract_quality_tier = "weak"

    return {
        "execution_score": round(execution_score, 2),
        "contract_quality_tier": contract_quality_tier,
        "liquidity_tier": liquidity_tier,
        "quote_age_seconds": quote_age_seconds,
        "dte_bucket": dte_bucket,
        "moneyness_bucket": moneyness_bucket,
        "vehicle_recommendation": "stand_down",
        "vehicle_reason": "Vehicle selection pending.",
        "reject_reasons": list(dict.fromkeys(reason for reason in reject_reasons if reason)),
    }


def select_trade_vehicle(
    *,
    verdict: str,
    trade_decision: str,
    reject_reason: str,
    setup_score: float,
    option_execution_profile: OptionExecutionProfile,
) -> tuple[str, str]:
    normalized_verdict = str(verdict or "").strip().upper()
    normalized_trade_decision = str(trade_decision or "").strip().upper()
    execution_score = _safe_float(option_execution_profile.get("execution_score"), default=0.0)
    contract_quality_tier = str(option_execution_profile.get("contract_quality_tier") or "weak").strip().lower()
    reject_reasons = [str(reason).strip() for reason in option_execution_profile.get("reject_reasons") or [] if str(reason).strip()]

    if normalized_verdict not in {"BULLISH", "BEARISH"}:
        return "stand_down", "Directional signal is not strong enough to express with stock or options."
    if normalized_trade_decision != "VALID TRADE":
        return "stand_down", str(reject_reason or "Signal quality did not clear the promotion gate.")
    if contract_quality_tier == "strong" and execution_score >= OPTION_EXECUTION_STRONG_SCORE:
        return "listed_option", "Signal quality and option execution quality are both strong."
    if setup_score >= 55.0:
        if reject_reasons:
            return "equity", f"Signal is promotable, but the option chain is weaker than stock execution: {reject_reasons[0]}"
        return "equity", "Signal is promotable, but the option chain is not strong enough to justify listed options."
    return "stand_down", str(reject_reason or "Signal quality is too weak to promote.")


def _safe_bool(value: object) -> bool:
    if isinstance(value, np.bool_):
        return True if value.item() else False
    if isinstance(value, bool):
        return True if value else False
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value) != 0.0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _proxy_correlation_bucket_for_ticker(ticker: str) -> str:
    normalized = str(ticker or "").strip().upper()
    for bucket, members in _PROXY_CORRELATION_BUCKETS.items():
        if normalized in members:
            return bucket
    return f"symbol:{normalized or 'UNKNOWN'}"


def _benchmark_symbol_for_ticker(ticker: str) -> str:
    bucket = _proxy_correlation_bucket_for_ticker(ticker)
    if bucket in {"semiconductors", "mega_cap_tech", "crypto_proxy"}:
        return "QQQ"
    return "SPY"


def _sector_symbol_for_ticker(ticker: str) -> str | None:
    bucket = _proxy_correlation_bucket_for_ticker(ticker)
    return _SECTOR_PROXY_BY_BUCKET.get(bucket)


def _peer_symbols_for_ticker(ticker: str, *, limit: int = 6) -> list[str]:
    normalized = _normalize_symbol(ticker)
    bucket = _proxy_correlation_bucket_for_ticker(normalized)
    members = _PROXY_CORRELATION_BUCKETS.get(bucket)
    if not members:
        return []
    peers = [symbol for symbol in members if symbol != normalized]
    return peers[: max(0, int(limit))]


def _snapshot_metadata(
    *,
    source: object,
    source_detail: object = "",
    fetched_at: object = "",
    freshness_seconds: object = None,
    freshness_ttl_seconds: object = 0,
    freshness_status: object = "unknown",
    cache_status: object = "miss",
    degraded: object = False,
) -> dict[str, object]:
    resolved_freshness_seconds = _safe_float(freshness_seconds, float("nan"))
    return {
        "source": str(source or "fallback"),
        "source_detail": str(source_detail or source or "fallback"),
        "fetched_at": str(fetched_at or ""),
        "freshness_seconds": None if math.isnan(resolved_freshness_seconds) else round(float(resolved_freshness_seconds), 4),
        "freshness_ttl_seconds": max(0, _safe_int(freshness_ttl_seconds, 0)),
        "freshness_status": str(freshness_status or "unknown"),
        "cache_status": str(cache_status or "miss"),
        "degraded": _safe_bool(degraded),
    }


def _market_state_snapshot_from_dataclass(snapshot: MarketStateSnapshot) -> MarketStateInfo:
    return {
        "breadth_score": round(_safe_float(snapshot.breadth_score, 0.0), 4),
        "breadth_momentum": round(_safe_float(snapshot.breadth_momentum, 0.0), 4),
        "market_trend_score": round(_safe_float(snapshot.market_trend_score, 0.0), 4),
        "dispersion_score": round(_safe_float(snapshot.dispersion_score, 0.0), 4),
        "vix_level": snapshot.vix_level if isinstance(snapshot.vix_level, (int, float)) else None,
        "vix_change_pct": round(_safe_float(snapshot.vix_change_pct, 0.0), 4),
        "vix_term_structure": round(_safe_float(snapshot.vix_term_structure, 0.0), 4),
        "realized_volatility": round(_safe_float(snapshot.realized_volatility, 0.0), 4),
        "realized_volatility_percentile": round(_safe_float(snapshot.realized_volatility_percentile, 0.5), 4),
        "opening_range_bias": round(_safe_float(snapshot.opening_range_bias, 0.0), 4),
        "opening_range_breakout": round(_safe_float(snapshot.opening_range_breakout, 0.0), 4),
        "time_of_day_bucket": str(snapshot.time_of_day_bucket or "unknown"),
        "time_of_day_progress": round(_safe_float(snapshot.time_of_day_progress, 0.0), 4),
        **_snapshot_metadata(
            source=snapshot.source,
            source_detail=snapshot.source_detail,
            fetched_at=snapshot.fetched_at,
            freshness_seconds=snapshot.freshness_seconds,
            freshness_ttl_seconds=snapshot.freshness_ttl_seconds,
            freshness_status=snapshot.freshness_status,
            cache_status=snapshot.cache_status,
            degraded=snapshot.degraded,
        ),
    }


def _relative_strength_snapshot_from_dataclass(snapshot: RelativeStrengthSnapshot) -> RelativeStrengthInfo:
    return {
        "benchmark_symbol": str(snapshot.benchmark_symbol or "SPY"),
        "sector_symbol": str(snapshot.sector_symbol or ""),
        "benchmark_relative_return": round(_safe_float(snapshot.benchmark_relative_return, 0.0), 4),
        "sector_relative_return": round(_safe_float(snapshot.sector_relative_return, 0.0), 4),
        "residual_return": round(_safe_float(snapshot.residual_return, 0.0), 4),
        "peer_momentum_rank": round(_safe_float(snapshot.peer_momentum_rank, 0.5), 4),
        "peer_count": int(snapshot.peer_count or 0),
        "relative_strength_score": round(_safe_float(snapshot.relative_strength_score, 0.5), 4),
        **_snapshot_metadata(
            source=snapshot.source,
            source_detail=snapshot.source_detail,
            fetched_at=snapshot.fetched_at,
            freshness_seconds=snapshot.freshness_seconds,
            freshness_ttl_seconds=snapshot.freshness_ttl_seconds,
            freshness_status=snapshot.freshness_status,
            cache_status=snapshot.cache_status,
            degraded=snapshot.degraded,
        ),
    }


def _options_flow_snapshot_from_dataclass(snapshot: OptionsFlowSnapshot) -> OptionsFlowInfo:
    return {
        "implied_volatility": round(_safe_float(snapshot.implied_volatility, 0.0), 4),
        "iv_rank": round(_safe_float(snapshot.iv_rank, 0.5), 4),
        "iv_percentile": round(_safe_float(snapshot.iv_percentile, 0.5), 4),
        "iv_realized_vol_spread": round(_safe_float(snapshot.iv_realized_vol_spread, 0.0), 4),
        "put_call_open_interest_ratio": round(_safe_float(snapshot.put_call_open_interest_ratio, 1.0), 4),
        "call_volume_pressure": round(_safe_float(snapshot.call_volume_pressure, 0.0), 4),
        "put_volume_pressure": round(_safe_float(snapshot.put_volume_pressure, 0.0), 4),
        "net_flow_pressure": round(_safe_float(snapshot.net_flow_pressure, 0.0), 4),
        "skew_score": round(_safe_float(snapshot.skew_score, 0.0), 4),
        "unusual_volume_score": round(_safe_float(snapshot.unusual_volume_score, 0.0), 4),
        **_snapshot_metadata(
            source=snapshot.source,
            source_detail=snapshot.source_detail,
            fetched_at=snapshot.fetched_at,
            freshness_seconds=snapshot.freshness_seconds,
            freshness_ttl_seconds=snapshot.freshness_ttl_seconds,
            freshness_status=snapshot.freshness_status,
            cache_status=snapshot.cache_status,
            degraded=snapshot.degraded,
        ),
    }


def _event_revision_snapshot_from_dataclass(snapshot: EventRevisionSnapshot) -> EventRevisionInfo:
    return {
        "days_to_earnings": int(snapshot.days_to_earnings) if snapshot.days_to_earnings is not None else None,
        "event_pressure": round(_safe_float(snapshot.event_pressure, 0.0), 4),
        "analyst_revision_score": round(_safe_float(snapshot.analyst_revision_score, 0.0), 4),
        "target_revision_score": round(_safe_float(snapshot.target_revision_score, 0.0), 4),
        "estimate_revision_score": round(_safe_float(snapshot.estimate_revision_score, 0.0), 4),
        "macro_sensitivity": round(_safe_float(snapshot.macro_sensitivity, 0.0), 4),
        "revision_confidence": round(_safe_float(snapshot.revision_confidence, 0.0), 4),
        **_snapshot_metadata(
            source=snapshot.source,
            source_detail=snapshot.source_detail,
            fetched_at=snapshot.fetched_at,
            freshness_seconds=snapshot.freshness_seconds,
            freshness_ttl_seconds=snapshot.freshness_ttl_seconds,
            freshness_status=snapshot.freshness_status,
            cache_status=snapshot.cache_status,
            degraded=snapshot.degraded,
        ),
    }


def _time_of_day_bucket_from_index(index_value: object) -> tuple[str, float]:
    try:
        ts = pd.Timestamp(index_value)
    except Exception:
        return "unknown", 0.0
    if pd.isna(ts):
        return "unknown", 0.0
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    ts = ts.tz_convert(NEW_YORK_TZ)
    minutes = ts.hour * 60 + ts.minute
    session_start = 9 * 60 + 30
    session_end = 16 * 60
    progress = float(np.clip((minutes - session_start) / max(session_end - session_start, 1), 0.0, 1.0))
    if minutes < session_start:
        return "premarket", progress
    if minutes < 10 * 60 + 30:
        return "opening", progress
    if minutes < 12 * 60:
        return "morning", progress
    if minutes < 14 * 60:
        return "midday", progress
    if minutes <= session_end:
        return "closing", progress
    return "afterhours", progress


def _opening_range_state_from_price(price: pd.DataFrame, *, opening_bars: int = 6) -> tuple[float, float]:
    if price.empty:
        return 0.0, 0.0
    frame = normalize_model_ohlcv_frame(price.copy(), "STATE").tail(max(opening_bars * 8, 32))
    if frame.empty:
        return 0.0, 0.0
    local_index = pd.to_datetime(frame.index, errors="coerce")
    if getattr(local_index, "tz", None) is None:
        local_index = local_index.tz_localize("UTC")
    local_index = local_index.tz_convert(NEW_YORK_TZ)
    session_dates = pd.Series(local_index.normalize(), index=frame.index)
    bar_number = session_dates.groupby(session_dates).cumcount()
    high_series = pd.to_numeric(frame.get("High"), errors="coerce")
    low_series = pd.to_numeric(frame.get("Low"), errors="coerce")
    close_series = pd.to_numeric(frame.get("Close"), errors="coerce")
    opening_high = high_series.where(bar_number < opening_bars).groupby(session_dates).transform("max")
    opening_low = low_series.where(bar_number < opening_bars).groupby(session_dates).transform("min")
    latest_close = _safe_float(close_series.iloc[-1], 0.0)
    latest_high = _safe_float(opening_high.iloc[-1], latest_close)
    latest_low = _safe_float(opening_low.iloc[-1], latest_close)
    opening_range = max(latest_high - latest_low, max(abs(latest_close) * 0.001, 1e-6))
    bias = float(np.clip((latest_close - ((latest_high + latest_low) / 2.0)) / opening_range, -2.0, 2.0) / 2.0)
    breakout = 1.0 if latest_close > latest_high else -1.0 if latest_close < latest_low else 0.0
    return bias, breakout


def _fallback_market_state_info(price: pd.DataFrame, interval: str) -> MarketStateInfo:
    close = pd.to_numeric(price.get("Close"), errors="coerce")
    returns = close.pct_change().dropna()
    realized_volatility = float(returns.tail(20).std()) if len(returns) >= 5 else 0.0
    realized_vol_rank = float(np.clip((returns.abs() <= returns.abs().tail(1).iloc[0]).mean(), 0.0, 1.0)) if not returns.empty else 0.5
    latest_close = _safe_float(close.iloc[-1], 0.0) if not close.empty else 0.0
    sma_20 = _safe_float(close.tail(20).mean(), latest_close) if not close.empty else latest_close
    trend_score = float(np.clip(((latest_close / max(sma_20, 0.01)) - 1.0) * 8.0, -1.0, 1.0)) if latest_close > 0 else 0.0
    bucket, progress = _time_of_day_bucket_from_index(price.index[-1] if not price.empty else None)
    opening_bias, opening_breakout = _opening_range_state_from_price(price)
    return {
        "breadth_score": 0.0,
        "breadth_momentum": 0.0,
        "market_trend_score": round(trend_score, 4),
        "dispersion_score": 0.0,
        "vix_level": None,
        "vix_change_pct": 0.0,
        "vix_term_structure": 0.0,
        "realized_volatility": round(realized_volatility, 4),
        "realized_volatility_percentile": round(realized_vol_rank, 4),
        "opening_range_bias": round(opening_bias, 4),
        "opening_range_breakout": round(opening_breakout, 4),
        "time_of_day_bucket": bucket,
        "time_of_day_progress": round(progress, 4),
        **_snapshot_metadata(
            source=f"price-fallback:{interval}",
            source_detail="price_history_fallback",
            freshness_status="degraded",
            cache_status="fallback",
            degraded=True,
        ),
    }


def _neutral_relative_strength_info(
    *,
    benchmark_symbol: str = "SPY",
    sector_symbol: str = "",
    source: str = "fallback",
) -> RelativeStrengthInfo:
    return {
        "benchmark_symbol": benchmark_symbol,
        "sector_symbol": sector_symbol,
        "benchmark_relative_return": 0.0,
        "sector_relative_return": 0.0,
        "residual_return": 0.0,
        "peer_momentum_rank": 0.5,
        "peer_count": 0,
        "relative_strength_score": 0.5,
        **_snapshot_metadata(
            source=source,
            source_detail=f"{source}_relative_strength",
            freshness_status="degraded",
            cache_status="fallback",
            degraded=True,
        ),
    }


def _neutral_options_flow_info(source: str = "fallback") -> OptionsFlowInfo:
    return {
        "implied_volatility": 0.0,
        "iv_rank": 0.5,
        "iv_percentile": 0.5,
        "iv_realized_vol_spread": 0.0,
        "put_call_open_interest_ratio": 1.0,
        "call_volume_pressure": 0.0,
        "put_volume_pressure": 0.0,
        "net_flow_pressure": 0.0,
        "skew_score": 0.0,
        "unusual_volume_score": 0.0,
        **_snapshot_metadata(
            source=source,
            source_detail=f"{source}_options_flow",
            freshness_status="degraded",
            cache_status="fallback",
            degraded=True,
        ),
    }


def _neutral_event_revision_info(source: str = "fallback") -> EventRevisionInfo:
    return {
        "days_to_earnings": None,
        "event_pressure": 0.0,
        "analyst_revision_score": 0.0,
        "target_revision_score": 0.0,
        "estimate_revision_score": 0.0,
        "macro_sensitivity": 0.0,
        "revision_confidence": 0.0,
        **_snapshot_metadata(
            source=source,
            source_detail=f"{source}_event_revision",
            freshness_status="degraded",
            cache_status="fallback",
            degraded=True,
        ),
    }


def _estimate_average_dollar_volume(frame: pd.DataFrame | None, *, lookback: int = 20) -> float:
    if frame is None or frame.empty:
        return float("nan")
    tail = frame.tail(max(int(lookback), 1)).copy()
    close_source = tail.get("close")
    if close_source is None:
        close_source = tail.get("Close")
    volume_source = tail.get("volume")
    if volume_source is None:
        volume_source = tail.get("Volume")
    close_series = pd.to_numeric(close_source, errors="coerce")
    volume_series = pd.to_numeric(volume_source, errors="coerce")
    if close_series.empty or volume_series.empty:
        return float("nan")
    dollar_volume = (close_series * volume_series).dropna()
    if dollar_volume.empty:
        return float("nan")
    return float(dollar_volume.mean())


def _estimate_intraday_dollar_volume(frame: pd.DataFrame | None, *, lookback: int = 5) -> float:
    return _estimate_average_dollar_volume(frame, lookback=lookback)


def _estimate_quote_age_seconds(quote_timestamp: object) -> float:
    cleaned = str(quote_timestamp or "").strip()
    if not cleaned:
        return float("nan")
    try:
        parsed = pd.to_datetime(cleaned, errors="coerce", utc=True)
    except Exception:
        parsed = pd.NaT
    if pd.isna(parsed):
        return float("nan")
    now_utc = pd.Timestamp.now(tz="UTC")
    age_seconds = (now_utc - parsed).total_seconds()
    return float(max(age_seconds, 0.0))


def _estimate_spread_bps_proxy(
    *,
    contract: OptionContract | None,
    average_dollar_volume: float,
    controlled_universe: bool,
) -> float:
    if contract is not None:
        spread_pct = _safe_float(contract.get("spread_pct"))
        if not math.isnan(spread_pct) and spread_pct >= 0:
            return float(max(spread_pct * 10000.0, 0.0))
    if not math.isnan(average_dollar_volume):
        if average_dollar_volume >= 2_500_000_000.0:
            return 3.0
        if average_dollar_volume >= 500_000_000.0:
            return 5.0
        if average_dollar_volume >= 100_000_000.0:
            return 8.0
        if average_dollar_volume >= 25_000_000.0:
            return 12.0
    return 6.0 if controlled_universe else 18.0


def _estimate_option_delta_proxy(
    *,
    verdict: str,
    contract: OptionContract | None,
    underlying_price: float,
) -> float:
    if contract is None or underlying_price <= 0:
        return 0.5
    strike = _safe_float(contract.get("strike"))
    if math.isnan(strike) or strike <= 0:
        return 0.5
    option_type = str(contract.get("option_type") or "").strip().lower()
    if option_type not in {"call", "put"}:
        option_type = "call" if str(verdict or "").strip().upper() == "BULLISH" else "put"
    moneyness = strike / max(float(underlying_price), 0.01)
    if option_type == "call":
        if moneyness <= 0.97:
            return 0.68
        if moneyness <= 1.03:
            return 0.55
        return 0.38
    if moneyness >= 1.03:
        return 0.68
    if moneyness >= 0.97:
        return 0.55
    return 0.38


def _build_analysis_scoring_context(
    *,
    ticker: str,
    close_price: float,
    probability_up: float,
    expected_move: float,
    alignment_label: str,
    conviction_label: str,
    contract: OptionContract | None,
    forecast: PriceForecast,
    institutional_flow: InstitutionalFlowProfile,
    price_frame: pd.DataFrame | None,
    trade_decision: str,
    event_risk: bool,
) -> dict[str, float | str | bool]:
    regime_strength_score = _safe_float(forecast.get("regime_strength_score"), 0.5)
    forecast_confidence = _safe_float(forecast.get("confidence_score"), 0.5)
    uncertainty_score = _safe_float(forecast.get("uncertainty_score"), 0.35)
    degraded_prediction = _safe_bool(forecast.get("degraded_prediction"))
    prediction_data_quality = str(forecast.get("prediction_data_quality") or "degraded_fallback").strip().lower()
    driver_agreement_score = _safe_float(forecast.get("driver_agreement_score"), 0.5)
    relative_strength_score = _safe_float(forecast.get("relative_strength_score"), 0.5)
    institutional_flow_score = _safe_float(institutional_flow.get("score"), 0.5)
    volatility_regime = str(forecast.get("volatility_regime") or "normal").strip().lower()
    average_dollar_volume = _estimate_average_dollar_volume(price_frame)
    average_1m_dollar_volume = _estimate_intraday_dollar_volume(price_frame)
    controlled_universe = ticker in get_controlled_liquid_universe()
    spread_bps = _estimate_spread_bps_proxy(
        contract=contract,
        average_dollar_volume=average_dollar_volume,
        controlled_universe=controlled_universe,
    )
    quote_age_seconds = _estimate_quote_age_seconds((contract or {}).get("quote_timestamp"))
    if math.isnan(quote_age_seconds):
        quote_age_seconds = 30.0 if controlled_universe else 120.0

    alignment_component = {
        "BULLISH ALIGNMENT": 100.0,
        "BEARISH ALIGNMENT": 100.0,
        "PARTIAL ALIGNMENT": 68.0,
        "MIXED ALIGNMENT": 25.0,
        "NO DATA": 0.0,
    }.get(alignment_label, 50.0)
    conviction_component = {
        "HIGH CONVICTION CALL": 100.0,
        "HIGH CONVICTION PUT": 100.0,
        "MEDIUM CONVICTION": 68.0,
        "LOW CONVICTION": 35.0,
        "NO TRADE": 0.0,
    }.get(conviction_label, 45.0)
    probability_component = min(abs(float(probability_up) - 0.5) * 200.0, 100.0)
    alpha_score = float(
        np.clip(
            (alignment_component * 0.24)
            + (conviction_component * 0.2)
            + (probability_component * 0.18)
            + (forecast_confidence * 100.0 * 0.14)
            + (regime_strength_score * 100.0 * 0.08)
            + (institutional_flow_score * 100.0 * 0.08)
            + (driver_agreement_score * 100.0 * 0.05)
            + (relative_strength_score * 100.0 * 0.05)
            - (10.0 if degraded_prediction else 0.0)
            - (uncertainty_score * 100.0 * 0.06),
            0.0,
            100.0,
        )
    )

    expected_edge_bps = float("nan")
    if close_price > 0 and not math.isnan(expected_move):
        expected_edge_bps = abs(float(expected_move) / float(close_price)) * 10000.0
    estimated_cost_bps = float(max(4.0 + (spread_bps / 2.0), 0.01))
    edge_to_cost_ratio = (
        float(expected_edge_bps / estimated_cost_bps)
        if not math.isnan(expected_edge_bps) and estimated_cost_bps > 0
        else float("nan")
    )

    if spread_bps <= 5.0:
        spread_component = 95.0
    elif spread_bps <= 10.0:
        spread_component = 82.0
    elif spread_bps <= 20.0:
        spread_component = 68.0
    elif spread_bps <= 35.0:
        spread_component = 52.0
    else:
        spread_component = 20.0

    if contract is not None:
        volume = _safe_int(contract.get("volume"))
        open_interest = _safe_int(contract.get("open_interest"))
        volume_component = 96.0 if volume >= 500 else 78.0 if volume >= 100 else 55.0 if volume >= 25 else 20.0
        open_interest_component = 96.0 if open_interest >= 1000 else 78.0 if open_interest >= 500 else 58.0 if open_interest >= 100 else 20.0
    else:
        if not math.isnan(average_dollar_volume) and average_dollar_volume >= 2_500_000_000.0:
            volume_component = 95.0
        elif not math.isnan(average_dollar_volume) and average_dollar_volume >= 500_000_000.0:
            volume_component = 82.0
        elif not math.isnan(average_dollar_volume) and average_dollar_volume >= 100_000_000.0:
            volume_component = 68.0
        else:
            volume_component = 42.0
        open_interest_component = volume_component

    freshness_component = (
        96.0
        if quote_age_seconds <= 30.0
        else 80.0
        if quote_age_seconds <= 120.0
        else 56.0
        if quote_age_seconds <= 300.0
        else 28.0
    )
    if math.isnan(edge_to_cost_ratio):
        edge_component = 38.0
    else:
        edge_component = float(np.clip(edge_to_cost_ratio / 4.0 * 100.0, 0.0, 100.0))

    execution_score = float(
        np.clip(
            (spread_component * 0.24)
            + (volume_component * 0.22)
            + (open_interest_component * 0.16)
            + (freshness_component * 0.16)
            + (edge_component * 0.18)
            + (driver_agreement_score * 100.0 * 0.04),
            0.0,
            100.0,
        )
    )
    if prediction_data_quality == "cached_hybrid_paid_data":
        execution_score = max(execution_score - 4.0, 0.0)
    if degraded_prediction:
        execution_score = max(execution_score - 10.0, 0.0)
    portfolio_score = float(
        np.clip(
            (alpha_score * 0.56)
            + (execution_score * 0.39)
            + (relative_strength_score * 100.0 * 0.05)
            - (8.0 if volatility_regime == "elevated" else 0.0)
            - (6.0 if degraded_prediction else 0.0)
            - (uncertainty_score * 8.0),
            0.0,
            100.0,
        )
    )
    auto_entry_eligible = bool(
        str(trade_decision or "").strip().upper() == "VALID TRADE"
        and not bool(event_risk)
        and execution_score >= 60.0
        and portfolio_score >= 65.0
        and uncertainty_score <= 0.72
    )
    return {
        "alpha_score": round(alpha_score, 2),
        "execution_score": round(execution_score, 2),
        "portfolio_score": round(portfolio_score, 2),
        "edge_to_cost_ratio": round(edge_to_cost_ratio, 4) if not math.isnan(edge_to_cost_ratio) else float("nan"),
        "expected_edge_bps": round(expected_edge_bps, 4) if not math.isnan(expected_edge_bps) else float("nan"),
        "estimated_cost_bps": round(estimated_cost_bps, 4),
        "spread_bps": round(spread_bps, 4),
        "average_dollar_volume": round(average_dollar_volume, 4) if not math.isnan(average_dollar_volume) else float("nan"),
        "average_1m_dollar_volume": round(average_1m_dollar_volume, 4) if not math.isnan(average_1m_dollar_volume) else float("nan"),
        "quote_age_seconds": round(quote_age_seconds, 4),
        "uncertainty_score": round(uncertainty_score, 4),
        "driver_agreement_score": round(driver_agreement_score, 4),
        "relative_strength_score": round(relative_strength_score, 4),
        "volatility_regime": volatility_regime,
        "proxy_correlation_bucket": _proxy_correlation_bucket_for_ticker(ticker),
        "portfolio_rank": None,
        "auto_entry_eligible": auto_entry_eligible,
    }


def _cache_is_fresh(timestamp: float | None, ttl_seconds: float) -> bool:
    if timestamp is None:
        return False
    return (time.monotonic() - float(timestamp)) <= max(ttl_seconds, 0.0)


def _file_cache_key(file_path: Path) -> str:
    return str(file_path.resolve())


def _invalidate_file_read_cache(file_path: Path) -> None:
    cache_key = _file_cache_key(file_path)
    _FILE_READ_CACHE.pop(cache_key, None)
    _FILE_READ_CACHE_META.pop(cache_key, None)


def _read_csv_cached(file_path: Path) -> pd.DataFrame:
    if not file_path.exists():
        _invalidate_file_read_cache(file_path)
        return pd.DataFrame()

    cache_key = _file_cache_key(file_path)
    try:
        stat_result = file_path.stat()
        current_meta = (int(stat_result.st_mtime_ns), int(stat_result.st_size), float(stat_result.st_ctime_ns))
        cached_meta = _FILE_READ_CACHE_META.get(cache_key)
        cached_df = _FILE_READ_CACHE.get(cache_key)
        if cached_df is not None and cached_meta == current_meta:
            return cached_df.copy()

        loaded = pd.read_csv(file_path)
        _FILE_READ_CACHE[cache_key] = loaded.copy()
        _FILE_READ_CACHE_META[cache_key] = current_meta
        return loaded
    except Exception:
        _invalidate_file_read_cache(file_path)
        return pd.DataFrame()


def _download_cache_ttl_seconds(interval: str) -> float:
    if interval == "1m":
        return 4.0
    if interval == "5m":
        return 12.0
    if interval == "4h":
        return 60.0
    return 15.0


def _coerce_timestamp(value: object) -> Optional[pd.Timestamp]:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.tz_localize(None) if value.tzinfo is not None else value
    if isinstance(value, np.datetime64):
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            return None
        return ts.tz_localize(None) if ts.tzinfo is not None else ts
    if isinstance(value, (datetime, date, str, int, float, np.integer, np.floating)):
        try:
            ts = pd.Timestamp(value)
            if pd.isna(ts):
                return None
            return ts.tz_localize(None) if ts.tzinfo is not None else ts
        except Exception:
            return None
    try:
        ts = pd.Timestamp(str(value))
        if pd.isna(ts):
            return None
        return ts.tz_localize(None) if ts.tzinfo is not None else ts
    except Exception:
        return None


def _coerce_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): inner for key, inner in value.items()}
    if value is None:
        return {}
    if isinstance(value, float) and math.isnan(value):
        return {}

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return {}

    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return {str(key): inner for key, inner in parsed.items()}
    return {}


def _has_text_value(value: object) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    if isinstance(value, float) and math.isnan(value):
        return False
    text = str(value).strip()
    return bool(text and text.lower() not in {"nan", "none", "null"})


def _coerce_text(value: object, default: str = "") -> str:
    return str(value).strip() if _has_text_value(value) else default


def build_exit_plan(
    *,
    verdict: str,
    entry_reference_price: float,
    invalidation_price: float,
    time_stop_bars: int,
    entry_reference_source: str,
) -> ExitPlan:
    normalized_verdict = str(verdict or "").strip().upper()
    direction = "flat"
    direction_multiplier = 0.0
    if normalized_verdict == "BULLISH":
        direction = "long"
        direction_multiplier = 1.0
    elif normalized_verdict == "BEARISH":
        direction = "short"
        direction_multiplier = -1.0

    normalized_entry = _safe_float(entry_reference_price)
    normalized_stop = _safe_float(invalidation_price)
    risk_unit = float("nan")
    tp1_price = float("nan")
    tp2_price = float("nan")
    stop_after_tp1 = float("nan")
    stop_after_tp2 = float("nan")

    if (
        direction_multiplier != 0.0
        and not math.isnan(normalized_entry)
        and not math.isnan(normalized_stop)
    ):
        raw_risk_unit = abs(normalized_entry - normalized_stop)
        if raw_risk_unit > 0:
            risk_unit = float(raw_risk_unit)
            tp1_price = float(normalized_entry + (risk_unit * direction_multiplier))
            tp2_price = float(normalized_entry + ((risk_unit * 2.0) * direction_multiplier))
            stop_after_tp1 = float(normalized_entry)
            stop_after_tp2 = float(tp1_price)

    return {
        "entry_reference_price": normalized_entry,
        "entry_reference_source": str(entry_reference_source or "").strip() or "unknown",
        "direction": direction,
        "initial_stop_price": normalized_stop,
        "risk_unit": risk_unit,
        "tp1_price": tp1_price,
        "tp2_price": tp2_price,
        "stop_after_tp1": stop_after_tp1,
        "stop_after_tp2": stop_after_tp2,
        "time_stop_bars": max(0, _safe_int(time_stop_bars, 0)),
        "scale_out": {
            "tp1_fraction": 0.5,
            "tp2_fraction": 0.5,
        },
    }


def _build_report_exit_plan(report: AnalysisReport, *, entry_reference_price: float, entry_reference_source: str) -> ExitPlan:
    option_plan = dict(report.get("option_plan") or {})
    forecast = dict(report.get("forecast") or {})
    return build_exit_plan(
        verdict=str(report.get("verdict") or ""),
        entry_reference_price=entry_reference_price,
        invalidation_price=_safe_float(option_plan.get("invalidation_price")),
        time_stop_bars=_safe_int(forecast.get("forecast_horizon_bars"), 0),
        entry_reference_source=entry_reference_source,
    )


def _compute_trade_age_metrics(opened_at: object, interval: object) -> tuple[int, float]:
    opened_ts = _coerce_timestamp(opened_at)
    if opened_ts is None:
        return 0, 0.0

    current_ts = _coerce_timestamp(utc_now())
    if current_ts is None:
        return 0, 0.0

    elapsed_seconds = max((current_ts - opened_ts).total_seconds(), 0.0)
    bar_seconds = max(interval_seconds(str(interval or "")), 1)
    bars_held = int(elapsed_seconds // bar_seconds)
    trade_age_days = float(elapsed_seconds / (60.0 * 60.0 * 24.0))
    return bars_held, trade_age_days


def evaluate_open_trade_exit(
    trade_row: dict[str, object],
    *,
    current_underlying_price: float,
    current_contract_mid: float,
) -> ExitEvaluation:
    normalized_row = dict(trade_row or {})
    exit_plan = _coerce_dict(normalized_row.get("exit_plan"))
    if not exit_plan:
        exit_plan = build_exit_plan(
            verdict=str(normalized_row.get("verdict") or ""),
            entry_reference_price=_safe_float(
                normalized_row.get("live_price_at_open"),
                _safe_float(normalized_row.get("entry_price")),
            ),
            invalidation_price=_safe_float(normalized_row.get("invalidation_price")),
            time_stop_bars=_safe_int(
                normalized_row.get("horizon_bars"),
                _safe_int(normalized_row.get("horizon"), 0),
            ),
            entry_reference_source="legacy_open_trade",
        )

    tp1_taken = _has_text_value(normalized_row.get("tp1_taken_at")) or _has_text_value(
        normalized_row.get("automation_tp1_taken_at")
    )
    tp2_taken = _has_text_value(normalized_row.get("tp2_taken_at")) or _has_text_value(
        normalized_row.get("automation_tp2_taken_at")
    )
    initial_stop_price = _safe_float(exit_plan.get("initial_stop_price"))
    stop_after_tp1 = _safe_float(exit_plan.get("stop_after_tp1"))
    stop_after_tp2 = _safe_float(exit_plan.get("stop_after_tp2"))
    active_stop_price = _safe_float(normalized_row.get("active_stop_price"))
    if math.isnan(active_stop_price):
        if tp2_taken and not math.isnan(stop_after_tp2):
            active_stop_price = stop_after_tp2
        elif tp1_taken and not math.isnan(stop_after_tp1):
            active_stop_price = stop_after_tp1
        else:
            active_stop_price = initial_stop_price

    tp1_price = _safe_float(exit_plan.get("tp1_price"))
    tp2_price = _safe_float(exit_plan.get("tp2_price"))
    time_stop_bars = max(0, _safe_int(exit_plan.get("time_stop_bars"), 0))
    bars_held, trade_age_days = _compute_trade_age_metrics(
        normalized_row.get("opened_at"),
        normalized_row.get("interval"),
    )

    verdict = str(normalized_row.get("verdict") or "").strip().upper()
    current_underlying = _safe_float(current_underlying_price)
    current_contract = _safe_float(current_contract_mid)
    data_issue = math.isnan(current_underlying)
    tp1_hit = False
    tp2_hit = False
    stop_hit = False
    time_stop_hit = False
    action = "HOLD"
    exit_reason = _coerce_text(normalized_row.get("last_exit_reason")) or _coerce_text(
        normalized_row.get("automation_last_manage_action")
    )

    bullish = verdict == "BULLISH"
    bearish = verdict == "BEARISH"
    if not data_issue and bullish:
        stop_hit = not math.isnan(active_stop_price) and current_underlying <= active_stop_price
        tp2_hit = (not tp2_taken) and (not math.isnan(tp2_price)) and current_underlying >= tp2_price
        tp1_hit = (not tp1_taken) and (not math.isnan(tp1_price)) and current_underlying >= tp1_price
    elif not data_issue and bearish:
        stop_hit = not math.isnan(active_stop_price) and current_underlying >= active_stop_price
        tp2_hit = (not tp2_taken) and (not math.isnan(tp2_price)) and current_underlying <= tp2_price
        tp1_hit = (not tp1_taken) and (not math.isnan(tp1_price)) and current_underlying <= tp1_price

    if time_stop_bars > 0 and bars_held >= time_stop_bars:
        time_stop_hit = True

    if data_issue:
        action = "DATA ISSUE"
        exit_reason = exit_reason or "data_issue"
    elif stop_hit:
        action = "STOP HIT"
        exit_reason = "active_stop_hit"
    elif tp2_hit:
        action = "SELL MORE NOW"
        exit_reason = "take_profit_2"
    elif tp1_hit:
        action = "SELL 50% NOW"
        exit_reason = "take_profit_1"
    elif time_stop_hit:
        action = "TIME STOP"
        exit_reason = "time_stop"

    current_exit_stage = "INITIAL"
    if tp2_taken:
        current_exit_stage = "TP2_LOCKED"
    elif tp1_taken:
        current_exit_stage = "TP1_LOCKED"

    next_target_price = float("nan")
    if not tp1_taken:
        next_target_price = tp1_price
    elif not tp2_taken:
        next_target_price = tp2_price

    return {
        "monitor_action": action,
        "exit_reason": exit_reason,
        "current_exit_stage": current_exit_stage,
        "active_stop_price": active_stop_price,
        "next_target_price": next_target_price,
        "entry_reference_price": _safe_float(exit_plan.get("entry_reference_price")),
        "risk_unit": _safe_float(exit_plan.get("risk_unit")),
        "tp1_price": tp1_price,
        "tp2_price": tp2_price,
        "stop_after_tp1": stop_after_tp1,
        "stop_after_tp2": stop_after_tp2,
        "time_stop_bars": time_stop_bars,
        "bars_held": bars_held,
        "trade_age_days": trade_age_days,
        "tp1_taken": tp1_taken,
        "tp2_taken": tp2_taken,
        "tp1_hit": tp1_hit,
        "tp2_hit": tp2_hit,
        "stop_hit": stop_hit,
        "time_stop_hit": time_stop_hit,
        "data_issue": data_issue,
    }


def _session_label_from_timestamp(value: object) -> str:
    ts = _coerce_timestamp(value)
    if ts is None:
        return ""
    try:
        if ts.tzinfo is None:
            ts = ts.tz_localize(UTC_TZ)
        else:
            ts = ts.tz_convert(UTC_TZ)
        eastern_ts = ts.tz_convert(NEW_YORK_TZ)
    except Exception:
        return ""

    minutes = (eastern_ts.hour * 60) + eastern_ts.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "premarket"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular"
    if 16 * 60 <= minutes < 20 * 60:
        return "after_hours"
    return "overnight"


def _event_window_label_from_row(row: pd.Series) -> str:
    explicit_label = str(row.get("event_window_label") or "").strip().lower()
    if explicit_label:
        return explicit_label
    event_risk = _safe_bool(row.get("event_risk", False))
    next_event_name = str(row.get("next_event_name") or "").strip().lower()
    event_label = str(row.get("event_label") or "").strip().lower()
    event_reason = str(row.get("event_reason") or "").strip().lower()

    if not event_risk:
        return "quiet_window"
    if "earnings" in next_event_name or "earnings" in event_label or "earnings" in event_reason:
        return "earnings_window"
    if any(keyword in next_event_name for keyword in ["dividend", "split", "shareholder", "meeting", "investor"]):
        return "corporate_window"
    if next_event_name:
        return "macro_window"
    return "event_window"


def _align_timestamp_to_index_timezone(
    timestamp: Optional[pd.Timestamp],
    index: pd.Index,
) -> Optional[pd.Timestamp]:
    if timestamp is None or not isinstance(index, pd.DatetimeIndex):
        return timestamp

    index_tz = index.tz
    if index_tz is None:
        if timestamp.tzinfo is not None:
            try:
                return timestamp.tz_convert(None)
            except TypeError:
                return timestamp.tz_localize(None)
        return timestamp

    if timestamp.tzinfo is None:
        return timestamp.tz_localize(index_tz)
    return timestamp.tz_convert(index_tz)


def _empty_metrics() -> Metrics:
    return {
        "accuracy": 0.0,
        "precision": 0.0,
        "recall": 0.0,
        "roc_auc": float("nan"),
    }


def get_period_for_interval(interval: str) -> str:
    if interval == "1m":
        return "7d"
    if interval == "5m":
        return "60d"
    if interval == "4h":
        return "730d"
    return "60d"


def get_thresholds_for_interval(interval: str) -> Tuple[float, float]:
    if interval == "1m":
        return 0.0015, -0.0015
    if interval == "5m":
        return 0.0025, -0.0025
    if interval == "4h":
        return 0.01, -0.01
    return 0.0025, -0.0025


def interval_seconds(interval: str) -> int:
    normalized = str(interval or "").strip().lower()
    return {
        "1m": 60,
        "5m": 60 * 5,
        "15m": 60 * 15,
        "30m": 60 * 30,
        "1h": 60 * 60,
        "4h": 60 * 60 * 4,
        "1d": 60 * 60 * 24,
    }.get(normalized, 60 * 5)


def download_ohlcv(symbol: str, period: str, interval: str) -> pd.DataFrame:
    symbol = _normalize_symbol(symbol)
    provider = get_market_data_provider()
    cache_key = (symbol, period, interval)
    ttl_seconds = _download_cache_ttl_seconds(interval)
    cache_timestamp = _DOWNLOAD_CACHE_TS.get(cache_key)
    if cache_key in _DOWNLOAD_CACHE and _cache_is_fresh(cache_timestamp, ttl_seconds):
        return _DOWNLOAD_CACHE[cache_key].copy()

    if interval == "4h":
        raw = provider.download_bars(
            symbol,
            period=period,
            interval="60m",
            prepost=True,
        )
        clean = resample_ohlcv_to_4h(normalize_model_ohlcv_frame(raw, symbol))
    else:
        raw = provider.download_bars(
            symbol,
            period=period,
            interval=interval,
            prepost=interval != "1d",
        )
        clean = normalize_model_ohlcv_frame(raw, symbol)

    _DOWNLOAD_CACHE[cache_key] = clean.copy()
    _DOWNLOAD_CACHE_TS[cache_key] = time.monotonic()
    return clean.copy()


def _extract_symbol_frame_from_batch_download(downloaded: pd.DataFrame, symbol: str) -> pd.DataFrame:
    symbol_upper = _normalize_symbol(symbol)
    if downloaded.empty:
        raise ValueError(f"No batch data returned for {symbol_upper}")

    if isinstance(downloaded.columns, pd.MultiIndex):
        level0 = {str(value) for value in downloaded.columns.get_level_values(0)}
        if symbol_upper in level0:
            subset = downloaded.loc[:, symbol_upper]
            if isinstance(subset, pd.Series):
                subset = subset.to_frame()
            return normalize_model_ohlcv_frame(pd.DataFrame(subset), symbol_upper)

        level1 = {str(value) for value in downloaded.columns.get_level_values(1)}
        if symbol_upper in level1:
            subset = downloaded.xs(symbol_upper, axis=1, level=1, drop_level=True)
            if isinstance(subset, pd.Series):
                subset = subset.to_frame()
            return normalize_model_ohlcv_frame(pd.DataFrame(subset), symbol_upper)

    return normalize_model_ohlcv_frame(downloaded, symbol_upper)


def batch_download_ohlcv(symbols: List[str], period: str, interval: str) -> Dict[str, pd.DataFrame]:
    normalized_symbols = tuple(sorted({_normalize_symbol(symbol) for symbol in symbols if symbol.strip()}))
    provider = get_market_data_provider()
    cache_key = (normalized_symbols, period, interval)
    ttl_seconds = _download_cache_ttl_seconds(interval)
    cache_timestamp = _BATCH_CACHE_TS.get(cache_key)

    if cache_key in _BATCH_CACHE and _cache_is_fresh(cache_timestamp, ttl_seconds):
        return {symbol: frame.copy() for symbol, frame in _BATCH_CACHE[cache_key].items()}

    if not normalized_symbols:
        return {}

    results: Dict[str, pd.DataFrame] = {}
    raw_interval = "60m" if interval == "4h" else interval

    try:
        downloaded = provider.download_bars(
            list(normalized_symbols),
            period=period,
            interval=raw_interval,
            group_by="ticker",
            prepost=interval != "1d",
        )
    except Exception:
        downloaded = pd.DataFrame()

    if isinstance(downloaded, pd.DataFrame) and not downloaded.empty:
        for symbol in normalized_symbols:
            try:
                frame = _extract_symbol_frame_from_batch_download(downloaded, symbol)
                if interval == "4h":
                    frame = resample_ohlcv_to_4h(frame)
                results[symbol] = frame
                individual_cache_key = (symbol, period, interval)
                _DOWNLOAD_CACHE[individual_cache_key] = frame.copy()
                _DOWNLOAD_CACHE_TS[individual_cache_key] = time.monotonic()
            except Exception:
                continue

    for symbol in normalized_symbols:
        if symbol not in results:
            try:
                results[symbol] = download_ohlcv(symbol, period, interval)
            except Exception:
                continue

    _BATCH_CACHE[cache_key] = {symbol: frame.copy() for symbol, frame in results.items()}
    _BATCH_CACHE_TS[cache_key] = time.monotonic()
    return {symbol: frame.copy() for symbol, frame in results.items()}


def batch_get_live_prices(symbols: List[str]) -> Dict[str, float]:
    normalized_symbols = tuple(sorted({_normalize_symbol(symbol) for symbol in symbols if symbol.strip()}))
    provider = get_market_data_provider()
    if not normalized_symbols:
        return {}

    now_monotonic = time.monotonic()
    results: Dict[str, float] = {}
    missing_symbols: List[str] = []

    for symbol in normalized_symbols:
        cache_timestamp = _LIVE_PRICE_CACHE_TS.get(symbol)
        if symbol in _LIVE_PRICE_CACHE and _cache_is_fresh(cache_timestamp, LIVE_PRICE_CACHE_TTL_SECONDS):
            results[symbol] = float(_LIVE_PRICE_CACHE[symbol])
        else:
            missing_symbols.append(symbol)

    if missing_symbols:
        try:
            quote_map = provider.get_latest_prices(missing_symbols, prepost=True)
        except Exception:
            quote_map = {}

        for symbol, value in quote_map.items():
            if not math.isnan(value):
                results[symbol] = value
                _LIVE_PRICE_CACHE[symbol] = value
                _LIVE_PRICE_CACHE_TS[symbol] = now_monotonic

        for symbol in missing_symbols:
            if symbol in results:
                continue
            try:
                fallback = download_ohlcv(symbol, "5d", "5m")
                value = latest_close_from_ohlcv_frame(fallback)
                if not math.isnan(value):
                    results[symbol] = value
                    _LIVE_PRICE_CACHE[symbol] = value
                    _LIVE_PRICE_CACHE_TS[symbol] = now_monotonic
            except Exception:
                continue

    return {symbol: float(price) for symbol, price in results.items()}


def get_live_price(symbol: str) -> float:
    symbol = _normalize_symbol(symbol)
    if not symbol:
        return float("nan")

    prices = batch_get_live_prices([symbol])
    return float(prices.get(symbol, float("nan")))


def _latest_return_from_frame(frame: pd.DataFrame | None, *, periods: int = 5) -> float:
    if frame is None or frame.empty:
        return 0.0
    close = pd.to_numeric(frame.get("Close"), errors="coerce").dropna()
    if len(close) <= periods:
        return 0.0
    return _safe_float(close.pct_change(periods).iloc[-1], 0.0)


def _relative_strength_info_from_frames(
    *,
    ticker: str,
    price: pd.DataFrame,
    benchmark_symbol: str,
    benchmark_frame: pd.DataFrame | None,
    sector_symbol: str | None,
    sector_frame: pd.DataFrame | None,
    peer_frames: dict[str, pd.DataFrame] | None = None,
    source: str = "frame-fallback",
) -> RelativeStrengthInfo:
    own_return = _latest_return_from_frame(price)
    benchmark_relative_return = own_return - _latest_return_from_frame(benchmark_frame)
    sector_relative_return = own_return - _latest_return_from_frame(sector_frame)
    residual_return = own_return - ((_latest_return_from_frame(benchmark_frame) * 0.7) + (_latest_return_from_frame(sector_frame) * 0.3))
    peer_returns = [_latest_return_from_frame(frame) for frame in (peer_frames or {}).values() if frame is not None and not frame.empty]
    peer_rank = 0.5
    if peer_returns:
        peer_rank = float(np.clip(np.mean(np.array(peer_returns) <= own_return), 0.0, 1.0))
    relative_strength_score = float(
        np.clip(
            0.5
            + (benchmark_relative_return * 6.0)
            + (sector_relative_return * 5.0)
            + (residual_return * 4.0)
            + ((peer_rank - 0.5) * 0.35),
            0.0,
            1.0,
        )
    )
    return {
        "benchmark_symbol": benchmark_symbol,
        "sector_symbol": str(sector_symbol or ""),
        "benchmark_relative_return": round(float(benchmark_relative_return), 4),
        "sector_relative_return": round(float(sector_relative_return), 4),
        "residual_return": round(float(residual_return), 4),
        "peer_momentum_rank": round(float(peer_rank), 4),
        "peer_count": len(peer_returns),
        "relative_strength_score": round(float(relative_strength_score), 4),
        "source": source,
    }


def _resolve_prediction_companion_frames(
    ticker: str,
    settings: ModelConfig,
    *,
    fast_mode: bool = False,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, dict[str, pd.DataFrame]]:
    benchmark_symbol = _benchmark_symbol_for_ticker(ticker)
    sector_symbol = _sector_symbol_for_ticker(ticker)
    peer_symbols = _peer_symbols_for_ticker(ticker)
    requested_symbols = [benchmark_symbol]
    if sector_symbol and sector_symbol != benchmark_symbol:
        requested_symbols.append(sector_symbol)
    requested_symbols.extend(peer_symbols)
    requested_symbols = [symbol for symbol in dict.fromkeys(requested_symbols) if symbol and symbol != _normalize_symbol(ticker)]
    if fast_mode or not requested_symbols:
        return None, None, {}
    try:
        frame_map = batch_download_ohlcv(requested_symbols, settings.period, settings.interval)
    except Exception:
        frame_map = {}
    benchmark_frame = frame_map.get(benchmark_symbol)
    sector_frame = frame_map.get(sector_symbol) if sector_symbol else None
    peer_frames = {symbol: frame_map[symbol] for symbol in peer_symbols if symbol in frame_map}
    return benchmark_frame, sector_frame, peer_frames


def _resolve_market_state_info(
    ticker: str,
    interval: str,
    price: pd.DataFrame,
    *,
    fast_mode: bool = False,
    provider: MarketDataProvider | None = None,
) -> MarketStateInfo:
    if fast_mode:
        return _fallback_market_state_info(price, interval)
    try:
        active_provider = provider or get_market_data_provider()
        snapshot = active_provider.get_market_state_snapshot(ticker, interval=interval)
        return _market_state_snapshot_from_dataclass(snapshot)
    except Exception:
        return _fallback_market_state_info(price, interval)


def _resolve_relative_strength_info(
    ticker: str,
    price: pd.DataFrame,
    *,
    interval: str,
    benchmark_frame: pd.DataFrame | None,
    sector_frame: pd.DataFrame | None,
    peer_frames: dict[str, pd.DataFrame],
    fast_mode: bool = False,
    provider: MarketDataProvider | None = None,
) -> RelativeStrengthInfo:
    benchmark_symbol = _benchmark_symbol_for_ticker(ticker)
    sector_symbol = _sector_symbol_for_ticker(ticker)
    peer_symbols = _peer_symbols_for_ticker(ticker)
    if fast_mode:
        return _relative_strength_info_from_frames(
            ticker=ticker,
            price=price,
            benchmark_symbol=benchmark_symbol,
            benchmark_frame=benchmark_frame,
            sector_symbol=sector_symbol,
            sector_frame=sector_frame,
            peer_frames=peer_frames,
            source="frame-fallback",
        )
    try:
        active_provider = provider or get_market_data_provider()
        snapshot = active_provider.get_relative_strength_snapshot(
            ticker,
            interval=interval,
            benchmark_symbol=benchmark_symbol,
            sector_symbol=sector_symbol,
            peer_symbols=peer_symbols,
        )
        return _relative_strength_snapshot_from_dataclass(snapshot)
    except Exception:
        return _relative_strength_info_from_frames(
            ticker=ticker,
            price=price,
            benchmark_symbol=benchmark_symbol,
            benchmark_frame=benchmark_frame,
            sector_symbol=sector_symbol,
            sector_frame=sector_frame,
            peer_frames=peer_frames,
            source="frame-fallback",
        )


def _resolve_options_flow_info(
    ticker: str,
    *,
    underlying_price: float,
    expiration: str | None = None,
    fast_mode: bool = False,
    provider: MarketDataProvider | None = None,
) -> OptionsFlowInfo:
    if fast_mode:
        return _neutral_options_flow_info(source="fast-mode")
    try:
        active_provider = provider or get_market_data_provider()
        snapshot = active_provider.get_options_flow_snapshot(
            ticker,
            underlying_price=underlying_price,
            expiration=expiration,
        )
        return _options_flow_snapshot_from_dataclass(snapshot)
    except Exception:
        return _neutral_options_flow_info(source="fallback")


def _resolve_event_revision_info(
    ticker: str,
    *,
    fast_mode: bool = False,
    provider: MarketDataProvider | None = None,
) -> EventRevisionInfo:
    if fast_mode:
        return _neutral_event_revision_info(source="fast-mode")
    try:
        active_provider = provider or get_market_data_provider()
        snapshot = active_provider.get_event_revision_snapshot(ticker)
        return _event_revision_snapshot_from_dataclass(snapshot)
    except Exception:
        return _neutral_event_revision_info(source="fallback")


def _prediction_source_family(value: object) -> str:
    text = str(value or "").strip().lower()
    if "alpaca" in text:
        return "alpaca"
    if "polygon" in text:
        return "polygon"
    if "yfinance" in text:
        return "yfinance"
    if "fallback" in text:
        return "fallback"
    return text or "unknown"


def _prediction_configuration_from_state(
    *,
    state_source_map: dict[str, str] | None,
    degraded_prediction: bool,
) -> str:
    source_map = dict(state_source_map or {})
    market_state_source = _prediction_source_family(source_map.get("market_state"))
    relative_strength_source = _prediction_source_family(source_map.get("relative_strength"))
    options_flow_source = _prediction_source_family(source_map.get("options_flow"))
    event_revision_source = _prediction_source_family(source_map.get("event_revision"))
    stock_side_paid = market_state_source == "alpaca" and relative_strength_source == "alpaca"
    polygon_side_paid = options_flow_source == "polygon" and event_revision_source == "polygon"
    if stock_side_paid and polygon_side_paid and not degraded_prediction:
        return "full_hybrid"
    if stock_side_paid:
        return "hybrid_stock_only"
    return "proxy_baseline"


def _is_snapshot_paid_primary(snapshot: dict[str, object], expected_source: str) -> bool:
    return str(snapshot.get("source") or "").strip().lower() == str(expected_source or "").strip().lower()


def _snapshot_source_label(snapshot: dict[str, object]) -> str:
    source_detail = str(snapshot.get("source_detail") or "").strip()
    if source_detail:
        return source_detail
    return str(snapshot.get("source") or "unknown").strip() or "unknown"


def _snapshot_freshness_payload(snapshot: dict[str, object]) -> dict[str, object]:
    return {
        "source": str(snapshot.get("source") or "unknown"),
        "source_detail": str(snapshot.get("source_detail") or snapshot.get("source") or "unknown"),
        "fetched_at": str(snapshot.get("fetched_at") or ""),
        "freshness_seconds": snapshot.get("freshness_seconds"),
        "freshness_ttl_seconds": _safe_int(snapshot.get("freshness_ttl_seconds"), 0),
        "freshness_status": str(snapshot.get("freshness_status") or "unknown"),
        "cache_status": str(snapshot.get("cache_status") or "miss"),
        "degraded": _safe_bool(snapshot.get("degraded")),
    }


def _prediction_data_quality_summary(
    *,
    market_state: dict[str, object],
    relative_strength: dict[str, object],
    options_flow: dict[str, object],
    event_revision: dict[str, object],
) -> tuple[str, bool, dict[str, str], dict[str, dict[str, object]], int]:
    state_map = {
        "market_state": dict(market_state or {}),
        "relative_strength": dict(relative_strength or {}),
        "options_flow": dict(options_flow or {}),
        "event_revision": dict(event_revision or {}),
    }
    state_source_map = {
        key: _snapshot_source_label(snapshot)
        for key, snapshot in state_map.items()
    }
    state_freshness = {
        key: _snapshot_freshness_payload(snapshot)
        for key, snapshot in state_map.items()
    }
    degraded_count = 0
    cached_primary_count = 0
    stale_count = 0
    for snapshot in state_map.values():
        degraded = _safe_bool(snapshot.get("degraded"))
        freshness_status = str(snapshot.get("freshness_status") or "unknown").strip().lower()
        cache_status = str(snapshot.get("cache_status") or "miss").strip().lower()
        if degraded:
            degraded_count += 1
        if freshness_status in {"stale", "degraded"}:
            stale_count += 1
        if cache_status == "cached":
            cached_primary_count += 1

    full_hybrid = (
        _is_snapshot_paid_primary(state_map["market_state"], "alpaca")
        and _is_snapshot_paid_primary(state_map["relative_strength"], "alpaca")
        and _is_snapshot_paid_primary(state_map["options_flow"], "polygon")
        and _is_snapshot_paid_primary(state_map["event_revision"], "polygon")
    )
    stock_side_hybrid = (
        _is_snapshot_paid_primary(state_map["market_state"], "alpaca")
        and _is_snapshot_paid_primary(state_map["relative_strength"], "alpaca")
    )

    if full_hybrid and degraded_count <= 0 and stale_count <= 0:
        quality = "cached_hybrid_paid_data" if cached_primary_count > 0 else "full_hybrid_paid_data"
    elif stock_side_hybrid:
        quality = "cached_hybrid_paid_data" if degraded_count <= 0 and cached_primary_count > 0 else "degraded_fallback"
    else:
        quality = "degraded_fallback"
    degraded_prediction = quality == "degraded_fallback" or degraded_count > 0 or stale_count > 0
    return quality, degraded_prediction, state_source_map, state_freshness, stale_count


def _is_equity_contract_symbol(contract_symbol: str) -> bool:
    return str(contract_symbol or "").strip().upper().startswith("EQUITY:")


def _equity_contract_mid_from_symbol(contract_symbol: str) -> float:
    normalized = _normalize_symbol(contract_symbol)
    if not _is_equity_contract_symbol(normalized):
        return float("nan")
    underlying_symbol = normalized.split(":", 1)[1].strip().upper()
    if not underlying_symbol:
        return float("nan")
    live_price = get_live_price(underlying_symbol)
    if math.isnan(live_price):
        return float("nan")
    return float(live_price / 100.0)


def get_contract_mid_from_symbol(contract_symbol: str) -> float:
    contract_symbol = _normalize_symbol(contract_symbol)
    if not contract_symbol:
        return float("nan")
    if _is_equity_contract_symbol(contract_symbol):
        return _equity_contract_mid_from_symbol(contract_symbol)
    provider = get_market_data_provider()

    cache_timestamp = _CONTRACT_MID_CACHE_TS.get(contract_symbol)
    if contract_symbol in _CONTRACT_MID_CACHE and _cache_is_fresh(cache_timestamp, CONTRACT_MID_CACHE_TTL_SECONDS):
        return float(_CONTRACT_MID_CACHE[contract_symbol])

    try:
        latest_prices = provider.get_latest_prices([contract_symbol], prepost=False)
        latest_value = latest_prices.get(contract_symbol)
        if isinstance(latest_value, (int, float)) and not math.isnan(float(latest_value)):
            value = float(latest_value)
            _CONTRACT_MID_CACHE[contract_symbol] = value
            _CONTRACT_MID_CACHE_TS[contract_symbol] = time.monotonic()
            return value
        hist = provider.get_contract_history(contract_symbol, period="1d", interval="1m")
        if isinstance(hist, pd.DataFrame) and not hist.empty:
            value = latest_close_from_ohlcv_frame(normalize_model_ohlcv_frame(hist, contract_symbol))
            if not math.isnan(value):
                _CONTRACT_MID_CACHE[contract_symbol] = value
                _CONTRACT_MID_CACHE_TS[contract_symbol] = time.monotonic()
                return value
    except Exception:
        return float("nan")

    return float("nan")


def batch_get_contract_mids(contract_symbols: List[str]) -> Dict[str, float]:
    normalized_symbols = tuple(sorted({_normalize_symbol(symbol) for symbol in contract_symbols if str(symbol).strip()}))
    provider = get_market_data_provider()
    if not normalized_symbols:
        return {}

    now_monotonic = time.monotonic()
    results: Dict[str, float] = {}
    missing_symbols: List[str] = []

    for symbol in normalized_symbols:
        if _is_equity_contract_symbol(symbol):
            value = _equity_contract_mid_from_symbol(symbol)
            if not math.isnan(value):
                results[symbol] = value
                _CONTRACT_MID_CACHE[symbol] = value
                _CONTRACT_MID_CACHE_TS[symbol] = now_monotonic
            continue
        cache_timestamp = _CONTRACT_MID_CACHE_TS.get(symbol)
        if symbol in _CONTRACT_MID_CACHE and _cache_is_fresh(cache_timestamp, CONTRACT_MID_CACHE_TTL_SECONDS):
            results[symbol] = float(_CONTRACT_MID_CACHE[symbol])
        else:
            missing_symbols.append(symbol)

    if missing_symbols:
        try:
            quote_map = provider.get_latest_prices(missing_symbols, prepost=False)
        except Exception:
            quote_map = {}

        for symbol, value in quote_map.items():
            if not math.isnan(value):
                results[symbol] = value
                _CONTRACT_MID_CACHE[symbol] = value
                _CONTRACT_MID_CACHE_TS[symbol] = now_monotonic

        for symbol in missing_symbols:
            if symbol in results:
                continue
            try:
                value = get_contract_mid_from_symbol(symbol)
                if not math.isnan(value):
                    results[symbol] = value
            except Exception:
                continue

    return {symbol: float(value) for symbol, value in results.items()}


def compute_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    average_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    average_loss = loss.ewm(alpha=1 / length, adjust=False).mean()

    relative_strength = average_gain / average_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + relative_strength))
    return rsi.replace([np.inf, -np.inf], np.nan).fillna(50.0)


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / length, adjust=False).mean().replace([np.inf, -np.inf], np.nan)


def compute_macd(close: pd.Series) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema_12 - ema_26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def load_macro_events() -> pd.DataFrame:
    global _MACRO_EVENTS_CACHE

    if _MACRO_EVENTS_CACHE is not None:
        return _MACRO_EVENTS_CACHE.copy()

    if not MACRO_EVENTS_PATH.exists():
        _MACRO_EVENTS_CACHE = pd.DataFrame(columns=["event_name", "event_date"])
        return _MACRO_EVENTS_CACHE.copy()

    try:
        raw_df = pd.read_csv(MACRO_EVENTS_PATH)
    except Exception:
        _MACRO_EVENTS_CACHE = pd.DataFrame(columns=["event_name", "event_date"])
        return _MACRO_EVENTS_CACHE.copy()

    if "event_name" not in raw_df.columns or "event_date" not in raw_df.columns:
        _MACRO_EVENTS_CACHE = pd.DataFrame(columns=["event_name", "event_date"])
        return _MACRO_EVENTS_CACHE.copy()

    normalized_rows: List[dict[str, object]] = []
    for _, row in raw_df.iterrows():
        event_name = str(row.get("event_name", "")).strip()
        event_date_ts = _coerce_timestamp(row.get("event_date"))
        if not event_name or event_date_ts is None:
            continue
        normalized_rows.append(
            {
                "event_name": event_name,
                "event_date": event_date_ts.normalize(),
            }
        )

    if not normalized_rows:
        _MACRO_EVENTS_CACHE = pd.DataFrame(columns=["event_name", "event_date"])
        return _MACRO_EVENTS_CACHE.copy()

    df = pd.DataFrame(normalized_rows)
    df = df.sort_values(by="event_date", ascending=True).reset_index(drop=True)
    _MACRO_EVENTS_CACHE = df
    return df.copy()


def _event_class_from_name(event_name: str, *, source: str = "") -> str:
    normalized_name = str(event_name or "").strip().lower()
    normalized_source = str(source or "").strip().lower()
    if "earnings" in normalized_name:
        return "earnings"
    if normalized_source == "macro":
        return "macro"
    return "corporate"


def _event_window_label(event_class: str) -> str:
    normalized = str(event_class or "").strip().lower()
    if normalized == "earnings":
        return "earnings_window"
    if normalized == "macro":
        return "macro_window"
    if normalized == "corporate":
        return "corporate_window"
    return "quiet_window"


def _event_severity_from_days(days_until: int | None, *, blocker: bool = False) -> str:
    if days_until is None:
        return "low"
    if blocker and days_until <= 0:
        return "critical"
    if blocker and days_until <= 3:
        return "high"
    if days_until <= 1:
        return "high"
    if days_until <= 3:
        return "medium"
    return "low"


def _trade_posture_for_event(*, event_risk: bool, event_severity: str, next_event_days: int | None) -> str:
    if event_risk:
        return "defer"
    if event_severity in {"high", "critical"}:
        return "caution"
    if next_event_days is not None and next_event_days <= 5:
        return "caution"
    return "clear"


def _build_event_marker(
    *,
    event_name: str,
    event_date: str,
    event_class: str,
    source: str,
    days_until: int | None,
    blocker: bool = False,
) -> EventMarker:
    return {
        "event_name": str(event_name or ""),
        "event_date": str(event_date or ""),
        "event_class": str(event_class or ""),
        "source": str(source or ""),
        "days_until": days_until,
        "severity": _event_severity_from_days(days_until, blocker=blocker),
    }


def get_next_earnings_event(ticker: str) -> tuple[str, str]:
    ticker = _normalize_symbol(ticker)
    if ticker in _EARNINGS_CACHE:
        return _EARNINGS_CACHE[ticker]
    if ticker in _EARNINGS_UNSUPPORTED_TICKERS:
        _EARNINGS_CACHE[ticker] = ("", "")
        return _EARNINGS_CACHE[ticker]
    provider = get_market_data_provider()

    event_name = ""
    event_date = ""

    try:
        calendar_events = provider.get_calendar_events(_normalize_symbol(ticker))

        for calendar_event in calendar_events:
            ts = _coerce_timestamp(calendar_event.event_date)
            if ts is not None:
                event_name = str(calendar_event.event_name or "Earnings")
                event_date = ts.normalize().date().isoformat()
                break

        if not event_date:
            try:
                earnings_events = provider.get_earnings_events(ticker, limit=4)
            except Exception:
                earnings_events = []

            normalized_dates: List[pd.Timestamp] = []
            for event in earnings_events:
                ts = _coerce_timestamp(event.event_date)
                if ts is not None:
                    normalized_dates.append(ts.normalize())

            future_dates = [ts for ts in normalized_dates if ts >= utc_now().normalize()]
            if future_dates:
                event_name = "Earnings"
                event_date = future_dates[0].date().isoformat()

    except Exception:
        event_name = ""
        event_date = ""

    _EARNINGS_CACHE[ticker] = (event_name, event_date)
    return event_name, event_date


def get_next_corporate_event(ticker: str) -> tuple[str, str]:
    ticker = _normalize_symbol(ticker)
    if ticker in _CORPORATE_EVENT_CACHE:
        return _CORPORATE_EVENT_CACHE[ticker]

    provider = get_market_data_provider()
    event_name = ""
    event_date = ""
    now = utc_now().normalize()

    try:
        calendar_events = provider.get_calendar_events(ticker)
    except Exception:
        calendar_events = []

    future_events: list[tuple[str, pd.Timestamp]] = []
    for calendar_event in calendar_events:
        ts = _coerce_timestamp(calendar_event.event_date)
        if ts is None:
            continue
        normalized_name = str(calendar_event.event_name or "").strip() or "Corporate event"
        if "earnings" in normalized_name.lower():
            continue
        normalized_ts = ts.normalize()
        if normalized_ts < now:
            continue
        future_events.append((normalized_name, normalized_ts))

    future_events.sort(key=lambda item: item[1])
    if future_events:
        event_name = future_events[0][0]
        event_date = future_events[0][1].date().isoformat()

    _CORPORATE_EVENT_CACHE[ticker] = (event_name, event_date)
    return event_name, event_date


def get_event_risk_info(ticker: str) -> EventRiskInfo:
    now = utc_now().normalize()
    ticker_upper = _normalize_symbol(ticker)
    next_event_name = ""
    next_event_date = ""
    next_event_days: int | None = None
    event_risk = False
    event_label = ""
    event_reason = ""
    event_class = "none"
    event_severity = "low"
    event_window_label = "quiet_window"
    session_label = "quiet_session"
    trade_posture = "clear"
    primary_event_label = "Quiet window"
    summary = "No near-term catalyst window is active."

    next_earnings_name = ""
    next_earnings_date = ""
    next_earnings_days: int | None = None
    next_macro_name = ""
    next_macro_date = ""
    next_macro_days: int | None = None
    next_corporate_name = ""
    next_corporate_date = ""
    next_corporate_days: int | None = None
    upcoming_events: list[EventMarker] = []

    earnings_name, earnings_date = get_next_earnings_event(ticker_upper)
    earnings_ts = _coerce_timestamp(earnings_date)
    if earnings_ts is not None:
        earnings_ts = earnings_ts.normalize()
        next_earnings_days = int((earnings_ts - now).days)
        if next_earnings_days >= 0:
            next_earnings_name = earnings_name
            next_earnings_date = earnings_ts.date().isoformat()
            upcoming_events.append(
                _build_event_marker(
                    event_name=next_earnings_name,
                    event_date=next_earnings_date,
                    event_class="earnings",
                    source="calendar",
                    days_until=next_earnings_days,
                    blocker=True,
                )
            )
            next_event_name = next_earnings_name
            next_event_date = next_earnings_date
            next_event_days = next_earnings_days
            event_class = "earnings"
            if next_earnings_days <= 3:
                event_risk = True
                event_label = "EVENT RISK"
                event_reason = f"{earnings_name} within {next_earnings_days} day(s)."

    macro_events = load_macro_events()
    if isinstance(macro_events, pd.DataFrame) and len(macro_events) > 0:
        future_rows: List[dict[str, object]] = []
        for _, row in macro_events.iterrows():
            macro_name = str(row.get("event_name", "")).strip()
            macro_ts = _coerce_timestamp(row.get("event_date"))
            if not macro_name or macro_ts is None:
                continue
            macro_ts = macro_ts.normalize()
            if macro_ts >= now:
                future_rows.append(
                    {
                        "event_name": macro_name,
                        "event_date": macro_ts,
                    }
                )

        if future_rows:
            future_rows.sort(key=lambda item: cast(pd.Timestamp, item["event_date"]))
            next_macro = future_rows[0]
            macro_name = str(next_macro["event_name"])
            macro_date_ts = cast(pd.Timestamp, next_macro["event_date"])
            next_macro_date = macro_date_ts.date().isoformat()
            next_macro_name = macro_name
            next_macro_days = int((macro_date_ts - now).days)
            upcoming_events.append(
                _build_event_marker(
                    event_name=next_macro_name,
                    event_date=next_macro_date,
                    event_class="macro",
                    source="macro",
                    days_until=next_macro_days,
                    blocker=ticker_upper in INDEX_EVENT_TICKERS,
                )
            )

            if next_event_days is None or next_macro_days < cast(int, next_event_days):
                next_event_name = next_macro_name
                next_event_date = next_macro_date
                next_event_days = next_macro_days
                event_class = "macro"

            if ticker_upper in INDEX_EVENT_TICKERS and next_macro_days <= 1:
                event_risk = True
                event_label = "EVENT RISK"
                event_reason = f"{macro_name} within {next_macro_days} day(s)."

    corporate_name, corporate_date = get_next_corporate_event(ticker_upper)
    corporate_ts = _coerce_timestamp(corporate_date)
    if corporate_ts is not None:
        corporate_ts = corporate_ts.normalize()
        next_corporate_days = int((corporate_ts - now).days)
        if next_corporate_days >= 0:
            next_corporate_name = corporate_name
            next_corporate_date = corporate_ts.date().isoformat()
            upcoming_events.append(
                _build_event_marker(
                    event_name=next_corporate_name,
                    event_date=next_corporate_date,
                    event_class="corporate",
                    source="calendar",
                    days_until=next_corporate_days,
                    blocker=False,
                )
            )
            if next_event_days is None or next_corporate_days < cast(int, next_event_days):
                next_event_name = next_corporate_name
                next_event_date = next_corporate_date
                next_event_days = next_corporate_days
                event_class = "corporate"

    if not next_event_name:
        event_class = "none"

    event_window_label = _event_window_label(event_class)
    event_severity = _event_severity_from_days(next_event_days, blocker=event_risk)
    trade_posture = _trade_posture_for_event(
        event_risk=event_risk,
        event_severity=event_severity,
        next_event_days=next_event_days,
    )
    session_label = (
        "event_heavy_session"
        if next_event_days is not None and next_event_days <= 1 and event_class != "none"
        else "quiet_session"
    )

    if event_risk:
        primary_event_label = "Defer"
        summary = event_reason or "Major event window is active."
    elif next_event_name and next_event_days is not None and next_event_days <= 5:
        event_label = "EVENT WATCH"
        primary_event_label = "Caution"
        summary = f"{next_event_name} is in {next_event_days} day(s), so treat this setup as catalyst-sensitive."
    elif next_event_name:
        event_label = "NO EVENT RISK"
        primary_event_label = "Clear"
        summary = f"Next event is {next_event_name} on {next_event_date}, outside the immediate risk window."
    else:
        event_label = "NO EVENT RISK"

    upcoming_events.sort(
        key=lambda item: (
            item.get("days_until") if item.get("days_until") is not None else 9999,
            str(item.get("event_name") or ""),
        )
    )
    upcoming_events = upcoming_events[:3]

    return {
        "event_risk": event_risk,
        "event_label": event_label,
        "event_reason": event_reason,
        "next_event_name": next_event_name,
        "next_event_date": next_event_date,
        "next_event_days": next_event_days,
        "next_earnings_name": next_earnings_name,
        "next_earnings_date": next_earnings_date,
        "next_earnings_days": next_earnings_days,
        "next_macro_name": next_macro_name,
        "next_macro_date": next_macro_date,
        "next_macro_days": next_macro_days,
        "next_corporate_name": next_corporate_name,
        "next_corporate_date": next_corporate_date,
        "next_corporate_days": next_corporate_days,
        "event_class": event_class,
        "event_severity": event_severity,
        "event_window_label": event_window_label,
        "session_label": session_label,
        "trade_posture": trade_posture,
        "primary_event_label": primary_event_label,
        "summary": summary,
        "upcoming_events": upcoming_events,
    }


_POSITIVE_NEWS_TERMS = {
    "beat": 1.2,
    "beats": 1.2,
    "surge": 1.1,
    "surges": 1.1,
    "record": 0.9,
    "growth": 0.8,
    "strong": 0.8,
    "stronger": 0.8,
    "upside": 0.9,
    "upgrade": 1.0,
    "upgraded": 1.0,
    "buyback": 0.9,
    "raised": 1.0,
    "raise": 1.0,
    "bullish": 1.1,
    "profit": 0.7,
    "profits": 0.7,
    "expands": 0.6,
    "expansion": 0.6,
    "partnership": 0.5,
    "launch": 0.5,
    "outperform": 1.0,
    "outperformed": 1.0,
}

_NEGATIVE_NEWS_TERMS = {
    "miss": 1.2,
    "misses": 1.2,
    "cuts": 1.0,
    "cut": 1.0,
    "downgrade": 1.1,
    "downgraded": 1.1,
    "lawsuit": 1.0,
    "probe": 0.9,
    "investigation": 1.0,
    "warning": 0.9,
    "weak": 0.8,
    "weaker": 0.8,
    "fall": 0.9,
    "falls": 0.9,
    "drop": 0.9,
    "drops": 0.9,
    "decline": 0.8,
    "declines": 0.8,
    "bearish": 1.1,
    "delay": 0.8,
    "delays": 0.8,
    "recall": 1.0,
    "risk": 0.5,
}

_POSITIVE_NEWS_PHRASES = {
    "raises guidance": 1.5,
    "beats estimates": 1.4,
    "beat estimates": 1.4,
    "tops estimates": 1.3,
    "strong demand": 1.1,
    "expands margin": 1.0,
    "new contract": 0.9,
    "wins contract": 1.0,
    "share repurchase": 1.0,
    "record revenue": 1.2,
    "record profit": 1.2,
}

_NEGATIVE_NEWS_PHRASES = {
    "cuts guidance": 1.6,
    "misses estimates": 1.4,
    "missed estimates": 1.4,
    "under investigation": 1.2,
    "class action": 1.1,
    "supply disruption": 1.0,
    "guidance cut": 1.5,
    "profit warning": 1.4,
    "credit downgrade": 1.3,
    "regulatory probe": 1.2,
}

_HIGH_SIGNAL_PUBLISHERS = {
    "reuters": 1.15,
    "bloomberg": 1.15,
    "wall street journal": 1.1,
    "financial times": 1.1,
    "dow jones": 1.08,
    "sec": 1.08,
    "associated press": 1.05,
}


def _news_cache_key(ticker: str, *, lookback_days: int, max_items: int) -> str:
    return f"{_normalize_symbol(ticker)}:{int(lookback_days)}:{int(max_items)}"


def _score_news_text(text: str) -> float:
    normalized_text = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    score = 0.0
    for phrase, weight in _POSITIVE_NEWS_PHRASES.items():
        if phrase in normalized_text:
            score += weight
    for phrase, weight in _NEGATIVE_NEWS_PHRASES.items():
        if phrase in normalized_text:
            score -= weight
    tokens = [token.strip(".,:;!?()[]{}'\"").lower() for token in normalized_text.split()]
    for token in tokens:
        if token in _POSITIVE_NEWS_TERMS:
            score += _POSITIVE_NEWS_TERMS[token]
        if token in _NEGATIVE_NEWS_TERMS:
            score -= _NEGATIVE_NEWS_TERMS[token]
    return max(-3.0, min(3.0, score))


def _news_publisher_weight(publisher: str) -> float:
    normalized = str(publisher or "").strip().lower()
    if not normalized:
        return 1.0
    for source, weight in _HIGH_SIGNAL_PUBLISHERS.items():
        if source in normalized:
            return weight
    return 1.0


def _normalize_news_relevance(value: object) -> float:
    try:
        numeric = float(value)
    except Exception:
        return 0.0
    if math.isnan(numeric):
        return 0.0
    return float(max(0.0, min(1.0, numeric)))


def _news_sentiment_label(score: float, confidence: float) -> str:
    adjusted = float(score) * max(0.2, float(confidence))
    if adjusted >= 0.35:
        return "Bullish news"
    if adjusted <= -0.35:
        return "Bearish news"
    return "Neutral news"


def get_news_sentiment_info(
    ticker: str,
    lookback_days: Optional[int] = None,
    max_items: Optional[int] = None,
) -> NewsSentimentInfo:
    resolved_lookback_days = max(1, int(lookback_days or settings.market_news_lookback_days))
    resolved_max_items = max(1, int(max_items or settings.market_news_max_headlines))
    cache_key = _news_cache_key(ticker, lookback_days=resolved_lookback_days, max_items=resolved_max_items)
    cache_timestamp = _NEWS_SENTIMENT_CACHE_TS.get(cache_key)
    if cache_key in _NEWS_SENTIMENT_CACHE and _cache_is_fresh(cache_timestamp, float(settings.market_news_cache_ttl_seconds)):
        return cast(NewsSentimentInfo, dict(_NEWS_SENTIMENT_CACHE[cache_key]))

    normalized_ticker = _normalize_symbol(ticker)
    provider = get_market_data_provider()
    now = utc_now()
    cutoff = now - pd.Timedelta(days=resolved_lookback_days)
    normalized_headlines: List[NewsHeadline] = []
    weighted_score = 0.0
    total_weight = 0.0

    try:
        raw_news = provider.get_news_items(normalized_ticker)
    except Exception:
        raw_news = []

    for raw_item in raw_news[: resolved_max_items * 3]:
        published_at = _coerce_timestamp(raw_item.published_at)
        if published_at is None or published_at < cutoff:
            continue
        title = str(raw_item.title or "").strip()
        publisher = str(raw_item.publisher or "").strip()
        url = str(raw_item.url or "").strip()
        summary = str(raw_item.summary or "").strip()
        article_text = str(getattr(raw_item, "article_text", "") or "").strip()
        source_type = str(getattr(raw_item, "source_type", "") or "headline").strip() or "headline"
        source_relevance = _normalize_news_relevance(getattr(raw_item, "relevance_score", 0.0))
        mentioned_tickers = [str(value).upper() for value in list(getattr(raw_item, "mentioned_tickers", ()) or []) if str(value).strip()]
        if not title:
            continue
        title_score = _score_news_text(title) * 1.4
        summary_score = _score_news_text(summary) * 0.7
        article_score = _score_news_text(article_text) * (0.45 if article_text else 0.0)
        headline_score = max(-3.0, min(3.0, title_score + summary_score + article_score))
        age_hours = max(0.0, (now - published_at).total_seconds() / 3600.0)
        recency_weight = max(0.2, math.exp(-age_hours / 36.0))
        emphasis_weight = 1.0 + min(abs(headline_score), 2.0) * 0.15
        publisher_weight = _news_publisher_weight(publisher)
        depth_weight = 1.12 if article_text else 1.0
        mention_weight = 1.1 if normalized_ticker in mentioned_tickers else 0.92 if mentioned_tickers else 1.0
        relevance_weight = recency_weight * emphasis_weight * publisher_weight * depth_weight * mention_weight
        relevance_weight *= max(0.2, source_relevance if source_relevance > 0 else 0.5)
        weighted_score += headline_score * relevance_weight
        total_weight += relevance_weight
        normalized_headlines.append(
            {
                "title": title,
                "publisher": publisher,
                "published_at": published_at.isoformat(),
                "url": url,
                "sentiment_score": max(-1.0, min(1.0, headline_score / 3.0)),
                "relevance_weight": round(relevance_weight, 4),
                "source_type": source_type,
                "relevance_score": round(source_relevance, 4),
                "mentioned_tickers": mentioned_tickers,
            }
        )
        if len(normalized_headlines) >= resolved_max_items:
            break

    sentiment_score = 0.0 if total_weight <= 0 else max(-1.0, min(1.0, weighted_score / (total_weight * 3.0)))
    article_count = len(normalized_headlines)
    confidence = min(1.0, total_weight / 3.0) if article_count else 0.0
    payload: NewsSentimentInfo = {
        "sentiment_score": round(sentiment_score, 4),
        "confidence": round(confidence, 4),
        "article_count": article_count,
        "weighted_article_count": round(total_weight, 4),
        "label": _news_sentiment_label(sentiment_score, confidence),
        "lookback_days": resolved_lookback_days,
        "updated_at": utc_now().isoformat(),
        "source": provider.provider_name,
        "headlines": normalized_headlines,
    }
    _NEWS_SENTIMENT_CACHE[cache_key] = payload
    _NEWS_SENTIMENT_CACHE_TS[cache_key] = time.monotonic()
    return cast(NewsSentimentInfo, dict(payload))


def _neutral_news_sentiment_info(*, source: str = "deferred") -> NewsSentimentInfo:
    return {
        "sentiment_score": 0.0,
        "confidence": 0.0,
        "article_count": 0,
        "weighted_article_count": 0.0,
        "label": "Neutral news",
        "lookback_days": max(1, int(settings.market_news_lookback_days)),
        "updated_at": utc_now().isoformat(),
        "source": source,
        "headlines": [],
    }


def _resolve_news_sentiment_info(
    ticker: str,
    *,
    fast_mode: bool = False,
    lookback_days: Optional[int] = None,
    max_items: Optional[int] = None,
) -> NewsSentimentInfo:
    if fast_mode:
        return _neutral_news_sentiment_info(source="fast-mode")

    resolved_lookback_days = max(1, int(lookback_days or settings.market_news_lookback_days))
    resolved_max_items = max(1, int(max_items or settings.market_news_max_headlines))
    cache_key = _news_cache_key(ticker, lookback_days=resolved_lookback_days, max_items=resolved_max_items)
    cache_timestamp = _NEWS_SENTIMENT_CACHE_TS.get(cache_key)
    if cache_key in _NEWS_SENTIMENT_CACHE and _cache_is_fresh(cache_timestamp, float(settings.market_news_cache_ttl_seconds)):
        return cast(NewsSentimentInfo, dict(_NEWS_SENTIMENT_CACHE[cache_key]))

    pending_future = _NEWS_SENTIMENT_FUTURES.get(cache_key)
    if pending_future is None or pending_future.done():
        pending_future = _NEWS_SENTIMENT_EXECUTOR.submit(
            get_news_sentiment_info,
            ticker,
            resolved_lookback_days,
            resolved_max_items,
        )
        _NEWS_SENTIMENT_FUTURES[cache_key] = pending_future

    try:
        return cast(NewsSentimentInfo, dict(pending_future.result(timeout=NEWS_SENTIMENT_RESOLUTION_TIMEOUT_SECONDS)))
    except FuturesTimeoutError:
        return _neutral_news_sentiment_info(source="deferred-refresh")
    except Exception:
        return _neutral_news_sentiment_info(source="deferred-error")
    finally:
        if pending_future.done():
            _NEWS_SENTIMENT_FUTURES.pop(cache_key, None)


def infer_market_regime_label(
    *,
    atr_pct: float,
    expected_move: float,
    event_risk: bool,
    news_sentiment_score: float = 0.0,
) -> str:
    if event_risk:
        return "event"

    atr_value = abs(float(atr_pct))
    move_value = abs(float(expected_move))
    news_value = abs(float(news_sentiment_score))
    edge_ratio = move_value / max(atr_value, 1e-6)

    if atr_value >= 0.03 or (atr_value >= 0.022 and news_value >= 0.45):
        return "volatile"
    if edge_ratio >= 1.05 or (edge_ratio >= 0.85 and news_value >= 0.35):
        return "trend"
    return "range"


def _regime_strength_score(calibration: dict[str, object], market_regime: str) -> float:
    default_score = 0.5
    active_regime = calibration.get("active_regime")
    if not isinstance(active_regime, dict):
        return default_score
    if str(active_regime.get("market_regime") or "").strip().lower() != str(market_regime or "").strip().lower():
        return default_score

    resolved_count = int(active_regime.get("resolved_count") or 0)
    empirical_hit_rate = active_regime.get("empirical_hit_rate")
    average_probability_up = active_regime.get("average_probability_up")
    average_error = active_regime.get("average_error")
    if (
        resolved_count < 5
        or not isinstance(empirical_hit_rate, (int, float))
        or not isinstance(average_probability_up, (int, float))
        or not isinstance(average_error, (int, float))
    ):
        return default_score

    edge = float(empirical_hit_rate) - float(average_probability_up)
    error_penalty = max(0.0, float(average_error) - 0.18)
    sample_bonus = min(0.1, resolved_count / 200.0)
    score = 0.5 + edge * 2.2 - error_penalty * 0.7 + sample_bonus
    return float(np.clip(score, 0.2, 0.85))


def _driver_reliability_weight(calibration: dict[str, object], driver_name: str) -> float:
    attribution_rows = calibration.get("driver_attribution")
    if not isinstance(attribution_rows, list):
        return 1.0

    target = next(
        (
            item for item in attribution_rows
            if isinstance(item, dict) and str(item.get("driver") or "").strip().lower() == str(driver_name or "").strip().lower()
        ),
        None,
    )
    if not isinstance(target, dict):
        return 1.0

    resolved_count = int(target.get("resolved_count") or 0)
    helpful_rate = target.get("helpful_rate")
    average_signed_impact = target.get("average_signed_impact")
    if (
        resolved_count < 5
        or not isinstance(helpful_rate, (int, float))
        or not isinstance(average_signed_impact, (int, float))
    ):
        return 1.0

    helpful_bias = (float(helpful_rate) - 0.5) * 0.9
    impact_bias = float(np.clip(float(average_signed_impact) * 6.0, -0.22, 0.22))
    sample_bonus = min(0.08, resolved_count / 250.0)
    weight = 1.0 + helpful_bias + impact_bias + (sample_bonus if helpful_bias >= 0 else 0.0)
    return float(np.clip(weight, 0.55, 1.2))


def build_price_forecast(
    *,
    latest_close: float,
    technical_probability_up: float,
    technical_expected_move: float,
    atr_pct: float,
    settings: ModelConfig,
    event_info: EventRiskInfo,
    news_info: NewsSentimentInfo,
    market_state: MarketStateInfo | None = None,
    relative_strength: RelativeStrengthInfo | None = None,
    options_flow: OptionsFlowInfo | None = None,
    event_revision: EventRevisionInfo | None = None,
    ensemble_summary: PredictionEnsembleSummary | None = None,
    journal_calibration: dict[str, float | int | None] | None = None,
) -> PriceForecast:
    news_score = float(news_info["sentiment_score"])
    news_confidence = float(news_info["confidence"])
    event_risk = _safe_bool(event_info.get("event_risk", False))
    calibration = dict(journal_calibration or {})
    resolved_market_state = dict(market_state or _market_state_snapshot_from_dataclass(MarketStateSnapshot()))
    resolved_relative_strength = dict(
        relative_strength or _neutral_relative_strength_info(source="fallback")
    )
    resolved_options_flow = dict(options_flow or _neutral_options_flow_info(source="fallback"))
    resolved_event_revision = dict(event_revision or _neutral_event_revision_info(source="fallback"))
    (
        prediction_data_quality,
        degraded_prediction,
        state_source_map,
        state_freshness,
        stale_state_count,
    ) = _prediction_data_quality_summary(
        market_state=resolved_market_state,
        relative_strength=resolved_relative_strength,
        options_flow=resolved_options_flow,
        event_revision=resolved_event_revision,
    )
    resolved_ensemble = dict(
        ensemble_summary
        or {
            "base_probability_up": technical_probability_up,
            "technical_probability_up": technical_probability_up,
            "microstructure_probability_up": technical_probability_up,
            "relative_strength_probability_up": technical_probability_up,
            "uncertainty_score": 0.35,
            "driver_weights": {},
            "driver_scores": {},
            "available_drivers": ["technical"],
            "calibration_sample_size": 0,
            "empirical_hit_rate": None,
            "purged_split_count": 0,
            "split_embargo_bars": max(1, int(settings.horizon)),
        }
    )
    resolved_count = int(calibration.get("resolved_count") or 0)
    empirical_hit_rate = calibration.get("empirical_hit_rate")
    average_probability_up = calibration.get("average_probability_up")
    ensemble_base_probability_up = _safe_float(
        resolved_ensemble.get("base_probability_up"),
        technical_probability_up,
    )
    market_regime = infer_market_regime_label(
        atr_pct=atr_pct,
        expected_move=technical_expected_move,
        event_risk=event_risk,
        news_sentiment_score=news_score,
    )
    technical_driver_weight = _driver_reliability_weight(calibration, "technical")
    journal_driver_weight = _driver_reliability_weight(calibration, "journal")
    news_driver_weight = _driver_reliability_weight(calibration, "news")
    regime_driver_weight = _driver_reliability_weight(calibration, "regime")
    market_state_driver_weight = _driver_reliability_weight(calibration, "market_state")
    relative_strength_driver_weight = _driver_reliability_weight(calibration, "relative_strength")
    options_flow_driver_weight = _driver_reliability_weight(calibration, "options_flow")
    event_revision_driver_weight = _driver_reliability_weight(calibration, "event_revision")
    regime_strength_score = _regime_strength_score(calibration, market_regime)
    regime_strength_score = float(0.5 + ((regime_strength_score - 0.5) * regime_driver_weight))

    weighted_technical_probability_up = float(
        np.clip(0.5 + ((ensemble_base_probability_up - 0.5) * technical_driver_weight), 0.02, 0.98)
    )
    weighted_technical_expected_move = float(technical_expected_move * technical_driver_weight)

    journal_adjusted_probability_up = float(weighted_technical_probability_up)
    if (
        resolved_count >= 8
        and isinstance(empirical_hit_rate, (int, float))
        and isinstance(average_probability_up, (int, float))
    ):
        calibration_bias = float(empirical_hit_rate) - float(average_probability_up)
        calibration_weight = min(0.22, 0.05 + resolved_count / 400.0)
        journal_adjusted_probability_up = float(
            np.clip(
                weighted_technical_probability_up + (calibration_bias * calibration_weight * journal_driver_weight),
                0.02,
                0.98,
            )
        )

    probability_adjustment = news_score * news_confidence * 0.12 * news_driver_weight
    market_state_signal = (
        (_safe_float(resolved_market_state.get("breadth_score"), 0.0) * 0.3)
        + (_safe_float(resolved_market_state.get("market_trend_score"), 0.0) * 0.34)
        + (_safe_float(resolved_market_state.get("opening_range_bias"), 0.0) * 0.18)
        + (_safe_float(resolved_market_state.get("opening_range_breakout"), 0.0) * 0.18)
    )
    market_state_probability_shift = market_state_signal * 0.085 * market_state_driver_weight

    relative_strength_score = _safe_float(resolved_relative_strength.get("relative_strength_score"), 0.5)
    relative_strength_signal = (
        ((relative_strength_score - 0.5) * 2.0)
        + (_safe_float(resolved_relative_strength.get("residual_return"), 0.0) * 6.0)
        + ((_safe_float(resolved_relative_strength.get("peer_momentum_rank"), 0.5) - 0.5) * 0.55)
    ) / 2.55
    relative_strength_probability_shift = relative_strength_signal * 0.07 * relative_strength_driver_weight

    options_flow_signal = (
        (_safe_float(resolved_options_flow.get("net_flow_pressure"), 0.0) * 0.8)
        - ((_safe_float(resolved_options_flow.get("put_call_open_interest_ratio"), 1.0) - 1.0) * 0.25)
        - (_safe_float(resolved_options_flow.get("iv_realized_vol_spread"), 0.0) * 0.3)
        + (_safe_float(resolved_options_flow.get("skew_score"), 0.0) * 0.2)
    )
    options_flow_probability_shift = options_flow_signal * 0.055 * options_flow_driver_weight

    event_revision_signal = (
        (_safe_float(resolved_event_revision.get("analyst_revision_score"), 0.0) * 0.34)
        + (_safe_float(resolved_event_revision.get("target_revision_score"), 0.0) * 0.33)
        + (_safe_float(resolved_event_revision.get("estimate_revision_score"), 0.0) * 0.33)
    ) - (_safe_float(resolved_event_revision.get("event_pressure"), 0.0) * 0.2)
    event_revision_probability_shift = event_revision_signal * 0.06 * event_revision_driver_weight

    base_probability_up = float(np.clip(journal_adjusted_probability_up, 0.02, 0.98))
    pre_event_probability_up = float(
        np.clip(
            base_probability_up
            + probability_adjustment
            + market_state_probability_shift
            + relative_strength_probability_shift
            + options_flow_probability_shift
            + event_revision_probability_shift,
            0.02,
            0.98,
        )
    )
    adjusted_probability_up = pre_event_probability_up
    if event_risk:
        adjusted_probability_up = 0.5 + (adjusted_probability_up - 0.5) * 0.6

    move_adjustment = news_score * news_confidence * max(abs(weighted_technical_expected_move) * 0.45, atr_pct * 0.7, 0.003) * news_driver_weight
    adjusted_expected_move = float(weighted_technical_expected_move + move_adjustment)
    probability_move_adjustment = (journal_adjusted_probability_up - weighted_technical_probability_up) * max(abs(weighted_technical_expected_move) * 0.35, atr_pct * 0.35, 0.0015)
    adjusted_expected_move += probability_move_adjustment
    adjusted_expected_move += market_state_signal * max(atr_pct * 0.3, 0.0015) * market_state_driver_weight
    adjusted_expected_move += relative_strength_signal * max(atr_pct * 0.25, 0.0012) * relative_strength_driver_weight
    adjusted_expected_move += options_flow_signal * max(abs(weighted_technical_expected_move) * 0.18, 0.001) * options_flow_driver_weight
    adjusted_expected_move += event_revision_signal * max(abs(weighted_technical_expected_move) * 0.15, 0.001) * event_revision_driver_weight
    if event_risk:
        adjusted_expected_move *= 0.82

    expected_price = latest_close * (1.0 + adjusted_expected_move)
    driver_scores = {
        "technical": round(float(np.clip(weighted_technical_probability_up, 0.02, 0.98)), 4),
        "journal": round(float(np.clip(journal_adjusted_probability_up, 0.02, 0.98)), 4),
        "news": round(float(np.clip(0.5 + (probability_adjustment * 3.2), 0.02, 0.98)), 4),
        "market_state": round(float(np.clip(0.5 + (market_state_probability_shift * 5.0), 0.02, 0.98)), 4),
        "relative_strength": round(float(np.clip(0.5 + (relative_strength_probability_shift * 5.5), 0.02, 0.98)), 4),
        "options_flow": round(float(np.clip(0.5 + (options_flow_probability_shift * 6.0), 0.02, 0.98)), 4),
        "event_revision": round(float(np.clip(0.5 + (event_revision_probability_shift * 5.8), 0.02, 0.98)), 4),
    }
    driver_agreement_score = float(
        np.clip(1.0 - (np.std(list(driver_scores.values())) * 2.6), 0.0, 1.0)
    )
    volatility_regime = "normal"
    realized_vol_percentile = _safe_float(resolved_market_state.get("realized_volatility_percentile"), 0.5)
    vix_level = _safe_float(resolved_market_state.get("vix_level"), float("nan"))
    if realized_vol_percentile >= 0.8 or (not math.isnan(vix_level) and vix_level >= 28.0):
        volatility_regime = "elevated"
    elif realized_vol_percentile <= 0.3 and (math.isnan(vix_level) or vix_level <= 18.0):
        volatility_regime = "compressed"

    data_quality_uncertainty_penalty = 0.0
    if prediction_data_quality == "cached_hybrid_paid_data":
        data_quality_uncertainty_penalty += 0.05
    if degraded_prediction:
        data_quality_uncertainty_penalty += 0.11
    data_quality_uncertainty_penalty += stale_state_count * 0.025
    uncertainty_score = float(
        np.clip(
            0.18
            + (_safe_float(resolved_ensemble.get("uncertainty_score"), 0.35) * 0.42)
            + ((1.0 - driver_agreement_score) * 0.24)
            + (max(0.0, realized_vol_percentile - 0.55) * 0.26)
            + (max(0.0, _safe_float(resolved_event_revision.get("event_pressure"), 0.0)) * 0.18)
            + data_quality_uncertainty_penalty
            + (0.1 if event_risk else 0.0),
            0.05,
            0.98,
        )
    )
    uncertainty = max(abs(adjusted_expected_move) * 0.45, atr_pct * (0.75 + (1.0 - news_confidence) * 0.9), 0.004)
    uncertainty *= 1.0 + ((0.5 - regime_strength_score) * 0.35)
    uncertainty *= 1.0 + (uncertainty_score * 0.55)
    if event_risk:
        uncertainty *= 1.25
    upper_price = latest_close * (1.0 + adjusted_expected_move + uncertainty)
    lower_price = latest_close * (1.0 + adjusted_expected_move - uncertainty)

    confidence_score = float(
        np.clip(
            0.45
            + abs(adjusted_probability_up - 0.5) * 0.9
            + news_confidence * 0.2 * news_driver_weight
            + (regime_strength_score - 0.5) * 0.35
            + ((driver_agreement_score - 0.5) * 0.28)
            - (uncertainty_score * 0.34)
            - (0.12 if event_risk else 0.0),
            0.1,
            0.99,
        )
    )
    technical_confidence_component = float(abs(weighted_technical_probability_up - 0.5) * 0.9)
    news_confidence_component = float(news_confidence * 0.2 * news_driver_weight)
    regime_confidence_component = float((regime_strength_score - 0.5) * 0.35)
    agreement_confidence_component = float((driver_agreement_score - 0.5) * 0.28)
    uncertainty_penalty_component = float(uncertainty_score * 0.34)
    event_confidence_penalty = float(0.12 if event_risk else 0.0)
    contribution_breakdown = {
        "base_probability_up": round(float(base_probability_up), 4),
        "technical_probability_up": round(float(weighted_technical_probability_up), 4),
        "journal_probability_shift": round(float(journal_adjusted_probability_up - weighted_technical_probability_up), 4),
        "news_probability_shift": round(float(probability_adjustment), 4),
        "market_state_probability_shift": round(float(market_state_probability_shift), 4),
        "relative_strength_probability_shift": round(float(relative_strength_probability_shift), 4),
        "options_flow_probability_shift": round(float(options_flow_probability_shift), 4),
        "event_revision_probability_shift": round(float(event_revision_probability_shift), 4),
        "event_probability_shift": round(float(adjusted_probability_up - pre_event_probability_up), 4),
        "technical_expected_move": round(float(weighted_technical_expected_move), 6),
        "journal_expected_move_shift": round(float(probability_move_adjustment), 6),
        "news_expected_move_shift": round(float(move_adjustment), 6),
        "technical_confidence_component": round(technical_confidence_component, 4),
        "news_confidence_component": round(news_confidence_component, 4),
        "regime_confidence_component": round(regime_confidence_component, 4),
        "agreement_confidence_component": round(agreement_confidence_component, 4),
        "uncertainty_penalty_component": round(uncertainty_penalty_component, 4),
        "data_quality_uncertainty_penalty": round(float(data_quality_uncertainty_penalty), 4),
        "event_confidence_penalty": round(event_confidence_penalty, 4),
        "technical_driver_weight": round(float(technical_driver_weight), 4),
        "journal_driver_weight": round(float(journal_driver_weight), 4),
        "news_driver_weight": round(float(news_driver_weight), 4),
        "regime_driver_weight": round(float(regime_driver_weight), 4),
        "market_state_driver_weight": round(float(market_state_driver_weight), 4),
        "relative_strength_driver_weight": round(float(relative_strength_driver_weight), 4),
        "options_flow_driver_weight": round(float(options_flow_driver_weight), 4),
        "event_revision_driver_weight": round(float(event_revision_driver_weight), 4),
        "confidence_base": 0.45,
    }
    label = "Expected breakout higher"
    if adjusted_probability_up < 0.45:
        label = "Expected drift lower"
    elif adjusted_probability_up <= 0.55:
        label = "Expected range / mixed"

    return {
        "forecast_horizon_bars": max(1, int(settings.horizon)),
        "base_probability_up": round(float(base_probability_up), 4),
        "state_adjusted_probability_up": round(float(adjusted_probability_up), 4),
        "adjusted_probability_up": round(adjusted_probability_up, 4),
        "technical_probability_up": round(float(technical_probability_up), 4),
        "journal_adjusted_probability_up": round(float(journal_adjusted_probability_up), 4),
        "adjusted_expected_move": round(adjusted_expected_move, 6),
        "technical_expected_move": round(float(technical_expected_move), 6),
        "expected_price": round(float(expected_price), 4),
        "upper_price": round(float(max(expected_price, upper_price)), 4),
        "lower_price": round(float(min(expected_price, lower_price)), 4),
        "confidence_score": round(confidence_score, 4),
        "uncertainty_score": round(float(uncertainty_score), 4),
        "prediction_data_quality": prediction_data_quality,
        "degraded_prediction": bool(degraded_prediction),
        "state_source_map": state_source_map,
        "state_freshness": state_freshness,
        "driver_scores": driver_scores,
        "driver_agreement_score": round(float(driver_agreement_score), 4),
        "volatility_regime": volatility_regime,
        "relative_strength_score": round(float(relative_strength_score), 4),
        "iv_context": {
            "iv_rank": round(_safe_float(resolved_options_flow.get("iv_rank"), 0.5), 4),
            "iv_percentile": round(_safe_float(resolved_options_flow.get("iv_percentile"), 0.5), 4),
            "iv_realized_vol_spread": round(_safe_float(resolved_options_flow.get("iv_realized_vol_spread"), 0.0), 4),
            "put_call_open_interest_ratio": round(_safe_float(resolved_options_flow.get("put_call_open_interest_ratio"), 1.0), 4),
            "net_flow_pressure": round(_safe_float(resolved_options_flow.get("net_flow_pressure"), 0.0), 4),
            "source": str(resolved_options_flow.get("source") or "fallback"),
        },
        "market_state": cast(MarketStateInfo, resolved_market_state),
        "relative_strength": cast(RelativeStrengthInfo, resolved_relative_strength),
        "options_flow": cast(OptionsFlowInfo, resolved_options_flow),
        "event_revision": cast(EventRevisionInfo, resolved_event_revision),
        "ensemble_summary": cast(PredictionEnsembleSummary, resolved_ensemble),
        "label": label,
        "market_regime": market_regime,
        "regime_strength_score": round(regime_strength_score, 4),
        "contribution_breakdown": contribution_breakdown,
        "news_sentiment": news_info,
        "journal_calibration": calibration,
    }


def build_feature_table(
    price: pd.DataFrame,
    settings: ModelConfig,
    *,
    benchmark_frame: pd.DataFrame | None = None,
    sector_frame: pd.DataFrame | None = None,
    peer_frames: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    close = price["Close"].astype(float)
    open_price = price["Open"].astype(float)
    high = price["High"].astype(float)
    low = price["Low"].astype(float)
    volume = price["Volume"].astype(float)

    feature_table = pd.DataFrame(index=price.index)

    for window in [1, 2, 3, 5, 10, 20]:
        feature_table[f"ret_{window}"] = close.pct_change(window)

    for window in [10, 20, 50]:
        moving_average = close.rolling(window).mean()
        feature_table[f"dist_sma_{window}"] = (close / moving_average) - 1

    feature_table["atr14"] = compute_atr(high, low, close) / close.replace(0, np.nan)
    feature_table["volatility20"] = close.pct_change().rolling(20).std()
    feature_table["rsi"] = compute_rsi(close)

    macd_line, signal_line, macd_hist = compute_macd(close)
    feature_table["macd"] = macd_line
    feature_table["macd_signal"] = signal_line
    feature_table["macd_hist"] = macd_hist

    feature_table["vol_change"] = volume.pct_change()

    candle_body = (close - open_price).abs()
    candle_range = (high - low).replace(0, np.nan)
    feature_table["body_pct"] = candle_body / candle_range
    feature_table["close_location"] = (close - low) / candle_range

    sma_20 = close.rolling(20).mean()
    sma_50 = close.rolling(50).mean()
    feature_table["above_sma20"] = (close > sma_20).astype(int)
    feature_table["above_sma50"] = (close > sma_50).astype(int)
    feature_table["range_pct"] = (high - low) / close.replace(0, np.nan)
    feature_table["gap_pct"] = open_price / close.shift(1).replace(0, np.nan) - 1
    feature_table["volume_vs_20"] = volume / volume.rolling(20).mean().replace(0, np.nan) - 1
    feature_table["realized_vol_10"] = close.pct_change().rolling(10).std()
    feature_table["realized_vol_ratio"] = feature_table["realized_vol_10"] / close.pct_change().rolling(40).std().replace(0, np.nan)

    local_index = pd.to_datetime(price.index, errors="coerce")
    if getattr(local_index, "tz", None) is None:
        local_index = local_index.tz_localize("UTC")
    local_index = local_index.tz_convert(NEW_YORK_TZ)
    session_dates = pd.Series(local_index.normalize(), index=price.index)
    session_progress = pd.Series(
        np.clip(
            (((local_index.hour * 60) + local_index.minute) - (9 * 60 + 30)) / float((16 * 60) - (9 * 60 + 30)),
            0.0,
            1.0,
        ),
        index=price.index,
        dtype=float,
    )
    feature_table["session_progress"] = session_progress
    feature_table["tod_sin"] = np.sin(2 * np.pi * session_progress)
    feature_table["tod_cos"] = np.cos(2 * np.pi * session_progress)
    bar_number = session_dates.groupby(session_dates).cumcount()
    opening_high = high.where(bar_number < 6).groupby(session_dates).transform("max")
    opening_low = low.where(bar_number < 6).groupby(session_dates).transform("min")
    opening_range = (opening_high - opening_low).replace(0, np.nan)
    feature_table["opening_range_bias"] = (close - ((opening_high + opening_low) / 2.0)) / opening_range
    feature_table["opening_range_breakout"] = np.where(close > opening_high, 1.0, np.where(close < opening_low, -1.0, 0.0))

    own_ret_1 = close.pct_change()
    own_ret_5 = close.pct_change(5)

    def _aligned_close(frame: pd.DataFrame | None) -> pd.Series:
        if frame is None or frame.empty:
            return pd.Series(index=feature_table.index, dtype=float)
        normalized = normalize_model_ohlcv_frame(frame.copy(), "AUX")
        aligned = pd.to_numeric(normalized.get("Close"), errors="coerce")
        return aligned.reindex(feature_table.index, method="ffill")

    benchmark_close = _aligned_close(benchmark_frame)
    sector_close = _aligned_close(sector_frame)
    benchmark_ret_1 = benchmark_close.pct_change(fill_method=None)
    benchmark_ret_5 = benchmark_close.pct_change(5, fill_method=None)
    sector_ret_1 = sector_close.pct_change(fill_method=None)
    sector_ret_5 = sector_close.pct_change(5, fill_method=None)
    if benchmark_close.dropna().empty:
        benchmark_ret_1 = pd.Series(0.0, index=feature_table.index, dtype=float)
        benchmark_ret_5 = pd.Series(0.0, index=feature_table.index, dtype=float)
    if sector_close.dropna().empty:
        sector_ret_1 = pd.Series(0.0, index=feature_table.index, dtype=float)
        sector_ret_5 = pd.Series(0.0, index=feature_table.index, dtype=float)

    feature_table["benchmark_ret_1"] = benchmark_ret_1
    feature_table["benchmark_ret_5"] = benchmark_ret_5
    feature_table["sector_ret_1"] = sector_ret_1
    feature_table["sector_ret_5"] = sector_ret_5
    feature_table["rs_benchmark_1"] = own_ret_1 - benchmark_ret_1
    feature_table["rs_benchmark_5"] = own_ret_5 - benchmark_ret_5
    feature_table["rs_sector_1"] = own_ret_1 - sector_ret_1
    feature_table["rs_sector_5"] = own_ret_5 - sector_ret_5
    feature_table["residual_ret_5"] = own_ret_5 - ((benchmark_ret_5.fillna(0.0) * 0.7) + (sector_ret_5.fillna(0.0) * 0.3))

    peer_return_columns: dict[str, pd.Series] = {}
    for symbol, frame in (peer_frames or {}).items():
        aligned_close = _aligned_close(frame)
        if aligned_close.empty:
            continue
        peer_return_columns[str(symbol)] = aligned_close.pct_change(5, fill_method=None)
    if peer_return_columns:
        peer_return_frame = pd.DataFrame(peer_return_columns, index=feature_table.index)
        feature_table["peer_momentum_rank"] = peer_return_frame.le(own_ret_5, axis=0).mean(axis=1)
        feature_table["peer_dispersion_5"] = peer_return_frame.std(axis=1)
    else:
        feature_table["peer_momentum_rank"] = 0.5
        feature_table["peer_dispersion_5"] = 0.0

    future_return = close.shift(-settings.horizon) / close.replace(0, np.nan) - 1
    feature_table["future_return"] = future_return
    feature_table["target_up"] = (future_return > settings.threshold_up).astype(int)

    feature_table = feature_table.replace([np.inf, -np.inf], np.nan)
    return feature_table


def build_model(max_iter: int = 200) -> Pipeline:
    model = HistGradientBoostingClassifier(
        max_depth=5,
        learning_rate=0.05,
        max_iter=max(20, int(max_iter)),
        random_state=42,
    )
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", model),
        ]
    )


def build_fast_model(max_iter: int = 80) -> Pipeline:
    model = LogisticRegression(
        max_iter=max(40, int(max_iter)),
        solver="liblinear",
        random_state=42,
    )
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", model),
        ]
    )


def warm_model_runtime() -> None:
    try:
        from joblib.parallel import cpu_count

        cpu_count()
    except Exception:
        return


def _metrics_from_probabilities(target: pd.Series, probabilities: pd.Series) -> Metrics:
    valid_mask = probabilities.notna()
    if int(valid_mask.sum()) == 0:
        return _empty_metrics()

    y_true = target.loc[valid_mask]
    y_prob = probabilities.loc[valid_mask]
    y_pred = (y_prob >= 0.5).astype(int)

    return {
        "accuracy": float(accuracy_score(y_true, y_pred)) if len(y_true) > 0 else 0.0,
        "precision": float(precision_score(y_true, y_pred, zero_division=0)) if len(y_true) > 0 else 0.0,
        "recall": float(recall_score(y_true, y_pred, zero_division=0)) if len(y_true) > 0 else 0.0,
        "roc_auc": float(roc_auc_score(y_true, y_prob)) if y_true.nunique() > 1 else float("nan"),
    }


def _calibrate_probability_from_backtest(
    raw_probability_up: float,
    historical_probabilities: pd.Series,
    target: pd.Series,
) -> tuple[float, float | None, int]:
    valid_probabilities = historical_probabilities.dropna()
    if valid_probabilities.empty:
        return float(raw_probability_up), None, 0

    aligned_target = target.reindex(valid_probabilities.index).dropna()
    valid_probabilities = valid_probabilities.reindex(aligned_target.index).dropna()
    if valid_probabilities.empty or len(valid_probabilities) < 30:
        return float(raw_probability_up), None, int(len(valid_probabilities))

    sample_window = min(240, len(valid_probabilities))
    recent_probabilities = valid_probabilities.tail(sample_window)
    recent_target = aligned_target.reindex(recent_probabilities.index).astype(float)
    probability_distances = (recent_probabilities - float(raw_probability_up)).abs()
    neighbor_count = max(20, min(80, max(20, sample_window // 3)))
    nearest_index = probability_distances.nsmallest(min(neighbor_count, len(probability_distances))).index
    if len(nearest_index) < 20:
        return float(raw_probability_up), None, int(len(nearest_index))

    empirical_hit_rate = float(recent_target.loc[nearest_index].mean())
    blend_weight = min(0.45, 0.15 + len(nearest_index) / 200.0)
    calibrated_probability = ((1.0 - blend_weight) * float(raw_probability_up)) + (blend_weight * empirical_hit_rate)
    calibrated_probability = float(np.clip(calibrated_probability, 0.02, 0.98))
    return calibrated_probability, empirical_hit_rate, int(len(nearest_index))


def _purged_time_series_splits(
    feature_matrix: pd.DataFrame,
    *,
    embargo_bars: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    if len(feature_matrix) < 30:
        return []
    n_splits = min(5, max(2, len(feature_matrix) // 40))
    splitter = TimeSeriesSplit(n_splits=n_splits)
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for train_index, test_index in splitter.split(feature_matrix):
        if embargo_bars > 0 and len(test_index):
            cutoff = max(0, int(test_index[0]) - int(embargo_bars))
            train_index = train_index[train_index < cutoff]
        if len(train_index) < 20 or len(test_index) == 0:
            continue
        splits.append((train_index, test_index))
    return splits


def run_backtest(
    feature_matrix: pd.DataFrame,
    target: pd.Series,
    *,
    model_builder: Callable[[], Pipeline] | None = None,
    embargo_bars: int = 0,
) -> Tuple[pd.Series, Metrics, int]:
    probabilities = pd.Series(index=feature_matrix.index, dtype=float)

    if len(feature_matrix) < 30 or target.nunique() < 2:
        return probabilities, _empty_metrics(), 0

    splits = _purged_time_series_splits(feature_matrix, embargo_bars=embargo_bars)
    builder = model_builder or build_model

    for train_index, test_index in splits:
        x_train = feature_matrix.iloc[train_index]
        x_test = feature_matrix.iloc[test_index]
        y_train = target.iloc[train_index]

        if len(x_train) < 20 or y_train.nunique() < 2:
            continue

        try:
            model = builder()
            model.fit(x_train, y_train)
            probabilities.iloc[test_index] = model.predict_proba(x_test)[:, 1]
        except Exception:
            continue

    return probabilities, _metrics_from_probabilities(target, probabilities), len(splits)


def _submodel_reliability_weight(metrics: Metrics) -> float:
    roc_auc = float(metrics.get("roc_auc", float("nan")))
    accuracy = float(metrics.get("accuracy", 0.5))
    precision = float(metrics.get("precision", 0.5))
    quality = roc_auc if not math.isnan(roc_auc) else accuracy
    score = 0.25 + ((quality - 0.5) * 2.0) + ((precision - 0.5) * 0.8)
    return float(np.clip(score, 0.1, 1.4))


def _prediction_feature_groups(feature_matrix: pd.DataFrame) -> dict[str, list[str]]:
    all_columns = list(feature_matrix.columns)
    technical = [
        column for column in all_columns
        if column.startswith("ret_")
        or column.startswith("dist_sma_")
        or column in {
            "atr14",
            "volatility20",
            "rsi",
            "macd",
            "macd_signal",
            "macd_hist",
            "vol_change",
            "body_pct",
            "close_location",
            "above_sma20",
            "above_sma50",
        }
    ]
    microstructure = [
        column for column in all_columns
        if column in {
            "range_pct",
            "gap_pct",
            "volume_vs_20",
            "realized_vol_10",
            "realized_vol_ratio",
            "session_progress",
            "tod_sin",
            "tod_cos",
            "opening_range_bias",
            "opening_range_breakout",
        }
    ]
    relative_strength = [
        column for column in all_columns
        if column in {
            "benchmark_ret_1",
            "benchmark_ret_5",
            "sector_ret_1",
            "sector_ret_5",
            "rs_benchmark_1",
            "rs_benchmark_5",
            "rs_sector_1",
            "rs_sector_5",
            "residual_ret_5",
            "peer_momentum_rank",
            "peer_dispersion_5",
        }
    ]
    return {
        "technical": technical,
        "microstructure": microstructure,
        "relative_strength": relative_strength,
    }


def _run_submodel(
    *,
    feature_matrix: pd.DataFrame,
    target: pd.Series,
    columns: Sequence[str],
    fast_mode: bool,
    embargo_bars: int,
) -> tuple[float, pd.Series, Metrics]:
    valid_columns = [column for column in columns if column in feature_matrix.columns]
    if not valid_columns:
        return float("nan"), pd.Series(index=feature_matrix.index, dtype=float), _empty_metrics()
    subset = feature_matrix.loc[:, valid_columns].copy()
    if subset.dropna(how="all").empty or len(subset) < 40:
        return float("nan"), pd.Series(index=feature_matrix.index, dtype=float), _empty_metrics()

    if fast_mode:
        train_matrix = subset.iloc[:-1]
        train_target = target.iloc[:-1]
        if len(train_matrix) < 30 or train_target.nunique() < 2:
            return float("nan"), pd.Series(index=subset.index, dtype=float), _empty_metrics()
        model = build_fast_model(max_iter=60)
        model.fit(train_matrix, train_target)
        historical_probabilities = pd.Series(
            model.predict_proba(train_matrix)[:, 1],
            index=train_matrix.index,
            dtype=float,
        )
        latest_probability = float(model.predict_proba(subset.iloc[[-1]])[0, 1])
        return latest_probability, historical_probabilities.reindex(subset.index), _metrics_from_probabilities(train_target, historical_probabilities)

    backtest_probabilities, metrics, _ = run_backtest(
        subset,
        target,
        embargo_bars=embargo_bars,
    )
    model = build_model()
    model.fit(subset.iloc[:-1], target.iloc[:-1])
    latest_probability = float(model.predict_proba(subset.iloc[[-1]])[0, 1])
    return latest_probability, backtest_probabilities, metrics


def build_notes(close: pd.Series, features: pd.DataFrame) -> List[str]:
    latest_close = float(close.iloc[-1])
    latest_rsi = float(features["rsi"].iloc[-1])
    latest_macd_hist = float(features["macd_hist"].iloc[-1])
    latest_above_20 = int(features["above_sma20"].iloc[-1])
    latest_above_50 = int(features["above_sma50"].iloc[-1])
    latest_atr_pct = float(features["atr14"].iloc[-1])

    notes: List[str] = []

    if latest_rsi > 70:
        notes.append("RSI is overbought.")
    elif latest_rsi < 30:
        notes.append("RSI is oversold.")

    if latest_macd_hist > 0:
        notes.append("MACD histogram is positive.")
    elif latest_macd_hist < 0:
        notes.append("MACD histogram is negative.")

    if latest_above_20 == 1 and latest_above_50 == 1:
        notes.append("Price is above the 20-period and 50-period averages.")
    elif latest_above_20 == 0 and latest_above_50 == 0:
        notes.append("Price is below the 20-period and 50-period averages.")

    notes.append(f"ATR is about {latest_atr_pct:.2%} of price.")
    notes.append(f"Current price is {latest_close:.2f}.")
    return notes


def _target_dte_range(dte_label: str) -> Tuple[int, int]:
    if dte_label == "21-45 DTE":
        return 21, 45
    if dte_label == "14-30 DTE":
        return 14, 30
    return 7, 14


def _choose_expiration(options: List[str], dte_label: str) -> Optional[str]:
    if not options:
        return None

    today = utc_now().normalize()
    min_days, max_days = _target_dte_range(dte_label)
    ranked: List[Tuple[int, str]] = []

    for expiration in options:
        try:
            expiration_ts = pd.Timestamp(expiration).normalize()
            dte = int((expiration_ts - today).days)
        except Exception:
            continue

        if dte < 0:
            continue

        if min_days <= dte <= max_days:
            return expiration

        distance = min(abs(dte - min_days), abs(dte - max_days))
        ranked.append((distance, expiration))

    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def _option_quote_to_contract(
    contract: OptionContractQuote,
    *,
    expiration: str,
) -> OptionContract | None:
    bid = pd.to_numeric(contract.bid, errors="coerce")
    ask = pd.to_numeric(contract.ask, errors="coerce")
    last_price = pd.to_numeric(contract.last_price, errors="coerce")
    strike = pd.to_numeric(contract.strike, errors="coerce")
    implied_volatility = pd.to_numeric(contract.implied_volatility, errors="coerce")
    if pd.isna(strike):
        return None

    mid = float("nan")
    if pd.notna(bid) and pd.notna(ask) and float(bid) >= 0 and float(ask) >= 0 and (float(bid) + float(ask)) > 0:
        mid = (float(bid) + float(ask)) / 2.0
    elif pd.notna(last_price) and float(last_price) > 0:
        mid = float(last_price)
    if math.isnan(mid) or mid <= 0:
        return None

    spread_pct = float("nan")
    if pd.notna(bid) and pd.notna(ask) and mid > 0:
        spread_pct = (float(ask) - float(bid)) / mid

    quote_timestamp = _parse_contract_quote_timestamp(contract.quote_timestamp)
    volume = pd.to_numeric(contract.volume, errors="coerce")
    open_interest = pd.to_numeric(contract.open_interest, errors="coerce")
    return {
        "contract_symbol": str(contract.contract_symbol or "").strip().upper(),
        "expiration": str(expiration),
        "strike": float(strike),
        "bid": float(bid) if pd.notna(bid) else float("nan"),
        "ask": float(ask) if pd.notna(ask) else float("nan"),
        "mid": float(mid),
        "last_price": float(last_price) if pd.notna(last_price) else float("nan"),
        "implied_volatility": float(implied_volatility) if pd.notna(implied_volatility) else float("nan"),
        "volume": int(volume) if pd.notna(volume) else 0,
        "open_interest": int(open_interest) if pd.notna(open_interest) else 0,
        "in_the_money": _safe_bool(contract.in_the_money),
        "spread_pct": float(spread_pct),
        "quote_timestamp": quote_timestamp.isoformat() if quote_timestamp is not None else "",
    }


def get_contract_quote_from_chain(
    ticker: str,
    contract_symbol: str,
    option_side: str | None = None,
    expiration: str | None = None,
) -> OptionContract | None:
    normalized_ticker = _normalize_symbol(ticker)
    normalized_contract_symbol = _normalize_symbol(contract_symbol)
    if not normalized_ticker or not normalized_contract_symbol or _is_equity_contract_symbol(normalized_contract_symbol):
        return None

    normalized_side = str(option_side or "").strip().upper()
    if normalized_side in {"CALL", "C"}:
        sides = ("CALL",)
    elif normalized_side in {"PUT", "P"}:
        sides = ("PUT",)
    else:
        sides = ("CALL", "PUT")

    provider = get_market_data_provider()
    try:
        expirations = [str(expiration).strip()] if str(expiration or "").strip() else provider.get_option_expirations(normalized_ticker)
        for candidate_expiration in expirations:
            if not candidate_expiration:
                continue
            chain = provider.get_option_chain(normalized_ticker, candidate_expiration)
            raw_contracts: list[OptionContractQuote] = []
            if "CALL" in sides:
                raw_contracts.extend(chain.calls or [])
            if "PUT" in sides:
                raw_contracts.extend(chain.puts or [])
            for contract in raw_contracts:
                if not isinstance(contract, OptionContractQuote):
                    continue
                if _normalize_symbol(contract.contract_symbol) != normalized_contract_symbol:
                    continue
                return _option_quote_to_contract(contract, expiration=str(candidate_expiration))
    except Exception:
        return None
    return None


def get_recommended_contract(
    ticker: str,
    option_side: str,
    close_price: float,
    dte_label: str,
) -> OptionContract | None:
    if option_side not in {"CALL", "PUT"}:
        return None
    provider = get_market_data_provider()

    try:
        normalized_ticker = _normalize_symbol(ticker)
        expirations = provider.get_option_expirations(normalized_ticker)
        if not expirations:
            return None

        expiration = _choose_expiration(expirations, dte_label)
        if expiration is None:
            return None

        chain = provider.get_option_chain(normalized_ticker, expiration)
        raw_chain = chain.calls if option_side == "CALL" else chain.puts
        if not raw_chain:
            return None

        contracts = pd.DataFrame(
            [
                {
                    "contractSymbol": contract.contract_symbol,
                    "strike": contract.strike,
                    "bid": contract.bid,
                    "ask": contract.ask,
                    "lastPrice": contract.last_price,
                    "impliedVolatility": contract.implied_volatility,
                    "volume": contract.volume,
                    "openInterest": contract.open_interest,
                    "inTheMoney": contract.in_the_money,
                    "quoteTimestamp": contract.quote_timestamp,
                }
                for contract in raw_chain
                if isinstance(contract, OptionContractQuote)
            ]
        )
        if contracts.empty:
            return None

        contracts["strike"] = pd.to_numeric(contracts["strike"], errors="coerce")
        contracts["bid"] = pd.to_numeric(contracts["bid"], errors="coerce")
        contracts["ask"] = pd.to_numeric(contracts["ask"], errors="coerce")
        contracts["lastPrice"] = pd.to_numeric(contracts["lastPrice"], errors="coerce")
        contracts["impliedVolatility"] = pd.to_numeric(contracts["impliedVolatility"], errors="coerce")
        contracts["volume"] = pd.to_numeric(contracts["volume"], errors="coerce").fillna(0)
        contracts["openInterest"] = pd.to_numeric(contracts["openInterest"], errors="coerce").fillna(0)
        contracts["quoteTimestamp"] = contracts["quoteTimestamp"].apply(
            lambda value: _parse_contract_quote_timestamp(value) or pd.NaT
        )

        contracts["mid"] = np.where(
            contracts["bid"].notna() & contracts["ask"].notna() & ((contracts["bid"] + contracts["ask"]) > 0),
            (contracts["bid"] + contracts["ask"]) / 2,
            contracts["lastPrice"],
        )
        contracts["spread_pct"] = np.where(
            contracts["mid"] > 0,
            (contracts["ask"] - contracts["bid"]) / contracts["mid"],
            np.nan,
        )

        contracts = contracts.replace([np.inf, -np.inf], np.nan)
        contracts = contracts.dropna(subset=["strike", "mid"])
        contracts = contracts[contracts["mid"] > 0]

        if contracts.empty:
            return None

        dte_days = _contract_days_to_expiration(
            {
                "contract_symbol": "",
                "expiration": str(expiration),
                "strike": close_price,
                "bid": float("nan"),
                "ask": float("nan"),
                "mid": float("nan"),
                "last_price": float("nan"),
                "implied_volatility": float("nan"),
                "volume": 0,
                "open_interest": 0,
                "in_the_money": False,
                "spread_pct": float("nan"),
                "quote_timestamp": "",
            }
        )
        dte_bucket = _contract_dte_bucket(
            {
                "contract_symbol": "",
                "expiration": str(expiration),
                "strike": close_price,
                "bid": float("nan"),
                "ask": float("nan"),
                "mid": float("nan"),
                "last_price": float("nan"),
                "implied_volatility": float("nan"),
                "volume": 0,
                "open_interest": 0,
                "in_the_money": False,
                "spread_pct": float("nan"),
                "quote_timestamp": "",
            },
            fallback_label=dte_label,
        )
        contracts["quote_age_seconds"] = contracts["quoteTimestamp"].apply(
            lambda value: (
                max(0, int((pd.Timestamp.now(tz="UTC") - value.tz_convert("UTC")).total_seconds()))
                if not pd.isna(value)
                else 9999
            )
        )
        contracts["dte_days"] = dte_days if dte_days is not None else 999
        contracts["moneyness_bucket"] = contracts["strike"].apply(
            lambda strike: _contract_moneyness_bucket(option_side, close_price, _safe_float(strike))
        )
        contracts["distance_score"] = (contracts["strike"] - close_price).abs() / max(close_price, 0.01)
        contracts["spread_score"] = contracts["spread_pct"].fillna(10.0)
        contracts["liquidity_score"] = -(contracts["volume"] + contracts["openInterest"]) / 10000.0
        contracts["quote_age_penalty"] = np.where(
            contracts["quote_age_seconds"] <= 3,
            0.0,
            np.where(contracts["quote_age_seconds"] <= 10, 0.12, 0.4),
        )
        contracts["moneyness_penalty"] = contracts["moneyness_bucket"].map(
            {
                "near_atm": 0.0,
                "slightly_itm": 0.03,
                "slightly_otm": 0.08,
                "deep_itm": 0.14,
                "deep_otm": 0.22,
            }
        ).fillna(0.18)
        contracts["dte_penalty"] = 0.0
        if dte_bucket == "0dte":
            contracts["dte_penalty"] = 0.3
        elif dte_bucket == "1_6dte":
            contracts["dte_penalty"] = 0.18
        contracts["tier_bias"] = _get_option_tier_bias(normalized_ticker)
        contracts["selection_score"] = (
            contracts["distance_score"] * 1.5
            + contracts["spread_score"]
            + contracts["quote_age_penalty"]
            + contracts["moneyness_penalty"]
            + contracts["dte_penalty"]
            + contracts["liquidity_score"]
            + contracts["tier_bias"]
        )

        best = contracts.sort_values(
            by=["selection_score", "spread_score", "distance_score"],
            ascending=[True, True, True],
        ).iloc[0]

        contract: OptionContract = {
            "contract_symbol": str(best["contractSymbol"]),
            "expiration": str(expiration),
            "strike": float(best["strike"]),
            "bid": float(best["bid"]) if not pd.isna(best["bid"]) else float("nan"),
            "ask": float(best["ask"]) if not pd.isna(best["ask"]) else float("nan"),
            "mid": float(best["mid"]),
            "last_price": float(best["lastPrice"]) if not pd.isna(best["lastPrice"]) else float("nan"),
            "implied_volatility": float(best["impliedVolatility"]) if not pd.isna(best["impliedVolatility"]) else float("nan"),
            "volume": int(best["volume"]),
            "open_interest": int(best["openInterest"]),
            "in_the_money": _safe_bool(best["inTheMoney"]),
            "spread_pct": float(best["spread_pct"]) if not pd.isna(best["spread_pct"]) else float("nan"),
            "quote_timestamp": best["quoteTimestamp"].isoformat() if not pd.isna(best["quoteTimestamp"]) else "",
        }
        return contract
    except Exception:
        return None


def contract_is_usable(contract: OptionContract | None) -> bool:
    if contract is None:
        return False

    spread_pct = float(contract["spread_pct"])
    volume = int(contract["volume"])
    open_interest = int(contract["open_interest"])

    if math.isnan(spread_pct):
        return False
    if spread_pct > 0.15:
        return False
    if volume < 25:
        return False
    if open_interest < 100:
        return False
    return True


def build_option_plan(
    results: dict[str, float | str],
    ticker: str,
    include_contract_lookup: bool = True,
) -> OptionPlan:
    probability_up = float(results["probability_up"])
    verdict = str(results["verdict"])
    expected_move = float(results["expected_move"])
    close_price = float(results["close"])
    atr_pct = float(results["atr_pct"])

    atr_dollars = max(close_price * atr_pct, close_price * 0.002)
    zone_half_width = max(atr_dollars * 0.35, close_price * 0.0015)

    bullish_confidence = probability_up
    bearish_confidence = 1.0 - probability_up

    if verdict == "BULLISH":
        option_side = "CALL"
        action = "BUY CALL"
        strike_style = "ATM or 1 strike ITM"
        entry_low_price = close_price - zone_half_width
        entry_high_price = close_price + zone_half_width
        invalidation_price = close_price - atr_dollars
        entry_signal = (
            f"Enter only if price is between {entry_low_price:.2f} and {entry_high_price:.2f} "
            f"or breaks higher with momentum."
        )
        sell_signal = "Trim 50% at 1R, trim more at 2R, move stop to breakeven after TP1, lock TP1 after TP2, then exit on stop or time stop."
        take_profit_1 = 0.30
        take_profit_2 = 0.60
        stop_loss = 0.20
        confidence = bullish_confidence
    elif verdict == "BEARISH":
        option_side = "PUT"
        action = "BUY PUT"
        strike_style = "ATM or 1 strike ITM"
        entry_low_price = close_price - zone_half_width
        entry_high_price = close_price + zone_half_width
        invalidation_price = close_price + atr_dollars
        entry_signal = (
            f"Enter only if price is between {entry_low_price:.2f} and {entry_high_price:.2f} "
            f"on weakness or breaks lower."
        )
        sell_signal = "Trim 50% at 1R, trim more at 2R, move stop to breakeven after TP1, lock TP1 after TP2, then exit on stop or time stop."
        take_profit_1 = 0.30
        take_profit_2 = 0.60
        stop_loss = 0.20
        confidence = bearish_confidence
    else:
        option_side = "NONE"
        action = "WAIT"
        strike_style = "none"
        entry_low_price = float("nan")
        entry_high_price = float("nan")
        invalidation_price = float("nan")
        entry_signal = "No high-quality options setup right now."
        sell_signal = "No trade."
        take_profit_1 = 0.0
        take_profit_2 = 0.0
        stop_loss = 0.0
        confidence = 0.50

    if confidence >= 0.65:
        dte_label = "21-45 DTE"
    elif confidence >= 0.58:
        dte_label = "14-30 DTE"
    else:
        dte_label = "7-14 DTE"

    expected_target = float("nan") if np.isnan(expected_move) else close_price * (1 + expected_move)
    recommended_contract = None
    if include_contract_lookup:
        recommended_contract = get_recommended_contract(ticker, option_side, close_price, dte_label)

    return {
        "action": action,
        "option_side": option_side,
        "strike_style": strike_style,
        "days_to_expiration": dte_label,
        "entry_signal": entry_signal,
        "entry_low_price": entry_low_price,
        "entry_high_price": entry_high_price,
        "sell_signal": sell_signal,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "stop_loss": stop_loss,
        "invalidation_price": invalidation_price,
        "expected_underlying_target": expected_target,
        "recommended_contract": recommended_contract,
    }


def _fast_training_window(interval: str) -> int:
    if interval == "1m":
        return 600
    if interval == "4h":
        return 260
    if interval == "1d":
        return 320
    return 400


def evaluate_model(
    price: pd.DataFrame,
    settings: ModelConfig,
    *,
    benchmark_frame: pd.DataFrame | None = None,
    sector_frame: pd.DataFrame | None = None,
    peer_frames: dict[str, pd.DataFrame] | None = None,
    fast_mode: bool = False,
) -> Tuple[float, float, str, float, float, Metrics, List[str], PredictionEnsembleSummary]:
    features = build_feature_table(
        price,
        settings,
        benchmark_frame=benchmark_frame,
        sector_frame=sector_frame,
        peer_frames=peer_frames,
    )
    features = features.replace([np.inf, -np.inf], np.nan).dropna()

    minimum_rows = max(120, settings.min_rows)
    if fast_mode:
        fast_window = _fast_training_window(settings.interval)
        if len(features) > fast_window:
            features = features.tail(fast_window)
        minimum_rows = min(minimum_rows, max(140, fast_window // 2))

    if len(features) < minimum_rows:
        raise ValueError(f"Too little clean training data after feature engineering for {settings.ticker}.")

    feature_matrix = features.drop(columns=["future_return", "target_up"])
    target = features["target_up"]

    if target.nunique() < 2:
        raise ValueError(f"Training target has only one class for {settings.ticker}.")

    embargo_bars = max(1, int(settings.horizon))
    feature_groups = _prediction_feature_groups(feature_matrix)
    group_outputs: dict[str, dict[str, object]] = {}
    for group_name, group_columns in feature_groups.items():
        latest_probability, historical_probabilities, group_metrics = _run_submodel(
            feature_matrix=feature_matrix,
            target=target,
            columns=group_columns,
            fast_mode=fast_mode,
            embargo_bars=embargo_bars,
        )
        if math.isnan(latest_probability):
            continue
        group_outputs[group_name] = {
            "latest_probability": latest_probability,
            "historical_probabilities": historical_probabilities,
            "metrics": group_metrics,
            "weight": _submodel_reliability_weight(group_metrics),
        }

    if not group_outputs:
        raise ValueError(f"Unable to build a prediction ensemble for {settings.ticker}.")

    combined_weight = float(sum(float(group_output["weight"]) for group_output in group_outputs.values()))
    raw_probability_up = float(
        sum(float(group_output["latest_probability"]) * float(group_output["weight"]) for group_output in group_outputs.values())
        / max(combined_weight, 1e-6)
    )

    combined_history = pd.DataFrame(
        {
            group_name: cast(pd.Series, group_output["historical_probabilities"])
            for group_name, group_output in group_outputs.items()
        },
        index=feature_matrix.index,
    )
    combined_history = combined_history.dropna(how="all")
    history_weights = {
        group_name: float(group_output["weight"])
        for group_name, group_output in group_outputs.items()
    }
    weighted_numerator = pd.Series(0.0, index=combined_history.index, dtype=float)
    weighted_denominator = pd.Series(0.0, index=combined_history.index, dtype=float)
    for group_name, series in combined_history.items():
        mask = series.notna()
        if not mask.any():
            continue
        weighted_numerator.loc[mask] += series.loc[mask] * history_weights.get(group_name, 1.0)
        weighted_denominator.loc[mask] += history_weights.get(group_name, 1.0)
    backtest_probabilities = (weighted_numerator / weighted_denominator.replace(0, np.nan)).reindex(feature_matrix.index)
    metrics = _metrics_from_probabilities(target, backtest_probabilities)
    probability_up, empirical_hit_rate, calibration_sample_size = _calibrate_probability_from_backtest(
        raw_probability_up,
        backtest_probabilities,
        target,
    )

    if probability_up > 0.65:
        verdict = "BULLISH"
    elif probability_up < 0.35:
        verdict = "BEARISH"
    else:
        verdict = "NEUTRAL / MIXED"

    probability_diffs = (backtest_probabilities.dropna() - probability_up).abs()
    if probability_diffs.empty:
        expected_move = float(features["future_return"].mean())
    else:
        nearest_index = probability_diffs.nsmallest(min(50, len(probability_diffs))).index
        expected_move = float(features.loc[nearest_index, "future_return"].mean())

    latest_close = float(price["Close"].iloc[-1])
    latest_atr_pct = float(features["atr14"].iloc[-1])
    notes = build_notes(price["Close"], features)
    if empirical_hit_rate is not None and calibration_sample_size >= 20:
        notes.append(
            f"Probability calibrated from {raw_probability_up:.0%} to {probability_up:.0%} using {calibration_sample_size} similar recent outcomes."
        )
    driver_weights = {
        group_name: round(float(group_output["weight"]), 4)
        for group_name, group_output in group_outputs.items()
    }
    driver_scores = {
        group_name: round(float(group_output["latest_probability"]), 4)
        for group_name, group_output in group_outputs.items()
    }
    disagreement = float(np.std([float(value) for value in driver_scores.values()])) if driver_scores else 0.0
    ensemble_summary: PredictionEnsembleSummary = {
        "base_probability_up": round(float(probability_up), 4),
        "technical_probability_up": round(float(driver_scores.get("technical", probability_up)), 4),
        "microstructure_probability_up": round(float(driver_scores.get("microstructure", probability_up)), 4),
        "relative_strength_probability_up": round(float(driver_scores.get("relative_strength", probability_up)), 4),
        "uncertainty_score": round(float(np.clip(disagreement * 2.4, 0.0, 1.0)), 4),
        "driver_weights": driver_weights,
        "driver_scores": driver_scores,
        "available_drivers": list(group_outputs.keys()),
        "calibration_sample_size": int(calibration_sample_size),
        "empirical_hit_rate": None if empirical_hit_rate is None else round(float(empirical_hit_rate), 4),
        "purged_split_count": 0 if fast_mode else int(len(_purged_time_series_splits(feature_matrix, embargo_bars=embargo_bars))),
        "split_embargo_bars": int(embargo_bars),
    }
    return latest_close, probability_up, verdict, expected_move, latest_atr_pct, metrics, notes, ensemble_summary


def get_interval_verdict_summary(
    ticker: str,
    horizon: int,
    current_interval: str | None = None,
    current_probability_up: float | None = None,
    current_verdict: str | None = None,
    preloaded_frames: Dict[str, pd.DataFrame] | None = None,
    fast_mode: bool = False,
) -> tuple[str, float]:
    cache_key = (_normalize_symbol(ticker), int(horizon), str(current_interval or ""))
    cache_timestamp = _ALIGNMENT_CACHE_TS.get(cache_key)
    if preloaded_frames is None and cache_key in _ALIGNMENT_CACHE and _cache_is_fresh(cache_timestamp, ALIGNMENT_CACHE_TTL_SECONDS):
        cached_label, cached_score = _ALIGNMENT_CACHE[cache_key]
        return str(cached_label), float(cached_score)

    verdicts: list[str] = []
    scores: list[float] = []

    for interval in ["1m", "5m", "4h"]:
        try:
            if interval == current_interval and current_probability_up is not None and current_verdict is not None:
                probability_up = float(current_probability_up)
                verdict = str(current_verdict)
            else:
                period = get_period_for_interval(interval)
                threshold_up, threshold_down = get_thresholds_for_interval(interval)
                settings = ModelConfig(
                    ticker=ticker,
                    horizon=horizon,
                    period=period,
                    interval=interval,
                    threshold_up=threshold_up,
                    threshold_down=threshold_down,
                    min_rows=120 if interval == "1m" else 180 if interval == "5m" else 140,
                )
                preloaded_price_frame = None
                if preloaded_frames is not None:
                    preloaded_price_frame = preloaded_frames.get(interval)
                if preloaded_price_frame is None:
                    price = download_ohlcv(ticker, settings.period, settings.interval)
                else:
                    price = normalize_model_ohlcv_frame(preloaded_price_frame.copy(), ticker)
                _, probability_up, verdict, _, _, _, _, _ = evaluate_model(
                    price,
                    settings,
                    fast_mode=fast_mode,
                )
            verdicts.append(verdict)
            score = probability_up if verdict == "BULLISH" else 1.0 - probability_up if verdict == "BEARISH" else 0.5
            scores.append(float(score))
        except Exception:
            continue

    if not verdicts:
        return "NO DATA", 0.0

    bullish_count = sum(v == "BULLISH" for v in verdicts)
    bearish_count = sum(v == "BEARISH" for v in verdicts)

    if bullish_count == len(verdicts):
        label = "FULL BULLISH ALIGNMENT"
    elif bearish_count == len(verdicts):
        label = "FULL BEARISH ALIGNMENT"
    elif bullish_count > 0 and bearish_count == 0:
        label = "BULLISH BIAS"
    elif bearish_count > 0 and bullish_count == 0:
        label = "BEARISH BIAS"
    else:
        label = "MIXED ALIGNMENT"

    score_value = float(sum(scores) / len(scores))
    if preloaded_frames is None:
        _ALIGNMENT_CACHE[cache_key] = (label, score_value)
        _ALIGNMENT_CACHE_TS[cache_key] = time.monotonic()
    return label, score_value


def get_conviction_label(
    verdict: str,
    probability_up: float,
    alignment_label: str,
    contract: OptionContract | None,
) -> tuple[str, bool]:
    if verdict not in {"BULLISH", "BEARISH"}:
        return "NO TRADE", False

    directional_confidence = probability_up if verdict == "BULLISH" else 1.0 - probability_up
    _ = contract

    if alignment_label in {"BULLISH ALIGNMENT", "BEARISH ALIGNMENT"} and directional_confidence >= 0.70:
        return f"HIGH CONVICTION {'CALL' if verdict == 'BULLISH' else 'PUT'}", True

    if alignment_label == "PARTIAL ALIGNMENT" and directional_confidence >= 0.62:
        return "MEDIUM CONVICTION", False

    if directional_confidence >= 0.58:
        return "LOW CONVICTION", False

    return "NO TRADE", False


def assess_setup(
    close_price: float,
    expected_move: float,
    atr_pct: float,
    metrics: Metrics,
    alignment_label: str,
    conviction_label: str,
    contract: OptionContract | None,
    entry_low: float,
    entry_high: float,
    event_risk: bool = False,
    event_reason: str = "",
    regime_strength_score: float = 0.5,
    institutional_flow_score: float = 0.5,
) -> tuple[float, str, str, str]:
    score = 0.0

    alignment_points = {
        "BULLISH ALIGNMENT": 25.0,
        "BEARISH ALIGNMENT": 25.0,
        "PARTIAL ALIGNMENT": 16.0,
        "MIXED ALIGNMENT": 5.0,
        "NO DATA": 0.0,
    }
    score += alignment_points.get(alignment_label, 0.0)

    conviction_points = {
        "HIGH CONVICTION CALL": 20.0,
        "HIGH CONVICTION PUT": 20.0,
        "MEDIUM CONVICTION": 13.0,
        "LOW CONVICTION": 7.0,
        "NO TRADE": 0.0,
    }
    score += conviction_points.get(conviction_label, 0.0)

    accuracy = float(metrics["accuracy"])
    precision = float(metrics["precision"])
    roc_auc = float(metrics["roc_auc"]) if not math.isnan(float(metrics["roc_auc"])) else 0.5
    score += max(min((accuracy - 0.50) * 30.0, 5.0), 0.0)
    score += max(min((precision - 0.50) * 30.0, 5.0), 0.0)
    score += max(min((roc_auc - 0.50) * 30.0, 5.0), 0.0)

    if not math.isnan(expected_move) and not math.isnan(atr_pct) and atr_pct > 0:
        edge_ratio = abs(expected_move) / atr_pct
        score += min(edge_ratio * 5.0, 10.0)

    if not math.isnan(entry_low) and not math.isnan(entry_high):
        if entry_low <= close_price <= entry_high:
            score += 10.0
        else:
            zone_width = max(entry_high - entry_low, close_price * 0.001)
            zone_center = (entry_low + entry_high) / 2.0
            distance_from_center = abs(close_price - zone_center)
            normalized = min(distance_from_center / zone_width, 2.0)
            score += max(10.0 - normalized * 5.0, 0.0)

    score += (float(regime_strength_score) - 0.5) * 16.0
    score += (float(institutional_flow_score) - 0.5) * 20.0

    if event_risk:
        score = max(score - 25.0, 0.0)

    score = max(0.0, min(score, 100.0))

    grade = "Avoid"
    if score >= 85:
        grade = "A+ setup"
    elif score >= 72:
        grade = "A setup"
    elif score >= 55:
        grade = "B setup"

    reject_reason = ""
    decision = "VALID TRADE"

    if event_risk:
        decision = "REJECT"
        reject_reason = event_reason or "Major event too close."
    elif alignment_label == "MIXED ALIGNMENT":
        decision = "PASS"
        reject_reason = "Mixed multi-timeframe alignment."
    elif conviction_label in {"LOW CONVICTION", "NO TRADE"}:
        decision = "PASS"
        reject_reason = "Conviction too weak."
    elif regime_strength_score < 0.38:
        decision = "PASS"
        reject_reason = "Active regime has a weak live track record."
    elif institutional_flow_score < 0.36:
        decision = "PASS"
        reject_reason = "Institutional flow support is too weak."
    elif math.isnan(expected_move) or abs(expected_move) < max(atr_pct * 0.5, 0.001):
        decision = "PASS"
        reject_reason = "Expected edge too small."
    elif score < 55:
        decision = "REJECT"
        reject_reason = "Setup score too low."

    return float(score), grade, decision, reject_reason


def analyze_ticker(
    settings: ModelConfig,
    make_chart: bool = True,
    preloaded_price_frame: pd.DataFrame | None = None,
    preloaded_frames: Dict[str, pd.DataFrame] | None = None,
    include_contract_lookup: bool = True,
    include_event_lookup: bool = True,
    include_alignment: bool = True,
    fast_mode: bool = False,
    journal_calibration: dict[str, float | int | None] | None = None,
) -> AnalysisReport:
    if preloaded_price_frame is None:
        price = download_ohlcv(settings.ticker, settings.period, settings.interval)
    else:
        price = normalize_model_ohlcv_frame(preloaded_price_frame.copy(), settings.ticker)

    benchmark_frame, sector_frame, peer_frames = _resolve_prediction_companion_frames(
        settings.ticker,
        settings,
        fast_mode=fast_mode,
    )

    if make_chart:
        _chart_days = settings.chart_days
        _ = _chart_days

    close_value, base_probability_up_value, technical_verdict, technical_expected_move, atr_pct_value, metrics_value, notes_value, ensemble_summary = evaluate_model(
        price,
        settings,
        benchmark_frame=benchmark_frame,
        sector_frame=sector_frame,
        peer_frames=peer_frames,
        fast_mode=fast_mode,
    )

    if include_event_lookup:
        event_info = get_event_risk_info(settings.ticker)
    else:
        event_info = {
            "event_risk": False,
            "event_label": "EVENT CHECK DEFERRED",
            "event_reason": "",
            "next_event_name": "",
            "next_event_date": "",
            "next_event_days": None,
            "next_earnings_name": "",
            "next_earnings_date": "",
            "next_earnings_days": None,
            "next_macro_name": "",
            "next_macro_date": "",
            "next_macro_days": None,
            "next_corporate_name": "",
            "next_corporate_date": "",
            "next_corporate_days": None,
            "event_class": "none",
            "event_severity": "low",
            "event_window_label": "quiet_window",
            "session_label": "quiet_session",
            "trade_posture": "clear",
            "primary_event_label": "Deferred",
            "summary": "Event checks are deferred for this pass.",
            "upcoming_events": [],
        }

    news_info = _resolve_news_sentiment_info(settings.ticker, fast_mode=fast_mode)
    market_state_info = _resolve_market_state_info(
        settings.ticker,
        settings.interval,
        price,
        fast_mode=fast_mode,
    )
    relative_strength_info = _resolve_relative_strength_info(
        settings.ticker,
        price,
        interval=settings.interval,
        benchmark_frame=benchmark_frame,
        sector_frame=sector_frame,
        peer_frames=peer_frames,
        fast_mode=fast_mode,
    )
    options_flow_info = _resolve_options_flow_info(
        settings.ticker,
        underlying_price=close_value,
        fast_mode=fast_mode,
    )
    event_revision_info = _resolve_event_revision_info(
        settings.ticker,
        fast_mode=fast_mode,
    )
    forecast = build_price_forecast(
        latest_close=close_value,
        technical_probability_up=float(ensemble_summary.get("technical_probability_up", base_probability_up_value)),
        technical_expected_move=technical_expected_move,
        atr_pct=atr_pct_value,
        settings=settings,
        event_info=event_info,
        news_info=news_info,
        market_state=market_state_info,
        relative_strength=relative_strength_info,
        options_flow=options_flow_info,
        event_revision=event_revision_info,
        ensemble_summary=ensemble_summary,
        journal_calibration=journal_calibration,
    )
    probability_up_value = float(forecast["state_adjusted_probability_up"])
    expected_move_value = float(forecast["adjusted_expected_move"])
    verdict_value = technical_verdict
    if probability_up_value > 0.65:
        verdict_value = "BULLISH"
    elif probability_up_value < 0.35:
        verdict_value = "BEARISH"
    else:
        verdict_value = "NEUTRAL / MIXED"

    notes_value = list(notes_value)
    if news_info["article_count"] > 0:
        notes_value.append(
            f"News bias is {news_info['label'].lower()} ({news_info['article_count']} headline(s), confidence {float(news_info['confidence']):.0%})."
        )
    if int((journal_calibration or {}).get("resolved_count") or 0) >= 8:
        calibration_scope = str((journal_calibration or {}).get("calibration_scope") or "global")
        scope_label = f"{calibration_scope} sample"
        if calibration_scope == "regime":
            scope_label = f"{str(forecast.get('market_regime') or 'matching')} regime sample"
        notes_value.append(
            f"Journal calibration adjusted the live bias using {int((journal_calibration or {}).get('resolved_count') or 0)} resolved forecasts from the {scope_label} for {settings.ticker} {settings.interval}."
        )
    if event_info["event_risk"]:
        notes_value.append(f"Event-risk dampener applied: {event_info['event_reason'] or 'major event nearby.'}")
    if str(forecast.get("volatility_regime") or "") == "elevated":
        notes_value.append("Volatility regime is elevated, so the prediction stack is applying a larger uncertainty penalty.")
    if float(forecast.get("driver_agreement_score", 0.5)) <= 0.42:
        notes_value.append("Driver agreement is weak across the intraday ensemble, so promotion standards are tighter.")
    elif float(forecast.get("driver_agreement_score", 0.5)) >= 0.72:
        notes_value.append("Driver agreement is strong across the intraday ensemble.")
    if float(forecast.get("relative_strength_score", 0.5)) >= 0.62:
        notes_value.append("Cross-sectional relative strength is supportive versus the benchmark and sector.")
    elif float(forecast.get("relative_strength_score", 0.5)) <= 0.38:
        notes_value.append("Cross-sectional relative strength is lagging the benchmark/sector backdrop.")
    if abs(_safe_float((forecast.get("iv_context") or {}).get("net_flow_pressure"), 0.0)) >= 0.12:
        notes_value.append("Options-flow pressure is materially influencing the state-adjusted probability.")
    if float(forecast.get("regime_strength_score", 0.5)) <= 0.42:
        notes_value.append(
            f"Active {str(forecast.get('market_regime') or 'market')} regime has been weak live, so confidence and risk are being reduced."
        )
    elif float(forecast.get("regime_strength_score", 0.5)) >= 0.62:
        notes_value.append(
            f"Active {str(forecast.get('market_regime') or 'market')} regime has been supportive live, so confidence is getting a modest boost."
        )
    prediction_data_quality = str(forecast.get("prediction_data_quality") or "degraded_fallback")
    if _safe_bool(forecast.get("degraded_prediction")):
        notes_value.append(
            f"Prediction data quality is degraded ({prediction_data_quality.replace('_', ' ')}), so the intraday stack is applying a confidence penalty."
        )
    elif prediction_data_quality == "cached_hybrid_paid_data":
        notes_value.append("Prediction state is using cached paid-data snapshots that are still within freshness limits.")
    elif prediction_data_quality == "full_hybrid_paid_data":
        notes_value.append("Prediction state is sourced from the full Alpaca plus Polygon hybrid data stack.")

    results_for_plan: dict[str, float | str] = {
        "close": close_value,
        "probability_up": probability_up_value,
        "verdict": verdict_value,
        "expected_move": expected_move_value,
        "atr_pct": atr_pct_value,
    }
    option_plan = build_option_plan(
        results_for_plan,
        settings.ticker,
        include_contract_lookup=include_contract_lookup,
    )
    exit_plan = build_exit_plan(
        verdict=verdict_value,
        entry_reference_price=close_value,
        invalidation_price=_safe_float(option_plan.get("invalidation_price")),
        time_stop_bars=int(settings.horizon),
        entry_reference_source="analysis_close",
    )
    institutional_flow = build_institutional_flow_profile(
        settings.ticker,
        settings.interval,
        price,
        option_plan["recommended_contract"],
        event_info,
    )
    if include_alignment:
        alignment_label, alignment_score = get_interval_verdict_summary(
            settings.ticker,
            settings.horizon,
            current_interval=settings.interval,
            current_probability_up=probability_up_value,
            current_verdict=verdict_value,
            preloaded_frames=preloaded_frames,
            fast_mode=fast_mode,
        )
    else:
        alignment_label, alignment_score = "ALIGNMENT DEFERRED", 0.0
    conviction_label, is_high_conviction = get_conviction_label(
        verdict=verdict_value,
        probability_up=probability_up_value,
        alignment_label=alignment_label,
        contract=option_plan["recommended_contract"],
    )

    setup_score, setup_grade, trade_decision, reject_reason = assess_setup(
        close_price=close_value,
        expected_move=expected_move_value,
        atr_pct=atr_pct_value,
        metrics=metrics_value,
        alignment_label=alignment_label,
        conviction_label=conviction_label,
        contract=option_plan["recommended_contract"],
        entry_low=float(option_plan["entry_low_price"]),
        entry_high=float(option_plan["entry_high_price"]),
        event_risk=event_info["event_risk"],
        event_reason=event_info["event_reason"],
        regime_strength_score=float(forecast.get("regime_strength_score", 0.5)),
        institutional_flow_score=float(institutional_flow.get("score", 0.5)),
    )
    scoring_context = _build_analysis_scoring_context(
        ticker=settings.ticker,
        close_price=close_value,
        probability_up=probability_up_value,
        expected_move=expected_move_value,
        alignment_label=alignment_label,
        conviction_label=conviction_label,
        contract=option_plan["recommended_contract"],
        forecast=forecast,
        institutional_flow=institutional_flow,
        price_frame=price,
        trade_decision=trade_decision,
        event_risk=bool(event_info["event_risk"]),
    )
    option_execution_profile = build_option_execution_profile(
        ticker=settings.ticker,
        interval=settings.interval,
        close_price=close_value,
        option_plan=option_plan,
        institutional_flow=institutional_flow,
        event_info=event_info,
    )
    vehicle_recommendation, vehicle_reason = select_trade_vehicle(
        verdict=verdict_value,
        trade_decision=trade_decision,
        reject_reason=reject_reason,
        setup_score=setup_score,
        option_execution_profile=option_execution_profile,
    )
    option_execution_profile["vehicle_recommendation"] = vehicle_recommendation
    option_execution_profile["vehicle_reason"] = vehicle_reason

    institutional_flow_label = str(institutional_flow.get("label") or "").strip()
    avg_flow_dollars = _safe_float(institutional_flow.get("avg_dollar_volume"))
    if institutional_flow_label:
        if math.isnan(avg_flow_dollars):
            notes_value.append(f"{institutional_flow_label.title()} for the current setup.")
        else:
            notes_value.append(
                f"{institutional_flow_label.title()} with average bar dollar volume near ${avg_flow_dollars:,.0f}."
            )
    for flow_note in institutional_flow.get("notes") or []:
        if flow_note not in notes_value:
            notes_value.append(flow_note)
    notes_value.append(f"Vehicle recommendation: {vehicle_recommendation.replace('_', ' ')}.")
    if vehicle_reason and vehicle_reason not in notes_value:
        notes_value.append(vehicle_reason)
    for execution_reason in option_execution_profile.get("reject_reasons") or []:
        if execution_reason not in notes_value:
            notes_value.append(execution_reason)

    return {
        "ticker": settings.ticker,
        "close": close_value,
        "base_probability_up": float(forecast["base_probability_up"]),
        "state_adjusted_probability_up": float(forecast["state_adjusted_probability_up"]),
        "probability_up": probability_up_value,
        "probability_not_up": 1.0 - probability_up_value,
        "technical_probability_up": float(forecast["technical_probability_up"]),
        "verdict": verdict_value,
        "expected_move": expected_move_value,
        "technical_expected_move": technical_expected_move,
        "atr_pct": atr_pct_value,
        "interval": settings.interval,
        "metrics": metrics_value,
        "notes": notes_value,
        "feature_importance": None,
        "option_plan": option_plan,
        "exit_plan": exit_plan,
        "alignment_label": alignment_label,
        "alignment_score": alignment_score,
        "conviction_label": conviction_label,
        "is_high_conviction": is_high_conviction,
        "setup_score": setup_score,
        "uncertainty_score": float(forecast["uncertainty_score"]),
        "prediction_data_quality": str(forecast.get("prediction_data_quality") or "degraded_fallback"),
        "degraded_prediction": _safe_bool(forecast.get("degraded_prediction")),
        "state_source_map": dict(forecast.get("state_source_map") or {}),
        "state_freshness": cast(dict[str, dict[str, object]], dict(forecast.get("state_freshness") or {})),
        "driver_scores": dict(forecast.get("driver_scores") or {}),
        "driver_agreement_score": float(forecast["driver_agreement_score"]),
        "volatility_regime": str(forecast["volatility_regime"]),
        "relative_strength_score": float(forecast["relative_strength_score"]),
        "iv_context": dict(forecast.get("iv_context") or {}),
        "market_state": cast(MarketStateInfo, dict(forecast.get("market_state") or {})),
        "relative_strength": cast(RelativeStrengthInfo, dict(forecast.get("relative_strength") or {})),
        "options_flow": cast(OptionsFlowInfo, dict(forecast.get("options_flow") or {})),
        "event_revision": cast(EventRevisionInfo, dict(forecast.get("event_revision") or {})),
        "ensemble_summary": cast(PredictionEnsembleSummary, dict(forecast.get("ensemble_summary") or {})),
        "alpha_score": float(scoring_context["alpha_score"]),
        "execution_score": float(scoring_context["execution_score"]),
        "portfolio_score": float(scoring_context["portfolio_score"]),
        "edge_to_cost_ratio": float(scoring_context["edge_to_cost_ratio"]),
        "expected_edge_bps": float(scoring_context["expected_edge_bps"]),
        "estimated_cost_bps": float(scoring_context["estimated_cost_bps"]),
        "spread_bps": float(scoring_context["spread_bps"]),
        "average_dollar_volume": float(scoring_context["average_dollar_volume"]),
        "average_1m_dollar_volume": float(scoring_context["average_1m_dollar_volume"]),
        "quote_age_seconds": float(scoring_context["quote_age_seconds"]),
        "proxy_correlation_bucket": str(scoring_context["proxy_correlation_bucket"]),
        "portfolio_rank": None,
        "auto_entry_eligible": _safe_bool(scoring_context["auto_entry_eligible"]),
        "setup_grade": setup_grade,
        "trade_decision": trade_decision,
        "reject_reason": reject_reason,
        "event_risk": event_info["event_risk"],
        "event_label": event_info["event_label"],
        "event_reason": event_info["event_reason"],
        "next_event_name": event_info["next_event_name"],
        "next_event_date": event_info["next_event_date"],
        "event_context": event_info,
        "news_sentiment": news_info,
        "forecast": forecast,
        "market_regime": str(forecast.get("market_regime", "")),
        "institutional_flow": institutional_flow,
        "option_execution_profile": option_execution_profile,
        "vehicle_recommendation": vehicle_recommendation,
        "vehicle_reason": vehicle_reason,
    }


def evaluate_trade_status(report: AnalysisReport, live_price: float | None = None) -> str:
    if report.get("event_risk", False):
        return "WAIT UNTIL AFTER EVENT"

    option_plan = report["option_plan"]
    current_price = report["close"] if live_price is None else float(live_price)
    verdict = report["verdict"]

    if option_plan["action"] == "WAIT":
        return "NO TRADE"

    entry_low = option_plan["entry_low_price"]
    entry_high = option_plan["entry_high_price"]
    target_price = option_plan["expected_underlying_target"]
    invalidation_price = option_plan["invalidation_price"]

    if verdict == "BULLISH":
        if not np.isnan(target_price) and current_price >= target_price:
            return "TAKE PROFIT"
        if not np.isnan(invalidation_price) and current_price <= invalidation_price:
            return "CUT LOSS"
    elif verdict == "BEARISH":
        if not np.isnan(target_price) and current_price <= target_price:
            return "TAKE PROFIT"
        if not np.isnan(invalidation_price) and current_price >= invalidation_price:
            return "CUT LOSS"

    if not np.isnan(entry_low) and not np.isnan(entry_high) and entry_low <= current_price <= entry_high:
        return "ENTER NOW"

    return "WAIT FOR ENTRY"


def build_alert_record(report: AnalysisReport, live_price: float) -> AlertRecord:
    option_plan = report["option_plan"]
    contract = option_plan["recommended_contract"]
    trade_status = evaluate_trade_status(report, live_price)

    alert_type = "INFO"
    message = "Monitoring trade."

    if report.get("event_risk", False):
        alert_type = "EVENT_RISK"
        message = report.get("event_reason", "Major event too close.")
    elif report["trade_decision"] == "REJECT":
        alert_type = "REJECTED_SETUP"
        message = report["reject_reason"] or "Setup rejected."
    elif report["conviction_label"].startswith("HIGH CONVICTION") and trade_status in {"ENTER NOW", "WAIT FOR ENTRY"}:
        alert_type = "HIGH_CONVICTION"
        message = f"{report['conviction_label']} setup detected."
    elif option_plan["action"] == "WAIT":
        alert_type = "NO_TRADE"
        message = "No trade setup right now."
    elif contract is None:
        alert_type = "NO_CLEAN_CONTRACT"
        message = "Setup exists but no clean contract was found."
    elif trade_status == "ENTER NOW":
        alert_type = "ENTRY_ZONE_HIT"
        message = "Price is inside the entry zone."
    elif trade_status == "TAKE PROFIT":
        alert_type = "TARGET_HIT"
        message = "Target price has been reached."
    elif trade_status == "CUT LOSS":
        alert_type = "INVALIDATION_HIT"
        message = "Invalidation level has been breached."
    elif trade_status == "WAIT FOR ENTRY":
        alert_type = "WAITING"
        message = "Trade setup exists but price is not in the entry zone yet."

    contract_symbol = ""
    if contract is not None:
        contract_symbol = contract["contract_symbol"]

    return {
        "timestamp": utc_now().isoformat(),
        "ticker": report["ticker"],
        "interval": report["interval"],
        "trade_status": trade_status,
        "alert_type": alert_type,
        "message": message,
        "live_price": float(live_price),
        "entry_low": float(option_plan["entry_low_price"]),
        "entry_high": float(option_plan["entry_high_price"]),
        "target_price": float(option_plan["expected_underlying_target"]),
        "invalidation_price": float(option_plan["invalidation_price"]),
        "contract_symbol": contract_symbol,
        "verdict": report["verdict"],
    }


def append_trade_journal(alert: AlertRecord, file_path: Path = TRADE_JOURNAL_PATH) -> None:
    new_row = pd.DataFrame([alert])

    if file_path.exists():
        try:
            existing = pd.read_csv(file_path)
            dedupe_cols = ["ticker", "interval", "trade_status", "alert_type", "contract_symbol"]
            if not existing.empty:
                last_row = existing.tail(1)
                is_duplicate = True
                for col in dedupe_cols:
                    last_value = "" if col not in last_row.columns else str(last_row.iloc[0].get(col, ""))
                    new_value = str(new_row.iloc[0].get(col, ""))
                    if last_value != new_value:
                        is_duplicate = False
                        break
                if is_duplicate:
                    return
            combined = pd.concat([existing, new_row], ignore_index=True)
        except Exception:
            combined = new_row
    else:
        combined = new_row

    write_dataframe_csv(file_path, combined)
    _invalidate_file_read_cache(file_path)


def read_trade_journal(file_path: Path = TRADE_JOURNAL_PATH, limit: int = 50) -> pd.DataFrame:
    journal = _read_csv_cached(file_path)
    if journal.empty:
        return journal

    return journal.tail(limit).reset_index(drop=True)


def calculate_position_sizing(
    report: AnalysisReport,
    account_size: float,
    risk_percent: float,
) -> PositionSizing:
    contract = report["option_plan"]["recommended_contract"]
    max_risk_dollars = max(account_size * (risk_percent / 100.0), 0.0)

    empty_result: PositionSizing = {
        "account_size": float(account_size),
        "risk_percent": float(risk_percent),
        "max_risk_dollars": float(max_risk_dollars),
        "effective_max_risk_dollars": float(max_risk_dollars),
        "risk_budget_multiplier": 1.0,
        "contract_mid": float("nan"),
        "estimated_cost_per_contract": float("nan"),
        "max_loss_per_contract": float("nan"),
        "suggested_contracts": 0,
        "total_position_cost": float("nan"),
        "total_max_loss": float("nan"),
        "affordable": False,
        "status": "SKIP TRADE",
        "reason": "No clean contract available.",
    }

    if contract is None:
        return empty_result

    contract_mid = float(contract["mid"])

    if math.isnan(contract_mid) or contract_mid <= 0:
        empty_result["reason"] = "Invalid contract pricing."
        return empty_result

    estimated_cost_per_contract = contract_mid * 100.0
    exit_plan = dict(report.get("exit_plan") or {})
    risk_unit = _safe_float(exit_plan.get("risk_unit"))
    entry_reference_price = _safe_float(exit_plan.get("entry_reference_price"), _safe_float(report.get("close")))
    delta_proxy = _estimate_option_delta_proxy(
        verdict=str(report.get("verdict") or ""),
        contract=contract,
        underlying_price=entry_reference_price if not math.isnan(entry_reference_price) else _safe_float(report.get("close"), 0.0),
    )
    max_loss_per_contract = float("nan")
    if not math.isnan(risk_unit) and risk_unit > 0:
        max_loss_per_contract = min(estimated_cost_per_contract, risk_unit * max(delta_proxy, 0.2) * 100.0)
    if math.isnan(max_loss_per_contract) or max_loss_per_contract <= 0:
        stop_loss = float(report["option_plan"]["stop_loss"])
        max_loss_per_contract = estimated_cost_per_contract * stop_loss

    if max_loss_per_contract <= 0:
        empty_result["reason"] = "Invalid stop loss sizing."
        return empty_result

    regime_strength_score = float(report.get("forecast", {}).get("regime_strength_score", 0.5))
    institutional_flow_score = float((report.get("institutional_flow") or {}).get("score", 0.5))
    regime_risk_multiplier = float(np.clip(0.65 + regime_strength_score * 0.5, 0.7, 1.0))
    flow_risk_multiplier = float(np.clip(0.55 + institutional_flow_score * 0.55, 0.65, 1.0))
    risk_budget_multiplier = float(np.clip(regime_risk_multiplier * flow_risk_multiplier, 0.55, 1.0))
    effective_max_risk_dollars = float(max_risk_dollars * risk_budget_multiplier)
    suggested_contracts = int(effective_max_risk_dollars // max_loss_per_contract)
    total_position_cost = float(suggested_contracts * estimated_cost_per_contract)
    total_max_loss = float(suggested_contracts * max_loss_per_contract)

    affordable = suggested_contracts >= 1 and total_position_cost <= account_size

    if report["trade_decision"] == "REJECT":
        status = "SKIP TRADE"
        reason = report["reject_reason"] or "Setup rejected."
        affordable = False
    elif suggested_contracts < 1:
        status = "SKIP TRADE"
        reason = "Contract too expensive for current risk rule."
    elif total_position_cost > account_size:
        status = "TOO LARGE"
        reason = "Position cost exceeds account size."
        affordable = False
    elif report["trade_decision"] == "PASS":
        status = "PASS"
        reason = report["reject_reason"] or "Setup is not strong enough."
        affordable = False
    else:
        status = "VALID"
        reason = "Position size is within risk limits."

    return {
        "account_size": float(account_size),
        "risk_percent": float(risk_percent),
        "max_risk_dollars": float(max_risk_dollars),
        "effective_max_risk_dollars": float(effective_max_risk_dollars),
        "risk_budget_multiplier": float(risk_budget_multiplier),
        "contract_mid": float(contract_mid),
        "estimated_cost_per_contract": float(estimated_cost_per_contract),
        "max_loss_per_contract": float(max_loss_per_contract),
        "suggested_contracts": int(suggested_contracts if suggested_contracts > 0 else 0),
        "total_position_cost": float(total_position_cost if suggested_contracts > 0 else 0.0),
        "total_max_loss": float(total_max_loss if suggested_contracts > 0 else 0.0),
        "affordable": bool(affordable),
        "status": status,
        "reason": reason,
    }


def _normalize_trade_route_family(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized or "legacy"


def _normalize_trade_route_version(value: object, *, route_family: str) -> str:
    normalized = str(value or "").strip()
    if route_family == "current" and not normalized:
        return "ranked_entry_v1"
    return normalized


def _normalize_thesis_direction(value: object) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in {"BULLISH", "BEARISH"} else ""


def _directional_exposure_for_trade(
    *,
    instrument_type: str,
    option_right: str,
    broker_side: str = "BUY",
    thesis_direction: str = "",
) -> str:
    normalized_instrument = str(instrument_type or "").strip().lower()
    normalized_option_right = str(option_right or "").strip().lower()
    normalized_broker_side = str(broker_side or "").strip().upper()

    if normalized_instrument == "equity":
        if normalized_broker_side == "SELL":
            return "bearish"
        if normalized_broker_side == "BUY":
            return "bullish"
        return "unknown"

    if normalized_option_right == "put":
        return "bearish" if normalized_broker_side == "BUY" else "bullish"
    if normalized_option_right == "call":
        return "bullish" if normalized_broker_side == "BUY" else "bearish"
    normalized_thesis_direction = str(thesis_direction or "").strip().upper()
    if normalized_thesis_direction == "BULLISH":
        return "bullish"
    if normalized_thesis_direction == "BEARISH":
        return "bearish"
    return "unknown"


def _validation_sample_bucket_for_route(route_family: str, route_version: str) -> str:
    if route_family == "current" and route_version == "ranked_entry_v1":
        return "current_route"
    return "legacy"


def open_trade_record(
    report: AnalysisReport,
    live_price: float,
    position: PositionSizing,
    order_ticket: dict[str, object] | None = None,
    trade_id: str | None = None,
    order_id: str | None = None,
) -> dict[str, object]:
    contract = report["option_plan"]["recommended_contract"]
    order_ticket = order_ticket or {}
    instrument_type = str(order_ticket.get("instrument_type", "listed_option") or "listed_option").strip().lower()
    option_strategy = str(order_ticket.get("option_strategy", "") or "").strip()
    option_right = str(order_ticket.get("option_right", "") or "").strip().lower()
    route_family = _normalize_trade_route_family(order_ticket.get("route_family"))
    route_version = _normalize_trade_route_version(order_ticket.get("route_version"), route_family=route_family)
    route_correlation_id = str(order_ticket.get("route_correlation_id") or "").strip()
    tenant_id = str(order_ticket.get("tenant_id") or "").strip()
    tenant_slug = str(order_ticket.get("tenant_slug") or "").strip().lower()
    broker_side = str(order_ticket.get("broker_side") or "BUY").strip().upper() or "BUY"
    thesis_direction = _normalize_thesis_direction(order_ticket.get("thesis_direction") or report.get("verdict"))
    directional_exposure = _directional_exposure_for_trade(
        instrument_type=instrument_type,
        option_right=option_right,
        broker_side=broker_side,
        thesis_direction=thesis_direction,
    )
    validation_sample_bucket = _validation_sample_bucket_for_route(route_family, route_version)

    contract_symbol = ""
    contract_mid = float("nan")
    contract_bid = float("nan")
    contract_ask = float("nan")
    contract_spread_pct = float("nan")
    contract_volume = 0
    contract_open_interest = 0
    contract_quote_timestamp = ""
    if instrument_type == "equity":
        contract_symbol = str(order_ticket.get("contract_symbol") or f"EQUITY:{report['ticker']}")
        contract_mid = float(live_price) / 100.0
    elif contract is not None:
        contract_symbol = contract["contract_symbol"]
        contract_mid = float(contract["mid"])
        contract_bid = float(contract["bid"]) if not pd.isna(contract["bid"]) else float("nan")
        contract_ask = float(contract["ask"]) if not pd.isna(contract["ask"]) else float("nan")
        contract_spread_pct = float(contract["spread_pct"]) if not pd.isna(contract["spread_pct"]) else float("nan")
        contract_volume = int(contract["volume"])
        contract_open_interest = int(contract["open_interest"])
        contract_quote_timestamp = str(contract.get("quote_timestamp") or "")

    exit_plan = _build_report_exit_plan(
        report,
        entry_reference_price=float(live_price),
        entry_reference_source="live_open",
    )
    active_stop_price = _safe_float(exit_plan.get("initial_stop_price"))
    horizon_bars = _safe_int(report.get("forecast", {}).get("forecast_horizon_bars"), 0)

    return {
        "trade_id": str(trade_id or uuid4()),
        "order_id": str(order_id or uuid4()),
        "opened_at": utc_now().isoformat(),
        "ticker": report["ticker"],
        "interval": report["interval"],
        "verdict": report["verdict"],
        "alignment_label": report["alignment_label"],
        "conviction_label": report["conviction_label"],
        "setup_score": float(report["setup_score"]),
        "alpha_score": float(_safe_float(report.get("alpha_score"))),
        "execution_score": float(_safe_float(report.get("execution_score"))),
        "portfolio_score": float(_safe_float(report.get("portfolio_score"))),
        "edge_to_cost_ratio": float(_safe_float(report.get("edge_to_cost_ratio"))),
        "proxy_correlation_bucket": str(report.get("proxy_correlation_bucket") or _proxy_correlation_bucket_for_ticker(report["ticker"])),
        "portfolio_rank": _safe_int(report.get("portfolio_rank"), 0) or None,
        "auto_entry_eligible": _safe_bool(report.get("auto_entry_eligible", False)),
        "setup_grade": report["setup_grade"],
        "trade_decision": report["trade_decision"],
        "reject_reason": report["reject_reason"],
        "event_risk": _safe_bool(report.get("event_risk", False)),
        "event_label": report.get("event_label", ""),
        "event_reason": report.get("event_reason", ""),
        "next_event_name": report.get("next_event_name", ""),
        "next_event_date": report.get("next_event_date", ""),
        "instrument_type": instrument_type,
        "instrument_label": "Equity" if instrument_type == "equity" else "Listed option",
        "unit_label": "shares" if instrument_type == "equity" else "contracts",
        "option_strategy": option_strategy,
        "option_right": option_right,
        "route_family": route_family,
        "route_version": route_version,
        "route_correlation_id": route_correlation_id,
        "tenant_id": tenant_id,
        "tenant_slug": tenant_slug,
        "broker_side": broker_side,
        "automation_entry_reason": str(order_ticket.get("automation_entry_reason", "") or "").strip(),
        "thesis_direction": thesis_direction,
        "directional_exposure": directional_exposure,
        "validation_sample_bucket": validation_sample_bucket,
        "source": str(order_ticket.get("source", "") or "").strip(),
        "portfolio_target_run_id": str(order_ticket.get("portfolio_target_run_id", "") or "").strip(),
        "strategy_desk_key": str(order_ticket.get("strategy_desk_key", "") or "").strip(),
        "desk_contributions": serialize_value(order_ticket.get("desk_contributions") or []),
        "live_price_at_open": float(live_price),
        "contract_symbol": contract_symbol,
        "contract_mid_at_open": contract_mid,
        "contract_bid_at_open": contract_bid,
        "contract_ask_at_open": contract_ask,
        "contract_spread_pct": contract_spread_pct,
        "contract_volume": contract_volume,
        "contract_open_interest": contract_open_interest,
        "contract_quote_timestamp": contract_quote_timestamp,
        "suggested_contracts": float(position["suggested_contracts"]),
        "position_cost": float(position["total_position_cost"]),
        "max_risk_dollars": float(position["max_risk_dollars"]),
        "horizon_bars": int(horizon_bars),
        "contract_expiration": str(order_ticket.get("contract_expiration", "") or ""),
        "contract_strike": float(order_ticket["contract_strike"]) if order_ticket.get("contract_strike") else float("nan"),
        "target_price": float(report["option_plan"]["expected_underlying_target"]),
        "invalidation_price": float(report["option_plan"]["invalidation_price"]),
        "tp1_pct": float(report["option_plan"]["take_profit_1"]),
        "tp2_pct": float(report["option_plan"]["take_profit_2"]),
        "exit_plan": exit_plan,
        "active_stop_price": active_stop_price,
        "tp1_taken_at": "",
        "tp2_taken_at": "",
        "bars_held": 0,
        "last_exit_reason": "",
        "current_exit_stage": "INITIAL",
        "next_target_price": float(_safe_float(exit_plan.get("tp1_price"))),
        "order_type": str(order_ticket.get("order_type", "market")),
        "time_in_force": str(order_ticket.get("time_in_force", "day")),
        "limit_price": float(order_ticket["limit_price"]) if order_ticket.get("limit_price") else float("nan"),
        "stop_price": float(order_ticket["stop_price"]) if order_ticket.get("stop_price") else float("nan"),
        "trailing_percent": float(order_ticket["trailing_percent"]) if order_ticket.get("trailing_percent") else float("nan"),
        "extended_hours": _safe_bool(order_ticket.get("extended_hours", False)),
        "fractional_shares_only": _safe_bool(order_ticket.get("fractional_shares_only", False)),
        "status": "OPEN",
    }


def pending_order_record(
    report: AnalysisReport,
    live_price: float,
    position: PositionSizing,
    order_ticket: dict[str, object] | None = None,
    trade_id: str | None = None,
    order_id: str | None = None,
) -> dict[str, object]:
    record = open_trade_record(
        report,
        live_price,
        position,
        order_ticket=order_ticket,
        trade_id=trade_id,
        order_id=order_id,
    )
    submitted_at = utc_now().isoformat()
    total_contracts = float(position["suggested_contracts"])
    record.update(
        {
            "submitted_at": submitted_at,
            "updated_at": submitted_at,
            "live_price_at_submit": float(live_price),
            "filled_contracts": 0.0,
            "remaining_contracts": total_contracts,
            "order_status": "WORKING",
            "route_state": "accepted",
            "book_state": "pending",
            "status": "PENDING",
        }
    )
    return record


def append_open_trade(record: dict[str, object], file_path: Path = OPEN_TRADES_PATH) -> None:
    new_row = pd.DataFrame([record])

    if file_path.exists():
        try:
            existing = pd.read_csv(file_path)
            if not existing.empty:
                last_row = existing.tail(1).iloc[0]
                dedupe_cols = ["tenant_id", "tenant_slug", "ticker", "interval", "contract_symbol", "verdict", "status"]
                duplicate = True
                for col in dedupe_cols:
                    existing_value = str(last_row.get(col, ""))
                    new_value = str(new_row.iloc[0].get(col, ""))
                    if existing_value != new_value:
                        duplicate = False
                        break
                if duplicate:
                    existing_time = str(last_row.get("opened_at", ""))[:16]
                    new_time = str(new_row.iloc[0].get("opened_at", ""))[:16]
                    if existing_time == new_time:
                        return
            combined = pd.concat([existing, new_row], ignore_index=True)
        except Exception:
            combined = new_row
    else:
        combined = new_row

    write_dataframe_csv(file_path, combined)
    _invalidate_file_read_cache(file_path)


def append_pending_order(record: dict[str, object], file_path: Path = PENDING_ORDERS_PATH) -> None:
    new_row = pd.DataFrame([record])

    if file_path.exists():
        try:
            existing = pd.read_csv(file_path)
            order_id = str(new_row.iloc[0].get("order_id", "") or "").strip()
            if order_id and not existing.empty and "order_id" in existing.columns:
                duplicate = existing["order_id"].astype(str).str.strip() == order_id
                if duplicate.any():
                    return
            combined = pd.concat([existing, new_row], ignore_index=True)
        except Exception:
            combined = new_row
    else:
        combined = new_row

    write_dataframe_csv(file_path, combined)
    _invalidate_file_read_cache(file_path)


def _deserialize_trade_frame_objects(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "exit_plan" not in frame.columns:
        return frame
    normalized = frame.copy()
    normalized["exit_plan"] = normalized["exit_plan"].apply(_coerce_dict)
    return normalized


def read_open_trades(file_path: Path = OPEN_TRADES_PATH) -> pd.DataFrame:
    return _deserialize_trade_frame_objects(_read_csv_cached(file_path))


def read_closed_trades(file_path: Path = CLOSED_TRADES_PATH) -> pd.DataFrame:
    return _deserialize_trade_frame_objects(_read_csv_cached(file_path))


def read_pending_orders(file_path: Path = PENDING_ORDERS_PATH) -> pd.DataFrame:
    return _deserialize_trade_frame_objects(_read_csv_cached(file_path))


def read_forecast_journal(file_path: Path = FORECAST_JOURNAL_PATH) -> pd.DataFrame:
    return _read_csv_cached(file_path)


def append_forecast_journal_record(record: dict[str, object], file_path: Path = FORECAST_JOURNAL_PATH) -> None:
    append_forecast_journal_records([record], file_path=file_path)


def append_forecast_journal_records(
    records: Sequence[dict[str, object]],
    file_path: Path = FORECAST_JOURNAL_PATH,
) -> None:
    journal_rows = [dict(record) for record in records if isinstance(record, dict) and record]
    if not journal_rows:
        return
    new_rows = pd.DataFrame(journal_rows)
    key_columns = [
        "tenant_id",
        "tenant_slug",
        "ticker",
        "interval",
        "forecast_at",
        "prediction_stack_version",
        "prediction_configuration",
    ]
    for column in key_columns:
        if column not in new_rows.columns:
            new_rows[column] = ""
        new_rows[column] = new_rows[column].fillna("").astype(str)

    if file_path.exists():
        try:
            existing = pd.read_csv(file_path)
            for column in key_columns:
                if column not in existing.columns:
                    existing[column] = ""
                existing[column] = existing[column].fillna("").astype(str)
            new_keys = {tuple(row) for row in new_rows[key_columns].itertuples(index=False, name=None)}
            if existing.empty:
                combined = existing
            else:
                existing_keys = existing[key_columns].apply(lambda row: tuple(row), axis=1)
                combined = existing.loc[~existing_keys.isin(new_keys)].copy()
            combined = pd.concat([combined, new_rows], ignore_index=True)
        except Exception:
            combined = new_rows
    else:
        combined = new_rows

    write_dataframe_csv(file_path, combined)
    _invalidate_file_read_cache(file_path)


def build_forecast_journal_record(
    report: AnalysisReport,
    *,
    forecast_at: str | None = None,
    forecast_group_id: str | None = None,
    prediction_stack_version: str | None = None,
    prediction_configuration: str | None = None,
    tenant_id: str | None = None,
    tenant_slug: str | None = None,
) -> dict[str, object]:
    forecast = dict(report.get("forecast") or {})
    news = dict(report.get("news_sentiment") or {})
    state_source_map = dict(report.get("state_source_map") or forecast.get("state_source_map") or {})
    state_freshness = dict(report.get("state_freshness") or forecast.get("state_freshness") or {})
    market_state = dict(report.get("market_state") or forecast.get("market_state") or {})
    event_context = dict(report.get("event_context") or {})

    def _freshness_status(key: str) -> str:
        freshness = state_freshness.get(key)
        if isinstance(freshness, dict):
            return str(freshness.get("freshness_status") or freshness.get("status") or "").strip().lower()
        return ""

    return {
        "forecast_at": str(forecast_at or utc_now().isoformat()),
        "forecast_group_id": str(forecast_group_id or ""),
        "prediction_stack_version": str(prediction_stack_version or ""),
        "prediction_configuration": str(prediction_configuration or ""),
        "tenant_id": str(tenant_id or "").strip(),
        "tenant_slug": str(tenant_slug or "").strip().lower(),
        "ticker": str(report.get("ticker", "")),
        "interval": str(report.get("interval", "")),
        "market_regime": str(report.get("market_regime") or forecast.get("market_regime") or ""),
        "volatility_regime": str(report.get("volatility_regime") or forecast.get("volatility_regime") or ""),
        "prediction_data_quality": str(report.get("prediction_data_quality") or forecast.get("prediction_data_quality") or ""),
        "degraded_prediction": _safe_bool(report.get("degraded_prediction", forecast.get("degraded_prediction", False))),
        "market_state_source": str(state_source_map.get("market_state") or market_state.get("source_detail") or market_state.get("source") or ""),
        "relative_strength_source": str(
            state_source_map.get("relative_strength")
            or (report.get("relative_strength") or forecast.get("relative_strength") or {}).get("source_detail")
            or (report.get("relative_strength") or forecast.get("relative_strength") or {}).get("source")
            or ""
        ),
        "options_flow_source": str(
            state_source_map.get("options_flow")
            or (report.get("options_flow") or forecast.get("options_flow") or {}).get("source_detail")
            or (report.get("options_flow") or forecast.get("options_flow") or {}).get("source")
            or ""
        ),
        "event_revision_source": str(
            state_source_map.get("event_revision")
            or (report.get("event_revision") or forecast.get("event_revision") or {}).get("source_detail")
            or (report.get("event_revision") or forecast.get("event_revision") or {}).get("source")
            or ""
        ),
        "market_state_freshness_status": _freshness_status("market_state"),
        "relative_strength_freshness_status": _freshness_status("relative_strength"),
        "options_flow_freshness_status": _freshness_status("options_flow"),
        "event_revision_freshness_status": _freshness_status("event_revision"),
        "session_label": str(market_state.get("time_of_day_bucket") or event_context.get("session_label") or ""),
        "event_window_label": str(event_context.get("event_window_label") or ""),
        "horizon": int(report.get("forecast", {}).get("forecast_horizon_bars") or 0),
        "close": float(report.get("close", float("nan"))),
        "probability_up": float(report.get("probability_up", float("nan"))),
        "technical_probability_up": float(report.get("technical_probability_up", float("nan"))),
        "expected_move": float(report.get("expected_move", float("nan"))),
        "technical_expected_move": float(report.get("technical_expected_move", float("nan"))),
        "expected_price": float(forecast.get("expected_price", float("nan"))),
        "upper_price": float(forecast.get("upper_price", float("nan"))),
        "lower_price": float(forecast.get("lower_price", float("nan"))),
        "forecast_confidence": float(forecast.get("confidence_score", float("nan"))),
        "technical_confidence_component": float(
            dict(forecast.get("contribution_breakdown") or {}).get("technical_confidence_component", float("nan"))
        ),
        "news_confidence_component": float(
            dict(forecast.get("contribution_breakdown") or {}).get("news_confidence_component", float("nan"))
        ),
        "regime_confidence_component": float(
            dict(forecast.get("contribution_breakdown") or {}).get("regime_confidence_component", float("nan"))
        ),
        "agreement_confidence_component": float(
            dict(forecast.get("contribution_breakdown") or {}).get("agreement_confidence_component", float("nan"))
        ),
        "uncertainty_penalty_component": float(
            dict(forecast.get("contribution_breakdown") or {}).get("uncertainty_penalty_component", float("nan"))
        ),
        "journal_probability_shift": float(
            dict(forecast.get("contribution_breakdown") or {}).get("journal_probability_shift", float("nan"))
        ),
        "news_probability_shift": float(
            dict(forecast.get("contribution_breakdown") or {}).get("news_probability_shift", float("nan"))
        ),
        "market_state_probability_shift": float(
            dict(forecast.get("contribution_breakdown") or {}).get("market_state_probability_shift", float("nan"))
        ),
        "relative_strength_probability_shift": float(
            dict(forecast.get("contribution_breakdown") or {}).get("relative_strength_probability_shift", float("nan"))
        ),
        "options_flow_probability_shift": float(
            dict(forecast.get("contribution_breakdown") or {}).get("options_flow_probability_shift", float("nan"))
        ),
        "event_revision_probability_shift": float(
            dict(forecast.get("contribution_breakdown") or {}).get("event_revision_probability_shift", float("nan"))
        ),
        "event_probability_shift": float(
            dict(forecast.get("contribution_breakdown") or {}).get("event_probability_shift", float("nan"))
        ),
        "news_sentiment_score": float(news.get("sentiment_score", float("nan"))),
        "news_confidence": float(news.get("confidence", float("nan"))),
        "event_risk": _safe_bool(report.get("event_risk", False)),
        "event_label": str(report.get("event_label", "")),
        "event_reason": str(report.get("event_reason", "")),
        "next_event_name": str(report.get("next_event_name", "")),
        "next_event_date": str(report.get("next_event_date", "")),
        "resolved_at": "",
        "actual_close": float("nan"),
        "actual_return": float("nan"),
        "actual_target_up": float("nan"),
    }


def _build_shadow_prediction_report(
    base_report: AnalysisReport,
    settings_obj: ModelConfig,
    price_frame: pd.DataFrame,
    *,
    provider: MarketDataProvider,
) -> AnalysisReport:
    close_value = _safe_float(base_report.get("close"), latest_close_from_ohlcv_frame(price_frame))
    technical_probability_up = _safe_float(base_report.get("technical_probability_up"), 0.5)
    technical_expected_move = _safe_float(base_report.get("technical_expected_move"), 0.0)
    atr_pct_value = _safe_float(base_report.get("atr_pct"), 0.0)
    news_info = cast(NewsSentimentInfo, dict(base_report.get("news_sentiment") or {}))
    event_info = cast(EventRiskInfo, dict(base_report.get("event_context") or {}))
    ensemble_summary = cast(PredictionEnsembleSummary, dict(base_report.get("ensemble_summary") or {}))
    journal_calibration = dict((base_report.get("forecast") or {}).get("journal_calibration") or {})

    market_state_info = _resolve_market_state_info(
        settings_obj.ticker,
        settings_obj.interval,
        price_frame,
        provider=provider,
    )
    relative_strength_info = _resolve_relative_strength_info(
        settings_obj.ticker,
        price_frame,
        interval=settings_obj.interval,
        benchmark_frame=None,
        sector_frame=None,
        peer_frames={},
        provider=provider,
    )
    options_flow_info = _resolve_options_flow_info(
        settings_obj.ticker,
        underlying_price=close_value,
        provider=provider,
    )
    event_revision_info = _resolve_event_revision_info(
        settings_obj.ticker,
        provider=provider,
    )
    forecast = build_price_forecast(
        latest_close=close_value,
        technical_probability_up=technical_probability_up,
        technical_expected_move=technical_expected_move,
        atr_pct=atr_pct_value,
        settings=settings_obj,
        event_info=event_info,
        news_info=news_info,
        market_state=market_state_info,
        relative_strength=relative_strength_info,
        options_flow=options_flow_info,
        event_revision=event_revision_info,
        ensemble_summary=ensemble_summary,
        journal_calibration=journal_calibration,
    )
    probability_up_value = float(forecast["state_adjusted_probability_up"])
    expected_move_value = float(forecast["adjusted_expected_move"])
    return cast(
        AnalysisReport,
        {
            "ticker": str(base_report.get("ticker", settings_obj.ticker)),
            "close": close_value,
            "interval": str(base_report.get("interval", settings_obj.interval)),
            "probability_up": probability_up_value,
            "technical_probability_up": technical_probability_up,
            "expected_move": expected_move_value,
            "technical_expected_move": technical_expected_move,
            "forecast": forecast,
            "market_regime": str(forecast.get("market_regime") or base_report.get("market_regime") or ""),
            "volatility_regime": str(forecast.get("volatility_regime") or ""),
            "prediction_data_quality": str(forecast.get("prediction_data_quality") or ""),
            "degraded_prediction": _safe_bool(forecast.get("degraded_prediction")),
            "state_source_map": dict(forecast.get("state_source_map") or {}),
            "state_freshness": cast(dict[str, dict[str, object]], dict(forecast.get("state_freshness") or {})),
            "market_state": cast(MarketStateInfo, dict(forecast.get("market_state") or {})),
            "relative_strength": cast(RelativeStrengthInfo, dict(forecast.get("relative_strength") or {})),
            "options_flow": cast(OptionsFlowInfo, dict(forecast.get("options_flow") or {})),
            "event_revision": cast(EventRevisionInfo, dict(forecast.get("event_revision") or {})),
            "news_sentiment": news_info,
            "event_context": event_info,
            "event_risk": _safe_bool(base_report.get("event_risk")),
            "event_label": str(base_report.get("event_label") or ""),
            "event_reason": str(base_report.get("event_reason") or ""),
            "next_event_name": str(base_report.get("next_event_name") or ""),
            "next_event_date": str(base_report.get("next_event_date") or ""),
        },
    )


def build_paired_forecast_journal_records(
    report: AnalysisReport,
    settings_obj: ModelConfig,
    price_frame: pd.DataFrame,
    *,
    forecast_at: str | None = None,
    tenant_id: str | None = None,
    tenant_slug: str | None = None,
) -> list[dict[str, object]]:
    forecast_at_value = str(forecast_at or utc_now().isoformat())
    if str(getattr(settings, "market_data_adapter", "") or "").strip().lower() != "hybrid":
        return [
            build_forecast_journal_record(
                report,
                forecast_at=forecast_at_value,
                tenant_id=tenant_id,
                tenant_slug=tenant_slug,
            )
        ]

    forecast_group_id = str(uuid4())
    hybrid_report = _build_shadow_prediction_report(
        report,
        settings_obj,
        price_frame,
        provider=get_market_data_provider(),
    )
    hybrid_configuration = _prediction_configuration_from_state(
        state_source_map=dict(hybrid_report.get("state_source_map") or {}),
        degraded_prediction=_safe_bool(hybrid_report.get("degraded_prediction")),
    )
    records = [
        build_forecast_journal_record(
            hybrid_report,
            forecast_at=forecast_at_value,
            forecast_group_id=forecast_group_id,
            prediction_stack_version=INTRADAY_PREDICTION_STACK_VERSION,
            prediction_configuration=hybrid_configuration,
            tenant_id=tenant_id,
            tenant_slug=tenant_slug,
        )
    ]
    if hybrid_configuration in {"hybrid_stock_only", "full_hybrid"}:
        baseline_report = _build_shadow_prediction_report(
            report,
            settings_obj,
            price_frame,
            provider=YFinanceMarketDataProvider(),
        )
        records.insert(
            0,
            build_forecast_journal_record(
                baseline_report,
                forecast_at=forecast_at_value,
                forecast_group_id=forecast_group_id,
                prediction_stack_version=INTRADAY_PREDICTION_STACK_VERSION,
                prediction_configuration="proxy_baseline",
                tenant_id=tenant_id,
                tenant_slug=tenant_slug,
            ),
        )
    return records


def resolve_forecast_journal_entries(
    ticker: str,
    interval: str,
    current_history: pd.DataFrame,
    file_path: Path = FORECAST_JOURNAL_PATH,
) -> pd.DataFrame:
    journal = read_forecast_journal(file_path)
    if journal.empty:
        return journal

    ticker_upper = _normalize_symbol(ticker)
    mask = (
        journal.get("ticker", pd.Series(dtype=str)).astype(str).str.upper().eq(ticker_upper)
        & journal.get("interval", pd.Series(dtype=str)).astype(str).eq(str(interval))
    )
    if not mask.any():
        return journal

    history = current_history.copy()
    if history.empty or "Close" not in history.columns:
        return journal
    history = history.sort_index()
    history.index = pd.to_datetime(history.index, errors="coerce")
    history = history[history.index.notna()]
    if history.empty:
        return journal

    if "resolved_at" in journal.columns:
        journal["resolved_at"] = journal["resolved_at"].astype(object)

    interval_delta = pd.Timedelta(seconds=interval_seconds(interval))
    updated = False

    for index in journal.index[mask]:
        resolved_value = journal.at[index, "resolved_at"]
        resolved_at = "" if pd.isna(resolved_value) else str(resolved_value).strip()
        horizon = _safe_int(journal.at[index, "horizon"], 0)
        forecast_close = _safe_float(journal.at[index, "close"], float("nan"))
        forecast_at = _coerce_timestamp(journal.at[index, "forecast_at"])
        if resolved_at or horizon <= 0 or forecast_at is None or math.isnan(forecast_close):
            continue

        target_time = forecast_at + (interval_delta * horizon)
        target_time = _align_timestamp_to_index_timezone(target_time, history.index)
        target_rows = history.loc[history.index >= target_time]
        if target_rows.empty:
            continue

        actual_close = float(pd.to_numeric(target_rows["Close"], errors="coerce").dropna().iloc[0])
        actual_return = (actual_close / forecast_close) - 1.0 if forecast_close else float("nan")
        journal.at[index, "resolved_at"] = target_rows.index[0].isoformat()
        journal.at[index, "actual_close"] = actual_close
        journal.at[index, "actual_return"] = actual_return
        journal.at[index, "actual_target_up"] = 1.0 if actual_return > 0 else 0.0
        updated = True

    if updated:
        write_dataframe_csv(file_path, journal)
        _invalidate_file_read_cache(file_path)
    return journal


def journal_probability_calibration_summary(
    ticker: str,
    interval: str,
    market_regime: str | None = None,
    file_path: Path = FORECAST_JOURNAL_PATH,
) -> dict[str, object]:
    driver_columns = {
        "technical": "technical_confidence_component",
        "news": "news_probability_shift",
        "journal": "journal_probability_shift",
        "regime": "regime_confidence_component",
        "market_state": "market_state_probability_shift",
        "relative_strength": "relative_strength_probability_shift",
        "options_flow": "options_flow_probability_shift",
        "event_revision": "event_revision_probability_shift",
        "event": "event_probability_shift",
    }

    def _empty_summary() -> dict[str, object]:
        return {
            "resolved_count": 0,
            "empirical_hit_rate": None,
            "average_error": None,
            "average_probability_up": None,
            "market_regime": str(market_regime or ""),
            "calibration_scope": "insufficient",
            "regime_breakdown": [],
            "best_regime": None,
            "weakest_regime": None,
            "session_breakdown": [],
            "best_session": None,
            "weakest_session": None,
            "event_breakdown": [],
            "best_event_window": None,
            "weakest_event_window": None,
            "driver_attribution": [],
            "best_driver": None,
            "weakest_driver": None,
        }

    journal = read_forecast_journal(file_path)
    if journal.empty:
        return _empty_summary()

    mask = (
        journal.get("ticker", pd.Series(dtype=str)).astype(str).str.upper().eq(_normalize_symbol(ticker))
        & journal.get("interval", pd.Series(dtype=str)).astype(str).eq(str(interval))
    )
    rows = journal.loc[mask].copy()
    if rows.empty:
        return _empty_summary()

    rows["probability_up"] = pd.to_numeric(rows.get("probability_up"), errors="coerce")
    rows["actual_target_up"] = pd.to_numeric(rows.get("actual_target_up"), errors="coerce")
    rows["market_regime"] = rows.get("market_regime", pd.Series(dtype=str)).astype(str).str.strip().str.lower()
    rows["session_label"] = rows.get("forecast_at", pd.Series(dtype=object)).apply(_session_label_from_timestamp)
    rows["event_window_label"] = rows.apply(_event_window_label_from_row, axis=1)
    resolved = rows.dropna(subset=["probability_up", "actual_target_up"]).tail(60)
    if resolved.empty:
        return _empty_summary()

    regime_breakdown: list[dict[str, object]] = []
    grouped = resolved.loc[resolved["market_regime"].ne("")].groupby("market_regime", sort=False)
    for regime_name, regime_rows in grouped:
        if regime_rows.empty:
            continue
        hit_rate = float(regime_rows["actual_target_up"].mean())
        average_error = float((regime_rows["probability_up"] - regime_rows["actual_target_up"]).abs().mean())
        average_probability_up = float(regime_rows["probability_up"].mean())
        edge = hit_rate - average_probability_up
        regime_breakdown.append(
            {
                "market_regime": str(regime_name),
                "resolved_count": int(len(regime_rows)),
                "empirical_hit_rate": hit_rate,
                "average_error": average_error,
                "average_probability_up": average_probability_up,
                "edge": edge,
            }
        )
    regime_breakdown.sort(
        key=lambda item: (
            -float(item.get("edge") or 0.0),
            -int(item.get("resolved_count") or 0),
            str(item.get("market_regime") or ""),
        )
    )
    session_breakdown: list[dict[str, object]] = []
    session_grouped = resolved.loc[resolved["session_label"].ne("")].groupby("session_label", sort=False)
    for session_name, session_rows in session_grouped:
        if session_rows.empty:
            continue
        hit_rate = float(session_rows["actual_target_up"].mean())
        average_error = float((session_rows["probability_up"] - session_rows["actual_target_up"]).abs().mean())
        average_probability_up = float(session_rows["probability_up"].mean())
        edge = hit_rate - average_probability_up
        session_breakdown.append(
            {
                "session_label": str(session_name),
                "resolved_count": int(len(session_rows)),
                "empirical_hit_rate": hit_rate,
                "average_error": average_error,
                "average_probability_up": average_probability_up,
                "edge": edge,
            }
        )
    session_breakdown.sort(
        key=lambda item: (
            -float(item.get("edge") or 0.0),
            -int(item.get("resolved_count") or 0),
            str(item.get("session_label") or ""),
        )
    )
    event_breakdown: list[dict[str, object]] = []
    event_grouped = resolved.loc[resolved["event_window_label"].ne("")].groupby("event_window_label", sort=False)
    for event_name, event_rows in event_grouped:
        if event_rows.empty:
            continue
        hit_rate = float(event_rows["actual_target_up"].mean())
        average_error = float((event_rows["probability_up"] - event_rows["actual_target_up"]).abs().mean())
        average_probability_up = float(event_rows["probability_up"].mean())
        edge = hit_rate - average_probability_up
        event_breakdown.append(
            {
                "event_window_label": str(event_name),
                "resolved_count": int(len(event_rows)),
                "empirical_hit_rate": hit_rate,
                "average_error": average_error,
                "average_probability_up": average_probability_up,
                "edge": edge,
            }
        )
    event_breakdown.sort(
        key=lambda item: (
            -float(item.get("edge") or 0.0),
            -int(item.get("resolved_count") or 0),
            str(item.get("event_window_label") or ""),
        )
    )
    driver_attribution: list[dict[str, object]] = []
    actual_direction = pd.to_numeric(resolved.get("actual_return"), errors="coerce")
    for driver_name, column_name in driver_columns.items():
        contribution_values = pd.to_numeric(resolved.get(column_name), errors="coerce")
        driver_rows = pd.DataFrame(
            {
                "contribution": contribution_values,
                "actual_return": actual_direction,
            }
        ).dropna(subset=["contribution", "actual_return"])
        driver_rows = driver_rows.loc[driver_rows["contribution"] != 0]
        if driver_rows.empty:
            continue
        alignment = (
            np.sign(driver_rows["contribution"].to_numpy(dtype=float))
            == np.sign(driver_rows["actual_return"].to_numpy(dtype=float))
        )
        helpful_rate = float(alignment.mean())
        average_contribution = float(driver_rows["contribution"].mean())
        average_signed_impact = float(
            (np.sign(driver_rows["actual_return"].to_numpy(dtype=float)) * driver_rows["contribution"].to_numpy(dtype=float)).mean()
        )
        driver_attribution.append(
            {
                "driver": driver_name,
                "resolved_count": int(len(driver_rows)),
                "helpful_rate": helpful_rate,
                "average_contribution": average_contribution,
                "average_signed_impact": average_signed_impact,
            }
        )
    driver_attribution.sort(
        key=lambda item: (
            -float(item.get("average_signed_impact") or 0.0),
            -float(item.get("helpful_rate") or 0.0),
            -int(item.get("resolved_count") or 0),
            str(item.get("driver") or ""),
        )
    )

    requested_regime = str(market_regime or "").strip().lower()
    active_regime_summary = next(
        (item for item in regime_breakdown if str(item.get("market_regime") or "") == requested_regime),
        None,
    )
    calibration_scope = "global"
    if requested_regime:
        regime_resolved = resolved.loc[resolved["market_regime"].eq(requested_regime)].tail(40)
        if len(regime_resolved) >= 8:
            resolved = regime_resolved
            calibration_scope = "regime"

    empirical_hit_rate = float(resolved["actual_target_up"].mean())
    average_error = float((resolved["probability_up"] - resolved["actual_target_up"]).abs().mean())
    average_probability_up = float(resolved["probability_up"].mean())
    return {
        "resolved_count": int(len(resolved)),
        "empirical_hit_rate": empirical_hit_rate,
        "average_error": average_error,
        "average_probability_up": average_probability_up,
        "market_regime": requested_regime,
        "calibration_scope": calibration_scope,
        "active_regime": active_regime_summary,
        "regime_breakdown": regime_breakdown,
        "best_regime": regime_breakdown[0] if regime_breakdown else None,
        "weakest_regime": regime_breakdown[-1] if regime_breakdown else None,
        "session_breakdown": session_breakdown,
        "best_session": session_breakdown[0] if session_breakdown else None,
        "weakest_session": session_breakdown[-1] if session_breakdown else None,
        "event_breakdown": event_breakdown,
        "best_event_window": event_breakdown[0] if event_breakdown else None,
        "weakest_event_window": event_breakdown[-1] if event_breakdown else None,
        "driver_attribution": driver_attribution,
        "best_driver": driver_attribution[0] if driver_attribution else None,
        "weakest_driver": driver_attribution[-1] if driver_attribution else None,
    }


def replace_pending_order(
    order_id: str,
    updates: dict[str, object],
    file_path: Path = PENDING_ORDERS_PATH,
) -> dict[str, object] | None:
    pending_orders = read_pending_orders(file_path)
    if pending_orders.empty:
        return None

    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id or "order_id" not in pending_orders.columns:
        return None

    matches = pending_orders["order_id"].astype(str).str.strip() == normalized_order_id
    if not matches.any():
        return None

    order_index = pending_orders.index[matches][0]
    for key, value in updates.items():
        pending_orders.loc[order_index, key] = value
    pending_orders.loc[order_index, "updated_at"] = utc_now().isoformat()
    pending_orders.loc[order_index, "order_status"] = "WORKING"
    pending_orders.loc[order_index, "route_state"] = "accepted"
    pending_orders.loc[order_index, "book_state"] = "pending"
    pending_orders.loc[order_index, "status"] = "PENDING"

    write_dataframe_csv(file_path, pending_orders)
    _invalidate_file_read_cache(file_path)
    return pending_orders.loc[order_index].to_dict()


def update_pending_order(
    order_id: str,
    updates: dict[str, object],
    file_path: Path = PENDING_ORDERS_PATH,
) -> dict[str, object] | None:
    pending_orders = read_pending_orders(file_path)
    if pending_orders.empty:
        return None

    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id or "order_id" not in pending_orders.columns:
        return None

    matches = pending_orders["order_id"].astype(str).str.strip() == normalized_order_id
    if not matches.any():
        return None

    order_index = pending_orders.index[matches][0]
    for key, value in updates.items():
        pending_orders.loc[order_index, key] = value
    pending_orders.loc[order_index, "updated_at"] = utc_now().isoformat()

    write_dataframe_csv(file_path, pending_orders)
    _invalidate_file_read_cache(file_path)
    return pending_orders.loc[order_index].to_dict()


def cancel_pending_order(
    order_id: str,
    file_path: Path = PENDING_ORDERS_PATH,
) -> dict[str, object] | None:
    pending_orders = read_pending_orders(file_path)
    if pending_orders.empty:
        return None

    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id or "order_id" not in pending_orders.columns:
        return None

    matches = pending_orders["order_id"].astype(str).str.strip() == normalized_order_id
    if not matches.any():
        return None

    order_index = pending_orders.index[matches][0]
    canceled = pending_orders.loc[order_index].to_dict()
    remaining = pending_orders.drop(index=order_index).reset_index(drop=True)
    write_dataframe_csv(file_path, remaining)
    _invalidate_file_read_cache(file_path)
    return canceled


def fill_pending_order(
    order_id: str,
    fill_underlying_price: float,
    file_path_pending: Path = PENDING_ORDERS_PATH,
    file_path_open: Path = OPEN_TRADES_PATH,
) -> dict[str, object] | None:
    pending_orders = read_pending_orders(file_path_pending)
    if pending_orders.empty:
        return None

    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id or "order_id" not in pending_orders.columns:
        return None

    matches = pending_orders["order_id"].astype(str).str.strip() == normalized_order_id
    if not matches.any():
        return None

    order_index = pending_orders.index[matches][0]
    order_row = pending_orders.loc[order_index].to_dict()
    opened_at = utc_now().isoformat()
    rebuilt_exit_plan = build_exit_plan(
        verdict=str(order_row.get("verdict") or ""),
        entry_reference_price=float(fill_underlying_price),
        invalidation_price=_safe_float(order_row.get("invalidation_price")),
        time_stop_bars=_safe_int(
            _coerce_dict(order_row.get("exit_plan")).get("time_stop_bars"),
            _safe_int(order_row.get("horizon_bars"), _safe_int(order_row.get("horizon"), 0)),
        ),
        entry_reference_source="filled_open",
    )
    filled_record = {
        **order_row,
        "opened_at": opened_at,
        "updated_at": opened_at,
        "live_price_at_open": float(fill_underlying_price),
        "exit_plan": rebuilt_exit_plan,
        "active_stop_price": _safe_float(rebuilt_exit_plan.get("initial_stop_price")),
        "tp1_taken_at": "",
        "tp2_taken_at": "",
        "bars_held": 0,
        "last_exit_reason": "",
        "current_exit_stage": "INITIAL",
        "next_target_price": _safe_float(rebuilt_exit_plan.get("tp1_price")),
        "filled_contracts": float(pd.to_numeric(order_row.get("suggested_contracts", 0), errors="coerce") or 0.0),
        "remaining_contracts": 0.0,
        "order_status": "FILLED",
        "route_state": "filled",
        "book_state": "open",
        "status": "OPEN",
    }
    append_open_trade(filled_record, file_path=file_path_open)
    remaining = pending_orders.drop(index=order_index).reset_index(drop=True)
    write_dataframe_csv(file_path_pending, remaining)
    _invalidate_file_read_cache(file_path_pending)
    return filled_record


def update_open_trade(
    updates: dict[str, object],
    *,
    trade_id: str | None = None,
    order_id: str | None = None,
    file_path: Path = OPEN_TRADES_PATH,
) -> dict[str, object] | None:
    open_trades = read_open_trades(file_path)
    if open_trades.empty:
        return None

    match_index = None
    normalized_trade_id = str(trade_id or "").strip()
    normalized_order_id = str(order_id or "").strip()

    if normalized_trade_id and "trade_id" in open_trades.columns:
        matches = open_trades["trade_id"].astype(str).str.strip() == normalized_trade_id
        if matches.any():
            match_index = open_trades.index[matches][0]

    if match_index is None and normalized_order_id and "order_id" in open_trades.columns:
        matches = open_trades["order_id"].astype(str).str.strip() == normalized_order_id
        if matches.any():
            match_index = open_trades.index[matches][0]

    if match_index is None:
        return None

    for key, value in updates.items():
        open_trades.loc[match_index, key] = value
    open_trades.loc[match_index, "updated_at"] = utc_now().isoformat()

    write_dataframe_csv(file_path, open_trades)
    _invalidate_file_read_cache(file_path)
    return open_trades.loc[match_index].to_dict()


def close_trade_by_index(
    trade_index: int,
    close_underlying_price: float,
    close_contract_mid: float,
    close_fraction: float = 1.0,
    close_updates: dict[str, object] | None = None,
    file_path_open: Path = OPEN_TRADES_PATH,
    file_path_closed: Path = CLOSED_TRADES_PATH,
) -> dict[str, object] | None:
    open_trades = read_open_trades(file_path_open)
    if open_trades.empty or trade_index < 0 or trade_index >= len(open_trades):
        return None

    trade = open_trades.iloc[trade_index].copy()
    instrument_type = str(trade.get("instrument_type", "listed_option") or "listed_option").strip().lower()
    broker_side = str(trade.get("broker_side") or "BUY").strip().upper() or "BUY"

    contracts = float(pd.to_numeric(trade.get("suggested_contracts", 0), errors="coerce"))
    if contracts <= 0:
        return None
    entry_contract_mid = float(pd.to_numeric(trade.get("contract_mid_at_open", float("nan")), errors="coerce"))
    normalized_close_contract_mid = float(close_contract_mid)
    if instrument_type == "equity":
        normalized_close_contract_mid = float(close_underlying_price) / 100.0

    fraction = min(max(float(close_fraction), 0.0), 1.0)
    if contracts <= 1:
        contracts_to_close = min(
            contracts,
            max(0.001, float(int((contracts * fraction) * 1000)) / 1000.0),
        )
    else:
        contracts_to_close = max(1.0, float(int(round(contracts * fraction))))
        contracts_to_close = min(contracts_to_close, contracts)
    remaining_contracts = contracts - contracts_to_close

    pnl_per_contract = float("nan")
    realized_pnl = float("nan")

    if not math.isnan(entry_contract_mid) and contracts > 0:
        if instrument_type == "equity" and broker_side == "SELL":
            pnl_per_contract = (entry_contract_mid - normalized_close_contract_mid) * 100.0
        else:
            pnl_per_contract = (normalized_close_contract_mid - entry_contract_mid) * 100.0
        realized_pnl = pnl_per_contract * contracts_to_close

    closed_record = {
        **trade.to_dict(),
        "closed_at": utc_now().isoformat(),
        "live_price_at_close": float(close_underlying_price),
        "contract_mid_at_close": normalized_close_contract_mid,
        "closed_contracts": float(contracts_to_close),
        "remaining_contracts_after_close": float(remaining_contracts),
        "close_fraction": float(fraction),
        "pnl_per_contract": pnl_per_contract,
        "realized_pnl": realized_pnl,
        "status": "CLOSED" if remaining_contracts == 0 else "PARTIAL",
    }
    if close_updates:
        closed_record.update(dict(close_updates))

    if remaining_contracts == 0:
        remaining = open_trades.drop(index=trade_index).reset_index(drop=True)
    else:
        updated_trade = trade.copy()
        updated_trade["suggested_contracts"] = float(remaining_contracts)
        updated_trade["position_cost"] = float(
            pd.to_numeric(trade.get("position_cost", 0.0), errors="coerce") * (remaining_contracts / contracts)
        )
        updated_trade["max_risk_dollars"] = float(
            pd.to_numeric(trade.get("max_risk_dollars", 0.0), errors="coerce") * (remaining_contracts / contracts)
        )
        remaining = open_trades.copy()
        remaining.iloc[trade_index] = updated_trade

    if file_path_closed.exists():
        try:
            existing_closed = pd.read_csv(file_path_closed)
            closed_df = pd.concat([existing_closed, pd.DataFrame([closed_record])], ignore_index=True)
        except Exception:
            closed_df = pd.DataFrame([closed_record])
    else:
        closed_df = pd.DataFrame([closed_record])

    write_dataframe_csv(file_path_open, remaining)
    write_dataframe_csv(file_path_closed, closed_df)
    _invalidate_file_read_cache(file_path_open)
    _invalidate_file_read_cache(file_path_closed)
    return closed_record


def trade_summary(closed_trades: pd.DataFrame) -> dict[str, float]:
    if closed_trades.empty or "realized_pnl" not in closed_trades.columns:
        return {
            "closed_trades": 0.0,
            "wins": 0.0,
            "losses": 0.0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "average_pnl": 0.0,
        }

    pnl = pd.to_numeric(closed_trades["realized_pnl"], errors="coerce").fillna(0.0)
    closed_count = float(len(pnl))
    wins = float((pnl > 0).sum())
    losses = float((pnl < 0).sum())
    win_rate = float(wins / closed_count) if closed_count > 0 else 0.0
    total_pnl = float(pnl.sum())
    average_pnl = float(pnl.mean()) if closed_count > 0 else 0.0

    return {
        "closed_trades": closed_count,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "average_pnl": average_pnl,
    }


def build_trade_replay(journal_df: pd.DataFrame) -> pd.DataFrame:
    if journal_df.empty:
        return pd.DataFrame()

    replay = journal_df.copy()
    replay["event_group"] = replay["ticker"].astype(str) + " | " + replay["interval"].astype(str)
    return replay.sort_values(by="timestamp", ascending=False).reset_index(drop=True)


def monitor_open_trades(file_path: Path = OPEN_TRADES_PATH) -> pd.DataFrame:
    open_trades = read_open_trades(file_path)
    if open_trades.empty:
        return pd.DataFrame()

    unique_tickers = [str(value) for value in open_trades.get("ticker", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()]
    unique_contract_symbols = [str(value) for value in open_trades.get("contract_symbol", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()]
    underlying_prices = batch_get_live_prices(unique_tickers)
    contract_mids = batch_get_contract_mids(unique_contract_symbols)
    monitored_rows: list[dict[str, object]] = []

    action_priority = {
        "STOP HIT": 0,
        "EXIT FULLY NOW": 1,
        "SELL MORE NOW": 2,
        "SELL 50% NOW": 3,
        "TIME STOP": 4,
        "DATA ISSUE": 5,
        "HOLD": 6,
    }

    for _, row in open_trades.iterrows():
        ticker = str(row.get("ticker", ""))
        contract_symbol = str(row.get("contract_symbol", ""))
        instrument_type = str(row.get("instrument_type", "listed_option") or "listed_option").strip().lower()
        entry_contract_mid = float(pd.to_numeric(row.get("contract_mid_at_open", float("nan")), errors="coerce"))
        contracts = float(pd.to_numeric(row.get("suggested_contracts", 0), errors="coerce"))

        current_underlying = float(underlying_prices.get(_normalize_symbol(ticker), float("nan"))) if ticker else float("nan")
        if instrument_type == "equity" and not math.isnan(current_underlying):
            current_contract_mid = float(current_underlying / 100.0)
        else:
            current_contract_mid = float(contract_mids.get(_normalize_symbol(contract_symbol), float("nan"))) if contract_symbol else float("nan")
            if math.isnan(current_contract_mid) and contract_symbol:
                current_contract_mid = get_contract_mid_from_symbol(contract_symbol)
        option_return_pct = float("nan")
        unrealized_pnl = float("nan")

        if not math.isnan(entry_contract_mid) and entry_contract_mid > 0 and not math.isnan(current_contract_mid):
            option_return_pct = (current_contract_mid - entry_contract_mid) / entry_contract_mid
            unrealized_pnl = (current_contract_mid - entry_contract_mid) * 100.0 * contracts

        exit_evaluation = evaluate_open_trade_exit(
            row.to_dict(),
            current_underlying_price=current_underlying,
            current_contract_mid=current_contract_mid,
        )
        action = str(exit_evaluation["monitor_action"])
        monitored_rows.append(
            {
                **row.to_dict(),
                "current_underlying": current_underlying,
                "current_underlying_price": current_underlying,
                "current_contract_mid": current_contract_mid,
                "option_return_pct": option_return_pct,
                "unrealized_pnl": unrealized_pnl,
                "monitor_action": action,
                "exit_reason": exit_evaluation["exit_reason"],
                "current_exit_stage": exit_evaluation["current_exit_stage"],
                "active_stop_price": exit_evaluation["active_stop_price"],
                "next_target_price": exit_evaluation["next_target_price"],
                "entry_reference_price": exit_evaluation["entry_reference_price"],
                "risk_unit": exit_evaluation["risk_unit"],
                "tp1_price": exit_evaluation["tp1_price"],
                "tp2_price": exit_evaluation["tp2_price"],
                "stop_after_tp1": exit_evaluation["stop_after_tp1"],
                "stop_after_tp2": exit_evaluation["stop_after_tp2"],
                "time_stop_bars": exit_evaluation["time_stop_bars"],
                "bars_held": exit_evaluation["bars_held"],
                "trade_age_days": exit_evaluation["trade_age_days"],
                "tp1_taken": exit_evaluation["tp1_taken"],
                "tp2_taken": exit_evaluation["tp2_taken"],
                "tp1_hit": exit_evaluation["tp1_hit"],
                "tp2_hit": exit_evaluation["tp2_hit"],
                "stop_hit": exit_evaluation["stop_hit"],
                "time_stop_hit": exit_evaluation["time_stop_hit"],
                "data_issue": exit_evaluation["data_issue"],
                "action_rank": int(action_priority.get(action, 5)),
            }
        )

    monitored = pd.DataFrame(monitored_rows)
    if monitored.empty:
        return monitored

    if "unrealized_pnl" in monitored.columns:
        monitored["_abs_unrealized_pnl"] = pd.to_numeric(monitored["unrealized_pnl"], errors="coerce").abs().fillna(0.0)
    else:
        monitored["_abs_unrealized_pnl"] = 0.0

    monitored = monitored.sort_values(["action_rank", "_abs_unrealized_pnl"], ascending=[True, False], na_position="last").reset_index(drop=True)
    return monitored.drop(columns=["_abs_unrealized_pnl"], errors="ignore")


def portfolio_summary(
    open_trades_monitored: pd.DataFrame,
    closed_trades: pd.DataFrame,
) -> dict[str, float]:
    open_risk = 0.0
    open_cost = 0.0
    unrealized = 0.0
    realized = 0.0
    active_count = 0.0

    if not open_trades_monitored.empty:
        if "max_risk_dollars" in open_trades_monitored.columns:
            open_risk = float(pd.to_numeric(open_trades_monitored["max_risk_dollars"], errors="coerce").fillna(0.0).sum())
        if "position_cost" in open_trades_monitored.columns:
            open_cost = float(pd.to_numeric(open_trades_monitored["position_cost"], errors="coerce").fillna(0.0).sum())
        if "unrealized_pnl" in open_trades_monitored.columns:
            unrealized = float(pd.to_numeric(open_trades_monitored["unrealized_pnl"], errors="coerce").fillna(0.0).sum())
        active_count = float(len(open_trades_monitored))

    if not closed_trades.empty and "realized_pnl" in closed_trades.columns:
        realized = float(pd.to_numeric(closed_trades["realized_pnl"], errors="coerce").fillna(0.0).sum())

    return {
        "open_risk": open_risk,
        "open_cost": open_cost,
        "unrealized_pnl": unrealized,
        "realized_pnl": realized,
        "active_trade_count": active_count,
    }


def performance_analytics(closed_trades: pd.DataFrame) -> dict[str, float]:
    if closed_trades.empty or "realized_pnl" not in closed_trades.columns:
        return {
            "win_rate": 0.0,
            "average_winner": 0.0,
            "average_loser": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
        }

    pnl = pd.to_numeric(closed_trades["realized_pnl"], errors="coerce").fillna(0.0)
    winners = pnl[pnl > 0]
    losers = pnl[pnl < 0]

    avg_winner = float(winners.mean()) if not winners.empty else 0.0
    avg_loser = float(losers.mean()) if not losers.empty else 0.0
    gross_profit = float(winners.sum()) if not winners.empty else 0.0
    gross_loss_abs = float(abs(losers.sum())) if not losers.empty else 0.0
    profit_factor = float(gross_profit / gross_loss_abs) if gross_loss_abs > 0 else 0.0
    expectancy = float(pnl.mean()) if not pnl.empty else 0.0
    win_rate = float((pnl > 0).sum() / len(pnl)) if len(pnl) > 0 else 0.0
    best_trade = float(pnl.max()) if not pnl.empty else 0.0
    worst_trade = float(pnl.min()) if not pnl.empty else 0.0

    return {
        "win_rate": win_rate,
        "average_winner": avg_winner,
        "average_loser": avg_loser,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
    }


def equity_curve(closed_trades: pd.DataFrame) -> pd.DataFrame:
    if closed_trades.empty or "realized_pnl" not in closed_trades.columns:
        return pd.DataFrame(columns=["closed_at", "realized_pnl", "equity_curve"])

    equity = closed_trades.copy()
    if "closed_at" in equity.columns:
        equity["closed_at"] = pd.to_datetime(equity["closed_at"], errors="coerce")
        equity = equity.sort_values("closed_at")
    equity["realized_pnl"] = pd.to_numeric(equity["realized_pnl"], errors="coerce").fillna(0.0)
    equity["equity_curve"] = equity["realized_pnl"].cumsum()
    return equity[["closed_at", "realized_pnl", "equity_curve"]].reset_index(drop=True)


def performance_breakdown(closed_trades: pd.DataFrame, group_by: str) -> pd.DataFrame:
    if closed_trades.empty or group_by not in closed_trades.columns or "realized_pnl" not in closed_trades.columns:
        return pd.DataFrame()

    df = closed_trades.copy()
    df["realized_pnl"] = pd.to_numeric(df["realized_pnl"], errors="coerce").fillna(0.0)

    grouped = (
        df.groupby(group_by, dropna=False)
        .agg(
            trades=("realized_pnl", "count"),
            total_pnl=("realized_pnl", "sum"),
            average_pnl=("realized_pnl", "mean"),
            wins=("realized_pnl", lambda s: float((s > 0).sum())),
            losses=("realized_pnl", lambda s: float((s < 0).sum())),
        )
        .reset_index()
    )
    grouped["win_rate"] = np.where(grouped["trades"] > 0, grouped["wins"] / grouped["trades"], 0.0)
    return grouped.sort_values("total_pnl", ascending=False).reset_index(drop=True)


def score_bucket_breakdown(closed_trades: pd.DataFrame) -> pd.DataFrame:
    if closed_trades.empty or "setup_score" not in closed_trades.columns or "realized_pnl" not in closed_trades.columns:
        return pd.DataFrame()

    df = closed_trades.copy()
    df["setup_score"] = pd.to_numeric(df["setup_score"], errors="coerce")
    df["realized_pnl"] = pd.to_numeric(df["realized_pnl"], errors="coerce").fillna(0.0)

    bins = [0, 55, 72, 85, 101]
    labels = ["Avoid", "B bucket", "A bucket", "A+ bucket"]
    df["score_bucket"] = pd.cut(df["setup_score"], bins=bins, labels=labels, right=False)

    return performance_breakdown(df, "score_bucket")


def best_conditions_summary(closed_trades: pd.DataFrame) -> str:
    if closed_trades.empty or "realized_pnl" not in closed_trades.columns:
        return "No closed-trade data yet."

    candidates = []
    for group_by in ["conviction_label", "alignment_label", "interval", "setup_grade"]:
        breakdown = performance_breakdown(closed_trades, group_by)
        if breakdown.empty:
            continue
        best_row = breakdown.sort_values(["average_pnl", "win_rate"], ascending=[False, False]).head(1)
        if best_row.empty:
            continue
        row = best_row.iloc[0]
        candidates.append(
            (
                float(row["average_pnl"]),
                f"Best current edge: {group_by} = {row[group_by]}, "
                f"average PnL {row['average_pnl']:.2f}, win rate {row['win_rate']:.2%}.",
            )
        )

    if not candidates:
        return "Not enough trade history to identify best conditions."

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def open_risk_dashboard(open_trades_monitored: pd.DataFrame, account_size: float) -> dict[str, float | str]:
    if open_trades_monitored.empty:
        return {
            "open_risk": 0.0,
            "open_cost": 0.0,
            "risk_pct_of_account": 0.0,
            "cost_pct_of_account": 0.0,
            "status": "OK",
        }

    open_risk = float(pd.to_numeric(open_trades_monitored.get("max_risk_dollars", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    open_cost = float(pd.to_numeric(open_trades_monitored.get("position_cost", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())

    risk_pct = (open_risk / account_size) if account_size > 0 else 0.0
    cost_pct = (open_cost / account_size) if account_size > 0 else 0.0

    if risk_pct > 0.10 or cost_pct > 0.70:
        status = "OVEREXPOSED"
    elif risk_pct > 0.06 or cost_pct > 0.50:
        status = "ELEVATED"
    else:
        status = "OK"

    return {
        "open_risk": open_risk,
        "open_cost": open_cost,
        "risk_pct_of_account": risk_pct,
        "cost_pct_of_account": cost_pct,
        "status": status,
    }


def generate_order_ticket(report: AnalysisReport, position: PositionSizing, live_price: float) -> pd.DataFrame:
    option_plan = report["option_plan"]
    exit_plan = _build_report_exit_plan(
        report,
        entry_reference_price=float(live_price),
        entry_reference_source="ticket_preview",
    )
    contract = option_plan["recommended_contract"]
    forecast = dict(report.get("forecast") or {})
    journal_calibration = dict(forecast.get("journal_calibration") or {})

    contract_symbol = ""
    expiration = ""
    strike = float("nan")
    spread_pct = float("nan")
    volume = 0
    open_interest = 0
    quote_timestamp = ""

    if contract is not None:
        contract_symbol = contract["contract_symbol"]
        expiration = contract["expiration"]
        strike = float(contract["strike"])
        spread_pct = float(contract["spread_pct"])
        volume = int(contract["volume"])
        open_interest = int(contract["open_interest"])
        quote_timestamp = str(contract.get("quote_timestamp") or "")

    row = {
        "ticker": report["ticker"],
        "interval": report["interval"],
        "decision": report["trade_decision"],
        "verdict": report["verdict"],
        "event_risk": _safe_bool(report.get("event_risk", False)),
        "event_label": report.get("event_label", ""),
        "event_reason": report.get("event_reason", ""),
        "next_event_name": report.get("next_event_name", ""),
        "next_event_date": report.get("next_event_date", ""),
        "option_side": option_plan["option_side"],
        "contract_symbol": contract_symbol,
        "expiration": expiration,
        "strike": strike,
        "live_price": float(live_price),
        "entry_low": float(option_plan["entry_low_price"]),
        "entry_high": float(option_plan["entry_high_price"]),
        "target_price": float(option_plan["expected_underlying_target"]),
        "invalidation_price": float(option_plan["invalidation_price"]),
        "tp1_pct": float(option_plan["take_profit_1"]),
        "tp2_pct": float(option_plan["take_profit_2"]),
        "exit_plan": exit_plan,
        "active_stop_price": float(_safe_float(exit_plan.get("initial_stop_price"))),
        "next_target_price": float(_safe_float(exit_plan.get("tp1_price"))),
        "current_exit_stage": "INITIAL",
        "contract_mid": float(position["contract_mid"]),
        "estimated_cost_per_contract": float(position["estimated_cost_per_contract"]),
        "suggested_contracts": int(position["suggested_contracts"]),
        "total_position_cost": float(position["total_position_cost"]),
        "max_risk_dollars": float(position["max_risk_dollars"]),
        "setup_score": float(report["setup_score"]),
        "alpha_score": float(_safe_float(report.get("alpha_score"))),
        "execution_score": float(_safe_float(report.get("execution_score"))),
        "portfolio_score": float(_safe_float(report.get("portfolio_score"))),
        "edge_to_cost_ratio": float(_safe_float(report.get("edge_to_cost_ratio"))),
        "expected_edge_bps": float(_safe_float(report.get("expected_edge_bps"))),
        "estimated_cost_bps": float(_safe_float(report.get("estimated_cost_bps"))),
        "spread_bps": float(_safe_float(report.get("spread_bps"))),
        "average_dollar_volume": float(_safe_float(report.get("average_dollar_volume"))),
        "average_1m_dollar_volume": float(_safe_float(report.get("average_1m_dollar_volume"))),
        "quote_age_seconds": float(_safe_float(report.get("quote_age_seconds"))),
        "proxy_correlation_bucket": str(report.get("proxy_correlation_bucket") or _proxy_correlation_bucket_for_ticker(report["ticker"])),
        "portfolio_rank": _safe_int(report.get("portfolio_rank"), 0) or None,
        "auto_entry_eligible": _safe_bool(report.get("auto_entry_eligible", False)),
        "setup_grade": report["setup_grade"],
        "conviction": report["conviction_label"],
        "alignment": report["alignment_label"],
        "spread_pct": spread_pct,
        "volume": volume,
        "open_interest": open_interest,
        "quote_timestamp": quote_timestamp,
        "forecast_confidence": float(forecast.get("confidence_score", float("nan"))),
        "regime_strength_score": float(forecast.get("regime_strength_score", float("nan"))),
        "resolved_count": int(journal_calibration.get("resolved_count") or 0),
        "empirical_hit_rate": float(journal_calibration.get("empirical_hit_rate", float("nan"))),
        "average_error": float(journal_calibration.get("average_error", float("nan"))),
        "average_probability_up": float(journal_calibration.get("average_probability_up", float("nan"))),
        "calibration_scope": str(journal_calibration.get("calibration_scope") or ""),
        "best_regime": str((journal_calibration.get("best_regime") or {}).get("market_regime") or ""),
        "best_regime_hit_rate": float((journal_calibration.get("best_regime") or {}).get("empirical_hit_rate", float("nan"))),
        "best_regime_edge": float((journal_calibration.get("best_regime") or {}).get("edge", float("nan"))),
        "best_regime_resolved_count": int((journal_calibration.get("best_regime") or {}).get("resolved_count") or 0),
        "weakest_regime": str((journal_calibration.get("weakest_regime") or {}).get("market_regime") or ""),
        "weakest_regime_hit_rate": float((journal_calibration.get("weakest_regime") or {}).get("empirical_hit_rate", float("nan"))),
        "weakest_regime_edge": float((journal_calibration.get("weakest_regime") or {}).get("edge", float("nan"))),
        "weakest_regime_resolved_count": int((journal_calibration.get("weakest_regime") or {}).get("resolved_count") or 0),
        "best_session": str((journal_calibration.get("best_session") or {}).get("session_label") or ""),
        "best_session_hit_rate": float((journal_calibration.get("best_session") or {}).get("empirical_hit_rate", float("nan"))),
        "best_session_edge": float((journal_calibration.get("best_session") or {}).get("edge", float("nan"))),
        "best_session_resolved_count": int((journal_calibration.get("best_session") or {}).get("resolved_count") or 0),
        "weakest_session": str((journal_calibration.get("weakest_session") or {}).get("session_label") or ""),
        "weakest_session_hit_rate": float((journal_calibration.get("weakest_session") or {}).get("empirical_hit_rate", float("nan"))),
        "weakest_session_edge": float((journal_calibration.get("weakest_session") or {}).get("edge", float("nan"))),
        "weakest_session_resolved_count": int((journal_calibration.get("weakest_session") or {}).get("resolved_count") or 0),
        "best_event_window": str((journal_calibration.get("best_event_window") or {}).get("event_window_label") or ""),
        "best_event_window_hit_rate": float((journal_calibration.get("best_event_window") or {}).get("empirical_hit_rate", float("nan"))),
        "best_event_window_edge": float((journal_calibration.get("best_event_window") or {}).get("edge", float("nan"))),
        "best_event_window_resolved_count": int((journal_calibration.get("best_event_window") or {}).get("resolved_count") or 0),
        "weakest_event_window": str((journal_calibration.get("weakest_event_window") or {}).get("event_window_label") or ""),
        "weakest_event_window_hit_rate": float((journal_calibration.get("weakest_event_window") or {}).get("empirical_hit_rate", float("nan"))),
        "weakest_event_window_edge": float((journal_calibration.get("weakest_event_window") or {}).get("edge", float("nan"))),
        "weakest_event_window_resolved_count": int((journal_calibration.get("weakest_event_window") or {}).get("resolved_count") or 0),
        "best_driver": str((journal_calibration.get("best_driver") or {}).get("driver") or ""),
        "best_driver_helpful_rate": float((journal_calibration.get("best_driver") or {}).get("helpful_rate", float("nan"))),
        "best_driver_average_signed_impact": float((journal_calibration.get("best_driver") or {}).get("average_signed_impact", float("nan"))),
        "best_driver_resolved_count": int((journal_calibration.get("best_driver") or {}).get("resolved_count") or 0),
        "weakest_driver": str((journal_calibration.get("weakest_driver") or {}).get("driver") or ""),
        "weakest_driver_helpful_rate": float((journal_calibration.get("weakest_driver") or {}).get("helpful_rate", float("nan"))),
        "weakest_driver_average_signed_impact": float((journal_calibration.get("weakest_driver") or {}).get("average_signed_impact", float("nan"))),
        "weakest_driver_resolved_count": int((journal_calibration.get("weakest_driver") or {}).get("resolved_count") or 0),
    }
    return pd.DataFrame([row])


def build_entry_checklist(report: AnalysisReport, live_price: float) -> pd.DataFrame:
    option_plan = report["option_plan"]
    contract = option_plan["recommended_contract"]

    entry_low = float(option_plan["entry_low_price"])
    entry_high = float(option_plan["entry_high_price"])
    in_entry_zone = (
        not math.isnan(entry_low)
        and not math.isnan(entry_high)
        and entry_low <= live_price <= entry_high
    )

    liquidity_ok = False
    spread_ok = False
    if contract is not None:
        spread_pct = float(contract["spread_pct"])
        spread_ok = (not math.isnan(spread_pct)) and spread_pct <= 0.15
        liquidity_ok = int(contract["volume"]) >= 25 and int(contract["open_interest"]) >= 100

    checklist_rows = [
        {"check": "No event risk blocker", "status": not bool(report.get("event_risk", False))},
        {"check": "Trade decision valid", "status": report["trade_decision"] == "VALID TRADE"},
        {"check": "In entry zone", "status": in_entry_zone},
        {"check": "Contract found", "status": contract is not None},
        {"check": "Spread acceptable", "status": spread_ok},
        {"check": "Liquidity acceptable", "status": liquidity_ok},
        {"check": "Setup score >= 55", "status": float(report["setup_score"]) >= 55.0},
        {"check": "Conviction not weak", "status": report["conviction_label"] not in {"LOW CONVICTION", "NO TRADE"}},
    ]
    return pd.DataFrame(checklist_rows)


def get_execution_decision(report: AnalysisReport, live_price: float) -> str:
    if _safe_bool(report.get("event_risk", False)):
        return "WAIT UNTIL AFTER EVENT"
    if report["trade_decision"] == "REJECT":
        return "REJECT"
    if report["trade_decision"] == "PASS":
        return "PASS"

    trade_status = evaluate_trade_status(report, live_price)
    if trade_status == "ENTER NOW":
        return "BUY NOW"
    if trade_status == "WAIT FOR ENTRY":
        return "WAIT FOR ENTRY"
    if trade_status == "TAKE PROFIT":
        return "TAKE PROFIT"
    if trade_status == "CUT LOSS":
        return "CUT LOSS"
    return "NO TRADE"


def evaluate_trade_alerts(report: AnalysisReport, live_price: float) -> List[str]:
    alerts: List[str] = []
    option_plan = report["option_plan"]
    verdict = report["verdict"]

    if _safe_bool(report.get("event_risk", False)):
        alerts.append("EVENT RISK")

    entry_low = _safe_float(option_plan.get("entry_low_price"))
    entry_high = _safe_float(option_plan.get("entry_high_price"))
    target = _safe_float(option_plan.get("expected_underlying_target"))
    invalidation = _safe_float(option_plan.get("invalidation_price"))

    if not math.isnan(entry_low) and not math.isnan(entry_high):
        if entry_low <= live_price <= entry_high:
            alerts.append("ENTRY ZONE")

    if float(report["setup_score"]) >= 70:
        alerts.append("HIGH SCORE SETUP")

    if report["setup_grade"] in {"A+ setup", "A setup"}:
        alerts.append("A-GRADE SETUP")

    if verdict == "BULLISH":
        if not math.isnan(target) and live_price >= target:
            alerts.append("TARGET REACHED")
        if not math.isnan(invalidation) and live_price <= invalidation:
            alerts.append("STOP HIT")
    elif verdict == "BEARISH":
        if not math.isnan(target) and live_price <= target:
            alerts.append("TARGET REACHED")
        if not math.isnan(invalidation) and live_price >= invalidation:
            alerts.append("STOP HIT")

    if report["conviction_label"].startswith("HIGH CONVICTION"):
        alerts.append("HIGH CONVICTION")

    return alerts


def parse_scan_tickers(text: str) -> List[str]:
    if text.lower().strip() == "default":
        return DEFAULT_SCAN_TICKERS
    return [_normalize_symbol(value) for value in text.split(",") if value.strip()]


def _prepare_closed_trades_for_optimizer(closed_trades: pd.DataFrame) -> pd.DataFrame:
    if closed_trades.empty or "realized_pnl" not in closed_trades.columns:
        return pd.DataFrame()

    df = closed_trades.copy()
    df["realized_pnl"] = pd.to_numeric(df["realized_pnl"], errors="coerce").fillna(0.0)

    if "setup_score" in df.columns:
        df["setup_score"] = pd.to_numeric(df["setup_score"], errors="coerce")
        bins = [0, 55, 72, 85, 101]
        labels = ["Avoid", "B bucket", "A bucket", "A+ bucket"]
        df["score_bucket"] = pd.cut(df["setup_score"], bins=bins, labels=labels, right=False)
    else:
        df["score_bucket"] = "Unknown"

    df["is_win"] = (df["realized_pnl"] > 0).astype(int)
    df["gross_profit"] = df["realized_pnl"].clip(lower=0.0)
    df["gross_loss_abs"] = (-df["realized_pnl"].clip(upper=0.0)).abs()
    return df


def optimizer_group_summary(
    closed_trades: pd.DataFrame,
    group_by: str,
    min_trades: int = 3,
) -> pd.DataFrame:
    df = _prepare_closed_trades_for_optimizer(closed_trades)
    if df.empty or group_by not in df.columns:
        return pd.DataFrame()

    grouped = (
        df.groupby(group_by, dropna=False)
        .agg(
            trades=("realized_pnl", "count"),
            wins=("is_win", "sum"),
            total_pnl=("realized_pnl", "sum"),
            average_pnl=("realized_pnl", "mean"),
            gross_profit=("gross_profit", "sum"),
            gross_loss_abs=("gross_loss_abs", "sum"),
        )
        .reset_index()
    )

    grouped["win_rate"] = np.where(grouped["trades"] > 0, grouped["wins"] / grouped["trades"], 0.0)
    grouped["profit_factor"] = np.where(
        grouped["gross_loss_abs"] > 0,
        grouped["gross_profit"] / grouped["gross_loss_abs"],
        np.nan,
    )
    grouped["expectancy"] = grouped["average_pnl"]
    grouped = grouped[grouped["trades"] >= min_trades].reset_index(drop=True)

    if grouped.empty:
        return grouped

    grouped["quality_score"] = (
        grouped["average_pnl"] * 0.50
        + grouped["win_rate"] * 100.0 * 0.30
        + grouped["total_pnl"] * 0.10
        + grouped["profit_factor"].fillna(0.0) * 10.0 * 0.10
    )
    return grouped.sort_values(["quality_score", "average_pnl", "win_rate"], ascending=[False, False, False]).reset_index(drop=True)


def optimizer_pattern_summary(
    closed_trades: pd.DataFrame,
    group_columns: List[str],
    min_trades: int = 3,
) -> pd.DataFrame:
    df = _prepare_closed_trades_for_optimizer(closed_trades)
    valid_columns = [column for column in group_columns if column in df.columns]
    if df.empty or not valid_columns:
        return pd.DataFrame()

    grouped = (
        df.groupby(valid_columns, dropna=False)
        .agg(
            trades=("realized_pnl", "count"),
            wins=("is_win", "sum"),
            total_pnl=("realized_pnl", "sum"),
            average_pnl=("realized_pnl", "mean"),
            gross_profit=("gross_profit", "sum"),
            gross_loss_abs=("gross_loss_abs", "sum"),
        )
        .reset_index()
    )

    grouped["win_rate"] = np.where(grouped["trades"] > 0, grouped["wins"] / grouped["trades"], 0.0)
    grouped["profit_factor"] = np.where(
        grouped["gross_loss_abs"] > 0,
        grouped["gross_profit"] / grouped["gross_loss_abs"],
        np.nan,
    )
    grouped["expectancy"] = grouped["average_pnl"]
    grouped = grouped[grouped["trades"] >= min_trades].reset_index(drop=True)

    if grouped.empty:
        return grouped

    grouped["pattern"] = grouped[valid_columns].astype(str).agg(" | ".join, axis=1)
    grouped["quality_score"] = (
        grouped["average_pnl"] * 0.55
        + grouped["win_rate"] * 100.0 * 0.25
        + grouped["total_pnl"] * 0.10
        + grouped["profit_factor"].fillna(0.0) * 10.0 * 0.10
    )
    return grouped.sort_values(["quality_score", "average_pnl", "win_rate"], ascending=[False, False, False]).reset_index(drop=True)


def optimizer_top_patterns(closed_trades: pd.DataFrame, min_trades: int = 3, limit: int = 5) -> pd.DataFrame:
    patterns = optimizer_pattern_summary(
        closed_trades,
        ["interval", "setup_grade", "alignment_label", "conviction_label"],
        min_trades=min_trades,
    )
    if patterns.empty:
        return patterns
    return patterns.head(limit).reset_index(drop=True)


def optimizer_bottom_patterns(closed_trades: pd.DataFrame, min_trades: int = 3, limit: int = 5) -> pd.DataFrame:
    patterns = optimizer_pattern_summary(
        closed_trades,
        ["interval", "setup_grade", "alignment_label", "conviction_label"],
        min_trades=min_trades,
    )
    if patterns.empty:
        return patterns
    return patterns.sort_values(["quality_score", "average_pnl", "win_rate"], ascending=[True, True, True]).head(limit).reset_index(drop=True)


def optimizer_low_sample_report(closed_trades: pd.DataFrame, min_trades: int = 3) -> pd.DataFrame:
    df = _prepare_closed_trades_for_optimizer(closed_trades)
    if df.empty:
        return pd.DataFrame()

    grouped = (
        df.groupby(["interval", "setup_grade", "alignment_label", "conviction_label"], dropna=False)
        .agg(trades=("realized_pnl", "count"))
        .reset_index()
    )
    grouped["pattern"] = grouped[["interval", "setup_grade", "alignment_label", "conviction_label"]].astype(str).agg(" | ".join, axis=1)
    grouped = grouped[grouped["trades"] < min_trades].sort_values("trades", ascending=True).reset_index(drop=True)
    return grouped[["pattern", "trades"]]


def optimizer_summary_text(closed_trades: pd.DataFrame, min_trades: int = 3) -> str:
    if closed_trades.empty or "realized_pnl" not in closed_trades.columns:
        return "No closed-trade data yet."

    top_patterns = optimizer_top_patterns(closed_trades, min_trades=min_trades, limit=1)
    bottom_patterns = optimizer_bottom_patterns(closed_trades, min_trades=min_trades, limit=1)

    messages: List[str] = []

    if not top_patterns.empty:
        top = top_patterns.iloc[0]
        messages.append(
            f"Best recent edge: {top['pattern']} "
            f"(avg PnL {float(top['average_pnl']):.2f}, win rate {float(top['win_rate']):.2%}, trades {int(top['trades'])})."
        )

    if not bottom_patterns.empty:
        bottom = bottom_patterns.iloc[0]
        messages.append(
            f"Avoid: {bottom['pattern']} "
            f"(avg PnL {float(bottom['average_pnl']):.2f}, win rate {float(bottom['win_rate']):.2%}, trades {int(bottom['trades'])})."
        )

    if not messages:
        return f"Not enough trade history for optimizer insights yet. Need at least {min_trades} similar trades per pattern."

    return " ".join(messages)


def open_paper_trade_record(
    report: AnalysisReport,
    live_price: float,
    position: PositionSizing,
) -> PaperTradeRecord:
    contract = report["option_plan"]["recommended_contract"]

    contract_symbol = ""
    contract_mid = float("nan")
    if contract is not None:
        contract_symbol = contract["contract_symbol"]
        contract_mid = float(contract["mid"])

    return {
        "opened_at": utc_now().isoformat(),
        "ticker": report["ticker"],
        "interval": report["interval"],
        "verdict": report["verdict"],
        "alignment_label": report["alignment_label"],
        "conviction_label": report["conviction_label"],
        "setup_score": float(report["setup_score"]),
        "alpha_score": float(_safe_float(report.get("alpha_score"))),
        "execution_score": float(_safe_float(report.get("execution_score"))),
        "portfolio_score": float(_safe_float(report.get("portfolio_score"))),
        "edge_to_cost_ratio": float(_safe_float(report.get("edge_to_cost_ratio"))),
        "proxy_correlation_bucket": str(report.get("proxy_correlation_bucket") or _proxy_correlation_bucket_for_ticker(report["ticker"])),
        "portfolio_rank": _safe_int(report.get("portfolio_rank"), 0) or None,
        "auto_entry_eligible": _safe_bool(report.get("auto_entry_eligible", False)),
        "setup_grade": report["setup_grade"],
        "trade_decision": report["trade_decision"],
        "reject_reason": report["reject_reason"],
        "event_risk": _safe_bool(report.get("event_risk", False)),
        "event_label": report.get("event_label", ""),
        "event_reason": report.get("event_reason", ""),
        "next_event_name": report.get("next_event_name", ""),
        "next_event_date": report.get("next_event_date", ""),
        "live_price_at_open": float(live_price),
        "contract_symbol": contract_symbol,
        "contract_mid_at_open": contract_mid,
        "suggested_contracts": float(position["suggested_contracts"]),
        "position_cost": float(position["total_position_cost"]),
        "max_risk_dollars": float(position["max_risk_dollars"]),
        "target_price": float(report["option_plan"]["expected_underlying_target"]),
        "invalidation_price": float(report["option_plan"]["invalidation_price"]),
        "tp1_pct": float(report["option_plan"]["take_profit_1"]),
        "tp2_pct": float(report["option_plan"]["take_profit_2"]),
        "status": "OPEN",
    }


def append_paper_trade(record: dict[str, object], file_path: Path = PAPER_OPEN_TRADES_PATH) -> None:
    new_row = pd.DataFrame([record])

    if file_path.exists():
        try:
            existing = pd.read_csv(file_path)
            combined = pd.concat([existing, new_row], ignore_index=True)
        except Exception:
            combined = new_row
    else:
        combined = new_row

    write_dataframe_csv(file_path, combined)


def read_paper_open_trades(file_path: Path = PAPER_OPEN_TRADES_PATH) -> pd.DataFrame:
    if not file_path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(file_path)
    except Exception:
        return pd.DataFrame()


def read_paper_closed_trades(file_path: Path = PAPER_CLOSED_TRADES_PATH) -> pd.DataFrame:
    if not file_path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(file_path)
    except Exception:
        return pd.DataFrame()


def close_paper_trade_by_index(
    trade_index: int,
    close_underlying_price: float,
    close_contract_mid: float,
    close_fraction: float = 1.0,
    file_path_open: Path = PAPER_OPEN_TRADES_PATH,
    file_path_closed: Path = PAPER_CLOSED_TRADES_PATH,
) -> None:
    open_trades = read_paper_open_trades(file_path_open)
    if open_trades.empty or trade_index < 0 or trade_index >= len(open_trades):
        return

    trade = open_trades.iloc[trade_index].copy()
    instrument_type = str(trade.get("instrument_type", "listed_option") or "listed_option").strip().lower()
    broker_side = str(trade.get("broker_side") or "BUY").strip().upper() or "BUY"

    original_contracts = float(pd.to_numeric(trade.get("suggested_contracts", 0), errors="coerce"))
    if original_contracts <= 0:
        return

    entry_contract_mid = float(pd.to_numeric(trade.get("contract_mid_at_open", float("nan")), errors="coerce"))
    fraction = min(max(float(close_fraction), 0.0), 1.0)

    if original_contracts <= 1:
        contracts_to_close = min(
            original_contracts,
            max(0.001, float(int((original_contracts * fraction) * 1000)) / 1000.0),
        )
    else:
        contracts_to_close = max(1.0, float(int(round(original_contracts * fraction))))
        contracts_to_close = min(contracts_to_close, original_contracts)
    remaining_contracts = original_contracts - contracts_to_close

    pnl_per_contract = float("nan")
    realized_pnl = float("nan")
    if not math.isnan(entry_contract_mid):
        if instrument_type == "equity" and broker_side == "SELL":
            pnl_per_contract = (entry_contract_mid - float(close_contract_mid)) * 100.0
        else:
            pnl_per_contract = (float(close_contract_mid) - entry_contract_mid) * 100.0
        realized_pnl = pnl_per_contract * contracts_to_close

    closed_status = "CLOSED" if remaining_contracts == 0 else "PARTIAL"

    closed_record = {
        **trade.to_dict(),
        "closed_at": utc_now().isoformat(),
        "live_price_at_close": float(close_underlying_price),
        "contract_mid_at_close": float(close_contract_mid),
        "closed_contracts": float(contracts_to_close),
        "remaining_contracts_after_close": float(remaining_contracts),
        "close_fraction": float(fraction),
        "pnl_per_contract": pnl_per_contract,
        "realized_pnl": realized_pnl,
        "status": closed_status,
    }

    if remaining_contracts == 0:
        remaining_open = open_trades.drop(index=trade_index).reset_index(drop=True)
    else:
        updated_trade = trade.copy()
        updated_trade["suggested_contracts"] = float(remaining_contracts)
        updated_trade["position_cost"] = float(
            pd.to_numeric(trade.get("position_cost", 0.0), errors="coerce") * (remaining_contracts / original_contracts)
        )
        updated_trade["max_risk_dollars"] = float(
            pd.to_numeric(trade.get("max_risk_dollars", 0.0), errors="coerce") * (remaining_contracts / original_contracts)
        )
        remaining_open = open_trades.copy()
        remaining_open.iloc[trade_index] = updated_trade

    if file_path_closed.exists():
        try:
            existing_closed = pd.read_csv(file_path_closed)
            closed_df = pd.concat([existing_closed, pd.DataFrame([closed_record])], ignore_index=True)
        except Exception:
            closed_df = pd.DataFrame([closed_record])
    else:
        closed_df = pd.DataFrame([closed_record])

    write_dataframe_csv(file_path_open, remaining_open)
    write_dataframe_csv(file_path_closed, closed_df)


def monitor_paper_trades(file_path: Path = PAPER_OPEN_TRADES_PATH) -> pd.DataFrame:
    paper_trades = read_paper_open_trades(file_path)
    if paper_trades.empty:
        return pd.DataFrame()

    monitored_rows: list[dict[str, object]] = []

    for _, row in paper_trades.iterrows():
        ticker = str(row.get("ticker", ""))
        contract_symbol = str(row.get("contract_symbol", ""))
        entry_contract_mid = float(pd.to_numeric(row.get("contract_mid_at_open", float("nan")), errors="coerce"))
        contracts = float(pd.to_numeric(row.get("suggested_contracts", 0), errors="coerce"))

        current_underlying = get_live_price(ticker) if ticker else float("nan")
        current_contract_mid = get_contract_mid_from_symbol(contract_symbol)
        option_return_pct = float("nan")
        unrealized_pnl = float("nan")

        if not math.isnan(entry_contract_mid) and entry_contract_mid > 0 and not math.isnan(current_contract_mid):
            option_return_pct = (current_contract_mid - entry_contract_mid) / entry_contract_mid
            unrealized_pnl = (current_contract_mid - entry_contract_mid) * 100.0 * contracts

        exit_evaluation = evaluate_open_trade_exit(
            row.to_dict(),
            current_underlying_price=current_underlying,
            current_contract_mid=current_contract_mid,
        )
        action = str(exit_evaluation["monitor_action"])

        monitored_rows.append(
            {
                **row.to_dict(),
                "current_underlying": current_underlying,
                "current_underlying_price": current_underlying,
                "current_contract_mid": current_contract_mid,
                "option_return_pct": option_return_pct,
                "unrealized_pnl": unrealized_pnl,
                "monitor_action": action,
                "exit_reason": exit_evaluation["exit_reason"],
                "current_exit_stage": exit_evaluation["current_exit_stage"],
                "active_stop_price": exit_evaluation["active_stop_price"],
                "next_target_price": exit_evaluation["next_target_price"],
                "entry_reference_price": exit_evaluation["entry_reference_price"],
                "risk_unit": exit_evaluation["risk_unit"],
                "tp1_price": exit_evaluation["tp1_price"],
                "tp2_price": exit_evaluation["tp2_price"],
                "stop_after_tp1": exit_evaluation["stop_after_tp1"],
                "stop_after_tp2": exit_evaluation["stop_after_tp2"],
                "time_stop_bars": exit_evaluation["time_stop_bars"],
                "bars_held": exit_evaluation["bars_held"],
                "trade_age_days": exit_evaluation["trade_age_days"],
                "tp1_hit": exit_evaluation["tp1_hit"],
                "tp2_hit": exit_evaluation["tp2_hit"],
                "stop_hit": exit_evaluation["stop_hit"],
                "target_hit": exit_evaluation["tp2_hit"],
                "time_stop_hit": exit_evaluation["time_stop_hit"],
            }
        )

    return pd.DataFrame(monitored_rows)


def paper_portfolio_summary(
    open_paper_trades_monitored: pd.DataFrame,
    closed_paper_trades: pd.DataFrame,
) -> dict[str, float]:
    open_cost = 0.0
    open_risk = 0.0
    unrealized = 0.0
    realized = 0.0
    active_count = 0.0
    closed_count = 0.0
    win_rate = 0.0
    average_trade = 0.0

    if not open_paper_trades_monitored.empty:
        if "position_cost" in open_paper_trades_monitored.columns:
            open_cost = float(pd.to_numeric(open_paper_trades_monitored["position_cost"], errors="coerce").fillna(0.0).sum())
        if "max_risk_dollars" in open_paper_trades_monitored.columns:
            open_risk = float(pd.to_numeric(open_paper_trades_monitored["max_risk_dollars"], errors="coerce").fillna(0.0).sum())
        if "unrealized_pnl" in open_paper_trades_monitored.columns:
            unrealized = float(pd.to_numeric(open_paper_trades_monitored["unrealized_pnl"], errors="coerce").fillna(0.0).sum())
        active_count = float(len(open_paper_trades_monitored))

    if not closed_paper_trades.empty and "realized_pnl" in closed_paper_trades.columns:
        pnl = pd.to_numeric(closed_paper_trades["realized_pnl"], errors="coerce").fillna(0.0)
        realized = float(pnl.sum())
        closed_count = float(len(pnl))
        average_trade = float(pnl.mean()) if len(pnl) > 0 else 0.0
        win_rate = float((pnl > 0).sum() / len(pnl)) if len(pnl) > 0 else 0.0

    return {
        "open_cost": open_cost,
        "open_risk": open_risk,
        "unrealized_pnl": unrealized,
        "realized_pnl": realized,
        "active_trade_count": active_count,
        "closed_trade_count": closed_count,
        "win_rate": win_rate,
        "average_trade": average_trade,
    }
