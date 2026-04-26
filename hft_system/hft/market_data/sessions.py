from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

MarketSession = Literal["pre_market", "regular", "after_hours", "closed"]

_EASTERN = ZoneInfo("America/New_York")
_MIN_REALISTIC_EPOCH_NS = 946_684_800_000_000_000


def classify_us_equity_session_ns(timestamp_ns: int) -> MarketSession:
    if int(timestamp_ns) < _MIN_REALISTIC_EPOCH_NS:
        return "regular"
    dt = datetime.fromtimestamp(int(timestamp_ns) / 1_000_000_000, tz=UTC).astimezone(_EASTERN)
    current = dt.time()
    if time(4, 0) <= current < time(9, 30):
        return "pre_market"
    if time(9, 30) <= current < time(16, 0):
        return "regular"
    if time(16, 0) <= current < time(20, 0):
        return "after_hours"
    return "closed"


def normalize_market_session(value: object, *, fallback_timestamp_ns: int | None = None) -> MarketSession:
    cleaned = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in {"premarket", "pre_market", "pre"}:
        return "pre_market"
    if cleaned in {"regular", "core", "rth"}:
        return "regular"
    if cleaned in {"after_hours", "afterhours", "post_market", "postmarket", "post"}:
        return "after_hours"
    if cleaned == "closed":
        return "closed"
    if fallback_timestamp_ns is not None:
        return classify_us_equity_session_ns(int(fallback_timestamp_ns))
    return "regular"


def is_extended_session(session: object) -> bool:
    return normalize_market_session(session) in {"pre_market", "after_hours"}
