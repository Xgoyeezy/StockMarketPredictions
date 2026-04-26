from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from hft.market_data.schemas import IngestionDiagnostics, MarketEvent
from hft.utils.ids import DeterministicIdGenerator


class RawEventIngestor:
    def __init__(self, base_dir: str | Path, *, stale_after_ns: int = 2_000_000_000):
        self.base_dir = Path(base_dir)
        self.stale_after_ns = int(stale_after_ns)
        self.ids = DeterministicIdGenerator(prefix="raw")

    def ingest(self, *, source: str, trading_date: str, symbol: str, rows: Iterable[dict[str, Any]]) -> tuple[list[MarketEvent], IngestionDiagnostics, Path]:
        symbol_path = self.base_dir / "raw" / str(source).lower() / trading_date
        symbol_path.mkdir(parents=True, exist_ok=True)
        output_path = symbol_path / f"{symbol.upper()}.jsonl"

        events: list[MarketEvent] = []
        stale_events = 0
        max_latency = 0
        min_latency = 0
        latency_total = 0
        previous_key: tuple[int, int, int] | None = None
        dropped_events = 0

        with output_path.open("a", encoding="utf-8") as handle:
            for offset, row in enumerate(rows, start=1):
                event = MarketEvent(
                    event_id=str(row.get("event_id") or self.ids.next("event")),
                    source=str(row.get("source") or source),
                    venue=str(row.get("venue") or "SIM"),
                    symbol=str(row.get("symbol") or symbol),
                    exchange_ts_ns=int(row["exchange_ts_ns"]),
                    receive_ts_ns=int(row.get("receive_ts_ns", row["exchange_ts_ns"])),
                    sequence=int(row.get("sequence", offset)),
                    event_type=str(row["event_type"]),
                    side=str(row.get("side") or "unknown"),
                    price=float(row.get("price", 0.0)),
                    size=float(row.get("size", 0.0)),
                    level=int(row.get("level", 0)),
                    order_id=row.get("order_id"),
                    trade_id=row.get("trade_id"),
                    metadata=dict(row.get("metadata") or {}),
                    session=str(row.get("session") or (row.get("metadata") or {}).get("session") or ""),
                )
                current_key = (event.exchange_ts_ns, event.receive_ts_ns, event.sequence)
                if previous_key and current_key < previous_key:
                    dropped_events += 1
                previous_key = current_key
                if event.latency_ns > self.stale_after_ns:
                    stale_events += 1
                max_latency = max(max_latency, event.latency_ns)
                min_latency = event.latency_ns if not events else min(min_latency, event.latency_ns)
                latency_total += event.latency_ns
                handle.write(json.dumps(event.to_record(), sort_keys=True) + "\n")
                events.append(event)

        average_latency = latency_total / len(events) if events else 0.0
        diagnostics = IngestionDiagnostics(
            event_count=len(events),
            stale_events=stale_events,
            dropped_events=dropped_events,
            max_latency_ns=max_latency,
            min_latency_ns=min_latency,
            average_latency_ns=average_latency,
        )
        return events, diagnostics, output_path
