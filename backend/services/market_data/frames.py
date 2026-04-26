from __future__ import annotations

import pandas as pd


MODEL_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
CHART_OHLCV_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def latest_close_from_ohlcv_frame(frame: pd.DataFrame) -> float:
    if frame.empty or "Close" not in frame.columns:
        return float("nan")
    close_series = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if close_series.empty:
        return float("nan")
    return float(close_series.iloc[-1])


def normalize_model_ohlcv_frame(frame: pd.DataFrame, symbol: str = "") -> pd.DataFrame:
    if frame.empty:
        raise ValueError(f"No data returned for {symbol or 'frame'}")

    normalized = frame.copy()
    required_columns = MODEL_OHLCV_COLUMNS.copy()

    if isinstance(normalized.columns, pd.MultiIndex):
        symbol_upper = _normalize_symbol(symbol)
        level0 = {str(value) for value in normalized.columns.get_level_values(0)}
        level1 = {str(value) for value in normalized.columns.get_level_values(1)}

        if set(required_columns).issubset(level0):
            normalized = normalized.droplevel(1, axis=1)
        elif symbol_upper and symbol_upper in level1:
            normalized = normalized.xs(symbol_upper, axis=1, level=1, drop_level=True)
        elif symbol_upper and symbol_upper in level0:
            normalized = normalized.xs(symbol_upper, axis=1, level=0, drop_level=True)
        else:
            normalized.columns = normalized.columns.get_level_values(0)

    rename_map = {}
    for column in list(normalized.columns):
        if not isinstance(column, str):
            continue
        lowered = column.strip().lower()
        if lowered == "open":
            rename_map[column] = "Open"
        elif lowered == "high":
            rename_map[column] = "High"
        elif lowered == "low":
            rename_map[column] = "Low"
        elif lowered == "close":
            rename_map[column] = "Close"
        elif lowered == "volume":
            rename_map[column] = "Volume"
        elif lowered in {"datetime", "date", "timestamp"}:
            rename_map[column] = "__datetime__"
    if rename_map:
        normalized = normalized.rename(columns=rename_map)

    if "__datetime__" in normalized.columns:
        normalized = normalized.set_index("__datetime__")

    normalized = normalized.loc[:, ~normalized.columns.duplicated(keep="last")]

    missing_columns = [column for column in required_columns if column not in normalized.columns]
    if missing_columns:
        raise ValueError(f"{symbol or 'frame'} missing columns: {missing_columns}")

    clean = normalized.loc[:, required_columns].copy()
    for column in required_columns:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")

    clean.index = pd.to_datetime(clean.index, errors="coerce", utc=True)
    valid_index_mask = pd.notna(clean.index)
    clean = clean.loc[valid_index_mask].copy()
    clean = clean.sort_index().dropna(how="all")
    clean = clean[~clean.index.duplicated(keep="last")]
    return clean


def resample_ohlcv_to_4h(frame: pd.DataFrame) -> pd.DataFrame:
    canonical = normalize_model_ohlcv_frame(frame)
    resampled = pd.DataFrame(
        {
            "Open": canonical["Open"].resample("4h").first(),
            "High": canonical["High"].resample("4h").max(),
            "Low": canonical["Low"].resample("4h").min(),
            "Close": canonical["Close"].resample("4h").last(),
            "Volume": canonical["Volume"].resample("4h").sum(),
        }
    )
    return resampled.dropna(subset=["Open", "High", "Low", "Close"])


def ohlcv_frame_to_chart_frame(frame: pd.DataFrame) -> pd.DataFrame:
    canonical = normalize_model_ohlcv_frame(frame)
    chart_df = canonical.reset_index().rename(
        columns={
            canonical.index.name or "index": "datetime",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )

    if "datetime" in chart_df.columns:
        chart_df["datetime"] = pd.to_datetime(chart_df["datetime"], errors="coerce", utc=True)

    for column in ["open", "high", "low", "close", "volume"]:
        if column in chart_df.columns:
            chart_df[column] = pd.to_numeric(chart_df[column], errors="coerce")

    chart_df = chart_df.dropna(subset=["datetime", "open", "high", "low", "close"]).copy()
    chart_df = chart_df.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
    chart_df["high"] = chart_df[["open", "high", "low", "close"]].max(axis=1)
    chart_df["low"] = chart_df[["open", "high", "low", "close"]].min(axis=1)
    chart_df["volume"] = chart_df["volume"].fillna(0)
    return chart_df.loc[:, CHART_OHLCV_COLUMNS].reset_index(drop=True)
