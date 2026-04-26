from __future__ import annotations

import math
import time
from datetime import datetime, time as clock_time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from backend.services.market_data import get_intraday_bar_provider, get_intraday_fallback_provider

EASTERN_TIME = ZoneInfo("America/New_York")
SESSION_OPEN = clock_time(9, 30)
SESSION_CLOSE = clock_time(16, 0)
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_TARGET_DAILY_VOL = 0.02
DEFAULT_LEVERAGE_CAP = 1.0
DEFAULT_RISK_CAP_FRACTION = 0.005
DEFAULT_ACCOUNT_SIZE = 10_000.0
CHECKPOINTS = tuple(
    clock_time(hour, minute)
    for hour, minute in (
        (10, 0),
        (10, 30),
        (11, 0),
        (11, 30),
        (12, 0),
        (12, 30),
        (13, 0),
        (13, 30),
        (14, 0),
        (14, 30),
        (15, 0),
        (15, 30),
    )
)

_INTRADAY_CACHE: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}


def _format_checkpoint(value: clock_time | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%H:%M")


def _ensure_timezone_index(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()

    normalized = frame.copy()
    index = pd.to_datetime(normalized.index, errors="coerce")
    valid_mask = pd.notna(index)
    normalized = normalized.loc[valid_mask].copy()
    index = index[valid_mask]

    if index.tz is None:
        index = index.tz_localize(timezone.utc)
    else:
        index = index.tz_convert(timezone.utc)

    normalized.index = index
    normalized = normalized.sort_index()
    normalized = normalized[~normalized.index.duplicated(keep="last")]
    return normalized


def _prepare_intraday_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()

    prepared = _ensure_timezone_index(frame)
    rename_map = {column: str(column).strip().lower() for column in prepared.columns}
    prepared = prepared.rename(columns=rename_map)

    column_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "vwap": "VWAP",
        "trade_count": "TradeCount",
    }

    selected: dict[str, Any] = {}
    for source, target in column_map.items():
        if source in prepared.columns:
            selected[target] = pd.to_numeric(prepared[source], errors="coerce")

    if not {"Open", "High", "Low", "Close"}.issubset(selected):
        return pd.DataFrame()

    normalized = pd.DataFrame(selected, index=prepared.index)
    if "Volume" not in normalized.columns:
        normalized["Volume"] = 0.0
    normalized["Volume"] = pd.to_numeric(normalized["Volume"], errors="coerce").fillna(0.0)
    normalized["High"] = normalized[["Open", "High", "Low", "Close"]].max(axis=1)
    normalized["Low"] = normalized[["Open", "High", "Low", "Close"]].min(axis=1)

    local_index = normalized.index.tz_convert(EASTERN_TIME)
    session_mask = (
        (local_index.dayofweek < 5)
        & (local_index.time >= SESSION_OPEN)
        & (local_index.time <= SESSION_CLOSE)
    )
    normalized = normalized.loc[session_mask].copy()
    if normalized.empty:
        return normalized

    normalized.index = normalized.index.tz_convert(EASTERN_TIME)
    normalized["SessionDate"] = pd.Index(normalized.index.date)

    if "VWAP" not in normalized.columns or normalized["VWAP"].isna().all():
        typical_price = (normalized["High"] + normalized["Low"] + normalized["Close"]) / 3.0
        turnover = typical_price * normalized["Volume"].clip(lower=0)
        cumulative_turnover = turnover.groupby(normalized["SessionDate"]).cumsum()
        cumulative_volume = normalized["Volume"].clip(lower=0).groupby(normalized["SessionDate"]).cumsum()
        normalized["VWAP"] = (cumulative_turnover / cumulative_volume.replace(0, pd.NA)).ffill()
        normalized["VWAP"] = normalized["VWAP"].fillna(normalized["Close"])
    else:
        normalized["VWAP"] = pd.to_numeric(normalized["VWAP"], errors="coerce").fillna(normalized["Close"])

    if "TradeCount" in normalized.columns:
        normalized["TradeCount"] = pd.to_numeric(normalized["TradeCount"], errors="coerce").fillna(0.0)

    return normalized


def load_intraday_bars(symbol: str) -> pd.DataFrame:
    normalized_symbol = str(symbol or "").strip().upper()
    primary_provider = get_intraday_bar_provider()
    cache_key = (normalized_symbol, primary_provider.provider_name)
    cached = _INTRADAY_CACHE.get(cache_key)
    now_monotonic = time.monotonic()
    if cached and (now_monotonic - cached[0]) <= 25:
        return cached[1].copy()

    raw_frame = primary_provider.load_intraday_bars(normalized_symbol, days=30)
    frame = _prepare_intraday_frame(raw_frame)
    source = primary_provider.provider_name
    if frame.empty:
        fallback_provider = get_intraday_fallback_provider()
        raw_frame = fallback_provider.load_intraday_bars(normalized_symbol, days=30)
        frame = _prepare_intraday_frame(raw_frame)
        source = fallback_provider.provider_name

    if not frame.empty:
        frame.attrs["data_source"] = source
        _INTRADAY_CACHE[cache_key] = (now_monotonic, frame.copy())
    return frame.copy()


def _session_map(frame: pd.DataFrame) -> dict[Any, pd.DataFrame]:
    if frame.empty or "SessionDate" not in frame.columns:
        return {}
    return {
        session_date: session_frame.copy()
        for session_date, session_frame in frame.groupby("SessionDate", sort=True)
    }


def _session_bar_at_or_before(session_frame: pd.DataFrame, checkpoint: clock_time) -> pd.Series | None:
    eligible = session_frame[session_frame.index.time <= checkpoint]
    if eligible.empty:
        return None
    return eligible.iloc[-1]


def _session_open(session_frame: pd.DataFrame) -> float | None:
    if session_frame.empty:
        return None
    open_value = pd.to_numeric(session_frame["Open"], errors="coerce").dropna()
    if open_value.empty:
        return None
    return float(open_value.iloc[0])


def _session_close(session_frame: pd.DataFrame) -> float | None:
    if session_frame.empty:
        return None
    close_value = pd.to_numeric(session_frame["Close"], errors="coerce").dropna()
    if close_value.empty:
        return None
    return float(close_value.iloc[-1])


def _compute_sigma(
    sessions: dict[Any, pd.DataFrame],
    session_dates: list[Any],
    checkpoint: clock_time,
    lookback_days: int,
) -> float | None:
    moves: list[float] = []
    for session_date in session_dates[-lookback_days:]:
        session_frame = sessions.get(session_date)
        if session_frame is None or session_frame.empty:
            continue
        open_price = _session_open(session_frame)
        checkpoint_bar = _session_bar_at_or_before(session_frame, checkpoint)
        if open_price is None or checkpoint_bar is None:
            continue
        checkpoint_close = float(checkpoint_bar["Close"])
        if open_price <= 0:
            continue
        moves.append(abs(checkpoint_close / open_price - 1.0))

    if not moves:
        return None
    return float(sum(moves) / len(moves))


def _realized_volatility(session_dates: list[Any], sessions: dict[Any, pd.DataFrame]) -> float | None:
    closes: list[float] = []
    for session_date in session_dates[-15:]:
        close_value = _session_close(sessions.get(session_date, pd.DataFrame()))
        if close_value is not None:
            closes.append(close_value)

    if len(closes) < 3:
        return None

    returns = pd.Series(closes, dtype=float).pct_change().dropna()
    if returns.empty:
        return None
    return float(returns.tail(14).std(ddof=1))


def _checkpoint_record(
    *,
    checkpoint: clock_time,
    sigma_pct: float,
    upper_band: float,
    lower_band: float,
    price: float,
    vwap: float,
    long_stop: float,
    short_stop: float,
    state_before: str,
    state_after: str,
    action: str,
    timestamp: pd.Timestamp,
) -> dict[str, Any]:
    return {
        "checkpoint": _format_checkpoint(checkpoint),
        "checkpoint_at": timestamp.isoformat(),
        "sigma_pct": sigma_pct,
        "upper_band": upper_band,
        "lower_band": lower_band,
        "price": price,
        "vwap": vwap,
        "long_stop": long_stop,
        "short_stop": short_stop,
        "state_before": state_before,
        "state_after": state_after,
        "action": action,
    }


def _action_label(action: str) -> str:
    return action.replace("_", " ").upper()


def _build_strategy_overlays(
    *,
    chart_df: pd.DataFrame | None,
    session_frame: pd.DataFrame,
    strategy_records: list[dict[str, Any]],
    session_date: Any,
) -> dict[str, list[float | None]]:
    if chart_df is None or chart_df.empty or not strategy_records:
        return {}

    chart_times = pd.to_datetime(chart_df.get("datetime"), errors="coerce", utc=True)
    if chart_times.isna().all():
        return {}

    strategy_state = [
        {
            **record,
            "checkpoint_dt": pd.Timestamp(record["checkpoint_at"]).tz_convert(EASTERN_TIME),
        }
        for record in strategy_records
    ]
    session_index = session_frame.index

    overlays = {
        "idm_upper_band": [],
        "idm_lower_band": [],
        "idm_vwap": [],
        "idm_trailing_stop": [],
    }

    for timestamp in chart_times:
        if pd.isna(timestamp):
            for values in overlays.values():
                values.append(None)
            continue

        eastern_timestamp = timestamp.tz_convert(EASTERN_TIME)
        if eastern_timestamp.date() != session_date:
            for values in overlays.values():
                values.append(None)
            continue

        active_record = None
        for record in strategy_state:
            if record["checkpoint_dt"] <= eastern_timestamp:
                active_record = record
            else:
                break

        eligible_bars = session_index[session_index <= eastern_timestamp]
        session_bar = session_frame.loc[eligible_bars[-1]] if len(eligible_bars) else None

        overlays["idm_upper_band"].append(
            float(active_record["upper_band"]) if active_record is not None else None
        )
        overlays["idm_lower_band"].append(
            float(active_record["lower_band"]) if active_record is not None else None
        )
        overlays["idm_vwap"].append(
            float(session_bar["VWAP"]) if session_bar is not None and not pd.isna(session_bar["VWAP"]) else None
        )

        if active_record is None:
            overlays["idm_trailing_stop"].append(None)
            continue

        if active_record["state_after"] == "long":
            overlays["idm_trailing_stop"].append(float(active_record["long_stop"]))
        elif active_record["state_after"] == "short":
            overlays["idm_trailing_stop"].append(float(active_record["short_stop"]))
        else:
            overlays["idm_trailing_stop"].append(None)

    return overlays


def build_intraday_momentum_snapshot(
    symbol: str,
    *,
    chart_df: pd.DataFrame | None = None,
    intraday_frame: pd.DataFrame | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    target_daily_vol: float = DEFAULT_TARGET_DAILY_VOL,
    leverage_cap: float = DEFAULT_LEVERAGE_CAP,
    risk_cap_fraction: float = DEFAULT_RISK_CAP_FRACTION,
    benchmark_account_size: float = DEFAULT_ACCOUNT_SIZE,
) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").strip().upper()
    bars = _prepare_intraday_frame(intraday_frame) if intraday_frame is not None else load_intraday_bars(normalized_symbol)
    if bars.empty:
        return {
            "available": False,
            "symbol": normalized_symbol,
            "strategy": "adaptive_intraday_momentum_vwap",
            "reason": "No intraday bars were available for the strategy engine.",
            "overlays": {},
        }

    sessions = _session_map(bars)
    session_dates = sorted(sessions.keys())
    if len(session_dates) < lookback_days + 1:
        return {
            "available": False,
            "symbol": normalized_symbol,
            "strategy": "adaptive_intraday_momentum_vwap",
            "reason": f"Need at least {lookback_days + 1} regular sessions to build the noise bands.",
            "overlays": {},
        }

    session_date = session_dates[-1]
    previous_session_date = session_dates[-2]
    lookback_dates = session_dates[-(lookback_days + 1):-1]
    today_frame = sessions[session_date]
    previous_frame = sessions[previous_session_date]

    today_open = _session_open(today_frame)
    previous_close = _session_close(previous_frame)
    latest_bar = today_frame.iloc[-1] if not today_frame.empty else None
    latest_price = float(latest_bar["Close"]) if latest_bar is not None else None
    latest_vwap = float(latest_bar["VWAP"]) if latest_bar is not None else None
    latest_timestamp = latest_bar.name if latest_bar is not None else None

    if today_open is None or previous_close is None or latest_bar is None:
        return {
            "available": False,
            "symbol": normalized_symbol,
            "strategy": "adaptive_intraday_momentum_vwap",
            "reason": "The current session did not have enough regular-hours data.",
            "overlays": {},
        }

    latest_checkpoint = None
    next_checkpoint = None
    for checkpoint in CHECKPOINTS:
        if latest_timestamp.time() >= checkpoint:
            latest_checkpoint = checkpoint
        elif next_checkpoint is None:
            next_checkpoint = checkpoint
            break

    position_state = "flat"
    action = "wait_for_breakout"
    strategy_records: list[dict[str, Any]] = []
    reference_record: dict[str, Any] | None = None

    for checkpoint in CHECKPOINTS:
        sigma_pct = _compute_sigma(sessions, lookback_dates, checkpoint, lookback_days)
        if sigma_pct is None:
            continue

        upper_band = max(today_open, previous_close) * (1.0 + sigma_pct)
        lower_band = min(today_open, previous_close) * (1.0 - sigma_pct)
        checkpoint_bar = _session_bar_at_or_before(today_frame, checkpoint)

        if checkpoint_bar is None:
            if reference_record is None:
                reference_record = {
                    "checkpoint": _format_checkpoint(checkpoint),
                    "sigma_pct": sigma_pct,
                    "upper_band": upper_band,
                    "lower_band": lower_band,
                    "vwap": latest_vwap,
                    "long_stop": max(upper_band, latest_vwap),
                    "short_stop": min(lower_band, latest_vwap),
                    "price": latest_price,
                    "state_after": position_state,
                }
            continue

        checkpoint_price = float(checkpoint_bar["Close"])
        checkpoint_vwap = float(checkpoint_bar["VWAP"])
        long_stop = max(upper_band, checkpoint_vwap)
        short_stop = min(lower_band, checkpoint_vwap)
        state_before = position_state

        if position_state == "flat":
            if checkpoint_price > upper_band:
                position_state = "long"
                action = "enter_long"
            elif checkpoint_price < lower_band:
                position_state = "short"
                action = "enter_short"
            else:
                action = "wait_for_breakout"
        elif position_state == "long":
            if checkpoint_price <= long_stop and checkpoint_price < lower_band:
                position_state = "short"
                action = "reverse_to_short"
            elif checkpoint_price <= long_stop:
                position_state = "flat"
                action = "exit_long"
            else:
                action = "hold_long"
        elif position_state == "short":
            if checkpoint_price >= short_stop and checkpoint_price > upper_band:
                position_state = "long"
                action = "reverse_to_long"
            elif checkpoint_price >= short_stop:
                position_state = "flat"
                action = "exit_short"
            else:
                action = "hold_short"

        record = _checkpoint_record(
            checkpoint=checkpoint,
            sigma_pct=float(sigma_pct),
            upper_band=float(upper_band),
            lower_band=float(lower_band),
            price=float(checkpoint_price),
            vwap=float(checkpoint_vwap),
            long_stop=float(long_stop),
            short_stop=float(short_stop),
            state_before=state_before,
            state_after=position_state,
            action=action,
            timestamp=checkpoint_bar.name,
        )
        strategy_records.append(record)
        reference_record = record

    if reference_record is None:
        return {
            "available": False,
            "symbol": normalized_symbol,
            "strategy": "adaptive_intraday_momentum_vwap",
            "reason": "No strategy checkpoint was ready for the current session yet.",
            "overlays": {},
        }

    current_upper_band = float(reference_record["upper_band"])
    current_lower_band = float(reference_record["lower_band"])
    long_stop = float(reference_record["long_stop"])
    short_stop = float(reference_record["short_stop"])

    breakout_bias = (
        "bullish"
        if latest_price is not None and latest_price > current_upper_band
        else "bearish"
        if latest_price is not None and latest_price < current_lower_band
        else "neutral"
    )

    if position_state == "long":
        decision = f"Long is active while price holds above {long_stop:.2f}."
        active_stop = long_stop
    elif position_state == "short":
        decision = f"Short is active while price stays below {short_stop:.2f}."
        active_stop = short_stop
    elif latest_timestamp.time() < CHECKPOINTS[0]:
        decision = "Waiting for the 10:00 ET checkpoint before taking a breakout."
        active_stop = None
    elif breakout_bias == "bullish":
        decision = (
            f"Price is above the upper noise band. Confirm at the next checkpoint"
            f"{f' ({_format_checkpoint(next_checkpoint)} ET)' if next_checkpoint else ''}."
        )
        active_stop = None
    elif breakout_bias == "bearish":
        decision = (
            f"Price is below the lower noise band. Confirm at the next checkpoint"
            f"{f' ({_format_checkpoint(next_checkpoint)} ET)' if next_checkpoint else ''}."
        )
        active_stop = None
    else:
        decision = "Price is inside the noise area, so the strategy stays flat."
        active_stop = None

    realized_volatility = _realized_volatility(session_dates[:-1], sessions)
    effective_vol = max(realized_volatility or 0.0, 1e-6)
    leverage = min(leverage_cap, target_daily_vol / effective_vol) if effective_vol > 0 else leverage_cap
    vol_target_shares = max(0, math.floor((benchmark_account_size * leverage) / max(today_open, 0.01)))

    if position_state == "long":
        reference_entry = current_upper_band
        per_share_risk = max(reference_entry - long_stop, 0.0)
    elif position_state == "short":
        reference_entry = current_lower_band
        per_share_risk = max(short_stop - reference_entry, 0.0)
    elif breakout_bias == "bullish":
        reference_entry = current_upper_band
        per_share_risk = max(reference_entry - long_stop, 0.0)
    else:
        reference_entry = current_lower_band
        per_share_risk = max(short_stop - reference_entry, 0.0)

    risk_budget = benchmark_account_size * risk_cap_fraction
    risk_based_shares = (
        max(0, math.floor(risk_budget / max(per_share_risk, 0.01)))
        if per_share_risk > 0
        else 0
    )
    suggested_shares = max(0, min(vol_target_shares, risk_based_shares)) if risk_based_shares else 0

    notes = [
        "Use this engine on liquid intraday charts; it is tuned around SPY-style liquidity.",
        "Signals trigger only at 30-minute checkpoints, not on every tick.",
        "The trailing stop uses the tighter of the current noise band and VWAP.",
        "Flatten any open state into the close if no earlier exit fires.",
    ]
    if str(bars.attrs.get("data_source") or "").lower() != "alpaca":
        notes.append("Historical bars fell back to cached chart data, so the band math is lower fidelity than the Alpaca minute feed.")

    strategy_overlays = _build_strategy_overlays(
        chart_df=chart_df,
        session_frame=today_frame,
        strategy_records=strategy_records,
        session_date=session_date,
    )

    return {
        "available": True,
        "symbol": normalized_symbol,
        "strategy": "adaptive_intraday_momentum_vwap",
        "tuned_for_symbol": "SPY",
        "session_date": str(session_date),
        "data_source": str(bars.attrs.get("data_source") or "unknown"),
        "lookback_days": lookback_days,
        "latest_bar_at": latest_timestamp.isoformat(),
        "latest_price": latest_price,
        "today_open": today_open,
        "prior_close": previous_close,
        "latest_checkpoint": _format_checkpoint(latest_checkpoint),
        "next_checkpoint": _format_checkpoint(next_checkpoint),
        "current_sigma_pct": float(reference_record["sigma_pct"]),
        "upper_band": current_upper_band,
        "lower_band": current_lower_band,
        "vwap": latest_vwap,
        "long_stop": long_stop,
        "short_stop": short_stop,
        "active_stop": active_stop,
        "state": position_state,
        "bias": breakout_bias,
        "latest_action": _action_label(action),
        "decision": decision,
        "sizing": {
            "benchmark_account_size": benchmark_account_size,
            "target_daily_vol": target_daily_vol,
            "leverage_cap": leverage_cap,
            "risk_cap_fraction": risk_cap_fraction,
            "realized_vol_14d": realized_volatility,
            "vol_target_shares": vol_target_shares,
            "risk_based_shares": risk_based_shares,
            "suggested_shares": suggested_shares,
            "reference_entry": reference_entry,
            "per_share_risk": per_share_risk,
        },
        "checkpoints": [_format_checkpoint(checkpoint) for checkpoint in CHECKPOINTS],
        "signal_history": strategy_records[-8:],
        "notes": notes,
        "overlays": strategy_overlays,
    }
