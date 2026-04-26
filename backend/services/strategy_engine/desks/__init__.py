from backend.services.strategy_engine.desks.equities_momentum import EquitiesMomentumDesk
from backend.services.strategy_engine.desks.event_driven import EventDrivenDesk
from backend.services.strategy_engine.desks.macro_trend import MacroTrendDesk
from backend.services.strategy_engine.desks.options_volatility import OptionsVolatilityDesk
from backend.services.strategy_engine.desks.stat_arb import StatisticalArbitrageDesk

__all__ = [
    "EquitiesMomentumDesk",
    "EventDrivenDesk",
    "MacroTrendDesk",
    "OptionsVolatilityDesk",
    "StatisticalArbitrageDesk",
]
