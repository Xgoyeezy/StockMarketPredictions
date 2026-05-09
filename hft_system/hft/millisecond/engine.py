from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from hft.live.paper_execution import PaperOrderIntent
from hft.market_data.schemas import MarketEvent


@dataclass(frozen=True)
class MillisecondEngineConfig:
    enabled: bool = True
    strategy_name: str = "millisecond_micro_scalper"
    order_quantity: float = 1.0
    min_edge_bps: float = 1.5
    max_spread_bps: float = 8.0
    max_quote_age_ns: int = 500_000_000
    max_decision_latency_ns: int = 2_000_000
    min_order_interval_ms: int = 250
    max_orders_per_second: int = 2
    allowed_sessions: tuple[str, ...] = ("regular",)
    price_offset_bps: float = 0.5


@dataclass
class TopOfBookState:
    symbol: str
    bid_price: float = 0.0
    bid_size: float = 0.0
    ask_price: float = 0.0
    ask_size: float = 0.0
    last_bid_ts_ns: int = 0
    last_ask_ts_ns: int = 0
    last_receive_ts_ns: int = 0

    @property
    def ready(self) -> bool:
        return self.bid_price > 0 and self.ask_price > 0 and self.ask_price > self.bid_price

    @property
    def mid_price(self) -> float:
        return (self.bid_price + self.ask_price) / 2.0 if self.ready else 0.0

    @property
    def spread(self) -> float:
        return max(self.ask_price - self.bid_price, 0.0) if self.ready else 0.0

    @property
    def spread_bps(self) -> float:
        return (self.spread / self.mid_price * 10_000.0) if self.mid_price > 0 else 0.0

    @property
    def microprice(self) -> float:
        depth = self.bid_size + self.ask_size
        if not self.ready or depth <= 0:
            return self.mid_price
        return ((self.ask_price * self.bid_size) + (self.bid_price * self.ask_size)) / depth

    @property
    def imbalance(self) -> float:
        depth = self.bid_size + self.ask_size
        return (self.bid_size - self.ask_size) / depth if depth > 0 else 0.0


@dataclass(frozen=True)
class MillisecondDecision:
    symbol: str
    action: str
    side: str | None
    price: float
    quantity: float
    edge_bps: float
    reason: str
    decision_ts_ns: int
    input_receive_ts_ns: int
    decision_latency_ns: int
    blocked: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    def to_paper_intent(self, *, order_id: str, strategy_name: str) -> PaperOrderIntent:
        return PaperOrderIntent(
            order_id=order_id,
            symbol=self.symbol,
            strategy_name=strategy_name,
            side=str(self.side or "").lower(),
            price=float(self.price),
            quantity=float(self.quantity),
            decision_timestamp=int(self.decision_ts_ns),
            quote_width=float(self.metadata.get("spread") or 0.0),
            metadata={**dict(self.metadata), "engine": "millisecond"},
            risk_checked=False,
            risk_reason="unvalidated",
            session=str(self.metadata.get("session") or "regular"),
            order_type="limit",
        )


class MillisecondThrottle:
    def __init__(self, *, min_order_interval_ms: int, max_orders_per_second: int):
        self.min_interval_ns = max(int(min_order_interval_ms), 0) * 1_000_000
        self.max_orders_per_second = max(int(max_orders_per_second), 0)
        self._last_order_by_symbol: dict[str, int] = {}
        self._recent_orders: deque[int] = deque()

    def allow(self, *, symbol: str, now_ns: int) -> tuple[bool, str]:
        normalized = symbol.upper()
        last_for_symbol = self._last_order_by_symbol.get(normalized, 0)
        if last_for_symbol and now_ns - last_for_symbol < self.min_interval_ns:
            return False, "symbol_order_interval"
        floor = now_ns - 1_000_000_000
        while self._recent_orders and self._recent_orders[0] < floor:
            self._recent_orders.popleft()
        if self.max_orders_per_second and len(self._recent_orders) >= self.max_orders_per_second:
            return False, "global_order_rate"
        return True, "allowed"

    def record(self, *, symbol: str, now_ns: int) -> None:
        normalized = symbol.upper()
        self._last_order_by_symbol[normalized] = int(now_ns)
        self._recent_orders.append(int(now_ns))


