from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timezone
from statistics import mean
from threading import Lock
from time import time
from typing import Any

from backend.core.config import settings

_lock = Lock()
_started_at = time()
_started_iso = datetime.now(timezone.utc).isoformat()
_lifetime_totals = {"requests": 0, "errors": 0}
_recent_requests: deque[dict[str, Any]] = deque(maxlen=settings.ops_metrics_window_size)
_recent_slow_requests: deque[dict[str, Any]] = deque(maxlen=24)
_operation_started_at = time()
_operation_started_iso = datetime.now(timezone.utc).isoformat()
_lifetime_operation_totals = {"operations": 0, "errors": 0}
_recent_operations: deque[dict[str, Any]] = deque(maxlen=settings.ops_operation_metrics_window_size)
_recent_slow_operations: deque[dict[str, Any]] = deque(maxlen=24)
_route_profile_started_at = time()
_route_profile_started_iso = datetime.now(timezone.utc).isoformat()
_lifetime_route_profile_totals = {"profiles": 0, "slow": 0}
_recent_route_profiles: deque[dict[str, Any]] = deque(maxlen=120)
_upstream_started_at = time()
_upstream_started_iso = datetime.now(timezone.utc).isoformat()
_lifetime_upstream_totals = {"calls": 0, "timeouts": 0, "errors": 0}
_recent_upstream_calls: deque[dict[str, Any]] = deque(maxlen=settings.ops_upstream_metrics_window_size)
_recent_upstream_timeouts: deque[dict[str, Any]] = deque(maxlen=24)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bucket_status(status_code: int) -> str:
    if status_code >= 500:
        return "5xx"
    if status_code >= 400:
        return "4xx"
    if status_code >= 300:
        return "3xx"
    if status_code >= 200:
        return "2xx"
    return "other"


def _route_group(path: str) -> str:
    segments = [segment for segment in str(path or "").split("?")[0].strip("/").split("/") if segment]
    api_prefix = settings.api_prefix.strip("/")
    if segments and segments[0] == api_prefix:
        segments = segments[1:]
    if not segments:
        return "root"
    if len(segments) == 1:
        return segments[0]
    return "/".join(segments[:2])


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return ordered[index]


def reset_request_metrics() -> None:
    global _started_at, _started_iso
    with _lock:
        _started_at = time()
        _started_iso = _utc_now_iso()
        _lifetime_totals["requests"] = 0
        _lifetime_totals["errors"] = 0
        _recent_requests.clear()
        _recent_slow_requests.clear()


def reset_operation_metrics() -> None:
    global _operation_started_at, _operation_started_iso
    with _lock:
        _operation_started_at = time()
        _operation_started_iso = _utc_now_iso()
        _lifetime_operation_totals["operations"] = 0
        _lifetime_operation_totals["errors"] = 0
        _recent_operations.clear()
        _recent_slow_operations.clear()


def reset_route_profile_metrics() -> None:
    global _route_profile_started_at, _route_profile_started_iso
    with _lock:
        _route_profile_started_at = time()
        _route_profile_started_iso = _utc_now_iso()
        _lifetime_route_profile_totals["profiles"] = 0
        _lifetime_route_profile_totals["slow"] = 0
        _recent_route_profiles.clear()


def reset_upstream_metrics() -> None:
    global _upstream_started_at, _upstream_started_iso
    with _lock:
        _upstream_started_at = time()
        _upstream_started_iso = _utc_now_iso()
        _lifetime_upstream_totals["calls"] = 0
        _lifetime_upstream_totals["timeouts"] = 0
        _lifetime_upstream_totals["errors"] = 0
        _recent_upstream_calls.clear()
        _recent_upstream_timeouts.clear()


