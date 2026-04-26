from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hft.fair_value.engine import FairValueEngine, FairValueEstimate
from hft.features.microstructure import MicrostructureFeatureEngine, MicrostructureFeatureSnapshot
from hft.inventory.inventory import InventoryState
from hft.market_data.schemas import MarketEvent
from hft.optimization.types import MarketMakingParameterSet
from hft.order_book.book import OrderBook
from hft.strategies.base import CancelInstruction, HFTStrategy, QuoteInstruction, StrategyHealthSnapshot


@dataclass
class InventoryAwareMarketMakingStrategy(HFTStrategy):
    strategy_name: str = "inventory_aware_market_making"
    enabled: bool = True
    parameter_set: MarketMakingParameterSet = field(default_factory=MarketMakingParameterSet)
    fair_value_engine: FairValueEngine | None = None

    def __post_init__(self) -> None:
        self.feature_engine = MicrostructureFeatureEngine()
        if self.fair_value_engine is None:
            self.fair_value_engine = FairValueEngine(fair_value_sensitivity=self.parameter_set.fair_value_sensitivity)
        self._last_event_ts_ns = 0
        self._last_features: MicrostructureFeatureSnapshot | None = None
        self._last_risk_state = "healthy"
        self._last_risk_detail = "Ready"

    def on_market_event(self, event: MarketEvent) -> None:
        self._last_event_ts_ns = event.receive_ts_ns

    def compute_features(self, book_state: OrderBook) -> MicrostructureFeatureSnapshot:
        if not book_state.recent_trade_flow:
            synthetic_event = MarketEvent(
                event_id="synthetic",
                source="sim",
                venue="SIM",
                symbol=book_state.symbol,
                exchange_ts_ns=book_state.last_timestamp_ns,
                receive_ts_ns=book_state.last_timestamp_ns,
                sequence=0,
                event_type="bbo",
                side="buy",
                price=book_state.best_bid,
                size=0.0,
            )
        else:
            last_trade = book_state.recent_trade_flow[-1]
            synthetic_event = MarketEvent(
                event_id=str(last_trade.get("trade_id") or "trade"),
                source="sim",
                venue="SIM",
                symbol=book_state.symbol,
                exchange_ts_ns=int(last_trade["timestamp_ns"]),
                receive_ts_ns=int(last_trade["timestamp_ns"]),
                sequence=0,
                event_type="trade",
                side=str(last_trade["side"]),
                price=float(last_trade["price"]),
                size=float(last_trade["size"]),
            )
        self._last_features = self.feature_engine.update(book_state=book_state.snapshot(), event=synthetic_event)
        return self._last_features

    def estimate_fair_value(self, features: MicrostructureFeatureSnapshot) -> FairValueEstimate:
        return self.fair_value_engine.estimate(features)

    def generate_quotes(self, features: MicrostructureFeatureSnapshot, inventory_state: InventoryState) -> list[QuoteInstruction]:
        if not self.enabled:
            self._last_risk_state = "disabled"
            self._last_risk_detail = "Strategy disabled by config."
            return []

        spread_bps = (features.spread / features.mid_price) * 10_000.0 if features.mid_price > 0 else 0.0
        if spread_bps >= self.parameter_set.max_quote_width_bps:
            self._last_risk_state = "paused"
            self._last_risk_detail = "Spread too wide for stable quoting."
            return []
        toxic_flow = abs(features.trade_imbalance) + abs(features.adverse_selection_estimate) + features.cancellation_burst_score
        if toxic_flow >= self.parameter_set.toxic_flow_suppression_threshold:
            self._last_risk_state = "paused"
            self._last_risk_detail = "Toxic flow suppression is active."
            return []

        fair_value = self.estimate_fair_value(features).fair_value
        regime_width_bps = max(
            self.parameter_set.min_quote_width_bps,
            spread_bps + (features.short_term_volatility * self.parameter_set.volatility_spread_multiplier * 10_000.0),
        )
        regime_width_bps = min(regime_width_bps, self.parameter_set.max_quote_width_bps)
        width = max(features.mid_price * regime_width_bps / 10_000.0, 0.01)
        skew = inventory_state.inventory_skew * self.parameter_set.inventory_skew_multiplier * width
        size_multiplier = max(0.10, 1.0 - abs(inventory_state.inventory_skew))
        order_size = max(1.0, self.parameter_set.base_order_size * size_multiplier)
        stop_side = None
        if inventory_state.inventory_skew >= self.parameter_set.one_sided_quoting_threshold:
            stop_side = "buy"
        elif inventory_state.inventory_skew <= -self.parameter_set.one_sided_quoting_threshold:
            stop_side = "sell"
        if abs(inventory_state.inventory_skew) >= 0.95:
            self._last_risk_state = "paused"
            self._last_risk_detail = "Inventory hard limit breached."
            return []
        bid_offset = features.mid_price * self.parameter_set.bid_offset_bps / 10_000.0
        ask_offset = features.mid_price * self.parameter_set.ask_offset_bps / 10_000.0
        quotes: list[QuoteInstruction] = []
        if stop_side != "buy":
            quotes.append(
                QuoteInstruction(
                    side="buy",
                    price=max(fair_value - width / 2.0 - skew - bid_offset, 0.01),
                    quantity=order_size,
                    quote_width=width,
                    metadata={
                        "fair_value": fair_value,
                        "regime_width_bps": regime_width_bps,
                        "toxic_flow": toxic_flow,
                        "quote_timestamp_ns": features.timestamp_ns,
                    },
                )
            )
        if stop_side != "sell":
            quotes.append(
                QuoteInstruction(
                    side="sell",
                    price=max(fair_value + width / 2.0 - skew + ask_offset, 0.01),
                    quantity=order_size,
                    quote_width=width,
                    metadata={
                        "fair_value": fair_value,
                        "regime_width_bps": regime_width_bps,
                        "toxic_flow": toxic_flow,
                        "quote_timestamp_ns": features.timestamp_ns,
                    },
                )
            )
        self._last_risk_state = "healthy"
        self._last_risk_detail = "Quoting normally."
        return quotes

    def generate_cancels(self, active_orders: list[Any], market_state: Any) -> list[CancelInstruction]:
        timestamp_ns = int(getattr(market_state, "timestamp_ns", self._last_event_ts_ns))
        fair_value = float(getattr(getattr(market_state, "fair_value", None), "fair_value", 0.0) or 0.0)
        mid_price = float(getattr(getattr(market_state, "features", None), "mid_price", 0.0) or 0.0)
        cancels: list[CancelInstruction] = []
        for order in active_orders:
            age = timestamp_ns - int(getattr(order, "ack_timestamp", timestamp_ns))
            order_fair_value = float(getattr(order, "metadata", {}).get("fair_value", fair_value) or 0.0)
            fair_value_shift_bps = (
                abs(fair_value - order_fair_value) / max(mid_price, 0.01) * 10_000.0
                if fair_value and mid_price
                else 0.0
            )
            if age >= self.parameter_set.stale_quote_ns or fair_value_shift_bps >= self.parameter_set.quote_refresh_threshold_bps:
                cancels.append(CancelInstruction(order_id=str(order.order_id), reason="stale_quote"))
        return cancels

    def publish_risk_state(self) -> StrategyHealthSnapshot:
        return StrategyHealthSnapshot(
            strategy_name=self.strategy_name,
            enabled=self.enabled,
            risk_state=self._last_risk_state,
            detail=self._last_risk_detail,
            timestamp_ns=self._last_event_ts_ns,
        )