class MillisecondSignalEngine:
    def __init__(self, *, config: MillisecondEngineConfig | None = None):
        self.config = config or MillisecondEngineConfig()
        self.books: dict[str, TopOfBookState] = {}
        self.throttle = MillisecondThrottle(
            min_order_interval_ms=self.config.min_order_interval_ms,
            max_orders_per_second=self.config.max_orders_per_second,
        )
        self.decisions: list[MillisecondDecision] = []

    def process_event(self, event: MarketEvent) -> MillisecondDecision:
        started_ns = time.perf_counter_ns()
        book = self._update_book(event)
        decision_ts_ns = time.time_ns()
        decision = self._decide(book, event=event, decision_ts_ns=decision_ts_ns, started_ns=started_ns)
        self.decisions.append(decision)
        if decision.action == "submit":
            self.throttle.record(symbol=decision.symbol, now_ns=decision.decision_ts_ns)
        return decision

    def _update_book(self, event: MarketEvent) -> TopOfBookState:
        symbol = event.symbol.upper()
        book = self.books.setdefault(symbol, TopOfBookState(symbol=symbol))
        if event.event_type == "bbo" and event.side == "buy":
            book.bid_price = float(event.price)
            book.bid_size = float(event.size)
            book.last_bid_ts_ns = int(event.receive_ts_ns)
        elif event.event_type == "bbo" and event.side == "sell":
            book.ask_price = float(event.price)
            book.ask_size = float(event.size)
            book.last_ask_ts_ns = int(event.receive_ts_ns)
        book.last_receive_ts_ns = max(book.last_receive_ts_ns, int(event.receive_ts_ns))
        return book

    def _decide(
        self,
        book: TopOfBookState,
        *,
        event: MarketEvent,
        decision_ts_ns: int,
        started_ns: int,
    ) -> MillisecondDecision:
        latency_ns = max(time.perf_counter_ns() - started_ns, 0)
        metadata = {
            "bid": book.bid_price,
            "ask": book.ask_price,
            "mid": book.mid_price,
            "microprice": book.microprice,
            "spread": book.spread,
            "spread_bps": book.spread_bps,
            "imbalance": book.imbalance,
            "session": event.session,
            "event_type": event.event_type,
        }
        if not self.config.enabled:
            return self._stand_down(book, event, decision_ts_ns, latency_ns, "engine_disabled", metadata)
        if event.session not in set(self.config.allowed_sessions):
            return self._stand_down(book, event, decision_ts_ns, latency_ns, f"session:{event.session}", metadata)
        if not book.ready:
            return self._stand_down(book, event, decision_ts_ns, latency_ns, "waiting_top_of_book", metadata)
        quote_age_ns = max(decision_ts_ns - min(book.last_bid_ts_ns, book.last_ask_ts_ns), 0)
        metadata["quote_age_ns"] = quote_age_ns
        if quote_age_ns > self.config.max_quote_age_ns:
            return self._stand_down(book, event, decision_ts_ns, latency_ns, "stale_quote", metadata)
        if latency_ns > self.config.max_decision_latency_ns:
            return self._stand_down(book, event, decision_ts_ns, latency_ns, "decision_latency_cap", metadata)
        if book.spread_bps > self.config.max_spread_bps:
            return self._stand_down(book, event, decision_ts_ns, latency_ns, "spread_too_wide", metadata)

        buy_edge_bps = ((book.microprice - book.mid_price) / book.mid_price * 10_000.0) if book.mid_price > 0 else 0.0
        sell_edge_bps = -buy_edge_bps
        if buy_edge_bps >= self.config.min_edge_bps:
            side = "buy"
            edge_bps = buy_edge_bps
            price = book.bid_price + (book.mid_price * self.config.price_offset_bps / 10_000.0)
        elif sell_edge_bps >= self.config.min_edge_bps:
            side = "sell"
            edge_bps = sell_edge_bps
            price = book.ask_price - (book.mid_price * self.config.price_offset_bps / 10_000.0)
        else:
            return self._stand_down(book, event, decision_ts_ns, latency_ns, "edge_below_threshold", metadata)

        allowed, reason = self.throttle.allow(symbol=book.symbol, now_ns=decision_ts_ns)
        if not allowed:
            return MillisecondDecision(
                symbol=book.symbol,
                action="blocked",
                side=side,
                price=round(float(price), 4),
                quantity=float(self.config.order_quantity),
                edge_bps=round(float(edge_bps), 4),
                reason=reason,
                decision_ts_ns=decision_ts_ns,
                input_receive_ts_ns=int(event.receive_ts_ns),
                decision_latency_ns=latency_ns,
                blocked=True,
                metadata=metadata,
            )
        return MillisecondDecision(
            symbol=book.symbol,
            action="submit",
            side=side,
            price=round(float(price), 4),
            quantity=float(self.config.order_quantity),
            edge_bps=round(float(edge_bps), 4),
            reason="edge_and_microstructure_passed",
            decision_ts_ns=decision_ts_ns,
            input_receive_ts_ns=int(event.receive_ts_ns),
            decision_latency_ns=latency_ns,
            metadata=metadata,
        )

    @staticmethod
    def _stand_down(
        book: TopOfBookState,
        event: MarketEvent,
        decision_ts_ns: int,
        latency_ns: int,
        reason: str,
        metadata: dict[str, Any],
    ) -> MillisecondDecision:
        return MillisecondDecision(
            symbol=book.symbol,
            action="stand_down",
            side=None,
            price=0.0,
            quantity=0.0,
            edge_bps=0.0,
            reason=reason,
            decision_ts_ns=decision_ts_ns,
            input_receive_ts_ns=int(event.receive_ts_ns),
            decision_latency_ns=latency_ns,
            metadata=metadata,
        )