def record_request(
    *,
    path: str,
    method: str,
    status_code: int,
    duration_seconds: float,
    request_id: str,
) -> None:
    duration_ms = round(max(duration_seconds, 0.0) * 1000, 2)
    item = {
        "at": _utc_now_iso(),
        "path": str(path or "/"),
        "method": str(method or "GET").upper(),
        "status_code": int(status_code or 0),
        "duration_ms": duration_ms,
        "request_id": str(request_id or ""),
        "route_group": _route_group(path),
        "status_bucket": _bucket_status(int(status_code or 0)),
    }
    with _lock:
        _lifetime_totals["requests"] += 1
        if item["status_code"] >= 400:
            _lifetime_totals["errors"] += 1
        _recent_requests.append(item)
        if duration_ms >= settings.ops_slow_request_ms:
            _recent_slow_requests.appendleft(item)


def record_upstream_event(
    *,
    target: str,
    operation: str,
    duration_seconds: float,
    status: str = "ok",
    error_message: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    duration_ms = round(max(duration_seconds, 0.0) * 1000, 2)
    normalized_status = str(status or "ok").lower()
    item = {
        "at": _utc_now_iso(),
        "target": str(target or "unknown"),
        "operation": str(operation or "unknown"),
        "duration_ms": duration_ms,
        "status": normalized_status,
        "error_message": str(error_message or "").strip() or None,
        "context": dict(context or {}),
    }
    with _lock:
        _lifetime_upstream_totals["calls"] += 1
        if normalized_status == "timeout":
            _lifetime_upstream_totals["timeouts"] += 1
            _recent_upstream_timeouts.appendleft(item)
        elif normalized_status != "ok":
            _lifetime_upstream_totals["errors"] += 1
        _recent_upstream_calls.append(item)


def record_operation(
    *,
    name: str,
    duration_seconds: float,
    status: str = "ok",
    cache_status: str = "bypass",
    context: dict[str, Any] | None = None,
) -> None:
    duration_ms = round(max(duration_seconds, 0.0) * 1000, 2)
    operation_name = str(name or "unknown")
    normalized_status = str(status or "ok").lower()
    normalized_cache_status = str(cache_status or "bypass").lower()
    item = {
        "at": _utc_now_iso(),
        "name": operation_name,
        "duration_ms": duration_ms,
        "status": normalized_status,
        "cache_status": normalized_cache_status,
        "context": dict(context or {}),
    }
    with _lock:
        _lifetime_operation_totals["operations"] += 1
        if normalized_status != "ok":
            _lifetime_operation_totals["errors"] += 1
        _recent_operations.append(item)
        if duration_ms >= settings.ops_slow_operation_ms:
            _recent_slow_operations.appendleft(item)


def record_route_profile(
    *,
    route_key: str,
    total_duration_seconds: float,
    stages: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
    status: str = "ok",
) -> None:
    total_duration_ms = round(max(total_duration_seconds, 0.0) * 1000, 2)
    item = {
        "at": _utc_now_iso(),
        "route_key": str(route_key or "unknown"),
        "total_duration_ms": total_duration_ms,
        "status": str(status or "ok").lower(),
        "context": dict(context or {}),
        "stages": [
            {
                "name": str(stage.get("name") or "unknown"),
                "duration_ms": round(float(stage.get("duration_ms") or 0.0), 2),
                "status": str(stage.get("status") or "ok").lower(),
            }
            for stage in list(stages or [])
        ],
    }
    with _lock:
        _lifetime_route_profile_totals["profiles"] += 1
        if total_duration_ms >= settings.ops_slow_operation_ms:
            _lifetime_route_profile_totals["slow"] += 1
        _recent_route_profiles.append(item)


def get_request_metrics_snapshot() -> dict[str, Any]:
    with _lock:
        requests = list(_recent_requests)
        slow_requests = list(_recent_slow_requests)
        lifetime_requests = int(_lifetime_totals["requests"])
        lifetime_errors = int(_lifetime_totals["errors"])
        started_iso = _started_iso
        uptime_seconds = max(0, int(time() - _started_at))

    durations = [float(item["duration_ms"]) for item in requests]
    status_counts = Counter(item["status_bucket"] for item in requests)
    method_counts = Counter(item["method"] for item in requests)
    route_counts = Counter(item["route_group"] for item in requests)
    error_count = sum(1 for item in requests if int(item["status_code"]) >= 400)
    timeout_warning_count = sum(
        1
        for item in requests
        if float(item["duration_ms"]) >= settings.ops_request_timeout_warning_ms
        or int(item["status_code"]) in {408, 504}
    )
    last_request_at = requests[-1]["at"] if requests else None
    timeout_risks = [
        item
        for item in reversed(requests)
        if float(item["duration_ms"]) >= settings.ops_request_timeout_warning_ms
        or int(item["status_code"]) in {408, 504}
    ]

    return {
        "window_size": len(requests),
        "lifetime_requests": lifetime_requests,
        "lifetime_errors": lifetime_errors,
        "started_at": started_iso,
        "uptime_seconds": uptime_seconds,
        "summary": {
            "total_requests": len(requests),
            "error_count": error_count,
            "error_rate": round((error_count / len(requests)) * 100, 2) if requests else 0.0,
            "average_duration_ms": round(mean(durations), 2) if durations else 0.0,
            "p95_duration_ms": round(_p95(durations), 2) if durations else 0.0,
            "max_duration_ms": round(max(durations), 2) if durations else 0.0,
            "slow_request_count": sum(1 for item in requests if float(item["duration_ms"]) >= settings.ops_slow_request_ms),
            "slow_request_threshold_ms": settings.ops_slow_request_ms,
            "timeout_warning_count": timeout_warning_count,
            "timeout_warning_threshold_ms": settings.ops_request_timeout_warning_ms,
            "last_request_at": last_request_at,
        },
        "route_groups": [
            {"key": key, "count": count}
            for key, count in route_counts.most_common(8)
        ],
        "methods": [
            {"key": key, "count": count}
            for key, count in method_counts.most_common()
        ],
        "status_buckets": [
            {"key": key, "count": count}
            for key, count in status_counts.most_common()
        ],
        "recent_slow_requests": slow_requests[:8],
        "recent_timeout_risks": timeout_risks[:8],
    }


def get_operation_metrics_snapshot() -> dict[str, Any]:
    with _lock:
        operations = list(_recent_operations)
        slow_operations = list(_recent_slow_operations)
        lifetime_operations = int(_lifetime_operation_totals["operations"])
        lifetime_errors = int(_lifetime_operation_totals["errors"])
        started_iso = _operation_started_iso
        uptime_seconds = max(0, int(time() - _operation_started_at))

    durations = [float(item["duration_ms"]) for item in operations]
    error_count = sum(1 for item in operations if str(item.get("status")) != "ok")
    timeout_count = sum(1 for item in operations if str(item.get("status")) == "timeout")
    last_operation_at = operations[-1]["at"] if operations else None
    cache_hits = sum(1 for item in operations if item.get("cache_status") == "hit")
    cache_misses = sum(1 for item in operations if item.get("cache_status") == "miss")
    cache_bypass = sum(1 for item in operations if item.get("cache_status") == "bypass")

    grouped: dict[str, dict[str, Any]] = {}
    for item in operations:
        key = str(item.get("name") or "unknown")
        entry = grouped.setdefault(
            key,
            {
                "key": key,
                "count": 0,
                "durations": [],
                "cache_hits": 0,
                "cache_misses": 0,
                "cache_bypass": 0,
                "error_count": 0,
                "timeout_count": 0,
                "last_at": None,
            },
        )
        entry["count"] += 1
        entry["durations"].append(float(item.get("duration_ms") or 0.0))
        if item.get("cache_status") == "hit":
            entry["cache_hits"] += 1
        elif item.get("cache_status") == "miss":
            entry["cache_misses"] += 1
        else:
            entry["cache_bypass"] += 1
        if str(item.get("status")) != "ok":
            entry["error_count"] += 1
        if str(item.get("status")) == "timeout":
            entry["timeout_count"] += 1
        entry["last_at"] = item.get("at")

    operation_groups = []
    for entry in grouped.values():
        entry_durations = entry.pop("durations")
        entry["average_duration_ms"] = round(mean(entry_durations), 2) if entry_durations else 0.0
        entry["p95_duration_ms"] = round(_p95(entry_durations), 2) if entry_durations else 0.0
        entry["max_duration_ms"] = round(max(entry_durations), 2) if entry_durations else 0.0
        operation_groups.append(entry)
    operation_groups.sort(key=lambda item: (-int(item["count"]), str(item["key"])))

    return {
        "window_size": len(operations),
        "lifetime_operations": lifetime_operations,
        "lifetime_errors": lifetime_errors,
        "started_at": started_iso,
        "uptime_seconds": uptime_seconds,
        "summary": {
            "total_operations": len(operations),
            "error_count": error_count,
            "error_rate": round((error_count / len(operations)) * 100, 2) if operations else 0.0,
            "timeout_count": timeout_count,
            "average_duration_ms": round(mean(durations), 2) if durations else 0.0,
            "p95_duration_ms": round(_p95(durations), 2) if durations else 0.0,
            "max_duration_ms": round(max(durations), 2) if durations else 0.0,
            "slow_operation_count": sum(
                1 for item in operations if float(item["duration_ms"]) >= settings.ops_slow_operation_ms
            ),
            "slow_operation_threshold_ms": settings.ops_slow_operation_ms,
            "last_operation_at": last_operation_at,
            "cache_hit_count": cache_hits,
            "cache_miss_count": cache_misses,
            "cache_bypass_count": cache_bypass,
        },
        "operations": operation_groups[:8],
        "recent_slow_operations": slow_operations[:8],
    }


def get_route_profile_snapshot() -> dict[str, Any]:
    with _lock:
        profiles = list(_recent_route_profiles)
        lifetime_profiles = int(_lifetime_route_profile_totals["profiles"])
        lifetime_slow = int(_lifetime_route_profile_totals["slow"])
        started_iso = _route_profile_started_iso
        uptime_seconds = max(0, int(time() - _route_profile_started_at))

    grouped: dict[str, dict[str, Any]] = {}
    for item in profiles:
        key = str(item.get("route_key") or "unknown")
        entry = grouped.setdefault(
            key,
            {
                "key": key,
                "count": 0,
                "durations": [],
                "last_at": None,
                "slow_count": 0,
                "timeout_count": 0,
                "stages": {},
            },
        )
        duration_ms = float(item.get("total_duration_ms") or 0.0)
        entry["count"] += 1
        entry["durations"].append(duration_ms)
        entry["last_at"] = item.get("at")
        if duration_ms >= settings.ops_slow_operation_ms:
            entry["slow_count"] += 1
        if str(item.get("status")) == "timeout":
            entry["timeout_count"] += 1
        for stage in list(item.get("stages") or []):
            stage_name = str(stage.get("name") or "unknown")
            stage_entry = entry["stages"].setdefault(
                stage_name,
                {"key": stage_name, "count": 0, "durations": [], "timeout_count": 0, "error_count": 0},
            )
            stage_entry["count"] += 1
            stage_entry["durations"].append(float(stage.get("duration_ms") or 0.0))
            stage_status = str(stage.get("status") or "ok")
            if stage_status == "timeout":
                stage_entry["timeout_count"] += 1
            elif stage_status != "ok":
                stage_entry["error_count"] += 1

    route_groups: list[dict[str, Any]] = []
    for entry in grouped.values():
        entry_durations = entry.pop("durations")
        stage_groups = []
        for stage_entry in entry.pop("stages").values():
            stage_durations = stage_entry.pop("durations")
            stage_entry["average_duration_ms"] = round(mean(stage_durations), 2) if stage_durations else 0.0
            stage_entry["p95_duration_ms"] = round(_p95(stage_durations), 2) if stage_durations else 0.0
            stage_entry["max_duration_ms"] = round(max(stage_durations), 2) if stage_durations else 0.0
            stage_groups.append(stage_entry)
        stage_groups.sort(key=lambda item: (-float(item["average_duration_ms"]), item["key"]))
        entry["average_duration_ms"] = round(mean(entry_durations), 2) if entry_durations else 0.0
        entry["p95_duration_ms"] = round(_p95(entry_durations), 2) if entry_durations else 0.0
        entry["max_duration_ms"] = round(max(entry_durations), 2) if entry_durations else 0.0
        entry["stages"] = stage_groups[:8]
        route_groups.append(entry)
    route_groups.sort(key=lambda item: (-float(item["average_duration_ms"]), item["key"]))

    return {
        "window_size": len(profiles),
        "lifetime_profiles": lifetime_profiles,
        "lifetime_slow_profiles": lifetime_slow,
        "started_at": started_iso,
        "uptime_seconds": uptime_seconds,
        "summary": {
            "total_profiles": len(profiles),
            "slow_profile_count": sum(
                1 for item in profiles if float(item.get("total_duration_ms") or 0.0) >= settings.ops_slow_operation_ms
            ),
            "slow_profile_threshold_ms": settings.ops_slow_operation_ms,
            "timeout_profile_count": sum(1 for item in profiles if str(item.get("status")) == "timeout"),
            "average_total_duration_ms": round(
                mean([float(item.get("total_duration_ms") or 0.0) for item in profiles]),
                2,
            ) if profiles else 0.0,
            "p95_total_duration_ms": round(
                _p95([float(item.get("total_duration_ms") or 0.0) for item in profiles]),
                2,
            ) if profiles else 0.0,
            "max_total_duration_ms": round(
                max(float(item.get("total_duration_ms") or 0.0) for item in profiles),
                2,
            ) if profiles else 0.0,
            "last_profile_at": profiles[-1]["at"] if profiles else None,
        },
        "routes": route_groups[:8],
        "recent_profiles": list(reversed(profiles[-8:])),
    }


def get_upstream_metrics_snapshot() -> dict[str, Any]:
    with _lock:
        upstream_calls = list(_recent_upstream_calls)
        timeout_calls = list(_recent_upstream_timeouts)
        lifetime_calls = int(_lifetime_upstream_totals["calls"])
        lifetime_timeouts = int(_lifetime_upstream_totals["timeouts"])
        lifetime_errors = int(_lifetime_upstream_totals["errors"])
        started_iso = _upstream_started_iso
        uptime_seconds = max(0, int(time() - _upstream_started_at))

    durations = [float(item["duration_ms"]) for item in upstream_calls]
    target_counts = Counter(item["target"] for item in upstream_calls)
    status_counts = Counter(item["status"] for item in upstream_calls)
    timeout_count = sum(1 for item in upstream_calls if item["status"] == "timeout")
    error_count = sum(1 for item in upstream_calls if item["status"] == "error")
    last_call_at = upstream_calls[-1]["at"] if upstream_calls else None
    grouped_targets: list[dict[str, Any]] = []
    for key, count in target_counts.most_common(8):
        grouped_targets.append(
            {
                "key": key,
                "count": count,
                "timeout_count": sum(
                    1 for item in upstream_calls if item["target"] == key and item["status"] == "timeout"
                ),
            }
        )

    return {
        "window_size": len(upstream_calls),
        "lifetime_calls": lifetime_calls,
        "lifetime_timeouts": lifetime_timeouts,
        "lifetime_errors": lifetime_errors,
        "started_at": started_iso,
        "uptime_seconds": uptime_seconds,
        "summary": {
            "total_calls": len(upstream_calls),
            "timeout_count": timeout_count,
            "error_count": error_count,
            "error_rate": round((error_count / len(upstream_calls)) * 100, 2) if upstream_calls else 0.0,
            "average_duration_ms": round(mean(durations), 2) if durations else 0.0,
            "p95_duration_ms": round(_p95(durations), 2) if durations else 0.0,
            "max_duration_ms": round(max(durations), 2) if durations else 0.0,
            "last_call_at": last_call_at,
        },
        "targets": grouped_targets,
        "status_buckets": [{"key": key, "count": count} for key, count in status_counts.most_common()],
        "recent_calls": list(reversed(upstream_calls[-8:])),
        "recent_timeouts": timeout_calls[:8],
    }
