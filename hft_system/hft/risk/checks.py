from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hft.market_data.schemas import MarketEvent
from hft.risk.kill_switch import KillSwitchRegistry
from hft.risk.limits import HFTLimitConfig


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    detail: str
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeRiskState:
    last_market_ts_ns: int = 0
    last_update_ts_ns: int = 0
    outstanding_orders: int = 0
    inventory: float = 0.0
    notional_exposure: float = 0.0
    message_count_window: list[int] = field(default_factory=list)
    cancel_count_window: list[int] = field(default_factory=list)
    realized_pnl: float = 0.0
    fill_count: int = 0
    rejected_cancel_count: int = 0
    latest_spread: float = 0.0
    latest_volatility: float = 0.0
    fill_anomaly_score: float = 0.0


class HFTRiskEngine:
    def __init__(self, *, limits: HFTLimitConfig, kill_switches: KillSwitchRegistry | None = None):
        self.limits = limits
        self.kill_switches = kill_switches or KillSwitchRegistry()
        self.state = RuntimeRiskState()

    def validate_order(self, order: Any, state: RuntimeRiskState | None = None) -> RiskDecision:
        state = state or self.state
        symbol = str(getattr(order, "symbol", "")).upper()
        strategy_name = str(getattr(order, "strategy_name", "unknown"))
        side = str(getattr(order, "side", "")).lower()
        qty = float(getattr(order, "quantity", 0.0))
        price = float(getattr(order, "price", 0.0))
        width = float(getattr(order, "quote_width", 0.0))
        symbol_limits = self.limits.for_symbol(symbol)
        global_limits = self.limits.global_limits

        if not global_limits.enabled:
            return RiskDecision(False, "global_disabled", "Global risk engine is disabled.")
        if not self.kill_switches.is_symbol_enabled(symbol):
            return RiskDecision(False, "symbol_disabled", f"{symbol} is disabled by kill switch.")
        if not self.kill_switches.is_strategy_enabled(strategy_name):
            return RiskDecision(False, "strategy_disabled", f"{strategy_name} is disabled by kill switch.")
        if qty <= 0:
            return RiskDecision(False, "invalid_size", "Orders must have positive quantity.")
        if qty > symbol_limits.max_order_size:
            return RiskDecision(False, "max_order_size", "Order exceeds max order size.", {"max_order_size": symbol_limits.max_order_size})
        projected_inventory = state.inventory + (qty if side == "buy" else -qty)
        if abs(projected_inventory) > symbol_limits.max_inventory:
            return RiskDecision(False, "max_inventory", "Projected inventory breaches symbol limit.", {"max_inventory": symbol_limits.max_inventory})
        projected_notional = abs(state.notional_exposure) + abs(price * qty)
        if projected_notional > global_limits.max_notional_exposure:
            return RiskDecision(False, "max_notional_exposure", "Projected notional breaches global limit.")
        if state.outstanding_orders >= global_limits.max_outstanding_orders:
            return RiskDecision(False, "max_outstanding_orders", "Outstanding order cap reached.")
        if not symbol_limits.market_state_enabled:
            return RiskDecision(False, "market_state_disabled", "Market state is disabled for this symbol.")
        if width > symbol_limits.max_quote_width:
            return RiskDecision(False, "max_quote_width", "Quote width too wide.")
        if width and width < symbol_limits.min_quote_width:
            return RiskDecision(False, "min_quote_width", "Quote width too narrow.")
        if state.latest_spread > symbol_limits.max_runtime_spread:
            return RiskDecision(False, "spread_too_wide", "Current spread breaches runtime quoting limit.")
        if state.latest_volatility > symbol_limits.max_short_term_volatility:
            return RiskDecision(False, "volatility_spike", "Current volatility breaches runtime quoting limit.")
        if self._rate_per_second(state.message_count_window) > global_limits.max_message_rate_per_second:
            return RiskDecision(False, "message_rate", "Message rate cap breached.")
        if self._rate_per_second(state.cancel_count_window) > global_limits.max_cancel_rate_per_second:
            return RiskDecision(False, "cancel_rate", "Cancel rate cap breached.")
        return RiskDecision(True, "allowed", "Order passed HFT risk checks.")

    def validate_cancel(self, cancel: Any, state: RuntimeRiskState | None = None) -> RiskDecision:
        state = state or self.state
        if self._rate_per_second(state.cancel_count_window) > self.limits.global_limits.max_cancel_rate_per_second:
            return RiskDecision(False, "cancel_rate", "Cancel rate cap breached.")
        return RiskDecision(True, "allowed", "Cancel passed HFT risk checks.")

    def update_runtime_state(self, event: MarketEvent | None = None, **updates: Any) -> RuntimeRiskState:
        if event is not None:
            self.state.last_market_ts_ns = event.receive_ts_ns
            self.state.last_update_ts_ns = event.receive_ts_ns
        for key, value in updates.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)
        return self.state

    def should_kill_strategy(self, state: RuntimeRiskState | None = None) -> RiskDecision:
        state = state or self.state
        limits = self.limits.global_limits
        now_ns = max(state.last_update_ts_ns, state.last_market_ts_ns)
        if state.last_market_ts_ns and now_ns and (now_ns - state.last_market_ts_ns) > limits.stale_feed_ns:
            return RiskDecision(False, "stale_feed", "Market data feed is stale.")
        if state.latest_spread > self.limits.for_symbol("DEFAULT").max_runtime_spread:
            return RiskDecision(False, "spread_too_wide", "Spread too wide for stable quoting.")
        if state.latest_volatility > self.limits.for_symbol("DEFAULT").max_short_term_volatility:
            return RiskDecision(False, "volatility_spike", "Short-term volatility breached the runtime threshold.")
        if abs(state.inventory) > limits.max_inventory_abs:
            return RiskDecision(False, "inventory_breach", "Inventory breached the runtime threshold.")
        if abs(state.realized_pnl) >= limits.loss_threshold and state.realized_pnl < 0:
            return RiskDecision(False, "loss_threshold", "Loss threshold breached.")
        if state.fill_anomaly_score > limits.max_fill_anomaly_score:
            return RiskDecision(False, "fill_anomaly", "Fill anomaly threshold breached.")
        if self._rate_per_second(state.message_count_window) > limits.max_message_rate_per_second:
            return RiskDecision(False, "message_rate", "Message rate threshold breached.")
        if self._rate_per_second(state.cancel_count_window) > limits.max_cancel_rate_per_second:
            return RiskDecision(False, "cancel_rate", "Cancel rate threshold breached.")
        if state.rejected_cancel_count > limits.max_rejected_cancel_count:
            return RiskDecision(False, "cancel_reject_rate", "Cancel reject threshold breached.")
        return RiskDecision(True, "healthy", "Strategy can keep quoting.")

    def record_message(self, timestamp_ns: int) -> None:
        self.state.message_count_window.append(int(timestamp_ns))
        self.state.message_count_window = self._trim_window(self.state.message_count_window, int(timestamp_ns))

    def record_cancel(self, timestamp_ns: int) -> None:
        self.state.cancel_count_window.append(int(timestamp_ns))
        self.state.cancel_count_window = self._trim_window(self.state.cancel_count_window, int(timestamp_ns))

    @staticmethod
    def _trim_window(values: list[int], now_ns: int) -> list[int]:
        floor = int(now_ns) - 1_000_000_000
        return [value for value in values if value >= floor]

    @staticmethod
    def _rate_per_second(values: list[int]) -> float:
        return float(len(values))
