from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KillSwitchRegistry:
    global_enabled: bool = True
    disabled_symbols: set[str] = field(default_factory=set)
    disabled_strategies: set[str] = field(default_factory=set)
    kill_reasons: dict[str, str] = field(default_factory=dict)

    def disable_global(self, reason: str) -> None:
        self.global_enabled = False
        self.kill_reasons["global"] = reason

    def disable_symbol(self, symbol: str, reason: str) -> None:
        self.disabled_symbols.add(symbol.upper())
        self.kill_reasons[f"symbol:{symbol.upper()}"] = reason

    def disable_strategy(self, strategy: str, reason: str) -> None:
        self.disabled_strategies.add(strategy)
        self.kill_reasons[f"strategy:{strategy}"] = reason

    def is_symbol_enabled(self, symbol: str) -> bool:
        return self.global_enabled and symbol.upper() not in self.disabled_symbols

    def is_strategy_enabled(self, strategy: str) -> bool:
        return self.global_enabled and strategy not in self.disabled_strategies
