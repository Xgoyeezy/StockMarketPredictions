from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MarketEvent:
    event_name: str
    event_date: pd.Timestamp


@dataclass(frozen=True)
class MarketStateSnapshot:
    breadth_score: float = 0.0
    breadth_momentum: float = 0.0
    market_trend_score: float = 0.0
    dispersion_score: float = 0.0
    vix_level: float | None = None
    vix_change_pct: float = 0.0
    vix_term_structure: float = 0.0
    realized_volatility: float = 0.0
    realized_volatility_percentile: float = 0.0
    opening_range_bias: float = 0.0
    opening_range_breakout: float = 0.0
    time_of_day_bucket: str = "unknown"
    time_of_day_progress: float = 0.0
    source: str = "fallback"
    source_detail: str | None = None
    fetched_at: str | None = None
    freshness_seconds: float | None = None
    freshness_ttl_seconds: int | None = None
    freshness_status: str = "unknown"
    cache_status: str = "miss"
    degraded: bool = True


@dataclass(frozen=True)
class RelativeStrengthSnapshot:
    benchmark_symbol: str = "SPY"
    sector_symbol: str = ""
    benchmark_relative_return: float = 0.0
    sector_relative_return: float = 0.0
    residual_return: float = 0.0
    peer_momentum_rank: float = 0.5
    peer_count: int = 0
    relative_strength_score: float = 0.5
    source: str = "fallback"
    source_detail: str | None = None
    fetched_at: str | None = None
    freshness_seconds: float | None = None
    freshness_ttl_seconds: int | None = None
    freshness_status: str = "unknown"
    cache_status: str = "miss"
    degraded: bool = True


@dataclass(frozen=True)
class OptionsFlowSnapshot:
    implied_volatility: float = 0.0
    iv_rank: float = 0.5
    iv_percentile: float = 0.5
    iv_realized_vol_spread: float = 0.0
    put_call_open_interest_ratio: float = 1.0
    call_volume_pressure: float = 0.0
    put_volume_pressure: float = 0.0
    net_flow_pressure: float = 0.0
    skew_score: float = 0.0
    unusual_volume_score: float = 0.0
    source: str = "fallback"
    source_detail: str | None = None
    fetched_at: str | None = None
    freshness_seconds: float | None = None
    freshness_ttl_seconds: int | None = None
    freshness_status: str = "unknown"
    cache_status: str = "miss"
    degraded: bool = True


@dataclass(frozen=True)
class EventRevisionSnapshot:
    days_to_earnings: int | None = None
    event_pressure: float = 0.0
    analyst_revision_score: float = 0.0
    target_revision_score: float = 0.0
    estimate_revision_score: float = 0.0
    macro_sensitivity: float = 0.0
    revision_confidence: float = 0.0
    source: str = "fallback"
    source_detail: str | None = None
    fetched_at: str | None = None
    freshness_seconds: float | None = None
    freshness_ttl_seconds: int | None = None
    freshness_status: str = "unknown"
    cache_status: str = "miss"
    degraded: bool = True


@dataclass(frozen=True)
class MarketNewsItem:
    title: str
    publisher: str
    url: str
    summary: str
    published_at: pd.Timestamp
    article_text: str = ""
    source_type: str = "headline"
    relevance_score: float = 0.0
    mentioned_tickers: tuple[str, ...] = ()


@dataclass(frozen=True)
class OptionContractQuote:
    contract_symbol: str
    strike: float | None
    bid: float | None
    ask: float | None
    last_price: float | None
    implied_volatility: float | None
    volume: int
    open_interest: int
    in_the_money: bool
    quote_timestamp: pd.Timestamp | None = None


@dataclass(frozen=True)
class OptionChainSnapshot:
    expiration: str
    calls: list[OptionContractQuote]
    puts: list[OptionContractQuote]
