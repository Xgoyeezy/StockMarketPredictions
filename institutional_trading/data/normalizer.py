from __future__ import annotations
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo
from institutional_trading.models import DataGranularity, MarketRecord, Session
EASTERN = ZoneInfo("America/New_York")
def classify_us_equity_session(timestamp: datetime) -> Session:
    local = timestamp.astimezone(EASTERN) if timestamp.tzinfo else timestamp.replace(tzinfo=UTC).astimezone(EASTERN); t = local.time()
    if time(4,0) <= t < time(9,30): return Session.PRE_MARKET
    if time(9,30) <= t < time(16,0): return Session.REGULAR
    if time(16,0) <= t < time(20,0): return Session.AFTER_HOURS
    return Session.CLOSED
class MarketDataNormalizer:
    def normalize_minute_bar(self, row: dict[str, Any], *, source: str) -> MarketRecord:
        ts = self._timestamp(row); return MarketRecord(str(row["symbol"]), ts, source, DataGranularity.MINUTE, classify_us_equity_session(ts), open=float(row["open"]), high=float(row["high"]), low=float(row["low"]), close=float(row["close"]), volume=int(row.get("volume",0)), sequence=int(row.get("sequence",0)), metadata=dict(row.get("metadata") or {}))
    def normalize_tick(self, row: dict[str, Any], *, source: str) -> MarketRecord:
        ts = self._timestamp(row); opt = lambda v: None if v is None else float(v); return MarketRecord(str(row["symbol"]), ts, source, DataGranularity.TICK, classify_us_equity_session(ts), last=opt(row.get("last")), bid=opt(row.get("bid")), ask=opt(row.get("ask")), volume=int(row.get("volume",0)), trade_size=int(row.get("trade_size", row.get("size",0))), sequence=int(row.get("sequence",0)), metadata=dict(row.get("metadata") or {}))
    def normalize(self, rows: list[dict[str, Any]], *, source: str, granularity: DataGranularity) -> list[MarketRecord]:
        out = [self.normalize_minute_bar(r, source=source) if granularity == DataGranularity.MINUTE else self.normalize_tick(r, source=source) for r in rows]; return sorted(out, key=lambda r: (r.timestamp, r.sequence, r.symbol))
    @staticmethod
    def _timestamp(row: dict[str, Any]) -> datetime:
        value = row.get("timestamp") or row.get("time") or row.get("ts")
        if isinstance(value, datetime): return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, (int,float)): return datetime.fromtimestamp(float(value), tz=UTC)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00")); return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        raise ValueError("Market data row requires timestamp/time/ts.")
