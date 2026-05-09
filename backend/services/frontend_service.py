from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import Lock, Thread
from time import perf_counter, time
from typing import Any

from backend.core.config import settings
from backend.core.database import SessionLocal
from backend.schemas import AnalyzeRequest, ChartRequest, ScanRequest, WatchlistRequest
from backend.services.alerts_service import get_alerts_snapshot
from backend.services.auth_provider_service import get_auth_provider_config
from backend.services.event_calendar_service import build_event_calendar_snapshot
from backend.services.market_service import (
    analyze_market,
    build_watchlist_from_scan_payload,
    get_chart_payload,
    get_defaults,
    get_health,
    get_market_data_freshness_snapshot,
    run_scan,
)
from backend.services.portfolio_service import (
    get_open_trades,
    get_portfolio,
    get_portfolio_dashboard_snapshot,
    get_trade_journal,
)
from backend.services.workspace_service import list_workspaces
from backend.services.ticker_hub_service import get_ticker_hub
from backend.services.notes_service import get_notes_summary, list_notes
from backend.services.ops_service import (
    get_operation_metrics_snapshot,
    get_request_metrics_snapshot,
    get_route_profile_snapshot,
    get_upstream_metrics_snapshot,
    record_route_profile,
    record_operation,
)
from backend.services.billing_service import get_billing_ops_snapshot
from backend.services.job_queue_service import drain_due_jobs, get_job_metrics_snapshot, get_job_worker_status
from backend.services.deployment_service import get_deployment_readiness_snapshot
from backend.services.enterprise_readiness_service import (
    build_enterprise_readiness_snapshot,
    load_validation_tracker_snapshot,
)
from backend.services.readiness_service import get_production_readiness_snapshot, get_release_gate_snapshot
from backend.services.rate_limit_service import get_rate_limit_snapshot
from backend.services.tenant_service import get_tenant_launch_rollup
from backend.services.trade_service import get_order_lifecycle_health_snapshot
from backend import stock_direction_model as sdm

_bootstrap_cache_lock = Lock()
_bootstrap_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
_dashboard_cache_lock = Lock()
_dashboard_snapshot_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
_dashboard_snapshot_refresh_inflight: set[tuple[Any, ...]] = set()
_ops_status_cache_lock = Lock()
_ops_status_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
_ops_status_refresh_inflight: set[tuple[Any, ...]] = set()
_watchlist_prefetch_lock = Lock()
_watchlist_prefetch_inflight: set[tuple[Any, ...]] = set()
_desk_prefetch_lock = Lock()
_desk_prefetch_inflight: set[tuple[Any, ...]] = set()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PHASE_A_TRACKER_PATH = PROJECT_ROOT / "PERSONAL_USE.md"
PHASE_A_GO_LIVE_PATH = PROJECT_ROOT / "PERSONAL_USE.md"
PHASE_A_EXIT_REPORT_PATH = PROJECT_ROOT / "REAL_MONEY_EXECUTION_ROADMAP.md"


def _freeze_cache_key(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze_cache_key(inner)) for key, inner in value.items()))
    if isinstance(value, (list, tuple, set)):
        return tuple(_freeze_cache_key(item) for item in value)
    return value


def clear_frontend_snapshot_cache() -> None:
    with _bootstrap_cache_lock:
        _bootstrap_cache.clear()
    with _dashboard_cache_lock:
        _dashboard_snapshot_cache.clear()
        _dashboard_snapshot_refresh_inflight.clear()
    with _ops_status_cache_lock:
        _ops_status_cache.clear()
        _ops_status_refresh_inflight.clear()


def _get_cached_bootstrap(cache_key: tuple[Any, ...]) -> Any | None:
    ttl_seconds = max(0, int(settings.frontend_snapshot_cache_ttl_seconds))
    if ttl_seconds <= 0:
        return None
    now = time()
    with _bootstrap_cache_lock:
        cached = _bootstrap_cache.get(cache_key)
        if not cached:
            return None
        expires_at, payload = cached
        if expires_at <= now:
            _bootstrap_cache.pop(cache_key, None)
            return None
        return deepcopy(payload)


def _dashboard_snapshot_ttl_seconds() -> int:
    configured = max(0, int(settings.frontend_snapshot_cache_ttl_seconds))
    if configured <= 0:
        return 0
    return min(configured, 15)


def _dashboard_snapshot_stale_seconds() -> int:
    ttl_seconds = _dashboard_snapshot_ttl_seconds()
    if ttl_seconds <= 0:
        return 0
    return min(max(ttl_seconds * 6, 60), 180)


def _get_cached_dashboard_snapshot(cache_key: tuple[Any, ...], *, allow_stale: bool = False) -> Any | None:
    ttl_seconds = _dashboard_snapshot_ttl_seconds()
    if ttl_seconds <= 0:
        return None
    now = time()
    with _dashboard_cache_lock:
        cached = _dashboard_snapshot_cache.get(cache_key)
        if not cached:
            return None
        expires_at, payload = cached
        if expires_at <= now:
            stale_until = expires_at + _dashboard_snapshot_stale_seconds()
            if allow_stale and stale_until > now:
                return payload
            if stale_until <= now:
                _dashboard_snapshot_cache.pop(cache_key, None)
            return None
        return payload


def _store_cached_dashboard_snapshot(cache_key: tuple[Any, ...], payload: Any) -> None:
    ttl_seconds = _dashboard_snapshot_ttl_seconds()
    if ttl_seconds <= 0:
        return
    now = time()
    expires_at = now + ttl_seconds
    with _dashboard_cache_lock:
        stale_seconds = _dashboard_snapshot_stale_seconds()
        expired_keys = [key for key, (expiry, _) in _dashboard_snapshot_cache.items() if expiry + stale_seconds <= now]
        for key in expired_keys:
            _dashboard_snapshot_cache.pop(key, None)
        _dashboard_snapshot_cache[cache_key] = (expires_at, payload)


def _claim_dashboard_snapshot_refresh(cache_key: tuple[Any, ...]) -> bool:
    with _dashboard_cache_lock:
        if cache_key in _dashboard_snapshot_refresh_inflight:
            return False
        _dashboard_snapshot_refresh_inflight.add(cache_key)
        return True


def _release_dashboard_snapshot_refresh(cache_key: tuple[Any, ...]) -> None:
    with _dashboard_cache_lock:
        _dashboard_snapshot_refresh_inflight.discard(cache_key)


def _ops_status_ttl_seconds() -> int:
    configured = max(0, int(settings.frontend_snapshot_cache_ttl_seconds))
    if configured <= 0:
        return 0
    return min(configured, 15)


def _ops_status_stale_seconds() -> int:
    ttl_seconds = _ops_status_ttl_seconds()
    if ttl_seconds <= 0:
        return 0
    return min(max(ttl_seconds * 8, 60), 180)


def _get_cached_ops_status(cache_key: tuple[Any, ...], *, allow_stale: bool = False) -> Any | None:
    ttl_seconds = _ops_status_ttl_seconds()
    if ttl_seconds <= 0:
        return None
    now = time()
    with _ops_status_cache_lock:
        cached = _ops_status_cache.get(cache_key)
        if not cached:
            return None
        expires_at, payload = cached
        if expires_at <= now:
            stale_until = expires_at + _ops_status_stale_seconds()
            if allow_stale and stale_until > now:
                return payload
            if stale_until <= now:
                _ops_status_cache.pop(cache_key, None)
            return None
        return payload


def _store_cached_ops_status(cache_key: tuple[Any, ...], payload: Any) -> None:
    ttl_seconds = _ops_status_ttl_seconds()
    if ttl_seconds <= 0:
        return
    now = time()
    expires_at = now + ttl_seconds
    with _ops_status_cache_lock:
        stale_seconds = _ops_status_stale_seconds()
        expired_keys = [key for key, (expiry, _) in _ops_status_cache.items() if expiry + stale_seconds <= now]
        for key in expired_keys:
            _ops_status_cache.pop(key, None)
        _ops_status_cache[cache_key] = (expires_at, payload)


def _claim_ops_status_refresh(cache_key: tuple[Any, ...]) -> bool:
    with _ops_status_cache_lock:
        if cache_key in _ops_status_refresh_inflight:
            return False
        _ops_status_refresh_inflight.add(cache_key)
        return True


def _release_ops_status_refresh(cache_key: tuple[Any, ...]) -> None:
    with _ops_status_cache_lock:
        _ops_status_refresh_inflight.discard(cache_key)


def _store_cached_bootstrap(cache_key: tuple[Any, ...], payload: Any) -> None:
    ttl_seconds = max(0, int(settings.frontend_snapshot_cache_ttl_seconds))
    if ttl_seconds <= 0:
        return
    now = time()
    expires_at = now + ttl_seconds
    with _bootstrap_cache_lock:
        expired_keys = [key for key, (expiry, _) in _bootstrap_cache.items() if expiry <= now]
        for key in expired_keys:
            _bootstrap_cache.pop(key, None)
        _bootstrap_cache[cache_key] = (expires_at, deepcopy(payload))


