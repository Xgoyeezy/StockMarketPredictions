from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HttpJsonResponse:
    reachable: bool
    status_code: int | None
    payload: dict[str, Any] | list[Any] | None
    message: str | None


def request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout_seconds: int = 10,
) -> HttpJsonResponse:
    query = urlencode({key: value for key, value in (params or {}).items() if value is not None}, doseq=True)
    target = f"{url}?{query}" if query else url
    raw_body = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(target, data=raw_body, headers=headers or {}, method=method.upper())
    if raw_body is not None and "Content-Type" not in request.headers:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw else None
            return HttpJsonResponse(True, int(getattr(response, "status", 0) or 0), payload, None)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else None
        except Exception:
            payload = None
        message = None
        if isinstance(payload, dict):
            for key in ("message", "detail", "description", "error"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    message = value.strip()
                    break
        return HttpJsonResponse(True, int(exc.code), payload, message or raw or None)
    except URLError as exc:
        return HttpJsonResponse(False, None, None, str(exc.reason or exc))
    except Exception as exc:  # pragma: no cover - defensive guard
        return HttpJsonResponse(False, None, None, str(exc))


def parse_timestamp_ns(value: object, *, fallback_ns: int) -> int:
    if value is None:
        return int(fallback_ns)
    if isinstance(value, (int, float)):
        numeric = int(value)
        return numeric if numeric > 1_000_000_000_000 else int(fallback_ns)
    cleaned = str(value).strip()
    if not cleaned:
        return int(fallback_ns)
    try:
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1_000_000_000)
    except Exception:
        return int(fallback_ns)
