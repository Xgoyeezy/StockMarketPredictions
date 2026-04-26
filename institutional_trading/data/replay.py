from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from institutional_trading.models import DataGranularity, MarketRecord, Session
def write_market_replay(path: str | Path, records: list[MarketRecord]) -> Path:
    out = Path(path); out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as h:
        for r in sorted(records, key=lambda x: (x.timestamp, x.sequence, x.symbol)): h.write(json.dumps(r.to_record(), sort_keys=True, separators=(",", ":")) + "\n")
    return out
def read_market_replay(path: str | Path) -> list[MarketRecord]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as h:
        for line in h:
            if not line.strip(): continue
            x = json.loads(line); rows.append(MarketRecord(x["symbol"], datetime.fromisoformat(x["timestamp"]), x["source"], DataGranularity(x["granularity"]), Session(x["session"]), open=x.get("open"), high=x.get("high"), low=x.get("low"), close=x.get("close"), last=x.get("last"), bid=x.get("bid"), ask=x.get("ask"), volume=int(x.get("volume",0)), trade_size=int(x.get("trade_size",0)), sequence=int(x.get("sequence",0)), metadata=dict(x.get("metadata") or {})))
    return rows
