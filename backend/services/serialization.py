from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd


def serialize_dataframe(df: pd.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    if df.empty:
        return []
    view = df.copy()
    if limit is not None:
        view = view.head(limit)
    view = view.where(pd.notna(view), None)
    records = view.to_dict(orient="records")
    return [serialize_value(record) for record in records]


def serialize_series(series: pd.Series) -> dict[str, Any]:
    return serialize_value(series.where(pd.notna(series), None).to_dict())


def serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): serialize_value(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [serialize_value(item) for item in value]
    if isinstance(value, pd.DataFrame):
        return serialize_dataframe(value)
    if isinstance(value, pd.Series):
        return serialize_series(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            return serialize_value(value.item())
        except Exception:
            pass
    return value
