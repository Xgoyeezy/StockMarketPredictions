from __future__ import annotations

import json
from pathlib import Path

from hft.market_data.schemas import MarketEvent

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None


class ReplayEventStream:
    def __init__(self, events: list[MarketEvent]):
        self.events = sorted(events)

    @classmethod
    def from_path(cls, path: str | Path) -> "ReplayEventStream":
        path = Path(path)
        if path.suffix == ".parquet":
            if pd is None:
                raise RuntimeError("pandas with parquet support is required to read parquet replay inputs")
            frame = pd.read_parquet(path)
            records = frame.to_dict(orient="records")
        else:
            with path.open("r", encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle if line.strip()]
        events = [
            MarketEvent(
                event_id=str(record["event_id"]),
                source=str(record["source"]),
                venue=str(record["venue"]),
                symbol=str(record["symbol"]),
                exchange_ts_ns=int(record["exchange_ts_ns"]),
                receive_ts_ns=int(record["receive_ts_ns"]),
                sequence=int(record["sequence"]),
                event_type=str(record["event_type"]),
                side=str(record.get("side") or "unknown"),
                price=float(record.get("price", 0.0)),
                size=float(record.get("size", 0.0)),
                level=int(record.get("level", 0)),
                order_id=record.get("order_id"),
                trade_id=record.get("trade_id"),
                metadata=dict(record.get("metadata") or {}),
            )
            for record in records
        ]
        return cls(events)

    def __iter__(self):
        return iter(self.events)
