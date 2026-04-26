from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any

from hft.live.http import parse_timestamp_ns, request_json
from hft.market_data.schemas import MarketEvent
from hft.market_data.sessions import classify_us_equity_session_ns
from hft.utils.env import bool_from_value, merged_env
from hft.utils.ids import DeterministicIdGenerator


@dataclass(frozen=True)
class FeedHealthSnapshot:
    ok: bool
    provider: str
    feed: str
    status_code: int | None
    symbol_count: int
    message: str
    metadata: dict[str, Any]


class LiveMarketDataAdapter(ABC):
    @abstractmethod
    def check_feed(self, symbols: list[str]) -> FeedHealthSnapshot:
        raise NotImplementedError

    @abstractmethod
    def poll(self, symbols: list[str]) -> list[MarketEvent]:
        raise NotImplementedError


@dataclass
class AlpacaEquityMarketDataAdapter(LiveMarketDataAdapter):
    api_key_id: str
    api_secret_key: str
    base_url: str = "https://data.alpaca.markets"
    feed: str = "iex"
    request_timeout_seconds: int = 10
    source_name: str = "alpaca_equities_live"

    def __post_init__(self) -> None:
        self._ids = DeterministicIdGenerator(prefix="live")
        self._sequence_by_symbol: dict[str, int] = {}

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "AlpacaEquityMarketDataAdapter":
        env = merged_env(env_file)
        use_sandbox = bool_from_value(env.get("ALPACA_USE_SANDBOX"), False)
        api_key_id = str(env.get("APCA_API_KEY_ID", env.get("ALPACA_API_KEY_ID", "")) or "").strip()
        api_secret_key = str(env.get("APCA_API_SECRET_KEY", env.get("ALPACA_API_SECRET_KEY", "")) or "").strip()
        base_url = str(
            env.get(
                "ALPACA_EQUITY_DATA_BASE_URL",
                "https://data.sandbox.alpaca.markets" if use_sandbox else "https://data.alpaca.markets",
            )
            or ""
        ).strip()
        feed = str(env.get("ALPACA_EQUITIES_FEED", env.get("ALPACA_STOCK_FEED", "iex")) or "iex").strip().lower()
        timeout_seconds = int(env.get("ALPACA_MARKET_DATA_REQUEST_TIMEOUT_SECONDS", "10") or 10)
        return cls(
            api_key_id=api_key_id,
            api_secret_key=api_secret_key,
            base_url=base_url.rstrip("/"),
            feed=feed,
            request_timeout_seconds=timeout_seconds,
        )

    def check_feed(self, symbols: list[str]) -> FeedHealthSnapshot:
        response = self._request_snapshots(symbols)
        payload = response.payload if isinstance(response.payload, dict) else {}
        snapshots = payload.get("snapshots") if isinstance(payload.get("snapshots"), dict) else payload
        count = len(snapshots or {})
        return FeedHealthSnapshot(
            ok=response.status_code == 200 and count > 0,
            provider="alpaca",
            feed=self.feed,
            status_code=response.status_code,
            symbol_count=count,
            message=response.message or ("ok" if response.status_code == 200 else "feed unavailable"),
            metadata={"base_url": self.base_url, "reachable": response.reachable},
        )

    def poll(self, symbols: list[str]) -> list[MarketEvent]:
        response = self._request_snapshots(symbols)
        if response.status_code != 200 or not isinstance(response.payload, dict):
            message = response.message or "Alpaca equities feed is unavailable."
            raise RuntimeError(message)
        fetched_at_ns = time.time_ns()
        snapshots = dict(response.payload.get("snapshots") or {})
        return self._parse_snapshot_payload(snapshots, fetched_at_ns=fetched_at_ns)

    def _request_snapshots(self, symbols: list[str]):
        headers = {
            "APCA-API-KEY-ID": self.api_key_id,
            "APCA-API-SECRET-KEY": self.api_secret_key,
            "Accept": "application/json",
            "User-Agent": "hft-system/equities-live-data",
        }
        return request_json(
            f"{self.base_url}/v2/stocks/snapshots",
            headers=headers,
            params={"symbols": ",".join(sorted({symbol.upper() for symbol in symbols})), "feed": self.feed},
            timeout_seconds=self.request_timeout_seconds,
        )

    def _parse_snapshot_payload(self, snapshots: dict[str, Any], *, fetched_at_ns: int) -> list[MarketEvent]:
        events: list[MarketEvent] = []
        for symbol, snapshot in snapshots.items():
            quote = dict((snapshot or {}).get("latestQuote") or {})
            trade = dict((snapshot or {}).get("latestTrade") or {})
            bid_price = float(quote.get("bp") or 0.0)
            ask_price = float(quote.get("ap") or 0.0)
            bid_size = float(quote.get("bs") or 0.0)
            ask_size = float(quote.get("as") or 0.0)
            quote_ts = parse_timestamp_ns(quote.get("t"), fallback_ns=fetched_at_ns)
            quote_receive_ts = max(fetched_at_ns, quote_ts)
            session = classify_us_equity_session_ns(quote_ts)
            if bid_price > 0:
                events.append(
                    MarketEvent(
                        exchange_ts_ns=quote_ts,
                        receive_ts_ns=quote_receive_ts,
                        sequence=self._next_sequence(symbol),
                        event_id=self._ids.next("quote"),
                        source=self.source_name,
                        venue=str(quote.get("bx") or "ALPACA"),
                        symbol=symbol,
                        event_type="bbo",
                        side="buy",
                        price=bid_price,
                        size=bid_size,
                        metadata={"feed": self.feed, "quote_type": "bid", "provider": "alpaca"},
                        session=session,
                    )
                )
            if ask_price > 0:
                events.append(
                    MarketEvent(
                        exchange_ts_ns=quote_ts,
                        receive_ts_ns=quote_receive_ts,
                        sequence=self._next_sequence(symbol),
                        event_id=self._ids.next("quote"),
                        source=self.source_name,
                        venue=str(quote.get("ax") or "ALPACA"),
                        symbol=symbol,
                        event_type="bbo",
                        side="sell",
                        price=ask_price,
                        size=ask_size,
                        metadata={"feed": self.feed, "quote_type": "ask", "provider": "alpaca"},
                        session=session,
                    )
                )
            trade_price = float(trade.get("p") or 0.0)
            trade_size = float(trade.get("s") or 0.0)
            if trade_price > 0 and trade_size > 0:
                trade_ts = parse_timestamp_ns(trade.get("t"), fallback_ns=fetched_at_ns)
                trade_receive_ts = max(fetched_at_ns, trade_ts)
                events.append(
                    MarketEvent(
                        exchange_ts_ns=trade_ts,
                        receive_ts_ns=trade_receive_ts,
                        sequence=self._next_sequence(symbol),
                        event_id=self._ids.next("trade"),
                        source=self.source_name,
                        venue=str(trade.get("x") or quote.get("bx") or quote.get("ax") or "ALPACA"),
                        symbol=symbol,
                        event_type="trade",
                        side=self._infer_trade_side(trade_price=trade_price, bid_price=bid_price, ask_price=ask_price),
                        price=trade_price,
                        size=trade_size,
                        trade_id=str(trade.get("i") or self._ids.next("provider_trade")),
                        metadata={"feed": self.feed, "provider": "alpaca"},
                        session=classify_us_equity_session_ns(trade_ts),
                    )
                )
        return sorted(events)

    def _next_sequence(self, symbol: str) -> int:
        normalized = str(symbol).upper()
        current = self._sequence_by_symbol.get(normalized, 0) + 1
        self._sequence_by_symbol[normalized] = current
        return current

    @staticmethod
    def _infer_trade_side(*, trade_price: float, bid_price: float, ask_price: float) -> str:
        if ask_price > 0 and trade_price >= ask_price:
            return "buy"
        if bid_price > 0 and trade_price <= bid_price:
            return "sell"
        midpoint = ((bid_price + ask_price) / 2.0) if bid_price > 0 and ask_price > 0 else trade_price
        return "buy" if trade_price >= midpoint else "sell"

    def to_dict(self) -> dict[str, Any]:
        return asdict(
            FeedHealthSnapshot(
                ok=True,
                provider="alpaca",
                feed=self.feed,
                status_code=200,
                symbol_count=0,
                message="configured",
                metadata={"base_url": self.base_url},
            )
        )
