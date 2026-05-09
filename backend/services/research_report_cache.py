from __future__ import annotations

import hashlib
import time
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable

from backend.services.candidate_outcome_stamping_service import research_source_paths, tenant_slug_from_user
from backend.services.serialization import serialize_value

DEFAULT_TTL_SECONDS = 60

_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = RLock()


def _fingerprint_paths(paths: Iterable[Path], *, limit: int = 200) -> str:
    parts: list[str] = []
    for path in list(paths)[:limit]:
        try:
            stat = path.stat()
        except OSError:
            continue
        parts.append(f"{path.as_posix()}:{int(stat.st_mtime_ns)}:{int(stat.st_size)}")
    digest = hashlib.sha1("|".join(sorted(parts)).encode("utf-8")).hexdigest()
    return digest


def cached_research_report(
    *,
    group: str,
    current_user: Any = None,
    builder: Callable[[], dict[str, Any]],
    source_paths: Iterable[Path] | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    tenant_slug = tenant_slug_from_user(current_user)
    paths = list(source_paths) if source_paths is not None else research_source_paths(tenant_slug=tenant_slug)
    fingerprint = _fingerprint_paths(paths)
    cache_key = f"{group}:{tenant_slug}:{fingerprint}"
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and cached.get("expires_at", 0) > now:
            payload = dict(cached.get("payload") or {})
            payload["cache_status"] = "hit"
            payload["source_fingerprint"] = fingerprint
            return serialize_value(payload)

    payload = dict(builder() or {})
    payload["cache_status"] = "miss"
    payload["source_fingerprint"] = fingerprint
    payload.setdefault("research_only", True)
    with _CACHE_LOCK:
        _CACHE[cache_key] = {
            "expires_at": now + int(ttl_seconds),
            "payload": serialize_value(payload),
        }
    return serialize_value(payload)


def clear_research_report_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
