from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SessionManifest:
    session_id: str
    symbol: str
    trading_day: str
    event_count: int
    start_ts_ns: int
    end_ts_ns: int
    average_spread_bps: float
    volatility_regime: str
    liquidity_regime: str
    source_path: str | None = None


@dataclass(frozen=True)
class WalkForwardSplit:
    split_id: str
    train_sessions: tuple[str, ...]
    validation_sessions: tuple[str, ...]
    holdout_sessions: tuple[str, ...]


@dataclass(frozen=True)
class MarketMakingParameterSet:
    bid_offset_bps: float = 0.0
    ask_offset_bps: float = 0.0
    fair_value_sensitivity: float = 1.0
    volatility_spread_multiplier: float = 0.8
    inventory_skew_multiplier: float = 0.6
    base_order_size: float = 100.0
    one_sided_quoting_threshold: float = 0.8
    stale_quote_ns: int = 1_000_000_000
    toxic_flow_suppression_threshold: float = 0.75
    quote_refresh_threshold_bps: float = 1.5
    max_quote_width_bps: float = 35.0
    min_quote_width_bps: float = 1.0


@dataclass(frozen=True)
class ObjectiveScore:
    value: float
    net_pnl: float
    drawdown_penalty: float
    adverse_selection_penalty: float
    inventory_penalty: float
    instability_penalty: float
    breach_penalty: float
    accuracy_bonus: float = 0.0


@dataclass(frozen=True)
class LatencyCalibrationArtifact:
    market_data_ns: int
    strategy_compute_ns: int
    order_submit_ns: int
    exchange_ack_ns: int
    cancel_ns: int
    fill_ns: int
    stage_error_by_name: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class QueueCalibrationArtifact:
    intercept: float = 0.0
    base_ratio_weight: float = 1.0
    trade_to_depth_weight: float = 0.5
    spread_bps_weight: float = 0.05
    imbalance_weight: float = 0.10
    latency_bucket_weight: float = 0.05
    quote_age_weight: float = 0.05
    noise_low: float = 0.98
    noise_high: float = 1.02


@dataclass(frozen=True)
class FillCalibrationReport:
    observation_count: int
    baseline_error: float
    calibrated_error: float
    missed_fill_rate: float
    adverse_selection_error: float
    latency_error_by_stage: dict[str, float]


@dataclass(frozen=True)
class ChampionSelectionReport:
    baseline_validation_score: float
    champion_validation_score: float
    holdout_score: float
    holdout_degradation: float
    accepted: bool
    reason: str
    champion_parameters: dict[str, Any]


@dataclass(frozen=True)
class OptimizationRunConfig:
    seed: int = 7
    random_candidates: int = 24
    top_candidates: int = 5
    refinement_rounds: int = 2
    refinement_radius: float = 0.15
    train_sessions: int = 3
    validation_sessions: int = 1
    holdout_sessions: int = 1
    horizons_ns: tuple[int, ...] = (100_000_000, 250_000_000, 500_000_000)
    ridge_lambda: float = 1e-3
    drawdown_cap: float = 10_000.0
    adverse_selection_cap: float = 2_500.0
    inventory_cap: float = 2_000.0
    message_rate_cap: float = 120.0
    holdout_degradation_cap: float = 0.35
    objective_weights: dict[str, float] = field(
        default_factory=lambda: {
            "drawdown": 0.50,
            "adverse_selection": 0.80,
            "inventory": 0.10,
            "instability": 0.25,
            "breach": 2_000.0,
            "accuracy_bonus": 250.0,
        }
    )
    parameter_bounds: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {
            "bid_offset_bps": (-2.0, 4.0),
            "ask_offset_bps": (-2.0, 4.0),
            "fair_value_sensitivity": (0.2, 2.0),
            "volatility_spread_multiplier": (0.2, 2.5),
            "inventory_skew_multiplier": (0.1, 2.0),
            "base_order_size": (10.0, 250.0),
            "one_sided_quoting_threshold": (0.4, 0.95),
            "stale_quote_ns": (50_000_000.0, 2_000_000_000.0),
            "toxic_flow_suppression_threshold": (0.2, 2.0),
            "quote_refresh_threshold_bps": (0.25, 5.0),
            "max_quote_width_bps": (5.0, 80.0),
            "min_quote_width_bps": (0.1, 3.0),
        }
    )
