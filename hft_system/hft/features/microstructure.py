from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import sqrt
from typing import Any

from hft.market_data.schemas import BookSnapshot, MarketEvent


@dataclass(frozen=True)
class MicrostructureFeatureSnapshot:
    timestamp_ns: int
    symbol: str
    spread: float
    mid_price: float
    mid_price_movement: float
    order_book_imbalance: float
    queue_imbalance: float
    trade_imbalance: float
    short_term_volatility: float
    quote_update_rate: float
    cancel_rate: float
    trade_arrival_rate: float
    depth_imbalance: float
    price_impact_estimate: float
    adverse_selection_estimate: float
    metadata: dict[str, Any]
    microprice: float = 0.0
    top_k_queue_depletion_rate: float = 0.0
    signed_trade_intensity: float = 0.0
    cancellation_burst_score: float = 0.0
    spread_state: str = "normal"
    volatility_regime: str = "normal"
    quote_age_ns: int = 0
    stale_book_indicator: float = 0.0
    short_horizon_realized_impact: float = 0.0


class MicrostructureFeatureEngine:
    def __init__(self, *, window_ns: int = 2_000_000_000, max_history: int = 512):
        self.window_ns = int(window_ns)
        self.mid_history: deque[tuple[int, float]] = deque(maxlen=max_history)
        self.event_history: deque[MarketEvent] = deque(maxlen=max_history)
        self.previous_mid = 0.0
        self.last_quote_change_ns = 0

    def update(self, *, book_state: BookSnapshot, event: MarketEvent) -> MicrostructureFeatureSnapshot:
        previous_top = self.mid_history[-1][1] if self.mid_history else 0.0
        self.mid_history.append((book_state.timestamp_ns, book_state.mid_price))
        self.event_history.append(event)
        self._trim(book_state.timestamp_ns)

        mid_move = book_state.mid_price - self.previous_mid if self.previous_mid else 0.0
        self.previous_mid = book_state.mid_price
        if previous_top != book_state.mid_price:
            self.last_quote_change_ns = book_state.timestamp_ns

        recent_events = list(self.event_history)
        total_window_seconds = max(self.window_ns / 1_000_000_000.0, 1e-9)
        quote_updates = sum(1 for item in recent_events if item.event_type in {"add", "modify", "bbo", "l2_snapshot"})
        cancels = sum(1 for item in recent_events if item.event_type == "cancel")
        trades = [item for item in recent_events if item.event_type == "trade"]
        trade_count = len(trades)

        buy_volume = sum(item.size for item in trades if item.side == "buy")
        sell_volume = sum(item.size for item in trades if item.side == "sell")
        trade_total = buy_volume + sell_volume
        trade_imbalance = ((buy_volume - sell_volume) / trade_total) if trade_total > 0 else 0.0

        depth = book_state.depth_by_level
        bid_depth = sum(level.size for level in depth["bids"])
        ask_depth = sum(level.size for level in depth["asks"])
        total_depth = bid_depth + ask_depth
        depth_imbalance = ((bid_depth - ask_depth) / total_depth) if total_depth > 0 else 0.0

        returns: list[float] = []
        mids = list(self.mid_history)
        for (_, previous), (_, current) in zip(mids, mids[1:]):
            if previous > 0:
                returns.append((current - previous) / previous)
        short_term_vol = sqrt(sum(value * value for value in returns) / len(returns)) if returns else 0.0
        adverse_selection = mid_move * trade_imbalance
        price_impact = abs(mid_move) * max(abs(trade_imbalance), abs(depth_imbalance))
        microprice = (
            ((book_state.best_ask * bid_depth) + (book_state.best_bid * ask_depth)) / total_depth
            if total_depth > 0 and book_state.best_bid and book_state.best_ask
            else book_state.mid_price
        )
        top_k_queue_depletion_rate = trade_total / max(total_depth, 1e-9)
        signed_trade_intensity = trade_imbalance * (trade_count / total_window_seconds)
        cancellation_burst_score = cancels / max(quote_updates, 1)
        spread_bps = (book_state.spread / book_state.mid_price) * 10_000.0 if book_state.mid_price > 0 else 0.0
        spread_state = "tight" if spread_bps < 5.0 else "wide" if spread_bps > 20.0 else "normal"
        volatility_regime = "low" if short_term_vol < 0.0005 else "high" if short_term_vol > 0.002 else "normal"
        quote_age_ns = max(book_state.timestamp_ns - self.last_quote_change_ns, 0)
        stale_book_indicator = 1.0 if quote_age_ns > (self.window_ns // 4) else 0.0
        short_horizon_realized_impact = trade_total * mid_move

        return MicrostructureFeatureSnapshot(
            timestamp_ns=book_state.timestamp_ns,
            symbol=book_state.symbol,
            spread=book_state.spread,
            mid_price=book_state.mid_price,
            mid_price_movement=mid_move,
            order_book_imbalance=book_state.order_book_imbalance,
            queue_imbalance=book_state.order_book_imbalance,
            trade_imbalance=trade_imbalance,
            short_term_volatility=short_term_vol,
            quote_update_rate=quote_updates / total_window_seconds,
            cancel_rate=cancels / total_window_seconds,
            trade_arrival_rate=trade_count / total_window_seconds,
            depth_imbalance=depth_imbalance,
            price_impact_estimate=price_impact,
            adverse_selection_estimate=adverse_selection,
            metadata={
                "recent_trade_volume": trade_total,
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
            },
            microprice=microprice,
            top_k_queue_depletion_rate=top_k_queue_depletion_rate,
            signed_trade_intensity=signed_trade_intensity,
            cancellation_burst_score=cancellation_burst_score,
            spread_state=spread_state,
            volatility_regime=volatility_regime,
            quote_age_ns=quote_age_ns,
            stale_book_indicator=stale_book_indicator,
            short_horizon_realized_impact=short_horizon_realized_impact,
        )

    def _trim(self, timestamp_ns: int) -> None:
        floor = int(timestamp_ns) - self.window_ns
        while self.mid_history and self.mid_history[0][0] < floor:
            self.mid_history.popleft()
        while self.event_history and self.event_history[0].receive_ts_ns < floor:
            self.event_history.popleft()
