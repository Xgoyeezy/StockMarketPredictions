from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from hft.market_data.schemas import MarketEvent
from hft.optimization.types import SessionManifest
from hft.order_book.book import OrderBook


def build_session_manifest(session_id: str, events: list[MarketEvent], *, source_path: str | None = None) -> SessionManifest:
    if not events:
        raise ValueError("Session manifest requires at least one event.")
    ordered = sorted(events)
    symbol = ordered[0].symbol
    book = OrderBook(symbol)
    spread_bps_samples: list[float] = []
    mids: list[float] = []
    trade_volume = 0.0
    for event in ordered:
        book.apply_event(event)
        snapshot = book.snapshot()
        if snapshot.mid_price > 0 and snapshot.spread > 0:
            spread_bps_samples.append((snapshot.spread / snapshot.mid_price) * 10_000.0)
        if snapshot.mid_price > 0:
            mids.append(snapshot.mid_price)
        if event.event_type == "trade":
            trade_volume += event.size
    average_spread_bps = sum(spread_bps_samples) / len(spread_bps_samples) if spread_bps_samples else 0.0
    volatility = 0.0
    if len(mids) > 1:
        deltas = [abs(curr - prev) / prev for prev, curr in zip(mids, mids[1:]) if prev > 0]
        volatility = sum(deltas) / len(deltas) if deltas else 0.0
    volatility_regime = "low" if volatility < 0.0005 else "high" if volatility > 0.002 else "normal"
    liquidity_regime = "low" if trade_volume < 100 else "high" if trade_volume > 500 else "normal"
    trading_day = datetime.fromtimestamp(ordered[0].exchange_ts_ns / 1_000_000_000.0, tz=timezone.utc).strftime("%Y-%m-%d")
    return SessionManifest(
        session_id=session_id,
        symbol=symbol,
        trading_day=trading_day,
        event_count=len(ordered),
        start_ts_ns=ordered[0].exchange_ts_ns,
        end_ts_ns=ordered[-1].exchange_ts_ns,
        average_spread_bps=average_spread_bps,
        volatility_regime=volatility_regime,
        liquidity_regime=liquidity_regime,
        source_path=source_path,
    )


def build_manifests_from_sessions(session_events: dict[str, list[MarketEvent]]) -> list[SessionManifest]:
    manifests = [build_session_manifest(session_id, events) for session_id, events in session_events.items()]
    return sorted(manifests, key=lambda item: (item.trading_day, item.session_id))


def discover_session_files(base_dir: str | Path) -> list[Path]:
    base = Path(base_dir)
    return sorted(base.rglob("*.jsonl")) + sorted(base.rglob("*.parquet"))


def group_events_by_day(events: Iterable[MarketEvent]) -> dict[str, list[MarketEvent]]:
    grouped: dict[str, list[MarketEvent]] = defaultdict(list)
    for event in sorted(events):
        trading_day = datetime.fromtimestamp(event.exchange_ts_ns / 1_000_000_000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        grouped[f"{event.symbol}-{trading_day}"].append(event)
    return dict(grouped)
