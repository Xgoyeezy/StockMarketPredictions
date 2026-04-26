from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.services.strategy_engine.base import StrategyDesk
from backend.services.strategy_engine.desks.equities_momentum import EquitiesMomentumDesk
from backend.services.strategy_engine.desks.event_driven import EventDrivenDesk
from backend.services.strategy_engine.desks.macro_trend import MacroTrendDesk
from backend.services.strategy_engine.desks.options_volatility import OptionsVolatilityDesk
from backend.services.strategy_engine.desks.stat_arb import StatisticalArbitrageDesk


@dataclass(frozen=True)
class StrategyDeskDefinition:
    key: str
    label: str
    category: str
    lifecycle_stage: str
    trading_mode: str
    paper_trading_enabled: bool
    description: str
    implementation_class: type[StrategyDesk]
    default_config: dict[str, Any]


def _session_profiles(*, allow_listed_options: bool = False) -> dict[str, dict[str, Any]]:
    return {
        "pre_market": {
            "enabled": True,
            "instruments": ["equity"],
            "order_type": "limit",
            "time_in_force": "day_ext",
            "risk_multiplier": 0.50,
            "size_cap_ratio": 0.35,
            "min_edge_to_cost_ratio": 4.0,
            "max_spread_bps": 12.5,
            "allow_aggressive_averaging": False,
        },
        "regular": {
            "enabled": True,
            "instruments": ["equity", "listed_option"] if allow_listed_options else ["equity"],
            "order_type": "limit",
            "time_in_force": "day",
            "risk_multiplier": 1.0,
            "size_cap_ratio": 1.0,
            "min_edge_to_cost_ratio": 2.5,
            "max_spread_bps": 25.0,
            "allow_aggressive_averaging": False,
        },
        "after_hours": {
            "enabled": True,
            "instruments": ["equity"],
            "order_type": "limit",
            "time_in_force": "day_ext",
            "risk_multiplier": 0.35,
            "size_cap_ratio": 0.25,
            "min_edge_to_cost_ratio": 5.0,
            "max_spread_bps": 10.0,
            "allow_aggressive_averaging": False,
        },
        "closed": {
            "enabled": True,
            "instruments": [],
            "allow_new_entries": False,
            "keep_monitoring": True,
            "keep_reconciliation": True,
        },
    }


_DESK_DEFINITIONS: tuple[StrategyDeskDefinition, ...] = (
    StrategyDeskDefinition(
        key="macro_trend",
        label="Macro Trend Desk",
        category="macro",
        lifecycle_stage="paper",
        trading_mode="paper",
        paper_trading_enabled=True,
        description="Medium-term trend desk across ETF and macro proxy exposures with volatility targeting.",
        implementation_class=MacroTrendDesk,
        default_config={
            "capital_base": 100000.0,
            "max_positions": 3,
            "interval": "1d",
            "period": "2y",
            "include_extended_hours": True,
            "session_profiles": _session_profiles(),
            "universe": ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "TLT", "GLD"],
        },
    ),
    StrategyDeskDefinition(
        key="stat_arb",
        label="Stat Arb Desk",
        category="relative_value",
        lifecycle_stage="paper",
        trading_mode="paper",
        paper_trading_enabled=True,
        description="Pair and basket relative-value desk with hedge ratios, spreads, and z-score entries.",
        implementation_class=StatisticalArbitrageDesk,
        default_config={
            "capital_base": 100000.0,
            "interval": "1d",
            "period": "1y",
            "include_extended_hours": True,
            "session_profiles": _session_profiles(),
            "entry_z": 1.5,
            "max_pairs": 2,
            "pairs": [["SPY", "QQQ"], ["XLE", "XOP"], ["SMH", "SOXX"], ["XLF", "KBE"]],
        },
    ),
    StrategyDeskDefinition(
        key="equities_momentum",
        label="Equities Momentum Desk",
        category="cross_sectional",
        lifecycle_stage="research",
        trading_mode="research",
        paper_trading_enabled=False,
        description="Cross-sectional momentum ranker for liquid equities with turnover controls.",
        implementation_class=EquitiesMomentumDesk,
        default_config={
            "capital_base": 100000.0,
            "interval": "1d",
            "period": "2y",
            "include_extended_hours": True,
            "session_profiles": _session_profiles(),
        },
    ),
    StrategyDeskDefinition(
        key="event_driven",
        label="Event-Driven Desk",
        category="catalyst",
        lifecycle_stage="research",
        trading_mode="research",
        paper_trading_enabled=False,
        description="Catalyst-driven desk around earnings and macro event windows.",
        implementation_class=EventDrivenDesk,
        default_config={
            "capital_base": 100000.0,
            "interval": "1d",
            "period": "6mo",
            "include_extended_hours": True,
            "session_profiles": _session_profiles(),
        },
    ),
    StrategyDeskDefinition(
        key="options_volatility",
        label="Options Volatility Desk",
        category="volatility",
        lifecycle_stage="research",
        trading_mode="research",
        paper_trading_enabled=False,
        description="Research desk for implied-versus-realized volatility, skew, and term structure.",
        implementation_class=OptionsVolatilityDesk,
        default_config={
            "capital_base": 100000.0,
            "interval": "1d",
            "period": "1y",
            "include_extended_hours": True,
            "session_profiles": _session_profiles(allow_listed_options=True),
        },
    ),
)

_DESK_BY_KEY = {definition.key: definition for definition in _DESK_DEFINITIONS}


def list_strategy_desk_definitions() -> tuple[StrategyDeskDefinition, ...]:
    return _DESK_DEFINITIONS


def get_strategy_desk_definition(desk_key: str) -> StrategyDeskDefinition:
    normalized = str(desk_key or "").strip().lower()
    if normalized not in _DESK_BY_KEY:
        raise KeyError(f"Unknown strategy desk: {desk_key}")
    return _DESK_BY_KEY[normalized]


def build_strategy_desk(desk_key: str, *, config: dict[str, Any] | None = None) -> StrategyDesk:
    definition = get_strategy_desk_definition(desk_key)
    merged_config = dict(definition.default_config)
    merged_config.update(dict(config or {}))
    return definition.implementation_class(config=merged_config)
