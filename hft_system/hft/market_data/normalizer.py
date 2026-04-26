from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hft.market_data.schemas import MarketEvent

try:
    import pandas as pd
except Exception:  # pragma: no cover - optional at runtime
    pd = None


class EventNormalizer:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)

    def normalize_records(self, records: list[dict[str, Any]]) -> list[MarketEvent]:
        normalized = [
            MarketEvent(
                event_id=str(item["event_id"]),
                source=str(item["source"]),
                venue=str(item["venue"]),
                symbol=str(item["symbol"]),
                exchange_ts_ns=int(item["exchange_ts_ns"]),
                receive_ts_ns=int(item["receive_ts_ns"]),
                sequence=int(item["sequence"]),
                event_type=str(item["event_type"]),
                side=str(item.get("side") or "unknown"),
                price=float(item.get("price", 0.0)),
                size=float(item.get("size", 0.0)),
                level=int(item.get("level", 0)),
                order_id=item.get("order_id"),
                trade_id=item.get("trade_id"),
                metadata=dict(item.get("metadata") or {}),
                session=str(item.get("session") or (item.get("metadata") or {}).get("session") or ""),
            )
            for item in records
        ]
        return sorted(normalized)

    def write_dataset(
        self,
        *,
        dataset_kind: str,
        trading_date: str,
        symbol: str,
        events: list[MarketEvent],
    ) -> Path:
        base = self.base_dir / "normalized" / f"dataset={dataset_kind}" / f"date={trading_date}" / f"symbol={symbol.upper()}"
        base.mkdir(parents=True, exist_ok=True)
        parquet_path = base / "part-0000.parquet"
        records = [event.to_record() for event in events]
        if pd is not None:
            try:
                frame = pd.DataFrame.from_records(records)
                frame.to_parquet(parquet_path, index=False)
                return parquet_path
            except Exception:
                pass
        fallback_path = base / "part-0000.jsonl"
        with fallback_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        return fallback_path
