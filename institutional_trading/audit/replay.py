from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from institutional_trading.models import AuditEvent, ReplayEvent
def read_replay_events(path: str | Path) -> list[ReplayEvent]:
    out = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            rec = json.loads(line); e = rec["event"]; out.append(ReplayEvent(int(rec["sequence"]), str(rec["event_hash"]), AuditEvent(e["event_type"], e["actor"], dict(e.get("payload") or {}), datetime.fromisoformat(e["timestamp"]), e.get("account_id"), e.get("symbol"), e.get("order_id"))))
    return out
