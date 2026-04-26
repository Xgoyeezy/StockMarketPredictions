
from __future__ import annotations
import hashlib, json, sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from institutional_trading.models import AuditEvent
GENESIS_HASH = "0" * 64
def canonical_json(record: dict[str, Any]) -> str: return json.dumps(record, sort_keys=True, separators=(",", ":"))
@dataclass
class HashChainedAuditLogger:
    log_path: str | Path; sqlite_path: str | Path | None = None; _sequence: int = field(init=False, default=0); _last_hash: str = field(init=False, default=GENESIS_HASH)
    def __post_init__(self) -> None:
        self.log_path = Path(self.log_path); self.log_path.parent.mkdir(parents=True, exist_ok=True); self.sqlite_path = Path(self.sqlite_path) if self.sqlite_path else self.log_path.with_suffix(".sqlite3"); self.sqlite_path.parent.mkdir(parents=True, exist_ok=True); self._init_index(); self._load_tail()
    def append(self, event: AuditEvent) -> dict[str, Any]:
        base = {"sequence": self._sequence + 1, "prev_hash": self._last_hash, "event": event.to_record()}; h = hashlib.sha256(canonical_json(base).encode()).hexdigest(); rec = {**base, "event_hash": h}
        with self.log_path.open("a", encoding="utf-8") as f: f.write(canonical_json(rec) + "\n")
        self._sequence += 1; self._last_hash = h; self._index(rec); return rec
    def verify_chain(self) -> bool:
        prev = GENESIS_HASH; seq = 1
        with self.log_path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                rec = json.loads(line)
                if rec.get("sequence") != seq or rec.get("prev_hash") != prev: return False
                expected = dict(rec); event_hash = expected.pop("event_hash", None)
                if hashlib.sha256(canonical_json(expected).encode()).hexdigest() != event_hash: return False
                prev = event_hash; seq += 1
        return True
    def events_by_account(self, account_id: str) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.sqlite_path)
        try:
            rows = conn.execute("select raw_json from audit_events where account_id = ? order by sequence", [account_id]).fetchall()
        finally:
            conn.close()
        return [json.loads(r[0]) for r in rows]
    def _init_index(self) -> None:
        conn = sqlite3.connect(self.sqlite_path)
        try:
            conn.execute("create table if not exists audit_events (sequence integer primary key, event_hash text not null unique, timestamp text not null, event_type text not null, account_id text, symbol text, order_id text, raw_json text not null)")
            conn.execute("create index if not exists audit_account_idx on audit_events(account_id, sequence)")
            conn.execute("create index if not exists audit_order_idx on audit_events(order_id, sequence)")
            conn.commit()
        finally:
            conn.close()
    def _load_tail(self) -> None:
        if not self.log_path.exists(): return
        with self.log_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip(): rec = json.loads(line); self._sequence = int(rec["sequence"]); self._last_hash = str(rec["event_hash"])
    def _index(self, rec: dict[str, Any]) -> None:
        e = rec["event"]
        conn = sqlite3.connect(self.sqlite_path)
        try:
            conn.execute("insert or replace into audit_events (sequence,event_hash,timestamp,event_type,account_id,symbol,order_id,raw_json) values (?,?,?,?,?,?,?,?)", [rec["sequence"], rec["event_hash"], e["timestamp"], e["event_type"], e.get("account_id"), e.get("symbol"), e.get("order_id"), canonical_json(rec)])
            conn.commit()
        finally:
            conn.close()
