from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hft.fair_value.engine import FairValueEngine, FairValueEstimate
from hft.features.microstructure import MicrostructureFeatureSnapshot
from hft.inventory.inventory import InventoryState
from hft.market_data.schemas import MarketEvent
from hft.strategies.base import CancelInstruction, HFTStrategy, QuoteInstruction, StrategyHealthSnapshot


@dataclass
class MeanReversionStrategy(HFTStrategy):
    strategy_name: str = "short_term_mean_reversion"
    enabled: bool = False
    zscore_threshold: float = 1.5
    max_holding_ns: int = 3_000_000_000
    max_spread_bps: float = 20.0
    min_trade_arrival_rate: float = 0.25
    max_stale_book_indicator: float = 0.5
    max_inventory_skew: float = 0.8
    base_order_size: float = 25.0

    def __post_init__(self) -> None:
        self.fair_value_engine = FairValueEngine()
        self._timestamp_ns = 0

    def on_market_event(self, event: MarketEvent) -> None:
        self._timestamp_ns = event.receive_ts_ns

    def compute_features(self, book_state: Any) -> MicrostructureFeatureSnapshot:
        return book_state

    def estimate_fair_value(self, features: MicrostructureFeatureSnapshot) -> FairValueEstimate:
        return self.fair_value_engine.estimate(features)

    def generate_quotes(self, features: MicrostructureFeatureSnapshot, inventory_state: InventoryState) -> list[QuoteInstruction]:
        if not self.enabled or features.short_term_volatility <= 0:
            return []
        if abs(inventory_state.inventory_skew) >= self.max_inventory_skew:
            return []
        if features.stale_book_indicator > self.max_stale_book_indicator:
            return []
        if features.trade_arrival_rate < self.min_trade_arrival_rate:
            return []
        spread_bps = (features.spread / max(features.mid_price, 0.01)) * 10_000.0
        if spread_bps > self.max_spread_bps:
            return []
        fair_value = self.estimate_fair_value(features)
        deviation = (fair_value.fair_value - features.mid_price) / max(features.spread, 1e-6)
        if deviation >= self.zscore_threshold:
            return [QuoteInstruction(side="buy", price=features.mid_price, quantity=self.base_order_size, quote_width=features.spread)]
        if deviation <= -self.zscore_threshold:
            return [QuoteInstruction(side="sell", price=features.mid_price, quantity=self.base_order_size, quote_width=features.spread)]
        return []

    def generate_cancels(self, active_orders: list[Any], market_state: Any) -> list[CancelInstruction]:
        now_ns = int(getattr(market_state, "timestamp_ns", self._timestamp_ns))
        return [
            CancelInstruction(order_id=str(order.order_id), reason="time_stop")
            for order in active_orders
            if (now_ns - int(getattr(order, "ack_timestamp", now_ns))) >= self.max_holding_ns
        ]

    def publish_risk_state(self) -> StrategyHealthSnapshot:
        return StrategyHealthSnapshot(
            strategy_name=self.strategy_name,
            enabled=self.enabled,
            risk_state="healthy" if self.enabled else "disabled",
            detail="Replay-only short-term mean reversion strategy with spread and stale-book guards.",
            timestamp_ns=self._timestamp_ns,
        )
