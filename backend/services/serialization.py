from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import math
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel

_MAX_SERIALIZE_DEPTH = 80


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
    return _serialize_value(value, set(), 0)


def _serialize_value(value: Any, active_ids: set[int], depth: int) -> Any:
    if depth > _MAX_SERIALIZE_DEPTH:
        return None
    if value is None:
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, pd.DataFrame):
        return serialize_dataframe(value)
    if isinstance(value, pd.Series):
        return serialize_series(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return _serialize_value(value.value, active_ids, depth + 1)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseModel):
        return _serialize_value(value.model_dump(mode="json"), active_ids, depth + 1)
    if isinstance(value, Mapping):
        value_id = id(value)
        if value_id in active_ids:
            return None
        active_ids.add(value_id)
        try:
            return {str(key): _serialize_value(inner, active_ids, depth + 1) for key, inner in value.items()}
        finally:
            active_ids.remove(value_id)
    if isinstance(value, (list, tuple, set, frozenset)):
        value_id = id(value)
        if value_id in active_ids:
            return None
        active_ids.add(value_id)
        try:
            return [_serialize_value(item, active_ids, depth + 1) for item in value]
        finally:
            active_ids.remove(value_id)
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            return _serialize_value(value.item(), active_ids, depth + 1)
        except Exception:
            pass
    return str(value)
