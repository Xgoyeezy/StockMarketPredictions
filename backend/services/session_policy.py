from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

TradingSessionMode = Literal["pre_market", "regular", "after_hours", "closed"]

EASTERN_MARKET_TIMEZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SessionProfile:
    mode: TradingSessionMode
    label: str
    feed_expected: bool
    equity_entries_allowed: bool
    listed_option_entries_allowed: bool
    extended_hours: bool
    force_limit_orders: bool
    time_in_force: str
    risk_multiplier: float
    size_cap_ratio: float
    min_edge_to_cost_ratio: float
    max_spread_bps: float
    min_cooldown_minutes: int
    max_daily_entries: int | None
    detail: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def normalize_session_mode(value: object) -> TradingSessionMode:
    cleaned = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in {"premarket", "pre_market", "pre"}:
        return "pre_market"
    if cleaned in {"regular", "core", "rth", "regular_session", "opening_range", "morning_session", "midday", "afternoon_session", "power_hour", "closing_window", "close_cleanup"}:
        return "regular"
    if cleaned in {"after_hours", "afterhours", "post_market", "postmarket", "post"}:
        return "after_hours"
    return "closed"


def classify_us_equity_session(now_utc: datetime | None = None) -> TradingSessionMode:
    current_utc = now_utc or datetime.now(timezone.utc)
    current_et = current_utc.astimezone(EASTERN_MARKET_TIMEZONE)
    if current_et.weekday() >= 5:
        return "closed"
    current = current_et.time()
    if time(4, 0) <= current < time(9, 30):
        return "pre_market"
    if time(9, 30) <= current < time(16, 0):
        return "regular"
    if time(16, 0) <= current < time(20, 0):
        return "after_hours"
    return "closed"


def build_market_session_context(now_utc: datetime | None = None) -> dict[str, Any]:
    current_utc = now_utc or datetime.now(timezone.utc)
    current_et = current_utc.astimezone(EASTERN_MARKET_TIMEZONE)
    mode = classify_us_equity_session(current_utc)
    if current_et.weekday() >= 5:
        legacy_session = "weekend"
        label = "Weekend"
    elif mode == "pre_market":
        legacy_session = "premarket"
        label = "Premarket"
    elif mode == "regular":
        legacy_session = "regular"
        label = "Regular"
    elif mode == "after_hours":
        legacy_session = "after_hours"
        label = "After hours"
    else:
        legacy_session = "closed"
        label = "Closed"
    profile = get_session_profile(mode, instrument_type="equity", regular_hours_only=False)
    return {
        "feed_expected": bool(profile.feed_expected),
        "session": legacy_session,
        "session_mode": mode,
        "label": label,
        "checked_at_et": current_et.isoformat(),
        "now_et": current_et,
    }


def get_session_profile(
    mode: object,
    *,
    instrument_type: str = "equity",
    regular_hours_only: bool = False,
) -> SessionProfile:
    normalized_mode = normalize_session_mode(mode)
    normalized_instrument = str(instrument_type or "equity").strip().lower()
    regular_only = bool(regular_hours_only)
    option_path = normalized_instrument == "listed_option"

    if option_path:
        return SessionProfile(
            mode=normalized_mode,
            label="Listed-option regular session",
            feed_expected=normalized_mode in {"pre_market", "regular", "after_hours"},
            equity_entries_allowed=False,
            listed_option_entries_allowed=normalized_mode == "regular",
            extended_hours=False,
            force_limit_orders=True,
            time_in_force="day",
            risk_multiplier=1.0 if normalized_mode == "regular" else 0.0,
            size_cap_ratio=1.0 if normalized_mode == "regular" else 0.0,
            min_edge_to_cost_ratio=2.5,
            max_spread_bps=25.0,
            min_cooldown_minutes=20,
            max_daily_entries=None,
            detail="Listed options remain regular-session only; extended-hours equity routing does not apply.",
        )

    if regular_only and normalized_mode != "regular":
        return SessionProfile(
            mode=normalized_mode,
            label="Regular-hours only",
            feed_expected=False,
            equity_entries_allowed=False,
            listed_option_entries_allowed=False,
            extended_hours=False,
            force_limit_orders=True,
            time_in_force="day",
            risk_multiplier=0.0,
            size_cap_ratio=0.0,
            min_edge_to_cost_ratio=2.5,
            max_spread_bps=25.0,
            min_cooldown_minutes=20,
            max_daily_entries=0,
            detail="Regular-hours-only mode is waiting for the next core session.",
        )

    if normalized_mode == "pre_market":
        return SessionProfile(
            mode="pre_market",
            label="Pre-market equity mode",
            feed_expected=True,
            equity_entries_allowed=True,
            listed_option_entries_allowed=False,
            extended_hours=True,
            force_limit_orders=True,
            time_in_force="day_ext",
            risk_multiplier=0.5,
            size_cap_ratio=0.35,
            min_edge_to_cost_ratio=4.0,
            max_spread_bps=12.5,
            min_cooldown_minutes=45,
            max_daily_entries=2,
            detail="Pre-market equities can route with limit DAY_EXT orders only, smaller size, and stricter edge/liquidity checks.",
        )
    if normalized_mode == "after_hours":
        return SessionProfile(
            mode="after_hours",
            label="After-hours equity mode",
            feed_expected=True,
            equity_entries_allowed=True,
            listed_option_entries_allowed=False,
            extended_hours=True,
            force_limit_orders=True,
            time_in_force="day_ext",
            risk_multiplier=0.35,
            size_cap_ratio=0.25,
            min_edge_to_cost_ratio=5.0,
            max_spread_bps=10.0,
            min_cooldown_minutes=60,
            max_daily_entries=1,
            detail="After-hours equities can route with limit DAY_EXT orders only and the most conservative size and liquidity settings.",
        )
    if normalized_mode == "regular":
        return SessionProfile(
            mode="regular",
            label="Regular equity mode",
            feed_expected=True,
            equity_entries_allowed=True,
            listed_option_entries_allowed=True,
            extended_hours=False,
            force_limit_orders=False,
            time_in_force="day",
            risk_multiplier=1.0,
            size_cap_ratio=1.0,
            min_edge_to_cost_ratio=2.5,
            max_spread_bps=25.0,
            min_cooldown_minutes=20,
            max_daily_entries=None,
            detail="Regular-session equity routing uses the normal risk envelope.",
        )
    return SessionProfile(
        mode="closed",
        label="Closed-session planning",
        feed_expected=False,
        equity_entries_allowed=False,
        listed_option_entries_allowed=False,
        extended_hours=False,
        force_limit_orders=True,
        time_in_force="day_ext",
        risk_multiplier=0.0,
        size_cap_ratio=0.0,
        min_edge_to_cost_ratio=5.0,
        max_spread_bps=10.0,
        min_cooldown_minutes=60,
        max_daily_entries=0,
        detail="Closed-session mode keeps monitoring and planning alive while blocking new entries.",
    )
