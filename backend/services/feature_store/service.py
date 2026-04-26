from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from backend.core.config import settings
from backend.services.market_data.frames import normalize_model_ohlcv_frame
from backend.services.market_data.provider_registry import get_market_data_provider
from backend.services.strategy_engine.types import DeskDataRequirement


QUANT_STORAGE_DIR = Path(settings.storage_dir) / "quant"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_quant_storage_dirs() -> None:
    for relative in ("features", "backtests", "replay", "events"):
        (QUANT_STORAGE_DIR / relative).mkdir(parents=True, exist_ok=True)


def load_market_state(requirements: tuple[DeskDataRequirement, ...]) -> dict[str, Any]:
    ensure_quant_storage_dirs()
    provider = get_market_data_provider()
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    requirement_payloads: list[dict[str, Any]] = []

    for requirement in requirements:
        requirement_payloads.append(requirement.to_dict())
        if requirement.family != "bars":
            continue
        tickers = [ticker for ticker in requirement.tickers if ticker]
        if not tickers:
            continue
        raw = provider.download_bars(
            tickers,
            period=requirement.period,
            interval=requirement.interval,
            prepost=requirement.prepost,
            group_by="ticker",
        )
        for ticker in tickers:
            try:
                bars_by_symbol[ticker] = normalize_model_ohlcv_frame(raw, ticker)
            except Exception:
                bars_by_symbol[ticker] = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    return {
        "as_of": _utc_now_iso(),
        "requirements": requirement_payloads,
        "bars": bars_by_symbol,
        "provider_name": provider.provider_name,
    }


def latest_close(frame: pd.DataFrame) -> float | None:
    if frame.empty or "Close" not in frame.columns:
        return None
    series = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if series.empty:
        return None
    return float(series.iloc[-1])


def compute_return(frame: pd.DataFrame, periods: int) -> float:
    if frame.empty or "Close" not in frame.columns or len(frame.index) <= periods:
        return 0.0
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if len(close) <= periods:
        return 0.0
    start = float(close.iloc[-(periods + 1)])
    end = float(close.iloc[-1])
    if start <= 0:
        return 0.0
    return float((end / start) - 1.0)


def compute_realized_vol(frame: pd.DataFrame, lookback: int = 20) -> float:
    if frame.empty or "Close" not in frame.columns:
        return 0.0
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    returns = close.pct_change().dropna()
    if returns.empty:
        return 0.0
    window = returns.tail(max(lookback, 2))
    if window.empty:
        return 0.0
    return float(window.std(ddof=0) * (252 ** 0.5))


def compute_average_volume(frame: pd.DataFrame, lookback: int = 20) -> float:
    if frame.empty or "Volume" not in frame.columns:
        return 0.0
    volume = pd.to_numeric(frame["Volume"], errors="coerce").dropna()
    if volume.empty:
        return 0.0
    return float(volume.tail(max(lookback, 1)).mean())


def compute_gap(frame: pd.DataFrame) -> float:
    if frame.empty or len(frame.index) < 2:
        return 0.0
    open_series = pd.to_numeric(frame["Open"], errors="coerce").dropna()
    close_series = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if len(open_series) < 1 or len(close_series) < 2:
        return 0.0
    prior_close = float(close_series.iloc[-2])
    current_open = float(open_series.iloc[-1])
    if prior_close <= 0:
        return 0.0
    return float((current_open / prior_close) - 1.0)


def compute_volume_spike(frame: pd.DataFrame, lookback: int = 20) -> float:
    if frame.empty or "Volume" not in frame.columns:
        return 0.0
    volume = pd.to_numeric(frame["Volume"], errors="coerce").dropna()
    if len(volume) < 2:
        return 0.0
    baseline = float(volume.iloc[-(lookback + 1):-1].mean()) if len(volume) > 1 else 0.0
    current = float(volume.iloc[-1])
    if baseline <= 0:
        return 0.0
    return float(current / baseline)


def compute_sector_relative_strength(frame: pd.DataFrame, sector_frame: pd.DataFrame) -> float:
    if frame.empty or sector_frame.empty:
        return 0.0
    asset_return = compute_return(frame, 20)
    sector_return = compute_return(sector_frame, 20)
    return float(asset_return - sector_return)


def frame_to_rows(frame: pd.DataFrame, *, limit: int = 120) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    subset = frame.tail(limit).reset_index()
    subset = subset.rename(columns={subset.columns[0]: "datetime"})
    rows: list[dict[str, Any]] = []
    for item in subset.to_dict(orient="records"):
        normalized = dict(item)
        value = normalized.get("datetime")
        if isinstance(value, pd.Timestamp):
            normalized["datetime"] = value.isoformat()
        rows.append(normalized)
    return rows
