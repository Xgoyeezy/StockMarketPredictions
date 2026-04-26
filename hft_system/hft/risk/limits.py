from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SymbolRiskLimits:
    max_order_size: float = 500.0
    max_inventory: float = 2_000.0
    max_quote_width: float = 0.25
    min_quote_width: float = 0.01
    market_state_enabled: bool = True
    max_runtime_spread: float = 0.50
    max_short_term_volatility: float = 0.02


@dataclass(frozen=True)
class GlobalRiskLimits:
    enabled: bool = True
    max_notional_exposure: float = 250_000.0
    max_outstanding_orders: int = 20
    max_message_rate_per_second: float = 120.0
    max_cancel_rate_per_second: float = 80.0
    loss_threshold: float = 5_000.0
    stale_feed_ns: int = 2_000_000_000
    max_rejected_cancel_count: int = 5
    max_fill_anomaly_score: float = 1.0
    max_inventory_abs: float = 2_000.0


@dataclass(frozen=True)
class HFTLimitConfig:
    global_limits: GlobalRiskLimits = field(default_factory=GlobalRiskLimits)
    symbol_limits: dict[str, SymbolRiskLimits] = field(default_factory=dict)

    def for_symbol(self, symbol: str) -> SymbolRiskLimits:
        normalized = symbol.upper()
        return self.symbol_limits.get(normalized, self.symbol_limits.get("DEFAULT", SymbolRiskLimits()))
