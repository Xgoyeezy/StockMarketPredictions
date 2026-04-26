from __future__ import annotations

from typing import Any

from backend.schemas import ApiEnvelope
from backend.services.serialization import serialize_value


def envelope(data: Any, meta: dict[str, Any] | None = None) -> ApiEnvelope:
    return ApiEnvelope(ok=True, data=serialize_value(data), meta=serialize_value(meta or {}))