def _build_review_loop_progress_summary(
    active_review_loop_notes: dict[str, Any] | None,
    resolved_review_loop_notes: dict[str, Any] | None,
) -> dict[str, Any]:
    active_payload = active_review_loop_notes if isinstance(active_review_loop_notes, dict) else {}
    resolved_payload = resolved_review_loop_notes if isinstance(resolved_review_loop_notes, dict) else {}
    resolved_items = resolved_payload.get("items") if isinstance(resolved_payload.get("items"), list) else []
    latest_resolved = resolved_items[0] if resolved_items else None

    return {
        "open_count": int(active_payload.get("total", active_payload.get("count", 0)) or 0),
        "resolved_count": int(resolved_payload.get("total", resolved_payload.get("count", 0)) or 0),
        "latest_resolved": latest_resolved,
    }


def _safe_call(func, fallback):
    try:
        return func()
    except Exception:
        return fallback


def _phase_a_doc_snapshot(path: Path, label: str) -> dict[str, Any]:
    exists = path.exists()
    return {
        "label": label,
        "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "exists": exists,
        "status": "ready" if exists else "missing",
    }


def _read_phase_a_tracker_snapshot() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    try:
        tracker_text = PHASE_A_TRACKER_PATH.read_text(encoding="utf-8")
    except OSError:
        return {
            "path": str(PHASE_A_TRACKER_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "count": 0,
            "completed_count": 0,
            "in_progress_count": 0,
            "queued_count": 0,
            "completed_items": [],
            "in_progress_items": [],
            "queued_items": [],
            "items": [],
        }

    for raw_line in tracker_text.splitlines():
        line = raw_line.strip()
        if not line or ". " not in line:
            continue
        prefix, remainder = line.split(". ", 1)
        if not prefix.isdigit():
            continue
        normalized = remainder.replace("`", "").strip()
        if not normalized.startswith("[") or "]" not in normalized:
            continue
        marker = normalized[1 : normalized.index("]")]
        if marker not in {"x", "~", " "}:
            continue
        text = normalized[normalized.index("]") + 1 :].strip()
        status = {"x": "completed", "~": "in_progress", " ": "queued"}[marker]
        items.append(
            {
                "index": int(prefix),
                "status": status,
                "text": text,
            }
        )

    completed_items = [item for item in items if item["status"] == "completed"]
    in_progress_items = [item for item in items if item["status"] == "in_progress"]
    queued_items = [item for item in items if item["status"] == "queued"]
    return {
        "path": str(PHASE_A_TRACKER_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "count": len(items),
        "completed_count": len(completed_items),
        "in_progress_count": len(in_progress_items),
        "queued_count": len(queued_items),
        "completed_items": completed_items,
        "in_progress_items": in_progress_items,
        "queued_items": queued_items,
        "items": items,
    }


def _summary_status_to_check(summary: dict[str, Any], label: str) -> dict[str, Any]:
    raw_status = str(summary.get("status") or "warning").strip().lower()
    if raw_status in {"ready", "healthy"}:
        status = "ready"
    elif raw_status in {"warning"}:
        status = "warning"
    else:
        status = "blocked"
    return {
        "key": label.lower().replace(" ", "_"),
        "label": label,
        "status": status,
        "message": str(summary.get("next_action") or f"{label} status is {raw_status}."),
    }


def get_phase_a_exit_snapshot(
    *,
    tenant_slug: str | None,
    readiness_snapshot: dict[str, Any],
    release_gates: dict[str, Any],
    deployment_snapshot: dict[str, Any],
    service_smoke: dict[str, Any],
) -> dict[str, Any]:
    tracker = _read_phase_a_tracker_snapshot()
    docs = [
        _phase_a_doc_snapshot(PHASE_A_GO_LIVE_PATH, "Personal-use boundary"),
        _phase_a_doc_snapshot(PHASE_A_EXIT_REPORT_PATH, "Real-money execution roadmap"),
    ]

    checklist = [
        {
            "key": "phase_a_tracker",
            "label": "Personal readiness checklist",
            "status": "ready" if tracker["queued_count"] == 0 and tracker["in_progress_count"] == 0 and tracker["count"] > 0 else "warning",
            "message": (
                f"{tracker['completed_count']} of {tracker['count']} personal readiness items are complete."
                if tracker["count"] > 0
                else "Personal readiness checklist items could not be loaded."
            ),
        },
        {
            "key": "liveness_probe",
            "label": "Liveness probe endpoint",
            "status": "ready",
            "message": "Deployment probe is available at /api/healthz.",
        },
        {
            "key": "readiness_probe",
            "label": "Readiness probe endpoint",
            "status": "ready",
            "message": "Deployment readiness probe is available at /api/readyz.",
        },
        {
            "key": "support_export",
            "label": "Local diagnostics export",
            "status": "ready",
            "message": "Local diagnostics export is available at /api/ops/diagnostics.",
        },
        _summary_status_to_check(readiness_snapshot.get("summary") or {}, "Personal readiness"),
        _summary_status_to_check(release_gates.get("summary") or {}, "Release gates"),
        _summary_status_to_check(service_smoke.get("summary") or {}, "Core service smoke"),
        _summary_status_to_check(deployment_snapshot.get("summary") or {}, "Deployment readiness"),
        {
            "key": "phase_a_docs",
            "label": "Personal docs available",
            "status": "ready" if all(item["exists"] for item in docs) else "blocked",
            "message": (
                "Personal-use boundary and real-money execution roadmap are available."
                if all(item["exists"] for item in docs)
                else "Personal-use boundary or real-money execution roadmap is missing."
            ),
        },
    ]

    blocked = [item for item in checklist if item["status"] == "blocked"]
    warnings = [item for item in checklist if item["status"] == "warning"]
    ready = [item for item in checklist if item["status"] == "ready"]
    status = "blocked" if blocked else "warning" if warnings else "ready"

    return {
        "tenant": {"slug": tenant_slug},
        "summary": {
            "status": status,
            "completed_checks": len(ready),
            "warning_checks": len(warnings),
            "blocked_checks": len(blocked),
            "total_checks": len(checklist),
            "tracker_completed": tracker["completed_count"],
            "tracker_total": tracker["count"],
            "next_action": (
                blocked[0]["message"]
                if blocked
                else warnings[0]["message"]
                if warnings
                else "Personal readiness criteria are in place."
            ),
        },
        "tracker": tracker,
        "docs": docs,
        "checklist": checklist,
        "remaining_items": tracker["queued_items"] + tracker["in_progress_items"],
        "probe_endpoints": {
            "liveness": "/api/healthz",
            "readiness": "/api/readyz",
            "diagnostics_export": "/api/ops/diagnostics",
        },
    }


def get_support_diagnostics_export(user_id: str, tenant_slug: str | None = None) -> dict[str, Any]:
    ops_status = get_operations_status(user_id, tenant_slug)
    return {
        "generated_at": ops_status.get("timestamp"),
        "capture": {
            "format": "personal-desk-support-v1",
            "source": "release_center",
        },
        "release": get_release_info(),
        "release_notes": get_release_notes(),
        "ops": ops_status,
    }


def _build_service_smoke_snapshot(
    *,
    tenant_slug: str | None,
    auth_config: dict[str, Any],
    billing_ops: dict[str, Any],
    market_data: dict[str, Any],
    job_metrics: dict[str, Any],
    worker_status: dict[str, Any],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    auth_mode = str(auth_config.get("mode") or "unknown").strip().lower()
    available_providers = list(auth_config.get("available_providers") or [])
    ready_provider_count = sum(
        1
        for provider in available_providers
        if provider.get("enabled") and (provider.get("mode") == "form" or provider.get("ready"))
    )
    if auth_mode == "configured" and ready_provider_count > 0:
        checks.append(
            {
                "key": "auth",
                "label": "Auth",
                "status": "ready",
                "message": f"{ready_provider_count} auth provider(s) are ready.",
            }
        )
    elif auth_mode == "demo":
        checks.append(
            {
                "key": "auth",
                "label": "Auth",
                "status": "warning",
                "message": "Auth is still running in demo mode.",
            }
        )
    else:
        checks.append(
            {
                "key": "auth",
                "label": "Auth",
                "status": "blocked",
                "message": "Configured auth providers are not ready.",
            }
        )

    billing_summary = dict(billing_ops.get("summary") or {})
    billing_sync = dict(billing_ops.get("sync") or {})
    billing_sync_status = str(billing_sync.get("status") or "unknown").strip().lower()
    failed_event_count = int(billing_summary.get("failed_event_count", 0) or 0)
    if billing_sync_status in {"ready", "healthy"} and failed_event_count == 0 and not billing_summary.get("needs_attention"):
        billing_status = "ready"
        billing_message = "Billing sync and recovery state look healthy."
    elif billing_sync_status in {"error", "failed", "blocked"} or failed_event_count > 0:
        billing_status = "blocked"
        billing_message = str(billing_sync.get("message") or billing_summary.get("message") or "Billing sync has blocking failures.")
    else:
        billing_status = "warning"
        billing_message = str(billing_sync.get("message") or billing_summary.get("message") or "Billing needs operator review.")
    checks.append(
        {
            "key": "billing",
            "label": "Billing",
            "status": billing_status,
            "message": billing_message,
        }
    )

    market_status_value = str(market_data.get("status") or "unknown").strip().lower()
    if market_status_value in {"fresh", "ready"}:
        market_status = "ready"
        market_message = str(market_data.get("message") or "Market data freshness checks are healthy.")
    elif market_status_value in {"warning", "idle"}:
        market_status = "warning"
        market_message = str(market_data.get("message") or "Market data has freshness warnings.")
    else:
        market_status = "blocked" if market_data.get("feed_expected") else "warning"
        market_message = str(market_data.get("message") or "Market data freshness checks are degraded.")
    checks.append(
        {
            "key": "market",
            "label": "Market",
            "status": market_status,
            "message": market_message,
        }
    )

    job_summary = dict(job_metrics.get("summary") or {})
    dead_letters = int(job_summary.get("dead_letter", 0) or 0)
    retrying = int(job_summary.get("retrying", 0) or 0)
    recent_failures = int(job_summary.get("recent_failure_count", 0) or 0)
    if worker_status.get("enabled") and not worker_status.get("running"):
        job_status = "blocked"
        job_message = "Background worker is enabled but not running."
    elif worker_status.get("stale") or worker_status.get("status") == "running_but_stale":
        job_status = "warning"
        job_message = "Background worker is running but stale."
    elif dead_letters > 0:
        job_status = "blocked"
        job_message = "Dead-letter jobs are present."
    elif retrying > 0 or recent_failures > 0:
        job_status = "warning"
        job_message = "Background jobs are retrying or have recent failures."
    else:
        job_status = "ready"
        job_message = "Background job subsystem is healthy."
    checks.append(
        {
            "key": "jobs",
            "label": "Jobs",
            "status": job_status,
            "message": job_message,
        }
    )

    blocked = [item for item in checks if item["status"] == "blocked"]
    warnings = [item for item in checks if item["status"] == "warning"]
    ready = [item for item in checks if item["status"] == "ready"]
    overall_status = "blocked" if blocked else "warning" if warnings else "ready"

    return {
        "tenant": {"slug": tenant_slug},
        "summary": {
            "status": overall_status,
            "ready_checks": len(ready),
            "warning_checks": len(warnings),
            "blocked_checks": len(blocked),
            "total_checks": len(checks),
            "blockers": [item["message"] for item in blocked],
            "warnings": [item["message"] for item in warnings],
            "next_action": (
                blocked[0]["message"]
                if blocked
                else warnings[0]["message"]
                if warnings
                else "Core pilot service checks are healthy."
            ),
        },
        "checks": checks,
    }


def _profile_stage(stages: list[dict[str, Any]], name: str, func):
    started_at = perf_counter()
    try:
        result = func()
    except Exception:
        stages.append(
            {
                "name": name,
                "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                "status": "error",
            }
        )
        raise
    stages.append(
        {
            "name": name,
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            "status": "ok",
        }
    )
    return result


_BOOTSTRAP_DEFAULT_KEYS = (
    "default_scan_tickers",
    "supported_intervals",
    "default_interval",
    "default_horizon",
    "live_update_seconds",
)


def _normalize_bootstrap_consumer(consumer: str | None) -> str:
    normalized = str(consumer or "full").strip().lower()
    if normalized in {"shell", "app", "dashboard"}:
        return "shell"
    if normalized in {"watchlist", "alerts", "full"}:
        return normalized
    return "full"


def _minimal_bootstrap_defaults(defaults: dict[str, Any]) -> dict[str, Any]:
    return {
        key: defaults.get(key)
        for key in _BOOTSTRAP_DEFAULT_KEYS
        if key in defaults
    }


def _prefetch_watchlist_market_data(defaults: dict[str, Any]) -> None:
    tickers = [
        str(ticker or "").strip().upper()
        for ticker in (defaults.get("default_scan_tickers") or [])
        if str(ticker or "").strip()
    ][:4]
    interval = str(defaults.get("default_interval") or "").strip().lower() or "5m"
    if not tickers:
        return
    period = sdm.get_period_for_interval(interval)
    prefetch_key = (tuple(tickers), period, interval)
    with _watchlist_prefetch_lock:
        if prefetch_key in _watchlist_prefetch_inflight:
            return
        _watchlist_prefetch_inflight.add(prefetch_key)

    def _run() -> None:
        try:
            sdm.batch_download_ohlcv(tickers, period, interval)
            sdm.batch_get_live_prices(tickers)
        except Exception:
            return
        finally:
            with _watchlist_prefetch_lock:
                _watchlist_prefetch_inflight.discard(prefetch_key)

    worker = Thread(target=_run, name="watchlist-prefetch", daemon=True)
    worker.start()


def _initial_chart_points_for_interval(interval: str) -> int:
    normalized = str(interval or "").strip().lower()
    point_map = {
        "1m": 1800,
        "5m": 600,
        "15m": 320,
        "30m": 240,
        "1h": 180,
        "4h": 180,
        "1d": 365,
    }
    return point_map.get(normalized, 600)


def _prefetch_desk_market_data(defaults: dict[str, Any]) -> None:
    ticker = str(defaults.get("default_ticker") or "SPY").strip().upper() or "SPY"
    interval = str(defaults.get("default_interval") or "5m").strip().lower() or "5m"
    horizon = max(1, int(defaults.get("default_horizon") or 5))
    points_limit = _initial_chart_points_for_interval(interval)
    prefetch_key = (ticker, interval, horizon, points_limit)
    with _desk_prefetch_lock:
        if prefetch_key in _desk_prefetch_inflight:
            return
        _desk_prefetch_inflight.add(prefetch_key)

    def _run() -> None:
        try:
            get_chart_payload(
                ChartRequest(
                    ticker=ticker,
                    interval=interval,
                    points_limit=points_limit,
                )
            )
            analyze_market(
                AnalyzeRequest(
                    ticker=ticker,
                    interval=interval,
                    horizon=horizon,
                    include_live_price=True,
                    include_history=False,
                    include_contract_lookup=True,
                    include_event_lookup=True,
                    include_alignment=True,
                    use_fast_model=True,
                )
            )
        except Exception:
            return
        finally:
            with _desk_prefetch_lock:
                _desk_prefetch_inflight.discard(prefetch_key)

    worker = Thread(target=_run, name="desk-prefetch", daemon=True)
    worker.start()


def _prefetch_desk_dashboard_snapshot(current_user: Any) -> None:
    tenant_slug = str(getattr(current_user, "tenant_slug", "") or getattr(current_user, "active_tenant_slug", "") or "")
    prefetch_key = (tenant_slug, "desk")
    with _desk_prefetch_lock:
        if prefetch_key in _desk_prefetch_inflight:
            return
        _desk_prefetch_inflight.add(prefetch_key)

    def _run() -> None:
        try:
            with SessionLocal() as db:
                get_dashboard_snapshot(current_user=current_user, db=db, consumer="desk")
        except Exception:
            return
        finally:
            with _desk_prefetch_lock:
                _desk_prefetch_inflight.discard(prefetch_key)

    worker = Thread(target=_run, name="desk-dashboard-prefetch", daemon=True)
    worker.start()


def _start_dashboard_snapshot_refresh(
    *,
    cache_key: tuple[Any, ...],
    current_user: Any | None,
    consumer: str | None,
    account_profile: str | None,
    linked_account_id: str | None,
) -> None:
    def _run() -> None:
        try:
            with SessionLocal() as db:
                get_dashboard_snapshot(
                    current_user=current_user,
                    db=db,
                    consumer=consumer,
                    account_profile=account_profile,
                    linked_account_id=linked_account_id,
                    _force_refresh=True,
                )
        except Exception:
            return
        finally:
            _release_dashboard_snapshot_refresh(cache_key)

    worker = Thread(target=_run, name="dashboard-refresh", daemon=True)
    worker.start()


def _build_bootstrap_watchlist_preview(defaults: dict[str, Any]) -> dict[str, Any]:
    tickers = [
        str(ticker or "").strip().upper()
        for ticker in (defaults.get("default_scan_tickers") or [])
        if str(ticker or "").strip()
    ][:4]
    board_name = "Controlled liquid ranking board"
    rows: list[dict[str, Any]] = []
    for index, ticker in enumerate(tickers, start=1):
        ranking_score = max(78.0 - ((index - 1) * 6.5), 52.0)
        ranking_tier = "promote" if index <= 2 else "review"
        ranking_label = "Promote first" if ranking_tier == "promote" else "Reviewable"
        rows.append(
            {
                "ticker": ticker,
                "trade_decision": "WATCH",
                "verdict": "Watching",
                "conviction_label": "FORMING",
                "ranking_score": ranking_score,
                "ranking_label": ranking_label,
                "ranking_tier": ranking_tier,
                "ranking_summary": f"Bootstrap preview keeps {ticker} near the top of the controlled liquid board until the fresh scan finishes.",
                "board_rank": index,
                "ranking_context": {
                    "board_name": board_name,
                    "board_short_name": "Liquid board",
                    "controlled_universe": True,
                    "score": ranking_score,
                    "tier": ranking_tier,
                    "tone": "positive" if ranking_tier == "promote" else "warning",
                    "label": ranking_label,
                    "summary": f"Bootstrap preview rank for {ticker}.",
                    "component_summary": "Bootstrap preview",
                    "board_rank": index,
                    "board_gap": 0.0 if index == 1 else round((index - 1) * 1.4, 1),
                    "leader": index == 1,
                },
                "source": "bootstrap-preview",
            }
        )

    ranking_board = {
        "board_name": board_name,
        "leader": rows[0] if rows else None,
        "promote_count": sum(1 for row in rows if str(row.get("ranking_tier") or "").strip().lower() == "promote"),
        "review_count": sum(1 for row in rows if str(row.get("ranking_tier") or "").strip().lower() == "review"),
        "stand_down_count": 0,
        "visible_count": len(rows),
    }
    return {
        "summary": {
            "valid_trades": 0,
            "high_conviction": 0,
            "entry_now": 0,
            "ranking_board": ranking_board,
        },
        "rows": rows,
        "results": rows,
        "count": len(rows),
        "validation_artifact": {
            "artifact_type": "candidate_board_snapshot",
            "source": "bootstrap-preview",
            "board_name": board_name,
            "interval": defaults.get("default_interval"),
            "horizon": defaults.get("default_horizon"),
            "summary": {
                "candidate_count": len(rows),
                "leader_ticker": (rows[0] or {}).get("ticker") if rows else None,
            },
        },
        "errors": [],
    }


def get_frontend_bootstrap(
    user_id: str,
    tenant_slug: str | None = None,
    tenant_name: str | None = None,
    tenant_brand_settings: dict[str, Any] | None = None,
    tenant_logo_url: str | None = None,
    tenant_delivery_settings: dict[str, Any] | None = None,
    consumer: str | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    stages: list[dict[str, Any]] = []
    normalized_consumer = _normalize_bootstrap_consumer(consumer)
    cache_key = (
        str(user_id or ""),
        str(tenant_slug or ""),
        str(tenant_name or ""),
        _freeze_cache_key(tenant_brand_settings or {}),
        str(tenant_logo_url or ""),
        _freeze_cache_key(tenant_delivery_settings or {}),
        normalized_consumer,
    )
    started_at = perf_counter()
    cached_payload = _get_cached_bootstrap(cache_key)
    if cached_payload is not None:
        stages.append({"name": "cache_lookup", "duration_ms": round((perf_counter() - started_at) * 1000, 2), "status": "ok"})
        record_route_profile(
            route_key="frontend.bootstrap",
            total_duration_seconds=perf_counter() - started_at,
            stages=stages,
            context={"tenant_slug": tenant_slug or "", "user_id": str(user_id or ""), "cache_status": "hit", "consumer": normalized_consumer},
        )
        record_operation(
            name="frontend.bootstrap",
            duration_seconds=perf_counter() - started_at,
            cache_status="hit",
            context={"tenant_slug": tenant_slug or "", "user_id": str(user_id or ""), "consumer": normalized_consumer},
        )
        return cached_payload

    stages.append({"name": "cache_lookup", "duration_ms": round((perf_counter() - started_at) * 1000, 2), "status": "ok"})
    defaults = (
        _profile_stage(stages, "defaults", get_defaults)
        if normalized_consumer in {"shell", "watchlist", "full"}
        else {}
    )
    if normalized_consumer in {"shell", "full"} and defaults:
        _prefetch_desk_market_data(defaults)
    if normalized_consumer in {"shell", "full"} and current_user is not None:
        _prefetch_desk_dashboard_snapshot(current_user)
    if normalized_consumer in {"watchlist", "full"} and defaults:
        _prefetch_watchlist_market_data(defaults)
    brand_settings = tenant_brand_settings or {}
    app_name = str(brand_settings.get("app_name") or "").strip() or "Stock Options Signal Dashboard"
    app_tagline = str(brand_settings.get("app_tagline") or "").strip() or "Probability-driven trade ideas, live tracking, and portfolio intelligence."
    payload = _profile_stage(stages, "assemble_payload", lambda: {
        "app": {
            "name": app_name,
            "tagline": app_tagline,
            "environment": settings.environment,
            "version": settings.app_version,
            "tenant_name": tenant_name,
            "logo_url": tenant_logo_url,
            "brand_settings": brand_settings,
            "delivery_settings": tenant_delivery_settings or {},
        },
        **(
            {"defaults": _minimal_bootstrap_defaults(defaults)}
            if normalized_consumer in {"shell", "watchlist", "full"}
            else {}
        ),
        **(
            {"watchlist_preview": _build_bootstrap_watchlist_preview(defaults)}
            if normalized_consumer in {"watchlist", "full"}
            else {}
        ),
    })

    if normalized_consumer == "full":
        alerts = _profile_stage(
            stages,
            "alerts",
            lambda: _safe_call(
                lambda: get_alerts_snapshot(limit=6, current_user=current_user),
                {'alerts': [], 'count': 0, 'total': 0},
            ),
        )
        workspaces = _profile_stage(stages, "workspaces", lambda: _safe_call(lambda: list_workspaces(user_id, tenant_slug=tenant_slug), {'items': [], 'count': 0}))
        payload["alerts"] = alerts
        payload["presets"] = {
            "scanner": {
                "tickers": defaults["default_scan_tickers"][:8],
                "interval": defaults["default_interval"],
                "horizon": defaults["default_horizon"],
                "top_n": 8,
            },
            "watchlist": {
                "tickers": defaults["default_scan_tickers"][:8],
                "interval": defaults["default_interval"],
                "horizon": defaults["default_horizon"],
                "limit": 8,
                "sort_by": "ranking_score",
                "descending": True,
            },
        }
        payload["workspace_count"] = workspaces["count"]
        payload["ticker_hub"] = _profile_stage(
            stages,
            "ticker_hub",
            lambda: _safe_call(get_ticker_hub, {"favorites": [], "recents": [], "favorite_count": 0, "recent_count": 0}),
        )
    _profile_stage(stages, "cache_store", lambda: _store_cached_bootstrap(cache_key, payload))
    record_route_profile(
        route_key="frontend.bootstrap",
        total_duration_seconds=perf_counter() - started_at,
        stages=stages,
        context={"tenant_slug": tenant_slug or "", "user_id": str(user_id or ""), "cache_status": "miss", "consumer": normalized_consumer},
    )
    record_operation(
        name="frontend.bootstrap",
        duration_seconds=perf_counter() - started_at,
        cache_status="miss",
        context={"tenant_slug": tenant_slug or "", "user_id": str(user_id or ""), "consumer": normalized_consumer},
    )
    return payload


def get_dashboard_snapshot(
    *,
    current_user: Any | None = None,
    db: Any | None = None,
    consumer: str | None = None,
    account_profile: str | None = None,
    linked_account_id: str | None = None,
    _force_refresh: bool = False,
) -> dict[str, Any]:
    started_at = perf_counter()
    stages: list[dict[str, Any]] = []
    normalized_consumer = str(consumer or "full").strip().lower()
    tenant_slug = ""
    if current_user is not None:
        tenant_slug = str(getattr(current_user, "active_tenant_slug", "") or "")
    normalized_account_profile = str(account_profile or "personal_paper").strip().lower() or "personal_paper"
    normalized_linked_account_id = str(linked_account_id or "").strip()
    cache_key = (tenant_slug, normalized_consumer, normalized_account_profile, normalized_linked_account_id)
    stages.append({"name": "cache_lookup", "duration_ms": round((perf_counter() - started_at) * 1000, 2), "status": "ok"})
    if not _force_refresh:
        cached_payload = _get_cached_dashboard_snapshot(cache_key)
        if cached_payload is not None:
            record_route_profile(
                route_key="frontend.dashboard_snapshot",
                total_duration_seconds=perf_counter() - started_at,
                stages=stages,
                context={
                    "tenant_slug": tenant_slug,
                    "ticker_count": 0,
                    "consumer": normalized_consumer,
                    "account_profile": normalized_account_profile,
                    "linked_account_id": normalized_linked_account_id,
                    "cache_status": "hit",
                },
            )
            record_operation(
                name="frontend.dashboard_snapshot",
                duration_seconds=perf_counter() - started_at,
                cache_status="hit",
                context={
                    "tenant_slug": tenant_slug,
                    "ticker_count": 0,
                    "consumer": normalized_consumer,
                    "account_profile": normalized_account_profile,
                    "linked_account_id": normalized_linked_account_id,
                },
            )
            return cached_payload
        stale_payload = _get_cached_dashboard_snapshot(cache_key, allow_stale=True)
        if stale_payload is not None:
            stages.append({"name": "stale_cache_lookup", "duration_ms": round((perf_counter() - started_at) * 1000, 2), "status": "ok"})
            if _claim_dashboard_snapshot_refresh(cache_key):
                _start_dashboard_snapshot_refresh(
                    cache_key=cache_key,
                    current_user=current_user,
                    consumer=consumer,
                    account_profile=account_profile,
                    linked_account_id=linked_account_id,
                )
            record_route_profile(
                route_key="frontend.dashboard_snapshot",
                total_duration_seconds=perf_counter() - started_at,
                stages=stages,
                context={
                    "tenant_slug": tenant_slug,
                    "ticker_count": 0,
                    "consumer": normalized_consumer,
                    "account_profile": normalized_account_profile,
                    "linked_account_id": normalized_linked_account_id,
                    "cache_status": "stale_hit",
                },
            )
            record_operation(
                name="frontend.dashboard_snapshot",
                duration_seconds=perf_counter() - started_at,
                cache_status="hit",
                context={
                    "tenant_slug": tenant_slug,
                    "ticker_count": 0,
                    "consumer": normalized_consumer,
                    "account_profile": normalized_account_profile,
                    "linked_account_id": normalized_linked_account_id,
                    "stale": True,
                },
            )
            return stale_payload
    defaults = _profile_stage(stages, "defaults", get_defaults)
    dashboard_ticker_limit = 3 if normalized_consumer == "desk" else 6
    dashboard_tickers = defaults["default_scan_tickers"][:dashboard_ticker_limit]
    scan_request = ScanRequest(
        tickers=dashboard_tickers,
        interval=defaults["default_interval"],
        horizon=defaults["default_horizon"],
        top_n=6,
        include_errors=True,
        include_contract_lookup=False,
        include_event_lookup=False,
        include_alignment=False,
        use_fast_model=True,
    )
    watchlist_request = WatchlistRequest(
        tickers=dashboard_tickers,
        interval=defaults["default_interval"],
        horizon=defaults["default_horizon"],
        limit=6,
        sort_by="ranking_score",
        descending=True,
        include_contract_lookup=False,
        include_event_lookup=False,
        include_alignment=False,
        use_fast_model=True,
    )
    portfolio = _profile_stage(
        stages,
        "portfolio",
        lambda: _safe_call(
            lambda: get_portfolio_dashboard_snapshot(
                db=db,
                current_user=current_user,
                account_profile=normalized_account_profile,
                linked_account_id=normalized_linked_account_id or None,
            ),
            {
                'summary': {},
                'trade_summary': {},
                'attribution_summary': {
                    'total_reviewed': 0,
                    'execution_review_count': 0,
                    'thesis_review_count': 0,
                    'risk_review_count': 0,
                    'clean_win_count': 0,
                    'flat_review_count': 0,
                    'latest_review': None,
                },
                'capital_preservation': {
                    'today_realized_pnl': 0.0,
                    'today_closed_trades': 0,
                    'consecutive_losses': 0,
                    'open_position_count': 0,
                    'pending_order_count': 0,
                    'active_ticket_count': 0,
                },
                'open_trades': [],
                'pending_orders': [],
                'monitored_open_trades': [],
                'order_events': {'items': [], 'count': 0, 'status_counts': {}},
                'broker_account': {},
            },
        ),
    )
    scan_payload = _profile_stage(
        stages,
        "scan",
        lambda: _safe_call(lambda: run_scan(scan_request), {'interval': defaults['default_interval'], 'horizon': defaults['default_horizon'], 'tickers_requested': dashboard_tickers, 'result_count': 0, 'results': [], 'errors': []}),
    )
    watchlist = _profile_stage(
        stages,
        "watchlist",
        lambda: _safe_call(lambda: build_watchlist_from_scan_payload(scan_payload, watchlist_request), {'summary': {'valid_trades': 0, 'high_conviction': 0, 'entry_now': 0}, 'rows': [], 'results': [], 'count': 0, 'errors': []}),
    )
    event_calendar = _profile_stage(
        stages,
        "event_calendar",
        lambda: _safe_call(
            lambda: build_event_calendar_snapshot(watchlist_rows=watchlist.get("results") or watchlist.get("rows") or []),
            {"count": 0, "total": 0, "items": [], "summary": {"macro_count": 0, "ticker_count": 0, "high_impact_count": 0, "caution_count": 0, "next_item": None, "board_label": "Macro and catalyst calendar"}},
        ),
    )
    review_loop_notes = _profile_stage(
        stages,
        "review_loop_notes",
        lambda: _safe_call(
            lambda: list_notes(status='active', tag='review-loop', completed='open', limit=4, sort_by='updated_desc'),
            {'items': [], 'count': 0, 'tags': [], 'tickers': [], 'owners': []},
        ),
    )
    review_loop_progress = _profile_stage(
        stages,
        "review_loop_progress",
        lambda: _safe_call(
            lambda: _build_review_loop_progress_summary(
                review_loop_notes,
                list_notes(status='all', tag='review-loop', completed='completed', limit=3, sort_by='updated_desc'),
            ),
            {'open_count': 0, 'resolved_count': 0, 'latest_resolved': None},
        ),
    )
    payload = _profile_stage(stages, "assemble_payload", lambda: {
        "scan": scan_payload,
        "watchlist": watchlist,
        "event_calendar": event_calendar,
        "portfolio": portfolio,
        "review_loop_notes": review_loop_notes,
        "review_loop_progress": review_loop_progress,
        **({"health": get_health(), "defaults": defaults} if normalized_consumer != "desk" else {}),
    })
    _profile_stage(stages, "cache_store", lambda: _store_cached_dashboard_snapshot(cache_key, payload))
    record_route_profile(
        route_key="frontend.dashboard_snapshot",
        total_duration_seconds=perf_counter() - started_at,
        stages=stages,
        context={
            "tenant_slug": tenant_slug,
            "ticker_count": len(dashboard_tickers),
            "consumer": normalized_consumer,
            "account_profile": normalized_account_profile,
            "linked_account_id": normalized_linked_account_id,
            "cache_status": "miss",
        },
    )
    record_operation(
        name="frontend.dashboard_snapshot",
        duration_seconds=perf_counter() - started_at,
        cache_status="bypass",
        context={
            "tenant_slug": tenant_slug,
            "ticker_count": len(dashboard_tickers),
            "consumer": normalized_consumer,
            "account_profile": normalized_account_profile,
            "linked_account_id": normalized_linked_account_id,
        },
    )
    return payload


def get_frontend_workspace_snapshot() -> dict[str, Any]:
    return {
        "dashboard": _safe_call(get_dashboard_snapshot, {'health': get_health(), 'defaults': get_defaults(), 'scan': {'results': [], 'errors': [], 'count': 0}, 'watchlist': {'rows': [], 'count': 0, 'summary': {}}, 'event_calendar': {'count': 0, 'total': 0, 'items': [], 'summary': {'macro_count': 0, 'ticker_count': 0, 'high_impact_count': 0, 'caution_count': 0, 'next_item': None, 'board_label': 'Macro and catalyst calendar'}}, 'portfolio': {'summary': {}, 'trade_summary': {}, 'attribution_summary': {'total_reviewed': 0, 'execution_review_count': 0, 'thesis_review_count': 0, 'risk_review_count': 0, 'clean_win_count': 0, 'flat_review_count': 0, 'latest_review': None}, 'monitored_open_trades': [], 'order_events': {'items': [], 'count': 0, 'status_counts': {}}}, 'review_loop_notes': {'items': [], 'count': 0, 'tags': [], 'tickers': [], 'owners': []}, 'review_loop_progress': {'open_count': 0, 'resolved_count': 0, 'latest_resolved': None}}),
        "open_trades": _safe_call(lambda: get_open_trades(limit=10, offset=0, search=""), {'open_trades': [], 'monitor': [], 'count': 0, 'total': 0, 'limit': 10, 'offset': 0, 'action_filter': 'all', 'order_events': {'items': [], 'count': 0, 'status_counts': {}}}),
        "journal": _safe_call(lambda: get_trade_journal(limit=10, offset=0, search=""), {'journal': [], 'replay': [], 'count': 0, 'total': 0, 'limit': 10, 'offset': 0}),
    }


def get_frontend_filters() -> dict[str, Any]:
    defaults = get_defaults()
    return {
        "intervals": defaults.get("supported_intervals", []),
        "directions": ["CALL", "PUT"],
        "journal_results": ["all", "win", "loss"],
        "sort_fields": ["ranking_score", "setup_score", "verdict", "trade_decision", "ticker", "live_price"],
        "trade_actions": ["all", "HOLD", "SELL 50% NOW", "SELL MORE NOW", "EXIT FULLY NOW", "STOP HIT", "TIME STOP", "DATA ISSUE"],
        "compare_metrics": ["setup_score", "probability_up", "live_price"],
        "alert_severities": ["all", "critical", "high", "medium", "low"],
        "alert_sources": ["all", "watchlist", "trade_monitor", "macro_calendar"],
        "workspace_pages": ["all", "dashboard", "compare", "watchlist", "trades", "journal", "portfolio", "alerts", "activity", "settings"],
        "activity_types": ["all", "alert", "workspace", "portfolio"],
        "note_statuses": ["all", "active", "archived"],
        "note_priorities": ["all", "high", "medium", "low"],
        "note_sorts": ["updated_desc", "updated_asc", "created_desc", "created_asc", "priority", "title", "due_asc", "due_desc"],
        "note_types": ["all", "general", "trade_idea", "risk_review", "market_note", "todo"],
        "note_due_states": ["all", "none", "upcoming", "today", "overdue", "completed"],
        "note_completion_states": ["all", "open", "completed"],
        "note_link_filters": ["all", "yes", "no"],
        "note_checklist_states": ["all", "none", "open", "done"],
        "note_reminder_states": ["all", "none", "scheduled", "today", "due", "upcoming"],
        "note_recurrences": ["all", "none", "daily", "weekly", "weekdays", "monthly"],
        "note_blocked_states": ["all", "ready", "blocked"],
        "note_progress_states": ["all", "not_started", "planned", "in_progress", "done"],
        "note_bulk_actions": ["complete", "reopen", "archive", "restore", "pin", "unpin", "delete"],
        "note_snooze_presets": [{"label": "30m", "minutes": 30}, {"label": "2h", "minutes": 120}, {"label": "Tomorrow", "minutes": 1440}],
        "ticker_hub": {"favorite_limit": 24, "recent_limit": 18},
    }


def get_release_info() -> dict[str, Any]:
    return {
        "version": settings.app_version,
        "phase": settings.app_phase,
        "environment": settings.environment,
        "api_prefix": settings.api_prefix,
        "highlights": [
            "FastAPI backend modularized for React consumption",
            "React workspace includes dashboard, watchlist, trades, journal, and portfolio pages",
            "CSV export and live monitoring controls are active",
            "Alerts center aggregates watchlist, trade monitor, and macro event signals",
            "Deployment files included for local Docker-based launch",
        ],
        "status": "stable-preview",
    }


def get_frontend_activity(
    user_id: str,
    tenant_slug: str | None = None,
    *,
    search: str = '',
    severity: str = 'all',
    activity_type: str = 'all',
    limit: int = 12,
    current_user: Any | None = None,
) -> dict[str, Any]:
    alerts = _safe_call(lambda: get_alerts_snapshot(limit=50, current_user=current_user), {'alerts': [], 'count': 0, 'total': 0})
    workspaces = _safe_call(lambda: list_workspaces(user_id, tenant_slug=tenant_slug), {'items': [], 'count': 0})
    portfolio = _safe_call(lambda: get_portfolio(current_user=current_user), {'summary': {}})
    summary = portfolio.get('summary', {})
    items: list[dict[str, Any]] = []
    for alert in alerts.get('alerts', []):
        items.append({
            'type': 'alert',
            'title': alert.get('title', 'Alert'),
            'detail': alert.get('message', ''),
            'severity': alert.get('severity', 'low'),
            'context': alert.get('context', {}),
        })
    for workspace in workspaces.get('items', [])[:10]:
        page_name = str(workspace.get('page', 'dashboard')).title()
        items.append({
            'type': 'workspace',
            'title': workspace.get('name', 'Saved workspace'),
            'detail': f'{page_name} workspace updated',
            'severity': 'low',
            'context': {'updated_at': workspace.get('updated_at', '—'), 'page': workspace.get('page', 'dashboard')},
        })
    items.append({
        'type': 'portfolio',
        'title': 'Portfolio snapshot',
        'detail': f"{summary.get('open_trade_count', 0)} open trades · realized PnL {summary.get('total_realized_pnl', 0)}",
        'severity': 'medium',
        'context': summary,
    })
    needle = str(search or '').strip().lower()
    if needle:
        items = [item for item in items if needle in str(item.get('title', '')).lower() or needle in str(item.get('detail', '')).lower()]
    sev_filter = str(severity or 'all').lower()
    if sev_filter != 'all':
        items = [item for item in items if str(item.get('severity', 'low')).lower() == sev_filter]
    type_filter = str(activity_type or 'all').lower()
    if type_filter != 'all':
        items = [item for item in items if str(item.get('type', '')).lower() == type_filter]
    visible = items[:max(1, int(limit))]
    return {
        'items': visible,
        'count': len(visible),
        'workspace_count': workspaces.get('count', 0),
        'alert_count': alerts.get('total', alerts.get('count', 0)),
    }



def get_release_notes() -> dict[str, Any]:
    milestones = [
        {
            "name": "Backend migration",
            "status": "complete",
            "details": [
                "FastAPI replaced Streamlit as the active runtime",
                "Routers and services were split for maintainability",
                "Core trading logic remained in Python",
            ],
        },
        {
            "name": "Frontend replacement",
            "status": "complete",
            "details": [
                "React workspace added with route-based navigation",
                "Dashboard, watchlist, compare, trades, journal, portfolio, alerts, activity, notes, and settings pages are live",
                "API client and polling hooks were added for a smoother operator workflow",
            ],
        },
        {
            "name": "Operator tooling",
            "status": "complete",
            "details": [
                "Saved workspaces, alerts center, ticker hub, and notes workflow were added",
                "CSV export, import, and persistence flows are available",
                "Topbar now exposes live operational counts and release metadata",
            ],
        },
        {
            "name": "Launch readiness",
            "status": "ready",
            "details": [
                "Docker, nginx, environment scaffolding, and Make targets are included",
                "The stack is aligned to Python, FastAPI, React, JavaScript, HTML, and CSS",
                "Remaining work is optional: auth provider hookup, broker integration, and branding polish",
            ],
        },
    ]
    return {
        "version": settings.app_version,
        "phase": settings.app_phase,
        "environment": settings.environment,
        "milestones": milestones,
        "next_steps": [
            "Connect a real auth provider when needed",
            "Integrate broker or order-routing APIs if live execution is required",
            "Deploy the API and frontend to a live environment",
        ],
    }



def _build_operations_status(user_id: str, tenant_slug: str | None = None, *, current_user: Any | None = None) -> dict[str, Any]:
    health = get_health()
    release = get_release_info()
    _safe_call(lambda: drain_due_jobs(limit=settings.job_worker_batch_size), {"claimed": 0, "succeeded": 0, "retried": 0, "dead_letter": 0})
    alerts = _safe_call(lambda: get_alerts_snapshot(limit=100, current_user=current_user), {'alerts': [], 'count': 0, 'total': 0})
    workspaces = _safe_call(lambda: list_workspaces(user_id, tenant_slug=tenant_slug), {'items': [], 'count': 0})
    ticker_hub = _safe_call(lambda: get_ticker_hub(limit_recent=12), {'favorites': [], 'recents': [], 'favorite_count': 0, 'recent_count': 0})
    notes_summary = _safe_call(get_notes_summary, {})
    portfolio = _safe_call(lambda: get_portfolio(current_user=current_user), {'summary': {}})
    portfolio_summary = portfolio.get('summary', {}) if isinstance(portfolio, dict) else {}
    request_metrics = _safe_call(
        get_request_metrics_snapshot,
        {
            "window_size": 0,
            "lifetime_requests": 0,
            "lifetime_errors": 0,
            "started_at": None,
            "uptime_seconds": 0,
            "summary": {
                "total_requests": 0,
                "error_count": 0,
                "error_rate": 0.0,
                "average_duration_ms": 0.0,
                "p95_duration_ms": 0.0,
                "max_duration_ms": 0.0,
                "slow_request_count": 0,
                "slow_request_threshold_ms": 0,
                "timeout_warning_count": 0,
                "timeout_warning_threshold_ms": 0,
                "last_request_at": None,
            },
            "route_groups": [],
            "methods": [],
            "status_buckets": [],
            "recent_slow_requests": [],
            "recent_timeout_risks": [],
        },
    )
    operation_metrics = _safe_call(
        get_operation_metrics_snapshot,
        {
            "window_size": 0,
            "lifetime_operations": 0,
            "lifetime_errors": 0,
            "started_at": None,
            "uptime_seconds": 0,
            "summary": {
                "total_operations": 0,
                "error_count": 0,
                "error_rate": 0.0,
                "timeout_count": 0,
                "average_duration_ms": 0.0,
                "p95_duration_ms": 0.0,
                "max_duration_ms": 0.0,
                "slow_operation_count": 0,
                "slow_operation_threshold_ms": 0,
                "last_operation_at": None,
                "cache_hit_count": 0,
                "cache_miss_count": 0,
                "cache_bypass_count": 0,
            },
            "operations": [],
            "recent_slow_operations": [],
        },
    )
    route_profiles = _safe_call(
        get_route_profile_snapshot,
        {
            "window_size": 0,
            "lifetime_profiles": 0,
            "lifetime_slow_profiles": 0,
            "started_at": None,
            "uptime_seconds": 0,
            "summary": {
                "total_profiles": 0,
                "slow_profile_count": 0,
                "slow_profile_threshold_ms": 0,
                "timeout_profile_count": 0,
                "average_total_duration_ms": 0.0,
                "p95_total_duration_ms": 0.0,
                "max_total_duration_ms": 0.0,
                "last_profile_at": None,
            },
            "routes": [],
            "recent_profiles": [],
        },
    )
    upstream_metrics = _safe_call(
        get_upstream_metrics_snapshot,
        {
            "window_size": 0,
            "lifetime_calls": 0,
            "lifetime_timeouts": 0,
            "lifetime_errors": 0,
            "started_at": None,
            "uptime_seconds": 0,
            "summary": {
                "total_calls": 0,
                "timeout_count": 0,
                "error_count": 0,
                "error_rate": 0.0,
                "average_duration_ms": 0.0,
                "p95_duration_ms": 0.0,
                "max_duration_ms": 0.0,
                "last_call_at": None,
            },
            "targets": [],
            "status_buckets": [],
            "recent_calls": [],
            "recent_timeouts": [],
        },
    )
    job_metrics = _safe_call(
        get_job_metrics_snapshot,
        {
            "summary": {
                "count": 0,
                "queued": 0,
                "retrying": 0,
                "running": 0,
                "succeeded": 0,
                "dead_letter": 0,
                "pending": 0,
                "stuck_running_count": 0,
                "oldest_pending_at": None,
                "oldest_running_at": None,
                "running_stale_after_minutes": 10,
                "recent_failure_count": 0,
                "last_finished_at": None,
            },
            "job_types": [],
            "recent_jobs": [],
            "recent_failures": [],
            "stuck_running": [],
            "dead_letters": [],
        },
    )
    worker_status = _safe_call(
        get_job_worker_status,
        {
            "enabled": False,
            "running": False,
            "thread_name": None,
            "stop_requested": False,
            "poll_seconds": 0,
            "batch_size": 0,
            "last_loop_at": None,
            "last_success_at": None,
            "last_error_at": None,
            "last_error_message": None,
        },
    )
    deployment_snapshot = _safe_call(
        get_deployment_readiness_snapshot,
        {
            "summary": {
                "status": "attention",
                "readiness_percent": 0,
                "ready_checks": 0,
                "total_checks": 0,
                "blockers": ["Deployment readiness snapshot is unavailable."],
                "next_action": "Restore deployment readiness telemetry.",
            },
            "deployment": {"items": [], "count": 0, "ready_count": 0, "next_action": "No deployment artifacts recorded."},
            "backups": {
                "status": "attention",
                "provider": "unknown",
                "schedule": None,
                "last_success_at": None,
                "last_attempt_at": None,
                "restore_tested_at": None,
                "retention_days": 0,
                "location": None,
                "notes": "Backup readiness snapshot unavailable.",
                "manifest_path": "runtime-logs/backup-status.json",
                "configured": False,
                "needs_attention": True,
                "checklist": [],
            },
            "runbooks": {"items": [], "count": 0, "ready_count": 0, "next_action": "No runbooks recorded."},
        },
    )
    market_data = _safe_call(
        get_market_data_freshness_snapshot,
        {
            "ticker": settings.market_freshness_probe_ticker,
            "interval": settings.market_freshness_probe_interval,
            "status": "unknown",
            "warning": False,
            "stale": False,
            "feed_expected": False,
            "session": "unknown",
            "session_label": "Unknown",
            "latest_bar_at": None,
            "latest_bar_age_seconds": None,
            "latest_bar_age_minutes": None,
            "warning_threshold_seconds": 0,
            "stale_threshold_seconds": 0,
            "point_count": 0,
            "source": "probe",
            "checked_at": None,
            "checked_at_et": None,
            "message": "Market-data freshness probe is unavailable.",
        },
    )
    readiness_snapshot = _safe_call(
        lambda: get_production_readiness_snapshot(tenant_slug=tenant_slug),
        {
            "summary": {
                "status": "warning",
                "ready": False,
                "checked_at": None,
                "ready_checks": 0,
                "warning_checks": 0,
                "blocked_checks": 1,
                "total_checks": 0,
                "readiness_percent": 0,
                "blockers": ["Production readiness snapshot is unavailable."],
                "warnings": [],
                "next_action": "Restore production readiness telemetry.",
            },
            "checks": [],
            "tenant": {
                "slug": tenant_slug,
                "name": None,
                "status": None,
                "plan_key": None,
            },
        },
    )
    release_gates = _safe_call(
        lambda: get_release_gate_snapshot(tenant_slug=tenant_slug),
        {
            "summary": {
                "status": "warning",
                "ready": False,
                "checked_at": None,
                "ready_gates": 0,
                "warning_gates": 0,
                "blocked_gates": 1,
                "total_gates": 0,
                "blockers": ["Release gate snapshot is unavailable."],
                "warnings": [],
                "next_action": "Restore release gate telemetry.",
            },
            "gates": [],
            "tenant": {
                "slug": tenant_slug,
                "name": None,
                "status": None,
                "plan_key": None,
            },
        },
    )
    billing_ops = _safe_call(
        lambda: get_billing_ops_snapshot(tenant_slug=tenant_slug),
        {
            "tenant": {"slug": tenant_slug, "name": None, "status": None, "plan_key": None, "provider": "unknown"},
            "summary": {
                "status": "unknown",
                "message": "Billing operations snapshot is unavailable.",
                "needs_attention": True,
                "pending_job_count": 0,
                "failed_event_count": 0,
                "drill_count": 0,
                "replay_count": 0,
                "last_drill_at": None,
                "last_replay_at": None,
            },
            "sync": {
                "status": "unknown",
                "message": "Billing sync state unavailable.",
                "provider": "unknown",
                "last_event_key": None,
                "last_event_at": None,
                "last_processed_at": None,
                "last_failed_at": None,
                "recent_failure_count": 0,
                "duplicate_count": 0,
                "needs_reconciliation": False,
                "available_actions": [],
            },
            "recovery": {
                "enabled": False,
                "last_reconciled_at": None,
                "last_recovery_action": None,
                "last_recovery_status": None,
                "last_recovery_error": None,
                "latest_failed_event_id": None,
                "latest_failed_event_at": None,
                "pending_job_count": 0,
                "failed_event_count": 0,
            },
            "drills": {
                "items": [],
                "count": 0,
                "replay_count": 0,
                "last_drill_at": None,
                "last_replay_at": None,
            },
            "recent_jobs": [],
            "failed_events": [],
            "events": {"count": 0, "status_counts": {}},
        },
    )
    rate_limits = _safe_call(
        lambda: get_rate_limit_snapshot(tenant_slug=tenant_slug, limit=12),
        {
            "summary": {
                "enabled": False,
                "throttle_event_count": 0,
                "blocked_actor_count": 0,
                "auth_lockout_count": 0,
                "abuse_failure_count": 0,
                "last_throttle_at": None,
                "last_abuse_event_at": None,
            },
            "recent_events": [],
            "recent_abuse": [],
            "blocked_actors": [],
        },
    )
    auth_config = _safe_call(
        get_auth_provider_config,
        {
            "mode": "unknown",
            "provider": "unknown",
            "available_providers": [],
        },
    )
    order_lifecycle = _safe_call(
        get_order_lifecycle_health_snapshot,
        {
            "summary": {
                "status": "unknown",
                "message": "Order lifecycle health snapshot is unavailable.",
                "pending_order_count": 0,
                "stale_pending_count": 0,
                "reject_count": 0,
                "fill_count": 0,
                "closed_count": 0,
                "last_event_at": None,
                "last_reject_at": None,
                "last_fill_at": None,
            },
            "checks": [],
            "stale_pending_orders": [],
            "recent_rejections": [],
            "recent_fills": [],
            "recent_closed": [],
        },
    )
    launch_rollup = _safe_call(
        lambda: get_tenant_launch_rollup(tenant_slug=tenant_slug),
        {
            "tenant": {"slug": tenant_slug, "name": None, "status": None, "plan_key": None},
            "summary": {
                "status": "unknown",
                "enabled": False,
                "stage": "Unknown",
                "launch_ready": False,
                "release_channel": "stable",
                "blocker_count": 0,
                "completed_checks": 0,
                "total_checks": 0,
                "last_ready_at": None,
                "last_failed_at": None,
                "next_action": "Tenant launch readiness snapshot is unavailable.",
            },
            "checks": {
                "domain_required": False,
                "domain_ready": False,
                "sender_required": False,
                "sender_ready": False,
                "auth_required": False,
                "auth_ready": False,
            },
            "checklist": [],
            "blockers": [],
            "recent_operations": [],
        },
    )
    service_smoke = _build_service_smoke_snapshot(
        tenant_slug=tenant_slug,
        auth_config=auth_config,
        billing_ops=billing_ops,
        market_data=market_data,
        job_metrics=job_metrics,
        worker_status=worker_status,
    )
    phase_a = get_phase_a_exit_snapshot(
        tenant_slug=tenant_slug,
        readiness_snapshot=readiness_snapshot,
        release_gates=release_gates,
        deployment_snapshot=deployment_snapshot,
        service_smoke=service_smoke,
    )
    validation_tracker = _safe_call(
        load_validation_tracker_snapshot,
        {
            "available": False,
            "status": "warning",
            "label": "Validation tracker unavailable",
            "detail": "Strategy validation tracker has not been exported yet.",
            "next_action": "Run the strategy validation pack before calling the stack enterprise-ready.",
            "status_counts": {"pass": 0, "partial": 0, "fail": 0, "pending": 0},
            "settings_locked": False,
        },
    )
    enterprise_readiness = build_enterprise_readiness_snapshot(
        readiness_snapshot=readiness_snapshot,
        deployment_snapshot=deployment_snapshot,
        launch_rollup=launch_rollup,
        order_lifecycle=order_lifecycle,
        validation_tracker=validation_tracker,
    )
    return {
        "health": health,
        "release": release,
        "counts": {
            "alerts": int(alerts.get('total', alerts.get('count', 0)) or 0),
            "workspaces": int(workspaces.get('count', 0) or 0),
            "favorite_tickers": int(ticker_hub.get('favorite_count', 0) or 0),
            "recent_tickers": int(ticker_hub.get('recent_count', 0) or 0),
            "active_notes": int(notes_summary.get('active_count', 0) or 0),
            "overdue_notes": int(notes_summary.get('overdue_count', 0) or 0),
            "high_priority_notes": int(notes_summary.get('high_priority_count', 0) or 0),
            "open_trades": int(portfolio_summary.get('open_trade_count', 0) or 0),
        },
        "portfolio": {
            "realized_pnl": portfolio_summary.get('total_realized_pnl', 0),
            "win_rate": portfolio_summary.get('win_rate', 0),
            "profit_factor": portfolio_summary.get('profit_factor', 0),
        },
        "observability": {
            "requests": request_metrics,
            "operations": operation_metrics,
            "route_profiles": route_profiles,
            "upstream": upstream_metrics,
            "jobs": {
                **job_metrics,
                "worker": worker_status,
            },
        },
        "readiness": readiness_snapshot,
        "release_gates": release_gates,
        "billing": billing_ops,
        "service_smoke": service_smoke,
        "rate_limits": rate_limits,
        "orders": order_lifecycle,
        "launch": launch_rollup,
        "phase_a": phase_a,
        "deployment": deployment_snapshot,
        "enterprise_readiness": enterprise_readiness,
        "market_data": market_data,
        "timestamp": health.get('timestamp'),
    }


def _minimal_operations_status(user_id: str, tenant_slug: str | None = None) -> dict[str, Any]:
    health = _safe_call(get_health, {"timestamp": None, "status": "unknown"})
    release = _safe_call(get_release_info, {"version": settings.app_version, "phase": settings.app_phase})
    request_metrics = _safe_call(get_request_metrics_snapshot, {"summary": {}})
    operation_metrics = _safe_call(get_operation_metrics_snapshot, {"summary": {}})
    route_profiles = _safe_call(get_route_profile_snapshot, {"summary": {}, "routes": []})
    upstream_metrics = _safe_call(get_upstream_metrics_snapshot, {"summary": {}})
    job_metrics = {"summary": {"status": "refreshing"}, "recent_jobs": []}
    worker_status = _safe_call(
        get_job_worker_status,
        {"enabled": False, "running": False, "status": "refreshing"},
    )
    return {
        "health": health,
        "release": release,
        "counts": {
            "alerts": 0,
            "workspaces": 0,
            "favorite_tickers": 0,
            "recent_tickers": 0,
            "active_notes": 0,
            "overdue_notes": 0,
            "high_priority_notes": 0,
            "open_trades": 0,
        },
        "portfolio": {"realized_pnl": 0, "win_rate": 0, "profit_factor": 0},
        "observability": {
            "requests": request_metrics,
            "operations": operation_metrics,
            "route_profiles": route_profiles,
            "upstream": upstream_metrics,
            "jobs": {**job_metrics, "worker": worker_status},
        },
        "readiness": {"summary": {"status": "refreshing", "ready": False, "next_action": "Operations snapshot is refreshing."}},
        "release_gates": {"summary": {"status": "refreshing", "ready": False}},
        "billing": {"summary": {"status": "refreshing"}},
        "service_smoke": {"summary": {"status": "refreshing"}},
        "rate_limits": {"summary": {"enabled": False}},
        "orders": {"summary": {"status": "refreshing"}},
        "launch": {"summary": {"status": "refreshing"}},
        "phase_a": {"summary": {"status": "refreshing"}},
        "deployment": {"summary": {"status": "refreshing"}},
        "enterprise_readiness": {"summary": {"status": "refreshing"}},
        "market_data": {"status": "refreshing", "message": "Operations snapshot is refreshing."},
        "timestamp": health.get("timestamp"),
        "source": "ops-status-fast-fallback",
        "user_id": user_id,
        "tenant_slug": tenant_slug,
    }


def _start_ops_status_refresh(
    *,
    cache_key: tuple[Any, ...],
    user_id: str,
    tenant_slug: str | None,
    current_user: Any | None,
) -> None:
    def _run() -> None:
        try:
            payload = _build_operations_status(user_id, tenant_slug, current_user=current_user)
            _store_cached_ops_status(cache_key, payload)
        except Exception:
            return
        finally:
            _release_ops_status_refresh(cache_key)

    Thread(target=_run, name="ops-status-refresh", daemon=True).start()


def _with_fresh_worker_status(payload: dict[str, Any]) -> dict[str, Any]:
    refreshed = dict(payload or {})
    worker_status = _safe_call(
        get_job_worker_status,
        {"enabled": False, "running": False, "status": "refreshing"},
    )
    observability = dict(refreshed.get("observability") or {})
    jobs = dict(observability.get("jobs") or {})
    jobs["worker"] = worker_status
    observability["jobs"] = jobs
    refreshed["observability"] = observability
    return refreshed


def get_operations_status(user_id: str, tenant_slug: str | None = None, *, current_user: Any | None = None) -> dict[str, Any]:
    cache_key = (str(user_id or ""), str(tenant_slug or ""), str(getattr(current_user, "active_tenant_slug", "") or ""))
    if current_user is None:
        payload = _build_operations_status(user_id, tenant_slug, current_user=current_user)
        _store_cached_ops_status(cache_key, payload)
        return _with_fresh_worker_status(payload)
    cached = _get_cached_ops_status(cache_key)
    if cached is not None:
        return _with_fresh_worker_status(cached)
    stale = _get_cached_ops_status(cache_key, allow_stale=True)
    if stale is not None:
        if _claim_ops_status_refresh(cache_key):
            _start_ops_status_refresh(
                cache_key=cache_key,
                user_id=user_id,
                tenant_slug=tenant_slug,
                current_user=current_user,
            )
        return _with_fresh_worker_status(stale)
    if _claim_ops_status_refresh(cache_key):
        _start_ops_status_refresh(
            cache_key=cache_key,
            user_id=user_id,
            tenant_slug=tenant_slug,
            current_user=current_user,
        )
    return _with_fresh_worker_status(_minimal_operations_status(user_id, tenant_slug))
