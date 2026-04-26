from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from hft.market_data.sessions import MarketSession, normalize_market_session


EventType = Literal["add", "cancel", "modify", "trade", "bbo", "l2_snapshot"]
Side = Literal["buy", "sell", "unknown"]


def _normalize_side(value: str | None) -> Side:
    cleaned = str(value or "").strip().lower()
    if cleaned in {"b", "bid", "buy"}:
        return "buy"
    if cleaned in {"s", "ask", "sell"}:
        return "sell"
    return "unknown"


@dataclass(frozen=True, order=True)
class MarketEvent:
    exchange_ts_ns: int
    receive_ts_ns: int
    sequence: int
    event_id: str
    source: str
    venue: str
    symbol: str
    event_type: EventType
    side: Side = "unknown"
    price: float = 0.0
    size: float = 0.0
    level: int = 0
    order_id: str | None = None
    trade_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)
    session: MarketSession = field(default="regular", compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).strip().upper())
        object.__setattr__(self, "source", str(self.source).strip().lower())
        object.__setattr__(self, "venue", str(self.venue).strip().upper())
        object.__setattr__(self, "event_type", str(self.event_type).strip().lower())
        object.__setattr__(self, "side", _normalize_side(self.side))
        metadata_session = self.metadata.get("session") if isinstance(self.metadata, dict) else None
        raw_session = metadata_session or ("" if self.session == "regular" else self.session)
        session = normalize_market_session(raw_session, fallback_timestamp_ns=int(self.exchange_ts_ns))
        object.__setattr__(self, "session", session)
        if isinstance(self.metadata, dict) and not self.metadata.get("session"):
            enriched_metadata = dict(self.metadata)
            enriched_metadata["session"] = session
            object.__setattr__(self, "metadata", enriched_metadata)
        if int(self.receive_ts_ns) < int(self.exchange_ts_ns):
            raise ValueError("receive_ts_ns must be greater than or equal to exchange_ts_ns")

    @property
    def latency_ns(self) -> int:
        return int(self.receive_ts_ns) - int(self.exchange_ts_ns)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class BookSnapshot:
    symbol: str
    timestamp_ns: int
    best_bid: float
    best_ask: float
    mid_price: float
    spread: float
    order_book_imbalance: float
    depth_by_level: dict[str, list[BookLevel]]
    recent_trade_flow: list[dict[str, Any]]


@dataclass(frozen=True)
class IngestionDiagnostics:
    event_count: int
    stale_events: int
    dropped_events: int
    max_latency_ns: int
    min_latency_ns: int
    average_latency_ns: float
