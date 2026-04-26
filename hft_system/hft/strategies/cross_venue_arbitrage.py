from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hft.fair_value.engine import FairValueEstimate
from hft.features.microstructure import MicrostructureFeatureSnapshot
from hft.inventory.inventory import InventoryState
from hft.market_data.schemas import MarketEvent
from hft.strategies.base import CancelInstruction, HFTStrategy, QuoteInstruction, StrategyHealthSnapshot


@dataclass(frozen=True)
class CrossVenueOpportunity:
    symbol: str
    buy_venue: str
    sell_venue: str
    theoretical_edge_bps: float
    executable_edge_bps: float
    stale_quote_detected: bool
    latency_haircut_bps: float
    fee_haircut_bps: float


@dataclass
class CrossVenueArbitrageStrategy(HFTStrategy):
    strategy_name: str = "cross_venue_arbitrage"
    enabled: bool = False
    edge_threshold_bps: float = 3.0
    latest_timestamp_ns: int = 0
    stale_after_ns: int = 1_000_000_000
    venue_fee_bps: float = 0.5
    latency_haircut_bps: float = 0.75
    last_opportunity: CrossVenueOpportunity | None = None

    def on_market_event(self, event: MarketEvent) -> None:
        self.latest_timestamp_ns = event.receive_ts_ns

    def compute_features(self, book_state: Any) -> MicrostructureFeatureSnapshot:
        return book_state

    def estimate_fair_value(self, features: MicrostructureFeatureSnapshot) -> FairValueEstimate:
        return FairValueEstimate(
            timestamp_ns=features.timestamp_ns,
            symbol=features.symbol,
            fair_value=features.mid_price,
            confidence_score=0.0,
            expected_next_tick_direction=0.0,
            expected_holding_time_ms=0.0,
        )

    def generate_quotes(self, features: MicrostructureFeatureSnapshot, inventory_state: InventoryState) -> list[QuoteInstruction]:
        return []

    def generate_cancels(self, active_orders: list[Any], market_state: Any) -> list[CancelInstruction]:
        return []

    def analyze_opportunity(self, venue_quotes: dict[str, dict[str, float | int]]) -> CrossVenueOpportunity | None:
        if len(venue_quotes) < 2:
            self.last_opportunity = None
            return None
        best_buy = None
        best_sell = None
        for venue, quote in venue_quotes.items():
            ask = float(quote.get("ask") or 0.0)
            bid = float(quote.get("bid") or 0.0)
            if ask > 0 and (best_buy is None or ask < best_buy[1]):
                best_buy = (venue, ask, int(quote.get("timestamp_ns") or 0))
            if bid > 0 and (best_sell is None or bid > best_sell[1]):
                best_sell = (venue, bid, int(quote.get("timestamp_ns") or 0))
        if best_buy is None or best_sell is None:
            self.last_opportunity = None
            return None
        theoretical_edge_bps = ((best_sell[1] - best_buy[1]) / max(best_buy[1], 0.01)) * 10_000.0
        latest_ts = max(best_buy[2], best_sell[2], self.latest_timestamp_ns)
        stale = (latest_ts - best_buy[2]) > self.stale_after_ns or (latest_ts - best_sell[2]) > self.stale_after_ns
        executable_edge_bps = theoretical_edge_bps - (2.0 * self.venue_fee_bps) - self.latency_haircut_bps
        self.last_opportunity = CrossVenueOpportunity(
            symbol=str(next(iter(venue_quotes.values())).get("symbol") or ""),
            buy_venue=best_buy[0],
            sell_venue=best_sell[0],
            theoretical_edge_bps=theoretical_edge_bps,
            executable_edge_bps=executable_edge_bps,
            stale_quote_detected=stale,
            latency_haircut_bps=self.latency_haircut_bps,
            fee_haircut_bps=2.0 * self.venue_fee_bps,
        )
        return self.last_opportunity

    def publish_risk_state(self) -> StrategyHealthSnapshot:
        detail = "Research-only cross-venue arbitrage strategy with no live routing."
        if self.last_opportunity is not None:
            detail = (
                f"{self.last_opportunity.buy_venue}->{self.last_opportunity.sell_venue} "
                f"theoretical {self.last_opportunity.theoretical_edge_bps:.2f}bps, "
                f"executable {self.last_opportunity.executable_edge_bps:.2f}bps"
            )
        return StrategyHealthSnapshot(
            strategy_name=self.strategy_name,
            enabled=self.enabled,
            risk_state="disabled" if not self.enabled else "healthy",
            detail=detail,
            timestamp_ns=self.latest_timestamp_ns,
        )
