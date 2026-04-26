from __future__ import annotations

from typing import Any

from backend.schemas import ApiEnvelope


def envelope(data: Any, meta: dict[str, Any] | None = None) -> ApiEnvelope:
    return ApiEnvelope(ok=True, data=data, meta=meta or {})
