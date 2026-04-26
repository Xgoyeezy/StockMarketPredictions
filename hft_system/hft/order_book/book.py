from __future__ import annotations

from collections import deque
from dataclasses import asdict
from typing import Any

from hft.market_data.schemas import BookLevel, BookSnapshot, MarketEvent


class OrderBook:
    def __init__(self, symbol: str, *, max_trade_flow: int = 128):
        self.symbol = symbol.upper()
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.last_timestamp_ns = 0
        self.recent_trade_flow: deque[dict[str, Any]] = deque(maxlen=max_trade_flow)

    def apply_event(self, event: MarketEvent) -> None:
        if event.symbol != self.symbol:
            raise ValueError(f"OrderBook for {self.symbol} cannot ingest {event.symbol}")
        self.last_timestamp_ns = max(self.last_timestamp_ns, event.receive_ts_ns)
        if event.event_type == "add":
            self._set_level(event.side, event.price, self._level_size(event.side, event.price) + max(event.size, 0.0))
        elif event.event_type == "cancel":
            remaining = max(self._level_size(event.side, event.price) - max(event.size, 0.0), 0.0)
            self._set_level(event.side, event.price, remaining)
        elif event.event_type == "modify":
            self._set_level(event.side, event.price, max(event.size, 0.0))
        elif event.event_type == "trade":
            book_side = "sell" if event.side == "buy" else "buy"
            remaining = max(self._level_size(book_side, event.price) - max(event.size, 0.0), 0.0)
            self._set_level(book_side, event.price, remaining)
            self.recent_trade_flow.append(
                {
                    "timestamp_ns": event.receive_ts_ns,
                    "side": event.side,
                    "price": event.price,
                    "size": event.size,
                    "trade_id": event.trade_id,
                }
            )
        elif event.event_type == "bbo":
            if event.side == "buy":
                self._set_level("buy", event.price, max(event.size, 0.0))
            elif event.side == "sell":
                self._set_level("sell", event.price, max(event.size, 0.0))
        elif event.event_type == "l2_snapshot":
            self._set_level(event.side, event.price, max(event.size, 0.0))

    def get_top_of_book(self) -> tuple[float, float]:
        return self.best_bid, self.best_ask

    def get_depth(self, levels: int) -> dict[str, list[BookLevel]]:
        bid_prices = sorted(self.bids.keys(), reverse=True)[:levels]
        ask_prices = sorted(self.asks.keys())[:levels]
        return {
            "bids": [BookLevel(price=price, size=self.bids[price]) for price in bid_prices],
            "asks": [BookLevel(price=price, size=self.asks[price]) for price in ask_prices],
        }

    @property
    def best_bid(self) -> float:
        return max(self.bids) if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return min(self.asks) if self.asks else 0.0

    def get_mid_price(self) -> float:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2.0
        return self.best_bid or self.best_ask or 0.0

    def get_spread(self) -> float:
        if self.best_bid and self.best_ask:
            return max(self.best_ask - self.best_bid, 0.0)
        return 0.0

    def get_imbalance(self, levels: int = 3) -> float:
        depth = self.get_depth(levels)
        bid_depth = sum(level.size for level in depth["bids"])
        ask_depth = sum(level.size for level in depth["asks"])
        total = bid_depth + ask_depth
        if total <= 0:
            return 0.0
        return (bid_depth - ask_depth) / total

    def snapshot(self, levels: int = 5) -> BookSnapshot:
        depth = self.get_depth(levels)
        return BookSnapshot(
            symbol=self.symbol,
            timestamp_ns=self.last_timestamp_ns,
            best_bid=self.best_bid,
            best_ask=self.best_ask,
            mid_price=self.get_mid_price(),
            spread=self.get_spread(),
            order_book_imbalance=self.get_imbalance(levels=levels),
            depth_by_level=depth,
            recent_trade_flow=list(self.recent_trade_flow),
        )

    def _side_store(self, side: str) -> dict[float, float]:
        normalized = str(side).strip().lower()
        if normalized == "buy":
            return self.bids
        if normalized == "sell":
            return self.asks
        raise ValueError(f"Unsupported side {side}")

    def _level_size(self, side: str, price: float) -> float:
        return float(self._side_store(side).get(float(price), 0.0))

    def _set_level(self, side: str, price: float, size: float) -> None:
        store = self._side_store(side)
        price = float(price)
        size = float(size)
        if size <= 0:
            store.pop(price, None)
        else:
            store[price] = size
