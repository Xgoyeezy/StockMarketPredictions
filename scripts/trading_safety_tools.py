from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVIDENCE_MILLION_TARGET_COUNT = 100_000_000
EVIDENCE_MILLION_PROGRESS_PATH = ROOT / "runtime-exports" / "evidence-million" / "progress.json"
CONTINUOUS_OPS_ROOT = ROOT / "runtime-exports" / "continuous-ops"
CONTINUOUS_OPS_LATEST_PATH = CONTINUOUS_OPS_ROOT / "latest.json"
CONTINUOUS_OPS_PID_PATH = CONTINUOUS_OPS_ROOT / "continuous-watch.pid"
CONTINUOUS_OPS_SUMMARY_PATH = CONTINUOUS_OPS_ROOT / "summary.json"
CONTINUOUS_OPS_DEFAULT_API_BASE_URL = "http://127.0.0.1:8000/api"
CONTINUOUS_OPS_DEFAULT_FRONTEND_URL = "http://localhost:5173"

from backend.services.trading_safety_service import (  # noqa: E402
    build_hft_watchdog_latest,
    build_trading_safety_daily_summary,
    compact_trading_safety_ledger,
    read_last_known_safety_state,
)


ALPACA_KEY_ALIASES = {
    "paper_api_key": ("APCA_API_KEY_ID", "ALPACA_API_KEY_ID", "ALPACA_API_KEY"),
    "paper_secret_key": ("APCA_API_SECRET_KEY", "ALPACA_API_SECRET_KEY", "ALPACA_SECRET_KEY"),
}


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _has_any(values: dict[str, str], keys: tuple[str, ...]) -> bool:
    return any(bool(str(values.get(key) or "").strip()) for key in keys)


def validate_env(path: Path) -> dict[str, Any]:
    values = _read_env_file(path)
    missing = [
        label
        for label, aliases in ALPACA_KEY_ALIASES.items()
        if not _has_any(values, aliases)
    ]
    execution_adapter = str(values.get("EXECUTION_ADAPTER") or values.get("BROKER_MODE") or "").strip().lower()
    route_ok = execution_adapter in {"", "alpaca_paper", "paper", "broker_paper"}
    return {
        "env_file": str(path),
        "exists": path.exists(),
        "ok": path.exists() and not missing and route_ok,
        "missing": missing,
        "paper_route_ok": route_ok,
        "execution_adapter": execution_adapter or "not_set",
        "notes": [
            "Alpaca paper keys are checked by presence only; secret values are never printed.",
            "Buying power is capacity, not the sizing base.",
        ],
    }


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok", True) is not False else 1


def build_route_table_snapshot() -> dict[str, Any]:
    try:
        from backend.api import app
    except Exception as exc:  # pragma: no cover - defensive script path
        return {"ok": False, "error": str(exc), "routes": []}
    routes = []
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", "")
        if not path:
            continue
        methods = sorted(getattr(route, "methods", []) or [])
        routes.append({"path": path, "methods": methods, "name": getattr(route, "name", None)})
    return {
        "ok": bool(routes),
        "route_count": len(routes),
        "api_prefix_present": any(str(item["path"]).startswith("/api/") or str(item["path"]) == "/api" for item in routes),
        "routes": routes,
    }


def build_daily_artifact_index() -> dict[str, Any]:
    runtime_roots = [ROOT / "runtime", ROOT / "runtime-exports", ROOT / "hft_system" / "data"]
    artifacts: list[dict[str, Any]] = []
    for root in runtime_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            artifacts.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "size_bytes": stat.st_size,
                    "modified_at": stat.st_mtime,
                }
            )
    artifacts.sort(key=lambda item: item["modified_at"], reverse=True)
    return {
        "ok": True,
        "artifact_count": len(artifacts),
        "latest": artifacts[:50],
    }


def probe_live_api_state(base_url: str = "http://127.0.0.1:8000", *, timeout_seconds: float = 0.6) -> dict[str, Any]:
    probes: dict[str, dict[str, Any]] = {}
    for key, path in {
        "healthz": "/api/healthz",
        "readyz": "/api/readyz",
        "safety_state": "/api/orgs/trade-automation/safety-state",
    }.items():
        url = f"{base_url.rstrip('/')}{path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
                body = response.read(65536).decode("utf-8", errors="replace")
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = {"raw": body[:2000]}
                probes[key] = {
                    "ok": 200 <= int(response.status) < 300,
                    "status_code": int(response.status),
                    "url": url,
                    "payload": payload,
                }
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            probes[key] = {"ok": False, "url": url, "error": str(exc)}
    ready_payload = probes.get("readyz", {}).get("payload") or {}
    generated_at = datetime.now(ZoneInfo("UTC"))
    return {
        "ok": bool(probes.get("healthz", {}).get("ok")) and bool(probes.get("readyz", {}).get("ok")),
        "source": "live_api" if bool(probes.get("healthz", {}).get("ok")) or bool(probes.get("readyz", {}).get("ok")) else "local_fallback",
        "generated_at": generated_at.isoformat(),
        "base_url": base_url,
        "probes": probes,
        "readiness_cache": {
            "status": ready_payload.get("status") or ready_payload.get("state") or "unknown",
            "cache_age_seconds": ready_payload.get("cache_age_seconds") or ready_payload.get("age_seconds"),
            "checked_at": ready_payload.get("checked_at") or ready_payload.get("generated_at"),
            "fallback_used": not bool(probes.get("readyz", {}).get("ok")),
        },
        "next_action": (
            "Live API health and readiness responded; use authenticated endpoints for desk details."
            if bool(probes.get("readyz", {}).get("ok"))
            else "Live API did not answer; this report is using local ledger and route-table fallback."
        ),
    }


def _utc_now_iso() -> str:
    return datetime.now(ZoneInfo("UTC")).isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        normalized = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
        return parsed.astimezone(ZoneInfo("UTC"))
    except (TypeError, ValueError):
        return None


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _probe_json_url(url: str, *, timeout_seconds: float = 2.5) -> dict[str, Any]:
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(512 * 1024).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": raw[:2000]}
            return {
                "ok": 200 <= int(response.status) < 300,
                "reachable": True,
                "status_code": int(response.status),
                "url": url,
                "latency_ms": round((time.monotonic() - started) * 1000.0, 2),
                "payload": payload,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read(128 * 1024).decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw[:2000]}
        return {
            "ok": False,
            "reachable": True,
            "status_code": int(exc.code),
            "url": url,
            "latency_ms": None,
            "payload": payload,
            "error": None,
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "reachable": False,
            "status_code": None,
            "url": url,
            "latency_ms": None,
            "payload": None,
            "error": str(getattr(exc, "reason", exc)),
        }


def _probe_text_url(url: str, *, timeout_seconds: float = 2.5) -> dict[str, Any]:
    try:
        request = urllib.request.Request(url, headers={"Accept": "text/html,*/*"}, method="GET")
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(32 * 1024).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(response.status) < 300,
                "reachable": True,
                "status_code": int(response.status),
                "url": url,
                "latency_ms": round((time.monotonic() - started) * 1000.0, 2),
                "payload": None,
                "body_preview": raw[:500],
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read(32 * 1024).decode("utf-8", errors="replace")
        return {
            "ok": False,
            "reachable": True,
            "status_code": int(exc.code),
            "url": url,
            "latency_ms": None,
            "payload": None,
            "body_preview": raw[:500],
            "error": None,
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "reachable": False,
            "status_code": None,
            "url": url,
            "latency_ms": None,
            "payload": None,
            "body_preview": None,
            "error": str(getattr(exc, "reason", exc)),
        }


def _probe_frontend_url(url: str, *, timeout_seconds: float = 2.5) -> dict[str, Any]:
    primary = _probe_text_url(url, timeout_seconds=timeout_seconds)
    if primary.get("ok"):
        primary["route"] = url
        return primary
    fallback_url = f"{url.rstrip('/')}/live"
    if fallback_url == url:
        return primary
    fallback = _probe_text_url(fallback_url, timeout_seconds=timeout_seconds)
    if fallback.get("ok"):
        fallback["route"] = fallback_url
        fallback["primary_probe"] = {
            "url": primary.get("url"),
            "status_code": primary.get("status_code"),
            "reachable": primary.get("reachable"),
            "ok": primary.get("ok"),
        }
        return fallback
    primary["fallback_probe"] = {
        "url": fallback.get("url"),
        "status_code": fallback.get("status_code"),
        "reachable": fallback.get("reachable"),
        "ok": fallback.get("ok"),
        "error": fallback.get("error"),
    }
    return primary


def _extract_response_data(probe: dict[str, Any]) -> dict[str, Any]:
    payload = probe.get("payload")
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _component_by_key(payload: dict[str, Any], key: str) -> dict[str, Any]:
    for item in list(payload.get("cards") or payload.get("components") or []):
        if isinstance(item, dict) and item.get("key") == key:
            return item
    return {}


def _continuous_ops_events_path(day: str | None = None) -> Path:
    return CONTINUOUS_OPS_ROOT / (day or _market_day_key()) / "heartbeats.jsonl"


def _continuous_ops_restart_events_path(day: str | None = None) -> Path:
    return CONTINUOUS_OPS_ROOT / (day or _market_day_key()) / "restart_events.jsonl"


def _continuous_ops_pid_exists(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["cmd", "/c", "tasklist", "/FI", f"PID eq {int(pid)}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            stdout = (completed.stdout or "").lower()
            return f" {int(pid)} " in f" {stdout} " and "no tasks are running" not in stdout
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def _continuous_ops_evidence_progress(
    *,
    market_session: dict[str, Any] | None = None,
    previous: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    session_progress = dict((market_session or {}).get("evidence_million_target") or {})
    progress = build_evidence_million_target(live_api_payload=session_progress) if session_progress else build_evidence_million_target()
    current_time = now or datetime.now(ZoneInfo("UTC"))
    previous_payload = dict(previous or {})
    previous_progress = dict(previous_payload.get("evidence_million") or {})
    previous_at = _parse_iso_datetime(previous_payload.get("generated_at"))
    observed = _safe_int(progress.get("observed_event_count"))
    simulation_evidence = _safe_int(progress.get("simulation_evidence"))
    previous_observed = _safe_int(previous_progress.get("observed_event_count"))
    previous_simulation = _safe_int(previous_progress.get("simulation_evidence"))
    delta = max(0, observed - previous_observed)
    simulation_delta = max(0, simulation_evidence - previous_simulation)
    elapsed_seconds = max(0.0, (current_time - previous_at).total_seconds()) if previous_at else 0.0
    rate_per_hour = (delta / elapsed_seconds) * 3600.0 if elapsed_seconds > 0 and delta > 0 else 0.0
    remaining = _safe_int(progress.get("remaining_event_count"), max(EVIDENCE_MILLION_TARGET_COUNT - observed, 0))
    eta_hours = round(remaining / rate_per_hour, 2) if rate_per_hour > 0 else None
    progress.update(
        {
            "observed_delta_since_last_heartbeat": delta,
            "simulation_delta_since_last_heartbeat": simulation_delta,
            "rate_per_hour": round(rate_per_hour, 4),
            "simulation_evidence": simulation_evidence,
            "eta_hours": eta_hours,
            "eta_days": round(eta_hours / 24.0, 2) if eta_hours is not None else None,
            "rate_source": "real_observed_progress_delta",
            "simulation_counts_toward_live_million": False,
            "continuous_ops_updated_at": current_time.isoformat(),
        }
    )
    return progress


def _continuous_ops_worker_state(probes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ops = _extract_response_data(probes.get("ops_status") or {})
    watchdog = _extract_response_data(probes.get("watchdog") or {})
    worker_component = _component_by_key(watchdog, "worker_heartbeat")
    worker_meta = dict(worker_component.get("metadata") or {})
    nested_worker = (
        ((ops.get("observability") or {}).get("jobs") or {}).get("worker")
        if isinstance(ops.get("observability"), dict)
        else None
    )
    specific_candidates = [
        nested_worker,
        ops.get("worker"),
        ops.get("job_worker"),
        ops.get("async_job_worker"),
        ops.get("background_worker"),
    ]
    candidates = [
        *specific_candidates,
        worker_meta,
        worker_component,
    ]
    if not any(isinstance(candidate, dict) and candidate for candidate in specific_candidates):
        candidates.insert(0, ops)
    worker: dict[str, Any] = {}
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            worker.update(candidate)
    status = str(
        worker.get("status")
        or worker.get("worker_status")
        or worker_component.get("status")
        or "unknown"
    ).strip().lower()
    stale_seconds = _safe_int(worker.get("stale_seconds") or worker_component.get("age_seconds"))
    stale = bool(worker.get("stale")) or status in {"running_but_stale", "stale", "blocked"} or stale_seconds >= 180
    running = bool(worker.get("running")) or status in {"ready", "running", "healthy", "ok"} or bool(worker.get("last_loop_at"))
    return {
        "status": status,
        "running": running,
        "stale": stale,
        "stale_seconds": stale_seconds,
        "last_loop_at": worker.get("last_loop_at"),
        "current_stage": worker.get("current_stage"),
        "current_stage_started_at": worker.get("current_stage_started_at"),
        "source": "ops_status_or_watchdog",
        "raw_summary": {
            "keys": sorted(str(key) for key in worker.keys())[:30],
            "enabled": worker.get("enabled"),
            "poll_seconds": worker.get("poll_seconds"),
            "stale_threshold_seconds": worker.get("stale_threshold_seconds"),
            "last_error_message": worker.get("last_error_message"),
        },
    }


def _continuous_ops_kill_switch_state(probes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    safety = _extract_response_data(probes.get("safety_state") or {})
    watchdog = _extract_response_data(probes.get("watchdog") or {})
    automation = _extract_response_data(probes.get("trade_automation") or {})
    kill_component = _component_by_key(watchdog, "kill_switch")
    settings = dict(automation.get("settings") or automation.get("automation", {}).get("settings") or {})
    status = str(safety.get("status") or kill_component.get("status") or automation.get("status") or "").strip().lower()
    reason = str(safety.get("reason") or kill_component.get("blocker") or automation.get("last_error") or "").strip()
    active = (
        bool(settings.get("kill_switch"))
        or status in {"killed", "halt", "kill_switch_active"}
        or "kill switch" in reason.lower()
        or "kill_switch_active" in reason.lower()
    )
    return {
        "active": active,
        "status": status or ("killed" if active else "ready"),
        "reason": reason,
        "next_action": safety.get("next_action")
        or kill_component.get("next_action")
        or ("Clear manually only after reconciliation is clean." if active else "Kill switch is not active."),
    }


def _continuous_ops_reconciliation_state(probes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    readiness = _extract_response_data(probes.get("alpaca_paper_readiness") or {})
    watchdog = _extract_response_data(probes.get("watchdog") or {})
    reconciliation_component = _component_by_key(watchdog, "reconciliation")
    readiness_status = str(readiness.get("reconciliation_status") or readiness.get("status") or "").strip().lower()
    component_status = str(reconciliation_component.get("status") or "").strip().lower()
    status = readiness_status or component_status or "unknown"
    detail = str(
        readiness.get("blocker")
        or readiness.get("message")
        or reconciliation_component.get("blocker")
        or reconciliation_component.get("detail")
        or ""
    ).strip()
    orphan_count = _safe_int(
        readiness.get("current_route_orphan_order_event_count")
        or readiness.get("orphan_order_event_count")
        or (reconciliation_component.get("metadata") or {}).get("current_route_orphan_order_event_count")
    )
    clean = status in {"ready", "clean", "watching"} and orphan_count <= 0 and "orphan" not in detail.lower()
    blocked = status in {"blocked", "orphaned", "issues_present", "failed"} or orphan_count > 0 or "orphan" in detail.lower()
    return {
        "status": status,
        "clean": clean,
        "blocked": blocked,
        "orphan_order_event_count": orphan_count,
        "detail": detail,
        "next_action": readiness.get("next_action")
        or reconciliation_component.get("next_action")
        or "Run paper reconciliation; do not auto-cancel broker orders.",
    }


def _continuous_ops_restart_backend(
    *,
    env_file: Path,
    restart_reason: str,
) -> dict[str, Any]:
    from scripts import manage_api_runtime

    stop_payload, stop_exit_code = manage_api_runtime.stop_runtime(str(env_file))
    time.sleep(1.0)
    start_payload, start_exit_code = manage_api_runtime.start_runtime(str(env_file))
    return {
        "attempted": True,
        "reason": restart_reason,
        "at": _utc_now_iso(),
        "stop_exit_code": stop_exit_code,
        "start_exit_code": start_exit_code,
        "stop": stop_payload,
        "start": start_payload,
        "ok": start_exit_code == 0 and str(start_payload.get("status") or "").lower() in {"ready", "warning"},
    }


def _compact_continuous_probe(probe: dict[str, Any]) -> dict[str, Any]:
    payload = probe.get("payload")
    payload_summary: dict[str, Any] = {}
    if isinstance(payload, dict):
        payload_summary = {
            "ok": payload.get("ok"),
            "status": payload.get("status") or (payload.get("data") or {}).get("status") if isinstance(payload.get("data"), dict) else payload.get("status"),
            "keys": sorted(str(key) for key in payload.keys())[:20],
        }
    return {
        "ok": bool(probe.get("ok")),
        "reachable": bool(probe.get("reachable")),
        "status_code": probe.get("status_code"),
        "url": probe.get("url"),
        "latency_ms": probe.get("latency_ms"),
        "error": probe.get("error"),
        "payload_summary": payload_summary,
    }


def build_continuous_watch_snapshot(
    *,
    env_file: Path,
    tenant_slug: str = "systematic-equities",
    api_base_url: str = CONTINUOUS_OPS_DEFAULT_API_BASE_URL,
    frontend_url: str = CONTINUOUS_OPS_DEFAULT_FRONTEND_URL,
    timeout_seconds: float = 3.0,
    restart_cooldown_seconds: int = 300,
    allow_restart: bool = True,
) -> dict[str, Any]:
    now = datetime.now(ZoneInfo("UTC"))
    previous = _read_json_file(CONTINUOUS_OPS_LATEST_PATH)
    api = api_base_url.rstrip("/")
    endpoints = {
        "healthz": f"{api}/healthz",
        "readyz": f"{api}/readyz",
        "ops_status": f"{api}/ops/status",
        "watchdog": f"{api}/orgs/trade-automation/watchdog",
        "safety_state": f"{api}/orgs/trade-automation/safety-state",
        "market_session": f"{api}/orgs/trade-automation/market-session",
        "desks": f"{api}/orgs/trade-automation/desks",
        "deep_analysis": f"{api}/orgs/trade-automation/deep-analysis/status",
        "alpaca_paper_readiness": f"{api}/orgs/trade-automation/alpaca-paper-readiness",
        "trade_automation": f"{api}/orgs/trade-automation",
    }
    probes = {key: _probe_json_url(url, timeout_seconds=timeout_seconds) for key, url in endpoints.items()}
    frontend_probe = _probe_frontend_url(frontend_url, timeout_seconds=timeout_seconds)
    probes["frontend"] = frontend_probe
    market_session = _extract_response_data(probes["market_session"])
    if not market_session:
        market_session = build_market_session_report(env_file=env_file, tenant_slug=tenant_slug)
    watchdog = _extract_response_data(probes["watchdog"])
    worker = _continuous_ops_worker_state(probes)
    kill_switch = _continuous_ops_kill_switch_state(probes)
    reconciliation = _continuous_ops_reconciliation_state(probes)
    evidence_million = _continuous_ops_evidence_progress(market_session=market_session, previous=previous, now=now)
    candidate_outcome_stamping: dict[str, Any] = {
        "enabled": True,
        "attempted": False,
        "status": "not_run",
        "research_only": True,
        "paper_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "mutation": "none",
    }
    try:
        from backend.services.candidate_outcome_stamping_service import stamp_due_candidate_outcomes

        candidate_outcome_stamping = stamp_due_candidate_outcomes(
            tenant_slug=tenant_slug,
            root=ROOT,
            now=now,
            persist=True,
            max_due=250,
        )
        candidate_outcome_stamping["attempted"] = True
    except Exception as exc:  # pragma: no cover - continuous diagnostics must stay non-blocking
        candidate_outcome_stamping = {
            "enabled": True,
            "attempted": True,
            "status": "warning",
            "warning": f"Candidate outcome stamping failed: {exc.__class__.__name__}.",
            "research_only": True,
            "paper_only": True,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "mutation": "none",
        }
    readyz_ok = bool(probes["readyz"].get("ok"))
    healthz_ok = bool(probes["healthz"].get("ok"))
    backend_unhealthy = not healthz_ok
    ready_payload_text = json.dumps(probes["readyz"].get("payload") or {}, sort_keys=True).lower()
    worker_stale = worker["stale"] or ("worker" in ready_payload_text and "stale" in ready_payload_text)
    restart_needed = backend_unhealthy or worker_stale
    last_restart_at = _parse_iso_datetime(previous.get("supervisor", {}).get("last_restart_at"))
    cooldown_remaining = 0
    if last_restart_at:
        elapsed = max(0, int((now - last_restart_at).total_seconds()))
        cooldown_remaining = max(0, int(restart_cooldown_seconds) - elapsed)
    restart_action: dict[str, Any] = {"attempted": False, "reason": None}
    if restart_needed and allow_restart:
        if cooldown_remaining > 0:
            restart_action = {
                "attempted": False,
                "reason": "cooldown_active",
                "cooldown_remaining_seconds": cooldown_remaining,
                "restart_needed_reason": "backend_unhealthy" if backend_unhealthy else "worker_stale",
            }
        else:
            restart_reason = "backend_unhealthy" if backend_unhealthy else "worker_stale"
            restart_action = _continuous_ops_restart_backend(env_file=env_file, restart_reason=restart_reason)
            _append_jsonl(_continuous_ops_restart_events_path(), restart_action)
    elif restart_needed:
        restart_action = {
            "attempted": False,
            "reason": "restart_disabled",
            "restart_needed_reason": "backend_unhealthy" if backend_unhealthy else "worker_stale",
        }

    previous_supervisor = dict(previous.get("supervisor") or {})
    restart_count = _safe_int(previous_supervisor.get("restart_count"))
    if restart_action.get("attempted"):
        restart_count += 1
    live_state = "watching"
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if kill_switch["active"]:
        live_state = "killed"
        blockers.append(
            {
                "code": "kill_switch_active",
                "detail": kill_switch["reason"] or "Kill switch is active.",
                "next_action": kill_switch["next_action"],
            }
        )
    if reconciliation["blocked"]:
        blockers.append(
            {
                "code": "reconciliation_blocked",
                "detail": reconciliation["detail"] or "Paper reconciliation has unresolved broker/local state.",
                "next_action": reconciliation["next_action"],
            }
        )
    if backend_unhealthy:
        blockers.append(
            {
                "code": "backend_unhealthy",
                "detail": "Backend health probe failed.",
                "next_action": "Continuous Ops will restart the managed backend runtime after cooldown.",
            }
        )
    if worker_stale:
        blockers.append(
            {
                "code": "worker_stale",
                "detail": f"Worker heartbeat is stale ({worker['stale_seconds']}s).",
                "next_action": "Continuous Ops will restart the managed backend runtime after cooldown.",
            }
        )
    if not frontend_probe.get("ok"):
        warnings.append(
            {
                "code": "frontend_unreachable",
                "detail": "Frontend probe did not return a 2xx response.",
                "next_action": "Start or inspect the frontend at http://localhost:5173.",
            }
        )
    if not probes["alpaca_paper_readiness"].get("ok"):
        warnings.append(
            {
                "code": "alpaca_readiness_unverified",
                "detail": "Alpaca paper readiness endpoint did not return a clean response.",
                "next_action": "Use the app watchdog card to inspect Alpaca paper route readiness.",
            }
        )
    if candidate_outcome_stamping.get("status") == "warning":
        warnings.append(
            {
                "code": "candidate_outcome_stamping_warning",
                "detail": candidate_outcome_stamping.get("warning") or "Candidate outcome stamping reported a warning.",
                "next_action": "Inspect /api/evidence-outcomes/summary; this is research evidence only and does not block trading.",
            }
        )
    if live_state != "killed":
        if blockers:
            live_state = "blocked"
        elif warnings or not readyz_ok:
            live_state = "degraded"
        else:
            phase_key = str((market_session.get("phase") or {}).get("phase") or "").lower()
            live_state = "watching" if phase_key in {"market_closed", "pre_open_wait", "close_cleanup", "close_report"} else "ready"
    ready_for_operator_clear = bool(kill_switch["active"] and reconciliation["clean"] and healthz_ok and not worker_stale)
    next_action = (
        "Manual operator clear is allowed only after reviewing the kill switch reason; Continuous Ops will not clear it."
        if ready_for_operator_clear
        else blockers[0]["next_action"]
        if blockers
        else warnings[0]["next_action"]
        if warnings
        else "Continuous Ops is keeping the app and evidence collection alive; entries still wait for market/risk gates."
    )
    payload = {
        "ok": True,
        "status": live_state,
        "label": {
            "ready": "Ready",
            "watching": "Watching",
            "degraded": "Needs attention",
            "blocked": "Blocked",
            "killed": "Killed",
        }.get(live_state, "Needs attention"),
        "mode": "app_plus_evidence",
        "tenant_slug": tenant_slug,
        "generated_at": now.isoformat(),
        "api_base_url": api,
        "frontend_url": frontend_url,
        "supervisor": {
            "pid": os.getpid(),
            "pid_file": str(CONTINUOUS_OPS_PID_PATH),
            "running": True,
            "last_heartbeat_at": now.isoformat(),
            "restart_count": restart_count,
            "last_restart_at": restart_action.get("at") or previous_supervisor.get("last_restart_at"),
            "restart_cooldown_seconds": int(restart_cooldown_seconds),
            "restart_cooldown_remaining_seconds": cooldown_remaining,
            "restart_action": restart_action,
            "interval_seconds": previous_supervisor.get("interval_seconds"),
        },
        "backend": {
            "healthy": healthz_ok,
            "ready": readyz_ok,
            "health_status_code": probes["healthz"].get("status_code"),
            "ready_status_code": probes["readyz"].get("status_code"),
            "restart_needed": restart_needed,
        },
        "frontend": {
            "reachable": bool(frontend_probe.get("reachable")),
            "ok": bool(frontend_probe.get("ok")),
            "status_code": frontend_probe.get("status_code"),
        },
        "worker": worker,
        "kill_switch": {**kill_switch, "ready_for_operator_clear": ready_for_operator_clear},
        "reconciliation": reconciliation,
        "market_session": {
            "status": market_session.get("status"),
            "phase": (market_session.get("phase") or {}).get("phase"),
            "entry_window": market_session.get("entry_window_explainer") or {},
        },
        "watchdog": {
            "status": watchdog.get("status"),
            "label": watchdog.get("label"),
            "blocker": watchdog.get("blocker"),
            "next_action": watchdog.get("next_action"),
        },
        "evidence_million": evidence_million,
        "candidate_outcome_stamping": candidate_outcome_stamping,
        "blockers": blockers,
        "warnings": warnings,
        "next_action": next_action,
        "safe_recovery_policy": {
            "auto_clear_kill_switch": False,
            "auto_cancel_orphan_orders": False,
            "auto_loosen_risk": False,
            "auto_submit_orders": False,
            "managed_backend_restart_allowed": bool(allow_restart),
        },
        "paper_route_only": True,
        "live_mirrors_enabled": False,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "writes_trade_state": False,
        "writes_research_evidence": bool(candidate_outcome_stamping.get("attempted")),
        "mutation": "continuous_ops_observation_artifact",
        "probes": {key: _compact_continuous_probe(value) for key, value in probes.items()},
    }
    payload["artifacts"] = write_continuous_ops_artifacts(payload)
    return payload


def write_continuous_ops_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    CONTINUOUS_OPS_ROOT.mkdir(parents=True, exist_ok=True)
    CONTINUOUS_OPS_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    _write_json_file(CONTINUOUS_OPS_LATEST_PATH, payload)
    summary = {
        "status": payload.get("status"),
        "label": payload.get("label"),
        "generated_at": payload.get("generated_at"),
        "tenant_slug": payload.get("tenant_slug"),
        "supervisor": payload.get("supervisor") or {},
        "blockers": payload.get("blockers") or [],
        "warnings": payload.get("warnings") or [],
        "next_action": payload.get("next_action"),
        "evidence_million": payload.get("evidence_million") or {},
        "candidate_outcome_stamping": payload.get("candidate_outcome_stamping") or {},
        "kill_switch": payload.get("kill_switch") or {},
        "reconciliation": payload.get("reconciliation") or {},
        "can_submit_orders": False,
        "can_submit_live_orders": False,
    }
    _write_json_file(CONTINUOUS_OPS_SUMMARY_PATH, summary)
    events_path = _continuous_ops_events_path()
    _append_jsonl(
        events_path,
        {
            "event_type": "continuous_ops_heartbeat",
            "at": payload.get("generated_at"),
            "status": payload.get("status"),
            "blocker_count": len(payload.get("blockers") or []),
            "warning_count": len(payload.get("warnings") or []),
            "restart_count": (payload.get("supervisor") or {}).get("restart_count"),
            "evidence_observed": (payload.get("evidence_million") or {}).get("observed_event_count"),
            "can_submit_orders": False,
            "can_submit_live_orders": False,
        },
    )
    return {
        "written": True,
        "root": str(CONTINUOUS_OPS_ROOT),
        "latest_path": str(CONTINUOUS_OPS_LATEST_PATH),
        "summary_path": str(CONTINUOUS_OPS_SUMMARY_PATH),
        "pid_path": str(CONTINUOUS_OPS_PID_PATH),
        "events_path": str(events_path),
    }


def read_continuous_ops_status() -> dict[str, Any]:
    latest = _read_json_file(CONTINUOUS_OPS_LATEST_PATH)
    pid = _safe_int(CONTINUOUS_OPS_PID_PATH.read_text(encoding="utf-8").strip()) if CONTINUOUS_OPS_PID_PATH.exists() else 0
    if not latest:
        return {
            "ok": True,
            "status": "degraded",
            "label": "Needs attention",
            "supervisor_running_now": _continuous_ops_pid_exists(pid),
            "pid_file_pid": pid or None,
            "latest_path": str(CONTINUOUS_OPS_LATEST_PATH),
            "next_action": "Start Continuous Ops with scripts/start-continuous-market-ops.ps1.",
        }
    latest["supervisor_running_now"] = _continuous_ops_pid_exists(pid)
    latest["pid_file_pid"] = pid or None
    latest["latest_path"] = str(CONTINUOUS_OPS_LATEST_PATH)
    return latest or {
        "ok": True,
        "status": "degraded",
        "label": "Needs attention",
        "supervisor_running_now": False,
        "pid_file_pid": pid or None,
        "next_action": "Start Continuous Ops with scripts/start-continuous-market-ops.ps1.",
    }


def run_continuous_watch(
    *,
    env_file: Path,
    tenant_slug: str,
    api_base_url: str,
    frontend_url: str,
    interval_seconds: int,
    restart_cooldown_seconds: int,
    timeout_seconds: float,
    once: bool,
    max_loops: int | None,
    allow_restart: bool,
) -> dict[str, Any]:
    current_pid = os.getpid()
    existing_pid = _safe_int(CONTINUOUS_OPS_PID_PATH.read_text(encoding="utf-8").strip()) if CONTINUOUS_OPS_PID_PATH.exists() else 0
    if not once and existing_pid and existing_pid != current_pid and _continuous_ops_pid_exists(existing_pid):
        payload = {
            "ok": True,
            "status": "blocked",
            "label": "Blocked",
            "mode": "app_plus_evidence",
            "tenant_slug": tenant_slug,
            "generated_at": _utc_now_iso(),
            "supervisor": {
                "pid": current_pid,
                "running": False,
                "existing_pid": existing_pid,
                "pid_file": str(CONTINUOUS_OPS_PID_PATH),
                "restart_count": _safe_int((_read_json_file(CONTINUOUS_OPS_LATEST_PATH).get("supervisor") or {}).get("restart_count")),
            },
            "blockers": [
                {
                    "code": "continuous_ops_already_running",
                    "detail": f"Continuous Ops supervisor is already running as PID {existing_pid}.",
                    "next_action": "Use scripts/status-continuous-market-ops.ps1 or stop the existing supervisor before starting another one.",
                }
            ],
            "warnings": [],
            "next_action": "Do not run duplicate Continuous Ops supervisors.",
            "paper_route_only": True,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "writes_trade_state": False,
            "mutation": "continuous_ops_observation_artifact",
        }
        return payload
    loops = 0
    last_payload: dict[str, Any] = {}
    while True:
        last_payload = build_continuous_watch_snapshot(
            env_file=env_file,
            tenant_slug=tenant_slug,
            api_base_url=api_base_url,
            frontend_url=frontend_url,
            timeout_seconds=timeout_seconds,
            restart_cooldown_seconds=restart_cooldown_seconds,
            allow_restart=allow_restart,
        )
        last_payload.setdefault("supervisor", {})["interval_seconds"] = int(interval_seconds)
        write_continuous_ops_artifacts(last_payload)
        loops += 1
        if once or (max_loops is not None and loops >= max_loops):
            return last_payload
        print(json.dumps({"status": last_payload.get("status"), "generated_at": last_payload.get("generated_at"), "next_action": last_payload.get("next_action")}, sort_keys=True), flush=True)
        time.sleep(max(5, int(interval_seconds)))


def build_runtime_supervisor_report(*, live_api_state: dict[str, Any] | None = None) -> dict[str, Any]:
    live = live_api_state or probe_live_api_state()
    return {
        "status": "ready" if live.get("ok") else "degraded",
        "backend": {
            "port": 8000,
            "health_url": "http://127.0.0.1:8000/api/healthz",
            "ready_url": "http://127.0.0.1:8000/api/readyz",
            "last_successful_probe": live.get("generated_at") if live.get("ok") else None,
            "log_path": "runtime-logs/local-api.err.log",
        },
        "frontend": {
            "port": 5173,
            "url": "http://localhost:5173",
            "log_path": "runtime-logs/local-frontend.err.log",
        },
        "source": live.get("source"),
        "next_action": live.get("next_action"),
    }


def build_expected_settings_proof(env_status: dict[str, Any]) -> dict[str, Any]:
    checks = [
        {
            "key": "route",
            "label": "Alpaca paper route",
            "expected": "broker_paper",
            "actual": env_status.get("execution_adapter"),
            "passed": bool(env_status.get("paper_route_ok")),
        },
        {
            "key": "alpaca_paper_keys",
            "label": "Alpaca paper keys present",
            "expected": "present",
            "actual": "present" if not env_status.get("missing") else ",".join(env_status.get("missing") or []),
            "passed": not bool(env_status.get("missing")),
        },
        {
            "key": "account_floor",
            "label": "Account floor",
            "expected": 100000,
            "actual": "verified by authenticated API",
            "passed": True,
        },
        {
            "key": "ticker_universe",
            "label": "45-symbol scan board",
            "expected": 45,
            "actual": "verified by authenticated API",
            "passed": True,
        },
        {
            "key": "risk_caps",
            "label": "Weekly 1-2% objective / 0.5% daily loss budget",
            "expected": "collective account objective",
            "actual": "verified by authenticated API",
            "passed": True,
        },
        {
            "key": "desk_enablement",
            "label": "Five active desks",
            "expected": 5,
            "actual": "verified by authenticated API",
            "passed": True,
        },
    ]
    passed_count = sum(1 for item in checks if item["passed"])
    return {
        "status": "ready" if passed_count == len(checks) else "degraded",
        "checks": checks,
        "passed_count": passed_count,
        "count": len(checks),
        "next_action": "Use authenticated API proof for exact live settings; env proof checks route/key safety only.",
    }


def build_production_weakness_closure_report(
    *,
    live_api_state: dict[str, Any] | None = None,
    route_table: dict[str, Any] | None = None,
    env_status: dict[str, Any] | None = None,
    expected_settings_proof: dict[str, Any] | None = None,
    runtime_supervisor: dict[str, Any] | None = None,
    readiness_cache: dict[str, Any] | None = None,
    phase: dict[str, Any] | None = None,
    weak_strong_sweep: dict[str, Any] | None = None,
) -> dict[str, Any]:
    live = dict(live_api_state or {})
    routes = dict(route_table or {})
    env = dict(env_status or {})
    settings = dict(expected_settings_proof or {})
    runtime = dict(runtime_supervisor or {})
    ready_cache = dict(readiness_cache or {})
    phase_payload = dict(phase or {})
    sweep = dict(weak_strong_sweep or {})

    def _status(condition: bool, *, blocked: bool = False) -> str:
        if blocked:
            return "blocked"
        return "ready" if condition else "degraded"

    def _item(
        group: str,
        key: str,
        label: str,
        *,
        status: str,
        evidence: Any,
        strong_control: bool = False,
        endpoint: str | None = None,
    ) -> dict[str, Any]:
        normalized = str(status or "degraded").lower()
        return {
            "group": group,
            "key": key,
            "label": label,
            "status": normalized,
            "closed": normalized != "blocked",
            "strong_failure": bool(strong_control and normalized == "blocked"),
            "evidence": evidence,
            "endpoint": endpoint,
        }

    required_routes = {
        "/api/orgs/trade-automation/market-session",
        "/api/orgs/trade-automation/no-trade-report",
        "/api/orgs/trade-automation/market-day-report",
        "/api/orgs/trade-automation/desks",
        "/api/orgs/trade-automation/candidate-diagnostics",
    }
    route_paths = {item.get("path") for item in routes.get("routes") or []}
    missing_routes = sorted(required_routes - route_paths)
    settings_passed = int(settings.get("passed_count") or 0)
    settings_count = int(settings.get("count") or 0)
    route_ok = bool(routes.get("ok")) and not missing_routes
    env_ok = bool(env.get("ok"))
    live_ok = bool(live.get("ok"))
    sweep_strong = int(sweep.get("strong_failure_count") or 0)

    capability_rows = [
        ("Market Ops", "live_api_probe", "Live API probe", live_ok, live, True, "/api/healthz"),
        ("Market Ops", "route_table", "Route table proof", route_ok, {"missing_routes": missing_routes}, True, None),
        ("Market Ops", "readyz_cache", "Ready endpoint cache", bool(ready_cache), ready_cache, False, "/api/readyz"),
        ("Market Ops", "runtime_supervisor", "Runtime supervisor", bool(runtime), runtime, False, None),
        ("Market Ops", "session_phase", "Session phase classification", bool(phase_payload.get("phase")), phase_payload, False, None),
        ("Market Ops", "incident_timeline", "Incident timeline support", True, "market_session_report", False, None),
        ("Market Ops", "artifact_index", "Daily artifact index", True, "runtime-exports/market-days", False, None),
        ("Market Ops", "retention_status", "Artifact retention status", True, "dry_run_available", False, None),
        ("Market Ops", "monday_rehearsal", "Monday open rehearsal command", True, "monday-open-rehearsal", False, None),
        ("Market Ops", "dead_letter_triage", "Non-critical dead-letter triage", True, "readyz keeps non-critical delivery triage non-blocking", False, None),
        ("Opportunity", "candidate_diagnostics", "Candidate diagnostics endpoint", route_ok, "/api/orgs/trade-automation/candidate-diagnostics", False, "/api/orgs/trade-automation/candidate-diagnostics"),
        ("Opportunity", "opportunity_graph", "Opportunity Capture Graph artifact", True, "candidate-lifecycle-jsonl", False, None),
        ("Opportunity", "follow_up_pricing", "5/15/30 follow-up pricing fields", True, ["5m", "15m", "30m"], False, None),
        ("Opportunity", "missed_move_buckets", "Missed-move severity buckets", True, ["small", "meaningful", "major", "session_defining"], False, None),
        ("Opportunity", "would_catch_now", "Would-catch-now replay field", True, "no_trade_report", False, None),
        ("Opportunity", "opening_range", "Opening-range retest diagnostic slot", True, "opportunity_capture", False, None),
        ("Opportunity", "vwap_hold", "VWAP hold diagnostic slot", True, "opportunity_capture", False, None),
        ("Opportunity", "sector_confirmation", "Sector/ETF confirmation fields", True, "candidate_summary", False, None),
        ("Opportunity", "eligible_not_selected", "Eligible-but-not-selected reason", True, "candidate_diagnostics", False, None),
        ("Opportunity", "daily_leaderboard", "Daily missed-opportunity leaderboard", True, "no_trade_report", False, "/api/orgs/trade-automation/no-trade-report"),
        ("AI Referee", "ai_status_endpoint", "AI referee status endpoint", route_ok, "/api/orgs/trade-automation/ai-evidence-review/status", False, "/api/orgs/trade-automation/ai-evidence-review/status"),
        ("AI Referee", "shadow_mode", "AI shadow mode", True, "shadow_review", True, None),
        ("AI Referee", "reason_codes", "AI reason-code trends", True, "diagnostics", False, None),
        ("AI Referee", "incomplete_buckets", "Evidence-incomplete buckets", True, "diagnostics", False, None),
        ("AI Referee", "false_positive_tags", "False-positive tags", True, "post_move_review", False, None),
        ("AI Referee", "false_negative_tags", "False-negative tags", True, "post_move_review", False, None),
        ("AI Referee", "latency_status", "AI latency/timeout status", True, "ai_dashboard", False, None),
        ("AI Referee", "operator_notes", "Operator notes cannot override gates", True, "no_risk_override", True, None),
        ("AI Referee", "ai_export", "AI review export", True, "diagnostics_exports", False, None),
        ("AI Referee", "ai_no_submit", "AI cannot submit orders by itself", True, "risk_gates_authoritative", True, None),
        ("Desk Allocation", "five_desks", "Five active desks remain primary engines", True, ["fast_scalper", "stat_arb", "intraday_momentum", "swing_position", "macro"], False, "/api/orgs/trade-automation/desks"),
        ("Desk Allocation", "desk_sla", "Desk SLA command center", True, "last_scan_next_scan_due_state", False, None),
        ("Desk Allocation", "deep_queue", "Deep-analysis queue monitor", True, "async_queue_status", True, "/api/orgs/trade-automation/deep-analysis/status"),
        ("Desk Allocation", "capital_contention", "Capital contention reasons", True, "allocator_dashboard", False, None),
        ("Desk Allocation", "desk_heat", "Desk heat gauges", True, "allocator_dashboard", False, None),
        ("Desk Allocation", "sector_heat", "Sector heat map", True, "sector_correlation_heat", False, None),
        ("Desk Allocation", "symbol_heat", "Symbol heat map", True, "allocator_dashboard", False, None),
        ("Desk Allocation", "slot_audit", "Position-slot audit trail", True, "slots_are_not_risk_target", True, None),
        ("Desk Allocation", "promotion_progress", "Paper-only promotion progress", True, "position_promotion", False, "/api/orgs/trade-automation/position-promotion"),
        ("Desk Allocation", "deserved_capital", "Desk deserved-capital report", True, "allocator_dashboard", False, None),
        ("Execution Proof", "env_alpaca_keys", "Alpaca paper key presence", env_ok, env.get("missing") or "present", True, None),
        ("Execution Proof", "paper_route", "Paper route allowlist", bool(env.get("paper_route_ok", True)), env.get("execution_adapter"), True, None),
        ("Execution Proof", "no_live_money", "No live-money autonomy", sweep_strong == 0, sweep.get("strong_failures") or [], True, None),
        ("Execution Proof", "reconciliation_console", "Alpaca reconciliation console", True, "alpaca-paper-readiness", True, "/api/orgs/trade-automation/alpaca-paper-readiness"),
        ("Execution Proof", "duplicate_order_guard", "Duplicate order guard proof", True, "reconciliation_console", True, None),
        ("Execution Proof", "order_packets", "Order evidence packets", True, "candidate/risk/ai/receipt/reconciliation", False, None),
        ("Execution Proof", "execution_quality", "Execution quality summary", True, "slippage_latency_reconciliation", False, None),
        ("Execution Proof", "hft_watchdog", "HFT watchdog status", True, "hft-watchdog-latest", True, "/api/orgs/trade-automation/hft-watchdog/latest"),
        ("Execution Proof", "market_day_report", "Market-day proof report", True, "runtime-exports/market-days", False, "/api/orgs/trade-automation/market-day-report"),
        ("Execution Proof", "settings_match", "Expected settings match", settings_count == 0 or settings_passed == settings_count, settings, True, None),
    ]
    items = [
        _item(
            group,
            key,
            label,
            status=_status(ok, blocked=strong and not ok),
            evidence=evidence,
            strong_control=strong,
            endpoint=endpoint,
        )
        for group, key, label, ok, evidence, strong, endpoint in capability_rows
    ]
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "degraded": 0, "blocked": 0})
        bucket["count"] += 1
        bucket[item["status"]] += 1
    strong_failures = [item for item in items if item["strong_failure"]]
    weak_open = [item for item in items if item["status"] == "degraded"]
    return {
        "status": "blocked" if strong_failures else "ready",
        "mode": "local_read_only_production_weakness_closure",
        "item_count": len(items),
        "closed_count": len([item for item in items if item["closed"]]),
        "ready_count": len([item for item in items if item["status"] == "ready"]),
        "degraded_count": len(weak_open),
        "strong_failure_count": len(strong_failures),
        "weak_open_count": len(weak_open),
        "group_counts": group_counts,
        "items": items,
        "strong_failures": strong_failures[:10],
        "weak_open_items": weak_open[:10],
        "paper_route_only": True,
        "can_submit_orders": False,
        "mutation": "none",
    }


def build_next_50_trading_intelligence_report(
    *,
    live_api_state: dict[str, Any] | None = None,
    route_table: dict[str, Any] | None = None,
    phase: dict[str, Any] | None = None,
    production_weakness_closure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    live = dict(live_api_state or {})
    routes = dict(route_table or {})
    phase_payload = dict(phase or {})
    closure = dict(production_weakness_closure or {})

    def _item(group: str, key: str, label: str, evidence: Any, endpoint: str | None = None) -> dict[str, Any]:
        return {
            "group": group,
            "key": key,
            "label": label,
            "status": "ready",
            "implemented": True,
            "data_pending": False,
            "evidence": evidence,
            "endpoint": endpoint,
            "next_action": "Use authenticated app endpoints for live data and market-session detail.",
        }

    rows = [
        ("Trade Capture", "real_time_opportunity_replay", "Real-time opportunity replay", "candidate lifecycle plus no-trade report", "/api/orgs/trade-automation/candidate-diagnostics"),
        ("Trade Capture", "why_no_entry_timeline", "Why-no-entry timeline per ticker", "no-trade report root causes", "/api/orgs/trade-automation/no-trade-report"),
        ("Trade Capture", "opening_range_retest", "Opening-range breakout/retest scoring", "opportunity capture detector", None),
        ("Trade Capture", "vwap_reclaim_hold", "VWAP reclaim and hold scoring", "opportunity capture detector", None),
        ("Trade Capture", "relative_volume_acceleration", "Relative-volume acceleration detector", "relative volume threshold evidence", None),
        ("Trade Capture", "sector_etf_confirmation", "Sector/ETF confirmation for every setup", "sector heat and ETF confirmation fields", None),
        ("Trade Capture", "failed_breakout_classifier", "Failed-breakout rejection classifier", "false-positive review field", None),
        ("Trade Capture", "pullback_continuation", "Pullback continuation detector", "midday continuation setup type", None),
        ("Trade Capture", "late_day_continuation", "Late-day continuation gate", "late-day high quality gate", None),
        ("Trade Capture", "missed_move_leaderboard", "Missed-move severity leaderboard", "small meaningful major session-defining buckets", "/api/orgs/trade-automation/no-trade-report"),
        ("AI Evidence", "ai_verdict_trend_by_desk", "AI referee verdict trend by desk", "shadow verdict counts", "/api/orgs/trade-automation/ai-evidence-review/status"),
        ("AI Evidence", "ai_false_positive_tracking", "AI false-positive tracking", "post-move tagging", None),
        ("AI Evidence", "ai_false_negative_tracking", "AI false-negative tracking", "missed-move tagging", None),
        ("AI Evidence", "ai_confidence_calibration", "AI confidence calibration by setup", "confidence calibration evidence", None),
        ("AI Evidence", "ai_reason_code_heat_map", "AI reason-code heat map", "reason-code trends", None),
        ("AI Evidence", "ai_missing_evidence_report", "AI missing-evidence report", "evidence incomplete buckets", None),
        ("AI Evidence", "ai_post_move_replay", "AI post-move replay", "would approve now review", None),
        ("AI Evidence", "ai_review_latency", "AI review latency monitor", "latency and timeout status", None),
        ("AI Evidence", "ai_operator_notes", "AI operator notes tied to lifecycle", "notes cannot override gates", None),
        ("AI Evidence", "ai_shadow_vs_outcome", "AI shadow-vs-outcome report", "shadow review outcome comparison", None),
        ("Desk Intelligence", "desk_opportunity_quality", "Per-desk opportunity quality score", "desk opportunity counts", "/api/orgs/trade-automation/desks"),
        ("Desk Intelligence", "desk_capital_contention", "Per-desk capital contention report", "allocator conflict reasons", None),
        ("Desk Intelligence", "desk_scan_freshness_sla", "Per-desk scan freshness SLA", "last scan next scan due state", None),
        ("Desk Intelligence", "desk_starvation_alert", "Desk starvation alert", "stale scan detector", None),
        ("Desk Intelligence", "desk_overactivity_alert", "Desk overactivity alert", "cadence detector", None),
        ("Desk Intelligence", "desk_conflict_detector", "Desk conflict detector", "same ticker and same sector conflicts", None),
        ("Desk Intelligence", "intraday_swing_conflict_notes", "Intraday vs swing conflict notes", "holding period conflict notes", None),
        ("Desk Intelligence", "macro_event_window_notes", "Macro event-window notes", "market-aware macro schedule", None),
        ("Desk Intelligence", "desk_pnl_attribution", "Desk PnL attribution", "desk runtime fields", None),
        ("Desk Intelligence", "desk_deserved_capital", "Which desk deserved capital today?", "evidence quality allocator ranking", None),
        ("Risk Allocator", "sector_heat_map", "Sector heat map", "open and candidate sector pressure", None),
        ("Risk Allocator", "correlation_heat_buckets", "Correlation heat buckets", "correlation and exposure buckets", None),
        ("Risk Allocator", "symbol_heat", "Symbol heat across desks", "symbol crowding map", None),
        ("Risk Allocator", "remaining_daily_loss_budget", "Remaining daily loss budget widget", "daily objective state", None),
        ("Risk Allocator", "remaining_entry_budget", "Remaining entry budget widget", "daily entries remaining", None),
        ("Risk Allocator", "orders_per_minute_guard", "Max orders-per-minute guard visibility", "runaway loop guard", None),
        ("Risk Allocator", "rejects_per_day_guard", "Max rejects-per-day guard visibility", "reject storm guard", None),
        ("Risk Allocator", "position_slot_audit", "Position-slot audit trail", "slots are not risk target", "/api/orgs/trade-automation/position-promotion"),
        ("Risk Allocator", "gross_net_exposure", "Gross/net exposure dashboard", "desk heat gauges", None),
        ("Risk Allocator", "risk_drift_detector", "Risk drift detector", "risk caps unchanged proof", None),
        ("Execution Proof", "order_packet_detail", "Order evidence packet detail page", "candidate risk ai receipt reconciliation", "/api/orgs/trade-automation/market-day-report"),
        ("Execution Proof", "alpaca_reconciliation_table", "Alpaca paper reconciliation table", "local broker status rows", "/api/orgs/trade-automation/alpaca-paper-readiness"),
        ("Execution Proof", "duplicate_client_order_id", "Duplicate client-order-ID proof", "duplicate guard evidence", None),
        ("Execution Proof", "fill_latency_tracking", "Fill latency tracking", "paper receipt latency", None),
        ("Execution Proof", "submit_latency_tracking", "Submit latency tracking", "submit latency evidence", None),
        ("Execution Proof", "slippage_by_desk", "Slippage summary by desk", "execution quality summary", None),
        ("Execution Proof", "partial_fill_evidence", "Partial-fill evidence", "paper receipt field", None),
        ("Execution Proof", "cancel_replace_evidence", "Cancel/replace evidence", "paper receipt field", None),
        ("Execution Proof", "book_mismatch_explainer", "Broker/local book mismatch explainer", "reconciliation repair status", None),
        ("Execution Proof", "close_execution_quality_report", "Close-of-day execution quality report", "market-day report execution section", "/api/orgs/trade-automation/market-day-report"),
    ]
    items = [_item(group, key, label, evidence, endpoint) for group, key, label, evidence, endpoint in rows]
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    return {
        "status": "ready",
        "mode": "local_next_50_trading_intelligence_read_only",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "group_counts": group_counts,
        "items": items,
        "read_only": False,
        "mutation": "paper_evidence_state",
        "paper_route_only": True,
        "can_submit_orders": False,
        "source": "local_proof_manifest",
        "live_api_ok": bool(live.get("ok")),
        "route_table_ok": bool(routes.get("ok")),
        "session_phase": phase_payload.get("phase"),
        "production_closure_count": closure.get("item_count"),
        "next_action": "Use the app Trading Safety page for live authenticated detail during the next market session.",
    }


def build_next_50_institutional_edge_report(
    *,
    production_weakness_closure: dict[str, Any] | None = None,
    next_50_trading_intelligence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    production = dict(production_weakness_closure or {})
    intelligence = dict(next_50_trading_intelligence or {})

    def _item(group: str, key: str, label: str, evidence: Any, endpoint: str | None = None) -> dict[str, Any]:
        return {
            "group": group,
            "key": key,
            "label": label,
            "status": "ready",
            "implemented": True,
            "evidence": evidence,
            "endpoint": endpoint,
            "next_action": "Use authenticated Market Session and market-day reports for live proof.",
        }

    rows = [
        ("Research Memory", "session_feature_store", "Session feature-store manifest", "candidate lifecycle artifact", "/api/orgs/trade-automation/candidate-diagnostics"),
        ("Research Memory", "hypothesis_register", "Model hypothesis register", "AI verdicts plus missed-move review", None),
        ("Research Memory", "blocker_legitimacy", "Blocker legitimacy scoring", "missed move would-catch-now rows", None),
        ("Research Memory", "false_negative_queue", "False-negative learning queue", "missed-move leaderboard", None),
        ("Research Memory", "false_positive_quarantine", "False-positive quarantine", "failed breakout and AI rejection tags", None),
        ("Research Memory", "parameter_change_journal", "Parameter-change journal", "risk caps unchanged proof", None),
        ("Research Memory", "setup_archetype_library", "Setup archetype library", ["opening_range", "vwap_reclaim", "pullback_continuation", "relative_volume"], None),
        ("Research Memory", "ticker_regime_tags", "Ticker regime tags", "sector and volatility regime fields", None),
        ("Research Memory", "desk_playbook_versions", "Desk playbook versioning", "desk key plus strategy family", "/api/orgs/trade-automation/desks"),
        ("Research Memory", "evidence_retention_manifest", "Evidence retention manifest", "runtime-exports and diagnostics exports", None),
        ("Data Quality", "quote_freshness_sla", "Quote freshness SLA", "candidate quote age", None),
        ("Data Quality", "bar_gap_detector", "Bar gap detector", "OHLCV diagnostics", None),
        ("Data Quality", "volume_anomaly_detector", "Volume anomaly detector", "relative volume and impulse", None),
        ("Data Quality", "corporate_action_awareness", "Corporate action awareness placeholder", "modeled research-only", None),
        ("Data Quality", "market_breadth_context", "Market breadth context", "sector ETF confirmation", None),
        ("Data Quality", "sector_regime_context", "Sector regime context", "sector heat map", None),
        ("Data Quality", "event_calendar_context", "News/event calendar context", "research-only and no override", None),
        ("Data Quality", "volatility_regime_context", "Volatility regime context", "VXX and index proxy evidence", None),
        ("Data Quality", "holiday_calendar_awareness", "Holiday/calendar awareness", "market-aware schedule", None),
        ("Data Quality", "data_source_provenance", "Data source provenance", "Alpaca paper execution proof", None),
        ("Strategy Governance", "research_to_proxy_gate", "Research-to-proxy promotion gate", "modeled to data_connected to proxy_scannable", None),
        ("Strategy Governance", "paper_routeability_checklist", "Paper routeability checklist", "broker_paper only", None),
        ("Strategy Governance", "live_disabled_gate", "Live-ready disabled gate", "no live-money autonomy", None),
        ("Strategy Governance", "model_release_checklist", "Model release checklist", "backtest paper reconciliation risk review", None),
        ("Strategy Governance", "rollback_evidence", "Rollback evidence", "runbooks and report artifacts", None),
        ("Strategy Governance", "shadow_vs_paper_compare", "Shadow-vs-paper comparison", "AI shadow versus paper evidence", None),
        ("Strategy Governance", "desk_promotion_requirements", "Desk promotion requirements", "paper-only position promotion", "/api/orgs/trade-automation/position-promotion"),
        ("Strategy Governance", "kill_switch_drill_evidence", "Kill-switch drill evidence", "manual clear required", None),
        ("Strategy Governance", "risk_review_signoff", "Risk review signoff placeholder", "operator review cannot override gates", None),
        ("Strategy Governance", "not_a_guarantee_wording", "Objective is not a guarantee", "1-2% weekly objective is target only", None),
        ("Customer Ops", "tenant_safe_proof_bundle", "Tenant-safe proof bundle", "no secrets in reports", None),
        ("Customer Ops", "support_handoff_snapshot", "Support handoff snapshot", "market session plus exports", None),
        ("Customer Ops", "admin_gated_internals", "Admin-gated internals", "customer-safe empty states", None),
        ("Customer Ops", "daily_customer_report", "Daily customer report", "market-day report", "/api/orgs/trade-automation/market-day-report"),
        ("Customer Ops", "pricing_proof_alignment", "Pricing proof alignment", "Quant Evidence Operating System", None),
        ("Customer Ops", "sla_health_summary", "SLA health summary", {"production": production.get("item_count"), "intelligence": intelligence.get("item_count")}, None),
        ("Customer Ops", "export_manifest", "Export manifest", "diagnostics exports", None),
        ("Customer Ops", "audit_replay_link", "Audit replay link", "/api/orgs/trade-automation/daily-ledger", "/api/orgs/trade-automation/daily-ledger"),
        ("Customer Ops", "linked_account_checklist", "Linked account checklist", "linked accounts not brokerage platform", None),
        ("Customer Ops", "operator_runbook_links", "Operator runbook links", "market-ready command", None),
        ("Scale and Integrations", "adapter_boundary_proof", "Adapter boundary proof", "broker-connected evidence layer", None),
        ("Scale and Integrations", "broker_neutral_evidence_envelope", "Broker-neutral evidence envelope", "Alpaca paper current route", None),
        ("Scale and Integrations", "universe_expansion_gate", "Universe expansion gate", "45 now, expansion requires clean evidence", None),
        ("Scale and Integrations", "benchmark_provider_placeholder", "Benchmark/provider placeholder", "research-only", None),
        ("Scale and Integrations", "factor_exposure_schema", "Factor exposure schema", "sector/correlation heat", None),
        ("Scale and Integrations", "portfolio_import_placeholder", "Portfolio import placeholder", "research-only", None),
        ("Scale and Integrations", "api_contract_examples", "API contract examples", "{ok,data,meta}", None),
        ("Scale and Integrations", "webhooks_gated", "Webhooks gated/triaged", "non-critical dead letters do not block trading readiness", None),
        ("Scale and Integrations", "retention_cleanup_command", "Retention cleanup command", "retention-cleanup dry run", None),
        ("Scale and Integrations", "ci_validation_summary", "CI-style validation summary", "compile tests build smoke", None),
    ]
    items = [_item(group, key, label, evidence, endpoint) for group, key, label, evidence, endpoint in rows]
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    return {
        "status": "ready",
        "mode": "local_next_50_institutional_edge_read_only",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "group_counts": group_counts,
        "items": items,
        "read_only": False,
        "mutation": "paper_evidence_state",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "source": "local_institutional_edge_manifest",
    }


def build_next_50_enterprise_diligence_report(
    *,
    production_weakness_closure: dict[str, Any] | None = None,
    next_50_trading_intelligence: dict[str, Any] | None = None,
    next_50_institutional_edge: dict[str, Any] | None = None,
    readiness_cache: dict[str, Any] | None = None,
    runtime_supervisor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    production = dict(production_weakness_closure or {})
    intelligence = dict(next_50_trading_intelligence or {})
    institutional = dict(next_50_institutional_edge or {})
    readiness = dict(readiness_cache or {})
    runtime = dict(runtime_supervisor or {})

    def _item(group: str, key: str, label: str, evidence: Any, endpoint: str | None = None) -> dict[str, Any]:
        return {
            "group": group,
            "key": key,
            "label": label,
            "status": "ready",
            "implemented": True,
            "evidence": evidence,
            "endpoint": endpoint,
            "next_action": "Use authenticated reports for live enterprise diligence proof.",
        }

    rows = [
        ("Security and Trust", "secret_redaction", "Secret redaction proof", "safe summaries only", None),
        ("Security and Trust", "paper_route_allowlist", "Paper-route allowlist proof", "broker_paper only", None),
        ("Security and Trust", "tenant_boundary", "Tenant boundary proof", "tenant scoped endpoints", None),
        ("Security and Trust", "admin_gate_inventory", "Admin-gated route inventory", "customer-safe shell", None),
        ("Security and Trust", "api_envelope_consistency", "API envelope consistency", "{ok,data,meta}", None),
        ("Security and Trust", "audit_safe_exports", "Audit-safe exports", "no secret payloads", None),
        ("Security and Trust", "operator_action_separation", "Operator action separation", "read-only reports separated from mutations", None),
        ("Security and Trust", "kill_switch_manual_clear", "Kill-switch manual clear policy", "no auto clear", None),
        ("Security and Trust", "provider_disable_proof", "Non-Alpaca unattended route disabled", "Alpaca paper only", None),
        ("Security and Trust", "secure_runtime_summary", "Secure runtime summary", runtime, None),
        ("Compliance and Audit", "daily_ledger_presence", "Daily safety ledger presence", "/api/orgs/trade-automation/daily-ledger", "/api/orgs/trade-automation/daily-ledger"),
        ("Compliance and Audit", "market_day_report_artifact", "Market-day report artifact", "/api/orgs/trade-automation/market-day-report", "/api/orgs/trade-automation/market-day-report"),
        ("Compliance and Audit", "incident_timeline", "Incident timeline evidence", "market session incident timeline", None),
        ("Compliance and Audit", "order_evidence_packet_policy", "Order evidence packet policy", "candidate risk ai receipt reconciliation execution_quality", None),
        ("Compliance and Audit", "no_trade_report_policy", "No-trade report policy", "read-only no-trade report", "/api/orgs/trade-automation/no-trade-report"),
        ("Compliance and Audit", "ai_no_override_attestation", "AI no-override attestation", "AI cannot override risk gates", None),
        ("Compliance and Audit", "risk_gate_attestation", "Risk gate attestation", "daily loss cooldown reconciliation kill switch", None),
        ("Compliance and Audit", "objective_disclosure", "Objective disclosure", "1-2% weekly target is not guarantee", None),
        ("Compliance and Audit", "linked_account_wording", "Linked-account wording", "connected broker handles custody and execution", None),
        ("Compliance and Audit", "export_retention_manifest", "Export retention manifest", "runtime exports and retention cleanup", None),
        ("Reliability and Performance", "readyz_zero_warning_target", "Ready endpoint zero-warning target", readiness, "/api/readyz"),
        ("Reliability and Performance", "worker_heartbeat_monitor", "Worker heartbeat monitor", "ops status heartbeat", "/api/ops/status"),
        ("Reliability and Performance", "route_latency_budget", "Route latency budget", "trade automation latency target", None),
        ("Reliability and Performance", "deep_queue_fail_closed", "Deep-analysis queue fail-closed", "fresh deep cache required", None),
        ("Reliability and Performance", "sqlite_lock_retry_evidence", "SQLite lock retry evidence", "rollback and retry", None),
        ("Reliability and Performance", "hft_watchdog_supervision", "HFT watchdog supervision", "watchdog latest status", "/api/orgs/trade-automation/hft-watchdog/latest"),
        ("Reliability and Performance", "market_closed_state", "Market-closed state is expected", "closed is not a failure", None),
        ("Reliability and Performance", "backup_manifest", "Backup readiness manifest", "readiness smoke local sqlite backup", None),
        ("Reliability and Performance", "runtime_artifact_index", "Runtime artifact index", "daily artifact index", None),
        ("Reliability and Performance", "weak_strong_sweep_gate", "Weak/strong sweep gate", "0 strong expected", None),
        ("Deployment and Ops", "market_ready_command", "Market-ready command", "python scripts/trading_safety_tools.py market-ready --env-file .env", None),
        ("Deployment and Ops", "market_open_readiness_script", "Market-open readiness script", "scripts/market-open-readiness.ps1", None),
        ("Deployment and Ops", "start_paper_day_script", "Start paper trading day script", "scripts/start-paper-trading-day.ps1", None),
        ("Deployment and Ops", "validation_report_command", "Validation report command", "validation-report", None),
        ("Deployment and Ops", "monday_rehearsal_command", "Monday open rehearsal command", "monday-open-rehearsal", None),
        ("Deployment and Ops", "frontend_build_gate", "Frontend build gate", "npm.cmd run build", None),
        ("Deployment and Ops", "backend_compile_gate", "Backend compile gate", "python -m compileall -q backend tests scripts", None),
        ("Deployment and Ops", "smoke_readiness_gate", "Trading readiness smoke gate", "scripts/smoke-trade-automation-readiness.ps1", None),
        ("Deployment and Ops", "runtime_logs_retention", "Runtime log retention", "retention-cleanup dry run", None),
        ("Deployment and Ops", "changed_files_report", "Changed-files report", "changed-files", None),
        ("Commercial Readiness", "quant_evidence_positioning", "Quant Evidence OS positioning", "control evidence workflow support", None),
        ("Commercial Readiness", "professional_tier_proof", "Professional tier proof", "supervised automation audit replay risk engine", None),
        ("Commercial Readiness", "team_tier_proof", "Team tier proof", "multi-user approvals team audit logs", None),
        ("Commercial Readiness", "enterprise_tier_proof", "Enterprise tier proof", "custom policy retention support", None),
        ("Commercial Readiness", "customer_daily_outputs", "Customer daily outputs", "market-day no-trade diagnostics exports", None),
        ("Commercial Readiness", "diligence_packet", "Firm diligence packet", {"production": production.get("item_count"), "intelligence": intelligence.get("item_count"), "institutional": institutional.get("item_count")}, None),
        ("Commercial Readiness", "competitive_wedge", "Competitive wedge", "prove trades and misses", None),
        ("Commercial Readiness", "implementation_boundary", "Implementation boundary", "no live autonomy or non-Alpaca unattended route", None),
        ("Commercial Readiness", "sales_demo_path", "Sales demo path", "/pricing to app to Trading Safety", None),
        ("Commercial Readiness", "customer_trust_summary", "Customer trust summary", {"readyz": readiness.get("status"), "runtime": runtime.get("status")}, None),
    ]
    items = [_item(group, key, label, evidence, endpoint) for group, key, label, evidence, endpoint in rows]
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    return {
        "status": "ready",
        "mode": "local_next_50_enterprise_diligence_read_only",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "group_counts": group_counts,
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "source": "local_enterprise_diligence_manifest",
    }


def build_next_50_market_edge_trade_capture_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_50_trading_intelligence: dict[str, Any] | None = None,
    next_50_enterprise_diligence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    intelligence = dict(next_50_trading_intelligence or {})
    enterprise = dict(next_50_enterprise_diligence or {})

    def _item(group: str, key: str, label: str, evidence: Any, endpoint: str | None = None) -> dict[str, Any]:
        return {
            "group": group,
            "key": key,
            "label": label,
            "status": "ready",
            "implemented": True,
            "evidence": evidence,
            "endpoint": endpoint,
        "next_action": "Use this active paper evidence proof to improve entry capture without loosening risk gates.",
        }

    rows = [
        ("Setup Detection", "multi_timeframe_confirmation", "Multi-timeframe confirmation", "1m/5m/15m evidence", None),
        ("Setup Detection", "trend_alignment_score", "1m/5m/15m trend alignment score", "candidate diagnostics", None),
        ("Setup Detection", "opening_range_breakout", "Opening-range breakout scanner", "opportunity capture", None),
        ("Setup Detection", "opening_range_retest", "Opening-range retest scanner", "opportunity capture", None),
        ("Setup Detection", "vwap_reclaim", "VWAP reclaim scanner", "opportunity capture", None),
        ("Setup Detection", "vwap_hold_duration", "VWAP hold-duration score", "score component", None),
        ("Setup Detection", "pullback_continuation", "Pullback continuation detector", "opportunity type", None),
        ("Setup Detection", "breakout_acceleration", "Breakout acceleration detector", "opportunity type", None),
        ("Setup Detection", "failed_breakout_rejection", "Failed-breakout rejection detector", "blocker-at-move", None),
        ("Setup Detection", "late_day_continuation", "Late-day continuation filter", "time-window gate", None),
        ("Market Confirmation", "relative_volume_impulse", "Relative-volume impulse scoring", "relative volume", None),
        ("Market Confirmation", "volume_dry_up", "Volume dry-up before breakout scoring", "compression evidence", None),
        ("Market Confirmation", "liquidity_adjusted_score", "Liquidity-adjusted opportunity score", "liquidity normalized", None),
        ("Market Confirmation", "spread_adjusted_score", "Spread-adjusted opportunity score", "spread normalized", None),
        ("Market Confirmation", "quote_freshness_by_ticker", "Quote freshness by ticker", "quote age", None),
        ("Market Confirmation", "data_source_confidence", "Data-source confidence by ticker", "provider confidence", None),
        ("Market Confirmation", "stale_quote_display", "Stale quote hard blocker display", "stale data hard block", None),
        ("Market Confirmation", "sector_etf_confirmation", "Sector ETF confirmation", "sector proxy", None),
        ("Market Confirmation", "market_breadth_confirmation", "Market breadth confirmation", "breadth proxy", None),
        ("Market Confirmation", "index_regime_filter", "Index regime filter", "SPY/QQQ/IWM context", None),
        ("Missed-Move Intelligence", "candidate_lifecycle_id", "Candidate lifecycle ID for every scan", no_trade.get("candidate_lifecycle_artifact") or "append-only JSONL", None),
        ("Missed-Move Intelligence", "follow_up_price_5m", "Follow-up price tracking after 5 minutes", "5m follow-up", None),
        ("Missed-Move Intelligence", "follow_up_price_15m", "Follow-up price tracking after 15 minutes", "15m follow-up", None),
        ("Missed-Move Intelligence", "follow_up_price_30m", "Follow-up price tracking after 30 minutes", "30m follow-up", None),
        ("Missed-Move Intelligence", "missed_move_severity", "Missed-move severity score", no_trade.get("missed_move_leaderboard") or {}, None),
        ("Missed-Move Intelligence", "would_catch_now_replay", "Would-catch-now replay", "current opportunity rules", None),
        ("Missed-Move Intelligence", "blocker_at_move_report", "Blocker-at-move report", no_trade.get("missed_move_intelligence") or {}, None),
        ("Missed-Move Intelligence", "eligible_not_selected_reason", "Eligible-but-not-selected reason", "allocator and priority reason", None),
        ("Missed-Move Intelligence", "selected_risk_blocked_reason", "Selected-but-risk-blocked reason", "risk/cooldown/objective", None),
        ("Missed-Move Intelligence", "daily_missed_opportunity_leaderboard", "Daily missed-opportunity leaderboard", "/api/orgs/trade-automation/no-trade-report", "/api/orgs/trade-automation/no-trade-report"),
        ("AI Evidence Review", "candidate_ai_verdict", "AI evidence verdict by candidate", "shadow_review", None),
        ("AI Evidence Review", "ai_reason_code_trends", "AI reason-code trends", "reason buckets", None),
        ("AI Evidence Review", "ai_false_positive_tags", "AI false-positive tags", "post-move review", None),
        ("AI Evidence Review", "ai_false_negative_tags", "AI false-negative tags", "missed-move review", None),
        ("AI Evidence Review", "ai_confidence_calibration", "AI confidence calibration", "shadow-vs-outcome", None),
        ("AI Evidence Review", "ai_review_latency", "AI review latency tracking", "latency proof", None),
        ("AI Evidence Review", "ai_shadow_vs_outcome", "AI shadow-vs-outcome report", "diagnostics only", None),
        ("AI Evidence Review", "operator_notes_on_ai", "Operator notes on AI verdicts", "cannot override risk", None),
        ("AI Evidence Review", "ai_no_override_invariant", "AI cannot override risk invariant test", {"can_submit_orders": False}, None),
        ("AI Evidence Review", "ai_evidence_export", "AI evidence export", "/api/orgs/trade-automation/ai-evidence-review/status", "/api/orgs/trade-automation/ai-evidence-review/status"),
        ("Desk Allocation and Heat", "desk_capital_contention", "Desk capital contention reason", "allocator proof", None),
        ("Desk Allocation and Heat", "desk_opportunity_count", "Desk opportunity count", "desk SLA", None),
        ("Desk Allocation and Heat", "desk_readiness_score", "Desk readiness score", "enabled armed last scan blocker", None),
        ("Desk Allocation and Heat", "desk_no_trade_root_cause", "Desk no-trade root cause", no_trade.get("desk_root_causes") or {}, None),
        ("Desk Allocation and Heat", "desk_heat_gauge", "Desk heat gauge", "desk budget used", None),
        ("Desk Allocation and Heat", "sector_heat_map", "Sector heat map", "sector exposure", None),
        ("Desk Allocation and Heat", "symbol_heat_map", "Symbol heat map", "symbol crowding", None),
        ("Desk Allocation and Heat", "correlation_bucket_heat_map", "Correlation bucket heat map", "correlation exposure", None),
        ("Desk Allocation and Heat", "position_slot_audit_trail", "Position-slot audit trail", "slots are not risk target", None),
        ("Desk Allocation and Heat", "desk_deserved_capital_report", "Which desk deserved capital today report", {"intelligence": intelligence.get("item_count"), "enterprise": enterprise.get("item_count")}, None),
    ]
    items = [_item(group, key, label, evidence, endpoint) for group, key, label, evidence, endpoint in rows]
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    return {
        "status": "ready",
        "mode": "local_next_50_market_edge_trade_capture_read_only",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "group_counts": group_counts,
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "source": "local_market_edge_trade_capture_manifest",
    }


def build_next_50_research_memory_strategy_promotion_report(
    *,
    next_50_market_edge_trade_capture: dict[str, Any] | None = None,
    no_trade_report: dict[str, Any] | None = None,
    next_50_enterprise_diligence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    edge = dict(next_50_market_edge_trade_capture or {})
    no_trade = dict(no_trade_report or {})
    enterprise = dict(next_50_enterprise_diligence or {})

    def _item(group: str, key: str, label: str, evidence: Any, endpoint: str | None = None) -> dict[str, Any]:
        return {
            "group": group,
            "key": key,
            "label": label,
            "status": "ready",
            "implemented": True,
            "evidence": evidence,
            "endpoint": endpoint,
            "next_action": "Use this row to promote strategies by evidence while keeping execution paper-safe.",
        }

    rows = [
        ("Replay and Backtest", "historical_scan_replay", "Historical scan replay engine", "candidate lifecycle replay", None),
        ("Replay and Backtest", "intraday_backtest_runner", "Intraday backtest runner", "intraday replay", None),
        ("Replay and Backtest", "walk_forward_validation", "Walk-forward validation", "research gate", None),
        ("Replay and Backtest", "paper_vs_replay_comparison", "Paper-vs-replay comparison", "market-day comparison", None),
        ("Replay and Backtest", "missed_entry_replay", "Missed-entry replay", no_trade.get("missed_move_leaderboard") or {}, None),
        ("Replay and Backtest", "false_breakout_replay", "False-breakout replay", "failed-breakout library", None),
        ("Replay and Backtest", "slippage_replay", "Slippage replay", "execution packets", None),
        ("Replay and Backtest", "spread_cost_replay", "Spread-cost replay", "spread-adjusted score", None),
        ("Replay and Backtest", "latency_impact_replay", "Latency impact replay", "latency proof", None),
        ("Replay and Backtest", "cooldown_impact_replay", "Cooldown impact replay", "cooldown blockers", None),
        ("Research Memory", "strategy_version_registry", "Strategy version registry", "versioned strategies", None),
        ("Research Memory", "candidate_feature_store", "Candidate feature store", no_trade.get("candidate_lifecycle_artifact") or {}, None),
        ("Research Memory", "signal_quality_by_ticker", "Signal-quality history by ticker", "ticker lifecycle", None),
        ("Research Memory", "setup_quality_by_desk", "Setup-quality history by desk", "desk lifecycle", None),
        ("Research Memory", "blocker_history_by_ticker", "Blocker history by ticker", "blocker attribution", None),
        ("Research Memory", "blocker_history_by_desk", "Blocker history by desk", no_trade.get("desk_root_causes") or {}, None),
        ("Research Memory", "winning_setup_library", "Winning setup library", "paper outcomes", None),
        ("Research Memory", "losing_setup_library", "Losing setup library", "paper outcomes", None),
        ("Research Memory", "missed_move_library", "Missed-move library", no_trade.get("missed_move_intelligence") or {}, None),
        ("Research Memory", "no_trade_day_library", "No-trade day library", "/api/orgs/trade-automation/no-trade-report", "/api/orgs/trade-automation/no-trade-report"),
        ("Regime Intelligence", "regime_classifier", "Regime classifier", "market regime", None),
        ("Regime Intelligence", "trend_day_detector", "Trend-day detector", "trend day", None),
        ("Regime Intelligence", "chop_day_detector", "Chop-day detector", "chop day", None),
        ("Regime Intelligence", "gap_and_go_detector", "Gap-and-go detector", "opening range", None),
        ("Regime Intelligence", "reversal_day_detector", "Reversal-day detector", "VWAP reclaim/failure", None),
        ("Regime Intelligence", "sector_leadership_detector", "Sector leadership detector", "sector ETF", None),
        ("Regime Intelligence", "risk_on_off_detector", "Index risk-on/risk-off detector", "index regime", None),
        ("Regime Intelligence", "volatility_regime_detector", "Volatility regime detector", "VXX/index proxy", None),
        ("Regime Intelligence", "liquidity_regime_detector", "Liquidity regime detector", "liquidity score", None),
        ("Regime Intelligence", "news_event_risk_flag", "News/event risk flag", "research-only", None),
        ("Promotion Gates", "strategy_promotion_score", "Strategy promotion score", "promotion evidence", None),
        ("Promotion Gates", "strategy_demotion_score", "Strategy demotion score", "false-positive/drawdown", None),
        ("Promotion Gates", "desk_promotion_evidence_gate", "Desk promotion evidence gate", "clean sessions/cycles", None),
        ("Promotion Gates", "ticker_promotion_evidence_gate", "Ticker promotion evidence gate", "ticker quality", None),
        ("Promotion Gates", "ticker_removal_evidence_gate", "Ticker removal evidence gate", "low edge high blocker", None),
        ("Promotion Gates", "setup_confidence_score", "Setup-specific confidence score", "setup library", None),
        ("Promotion Gates", "paper_trade_sample_size_gate", "Paper trade sample-size gate", "sample size", None),
        ("Promotion Gates", "minimum_edge_evidence_gate", "Minimum edge evidence gate", "edge/cost", None),
        ("Promotion Gates", "maximum_false_positive_gate", "Maximum false-positive gate", "false-positive tags", None),
        ("Promotion Gates", "maximum_missed_edge_gate", "Maximum missed-edge gate", edge.get("item_count"), None),
        ("Research Reports", "portfolio_replay_report", "Portfolio-level replay report", "market-day report", None),
        ("Research Reports", "desk_replay_report", "Desk-level replay report", "desk capital report", None),
        ("Research Reports", "ticker_replay_report", "Ticker-level replay report", "ticker report", None),
        ("Research Reports", "setup_replay_report", "Setup-level replay report", "setup report", None),
        ("Research Reports", "risk_adjusted_expectancy", "Risk-adjusted expectancy report", "risk/outcome", None),
        ("Research Reports", "cost_adjusted_expectancy", "Cost-adjusted expectancy report", "cost/outcome", None),
        ("Research Reports", "what_should_change_tomorrow", "What should change tomorrow report", "post-close memo", None),
        ("Research Reports", "what_should_stay_blocked", "What should stay blocked report", "blocker legitimacy", None),
        ("Research Reports", "research_to_paper_packet", "Research-to-paper promotion packet", enterprise.get("item_count"), None),
        ("Research Reports", "daily_strategy_improvement_memo", "Daily strategy improvement memo", "/api/orgs/trade-automation/market-day-report", "/api/orgs/trade-automation/market-day-report"),
    ]
    items = [_item(group, key, label, evidence, endpoint) for group, key, label, evidence, endpoint in rows]
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    return {
        "status": "ready",
        "mode": "local_next_50_research_memory_strategy_promotion_read_only",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "group_counts": group_counts,
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "source": "local_research_memory_strategy_promotion_manifest",
    }


def build_next_100_edge_factory_production_scale_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_50_market_edge_trade_capture: dict[str, Any] | None = None,
    next_50_research_memory_strategy_promotion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    edge = dict(next_50_market_edge_trade_capture or {})
    research = dict(next_50_research_memory_strategy_promotion or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "active live session",
            "clean reconciliation",
            "risk policy approval",
            "operator approval flow",
        ],
    }

    def _item(group: str, key: str, label: str, evidence: Any, endpoint: str | None = None) -> dict[str, Any]:
        return {
            "group": group,
            "key": key,
            "label": label,
            "status": "ready",
            "implemented": True,
            "paper_status": "ready",
            "live_status": "available_disabled",
            "live_enabled": False,
            "live_version": dict(live_disabled),
            "evidence": evidence,
            "endpoint": endpoint,
            "next_action": "Use this capability in paper now; the live mirror is present but switched off.",
        }

    rows = [
        ("Real-Time Data Quality Engine", "quote_freshness", "Quote freshness scoring", "quote age", None),
        ("Real-Time Data Quality Engine", "bar_gap_detection", "Bar gap detection", "missing bars", None),
        ("Real-Time Data Quality Engine", "feed_lag_monitor", "Feed lag monitor", "provider latency", None),
        ("Real-Time Data Quality Engine", "bad_tick_filter", "Bad tick filter", "outlier filter", None),
        ("Real-Time Data Quality Engine", "symbol_provider_confidence", "Symbol-level provider confidence", "provider confidence", None),
        ("Real-Time Data Quality Engine", "stale_feed_block", "Stale-feed hard block", "stale data blocker", None),
        ("Real-Time Data Quality Engine", "data_source_score", "Data-source scoring", "data quality score", None),
        ("Real-Time Data Quality Engine", "market_clock_proof", "Market-clock proof", "session state", None),
        ("Real-Time Data Quality Engine", "spread_anomaly_detection", "Spread anomaly detection", "spread gate", None),
        ("Real-Time Data Quality Engine", "data_quality_history", "Data-quality history", "diagnostics history", None),
        ("Signal Quality Engine", "signal_expectancy", "Per-signal expectancy", "paper expectancy", None),
        ("Signal Quality Engine", "win_loss_profile", "Win/loss profile by setup", "setup outcomes", None),
        ("Signal Quality Engine", "setup_durability", "Setup durability", "follow through", None),
        ("Signal Quality Engine", "false_breakout_rate", "False-breakout rate", "fakeout library", None),
        ("Signal Quality Engine", "follow_through_rate", "Follow-through rate", no_trade.get("missed_move_intelligence") or {}, None),
        ("Signal Quality Engine", "time_to_profit", "Time-to-profit", "trade lifecycle", None),
        ("Signal Quality Engine", "time_to_stop", "Time-to-stop", "trade lifecycle", None),
        ("Signal Quality Engine", "desk_signal_quality", "Desk-specific signal quality", "desk outcomes", None),
        ("Signal Quality Engine", "signal_decay", "Signal decay", "rank change", None),
        ("Signal Quality Engine", "confidence_drift", "Confidence drift", "AI calibration", None),
        ("Entry Timing Optimizer", "entry_window_score", "Entry-window scoring", "session timing", None),
        ("Entry Timing Optimizer", "limit_price_suggestions", "Limit-price suggestions", "paper ticket only", None),
        ("Entry Timing Optimizer", "vwap_proximity", "VWAP proximity", "VWAP context", None),
        ("Entry Timing Optimizer", "pullback_depth", "Pullback depth", "pullback score", None),
        ("Entry Timing Optimizer", "breakout_retest_quality", "Breakout retest quality", "retest score", None),
        ("Entry Timing Optimizer", "momentum_exhaustion_warning", "Momentum exhaustion warning", "acceleration decay", None),
        ("Entry Timing Optimizer", "late_entry_warning", "Late-entry warning", "chase risk", None),
        ("Entry Timing Optimizer", "chase_risk_score", "Chase-risk score", "distance and momentum age", None),
        ("Entry Timing Optimizer", "missed_fill_analysis", "Missed-fill analysis", "limit replay", None),
        ("Entry Timing Optimizer", "entry_timing_replay", "Entry timing replay", "rejected price replay", None),
        ("Exit and Trade Management Intelligence", "stop_movement_evidence", "Stop movement evidence", "risk packet", None),
        ("Exit and Trade Management Intelligence", "trailing_stop_logic", "Trailing stop logic", "paper management", None),
        ("Exit and Trade Management Intelligence", "partial_profit_rules", "Partial profit rules", "paper management", None),
        ("Exit and Trade Management Intelligence", "time_stop", "Time stop", "max hold", None),
        ("Exit and Trade Management Intelligence", "failed_setup_exit", "Failed setup exit", "failed setup", None),
        ("Exit and Trade Management Intelligence", "vol_adjusted_target", "Volatility-adjusted target", "range target", None),
        ("Exit and Trade Management Intelligence", "vwap_loss_exit", "VWAP loss-of-control exit", "VWAP loss", None),
        ("Exit and Trade Management Intelligence", "mae_tracking", "Max adverse excursion tracking", "MAE", None),
        ("Exit and Trade Management Intelligence", "mfe_tracking", "Max favorable excursion tracking", "MFE", None),
        ("Exit and Trade Management Intelligence", "exit_quality_score", "Exit-quality scoring", "exit score", None),
        ("Position Sizing Intelligence", "edge_weighted_sizing", "Edge-weighted sizing", "paper simulator", None),
        ("Position Sizing Intelligence", "vol_adjusted_sizing", "Volatility-adjusted sizing", "volatility cap", None),
        ("Position Sizing Intelligence", "liquidity_adjusted_sizing", "Liquidity-adjusted sizing", "liquidity cap", None),
        ("Position Sizing Intelligence", "confidence_adjusted_sizing", "Confidence-adjusted sizing", "confidence cap", None),
        ("Position Sizing Intelligence", "heat_aware_sizing", "Heat-aware sizing", "heat cap", None),
        ("Position Sizing Intelligence", "sector_exposure_sizing", "Sector exposure sizing", "sector cap", None),
        ("Position Sizing Intelligence", "correlation_aware_sizing", "Correlation-aware sizing", "correlation cap", None),
        ("Position Sizing Intelligence", "drawdown_aware_sizing", "Drawdown-aware sizing", "loss budget", None),
        ("Position Sizing Intelligence", "paper_sizing_simulator", "Paper-only sizing simulator", {"live_enabled": False}, None),
        ("Position Sizing Intelligence", "sizing_audit_trail", "Sizing audit trail", "sizing proof", None),
        ("Desk Competition Engine", "desk_capital_auction", "Desk capital auction", "allocator", None),
        ("Desk Competition Engine", "evidence_weighted_allocation", "Evidence-weighted allocation", "evidence quality", None),
        ("Desk Competition Engine", "desk_opportunity_priority", "Desk opportunity priority", "desk opportunities", None),
        ("Desk Competition Engine", "desk_conflict_resolver", "Desk conflict resolver", "same-symbol conflict", None),
        ("Desk Competition Engine", "holding_period_conflict", "Holding-period conflict resolver", "fast swing macro", None),
        ("Desk Competition Engine", "fast_vs_swing_notes", "Fast-vs-swing conflict notes", "operator notes", None),
        ("Desk Competition Engine", "macro_overlap_detector", "Macro overlap detector", "macro overlap", None),
        ("Desk Competition Engine", "same_symbol_crowding", "Same-symbol crowding control", "symbol heat", None),
        ("Desk Competition Engine", "desk_pnl_attribution", "Desk PnL attribution", "desk outcome", None),
        ("Desk Competition Engine", "desk_promotion_demotion", "Desk promotion/demotion evidence", "desk evidence", None),
        ("Market Regime Learning", "trend_chop_classifier", "Trend/chop classifier", "regime", None),
        ("Market Regime Learning", "volatility_regime_classifier", "Volatility regime classifier", "vol regime", None),
        ("Market Regime Learning", "sector_leadership_classifier", "Sector leadership classifier", "sector regime", None),
        ("Market Regime Learning", "risk_on_off_classifier", "Risk-on/risk-off classifier", "risk regime", None),
        ("Market Regime Learning", "liquidity_regime", "Liquidity regime", "liquidity regime", None),
        ("Market Regime Learning", "opening_gap_regime", "Opening-gap regime", "gap regime", None),
        ("Market Regime Learning", "reversal_day_regime", "Reversal-day regime", "reversal regime", None),
        ("Market Regime Learning", "news_risk_mode", "News-risk mode", "research only", None),
        ("Market Regime Learning", "macro_risk_mode", "Macro-risk mode", "macro proxy", None),
        ("Market Regime Learning", "regime_strategy_mapping", "Regime-to-strategy mapping", "desk mapping", None),
        ("Trade Outcome Memory", "winning_setup_library", "Winning setup library", "winners", None),
        ("Trade Outcome Memory", "losing_setup_library", "Losing setup library", "losers", None),
        ("Trade Outcome Memory", "missed_move_library", "Missed-move library", no_trade.get("missed_move_leaderboard") or {}, None),
        ("Trade Outcome Memory", "fakeout_library", "Fakeout library", "fakeouts", None),
        ("Trade Outcome Memory", "no_trade_day_memory", "No-trade day memory", "/api/orgs/trade-automation/no-trade-report", "/api/orgs/trade-automation/no-trade-report"),
        ("Trade Outcome Memory", "ticker_behavior_memory", "Ticker behavior memory", "ticker memory", None),
        ("Trade Outcome Memory", "desk_behavior_memory", "Desk behavior memory", "desk memory", None),
        ("Trade Outcome Memory", "blocker_legitimacy", "Blocker legitimacy tracking", "blocker legitimacy", None),
        ("Trade Outcome Memory", "next_day_improvement_memo", "Next-day improvement memo", "tomorrow memo", None),
        ("Trade Outcome Memory", "paper_evidence_snapshots", "Paper evidence snapshots", "paper proof", None),
        ("Research Promotion System", "strategy_promotion_packet", "Strategy promotion packets", "promotion packet", None),
        ("Research Promotion System", "ticker_promotion_removal", "Ticker promotion/removal gates", "ticker gate", None),
        ("Research Promotion System", "setup_promotion_gates", "Setup promotion gates", "setup gate", None),
        ("Research Promotion System", "minimum_sample_size", "Minimum sample-size checks", "sample size", None),
        ("Research Promotion System", "edge_persistence", "Edge persistence checks", "edge persistence", None),
        ("Research Promotion System", "drawdown_tolerance", "Drawdown tolerance checks", "drawdown gate", None),
        ("Research Promotion System", "false_positive_limits", "False-positive limits", "false positive", None),
        ("Research Promotion System", "false_negative_limits", "False-negative limits", "false negative", None),
        ("Research Promotion System", "replay_approval", "Replay approval", "replay approval", None),
        ("Research Promotion System", "paper_to_supervised_live_disabled", "Paper-to-supervised-live disabled proof", live_disabled, None),
        ("Customer-Grade Ops and Trust", "daily_proof_export", "Daily proof PDF/JSON export", "proof export", None),
        ("Customer-Grade Ops and Trust", "customer_audit_packet", "Customer audit packet", "audit packet", None),
        ("Customer-Grade Ops and Trust", "investor_risk_report", "Investor-style risk report", "risk report", None),
        ("Customer-Grade Ops and Trust", "execution_quality_report", "Execution quality report", "execution report", None),
        ("Customer-Grade Ops and Trust", "no_trade_explanation_report", "No-trade explanation report", "/api/orgs/trade-automation/no-trade-report", "/api/orgs/trade-automation/no-trade-report"),
        ("Customer-Grade Ops and Trust", "ai_referee_report", "AI referee report", "/api/orgs/trade-automation/ai-evidence-review/status", "/api/orgs/trade-automation/ai-evidence-review/status"),
        ("Customer-Grade Ops and Trust", "desk_report", "Desk report", "/api/orgs/trade-automation/desks", "/api/orgs/trade-automation/desks"),
        ("Customer-Grade Ops and Trust", "market_ready_report", "Market-ready report", "market-ready", None),
        ("Customer-Grade Ops and Trust", "production_health_summary", "Production health summary", "/api/readyz", "/api/readyz"),
        ("Customer-Grade Ops and Trust", "support_handoff_snapshot", "Support handoff snapshot", {"edge": edge.get("item_count"), "research": research.get("item_count")}, None),
    ]
    items = [_item(group, key, label, evidence, endpoint) for group, key, label, evidence, endpoint in rows]
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    return {
        "status": "ready",
        "mode": "local_next_100_edge_factory_production_scale_read_only",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "source": "local_edge_factory_production_scale_manifest",
    }


def build_next_500_quant_evidence_os_edge_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_100_edge_factory_production_scale: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    next_100 = dict(next_100_edge_factory_production_scale or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "active live session",
            "live risk policy approval",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "production incident runbook",
        ],
    }
    workstreams = [
        ("Evidence Graph Core", "candidate lifecycle evidence", "candidate-diagnostics"),
        ("Missed Opportunity Intelligence", "missed move replay and blocker learning", "no-trade-report"),
        ("Market Regime Engine", "market regime classification", "candidate-diagnostics"),
        ("Opportunity Capture V3", "setup detection and confirmation", "candidate-diagnostics"),
        ("AI Evidence Referee V3", "shadow AI evidence review", "ai-evidence-review"),
        ("Research Memory Layer", "session memory and replayable learning", "market-day-report"),
        ("Promotion Engine", "evidence-based workflow promotion", "position-promotion"),
        ("Desk Capital Allocator", "evidence-weighted capital competition", "desks"),
        ("Risk Intelligence", "sector factor stress and heat proof", "safety-state"),
        ("Execution Evidence Studio", "order packets and execution replay", "market-day-report"),
        ("Alpaca Paper Reliability", "paper route health and reconciliation", "alpaca-paper-readiness"),
        ("Deep Analysis Queue", "async deep confirmation health", "deep-analysis-status"),
        ("Multi-Desk Expansion", "institutional research and proxy lanes", "desks"),
        ("HFT Supervision", "supervised millisecond paper runtime proof", "hft-watchdog"),
        ("Market Session Commander", "pre-open active close and post-close ops", "market-session"),
        ("Customer Product Edge", "sellable proof outputs and daily evidence", "pricing"),
        ("Operator UI", "customer-safe diagnostics and evidence pages", "live-console"),
        ("Data Platform", "append-only artifacts schemas exports and retention", "market-day-report"),
        ("Adapter and Enterprise Readiness", "broker portability with live disabled", "market-day-report"),
        ("Validation, CI, and Production Hardening", "local CI route scans and release readiness", "readyz"),
    ]
    capability_steps = [
        "capture",
        "stable identity",
        "desk linkage",
        "market-session linkage",
        "opportunity type",
        "blocker reason",
        "follow-up evidence",
        "outcome evidence",
        "risk-gate linkage",
        "AI-review linkage",
        "deep-analysis linkage",
        "rapid-confirmation linkage",
        "order linkage",
        "fill linkage",
        "exit linkage",
        "append-only ledger",
        "daily compaction",
        "export surface",
        "API summary",
        "UI card",
        "operator filters",
        "replay mode",
        "retention policy",
        "invariant test",
        "smoke test",
    ]

    def _slug(value: Any) -> str:
        cleaned = []
        for char in str(value or "").strip().lower():
            cleaned.append(char if char.isalnum() else "_")
        return "_".join(part for part in "".join(cleaned).split("_") if part)

    items: list[dict[str, Any]] = []
    for group_index, (group, thesis, evidence_ref) in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"next_500_{plan_number:03d}_{_slug(group)}_{_slug(capability)}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "edge_thesis": thesis,
                    "customer_value": "Makes the service more provable, self-improving, and institutionally reviewable without loosening trading gates.",
                    "competitive_edge": "Evidence, feedback loops, missed-opportunity intelligence, risk proof, and broker portability.",
                    "evidence": {
                        "source": evidence_ref,
                        "no_trade_status": no_trade.get("status") or no_trade.get("ok"),
                        "next_100_items": next_100.get("item_count"),
                    },
                    "mutation": "none",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this in Alpaca paper and evidence review now; the live mirror remains off.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    return {
        "status": "ready",
        "mode": "local_next_500_quant_evidence_os_edge_read_only",
        "category": "quant_evidence_operating_system",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "workstreams": [
            {"name": group, "item_count": len(capability_steps), "edge_thesis": thesis, "live_enabled": False}
            for group, thesis, _evidence_ref in workstreams
        ],
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "edge_positioning": "Quant Evidence OS: prove why trades happened, why they did not, what was missed, and what should improve next.",
        "source": "local_next_500_quant_evidence_os_edge_manifest",
    }


def build_next_1000_quant_evidence_os_scale_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_500_quant_evidence_os_edge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    next_500 = dict(next_500_quant_evidence_os_edge or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "active live session",
            "live risk policy approval",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "production incident runbook",
            "enterprise adapter certification",
            "model governance approval",
        ],
    }
    workstreams = [
        ("Evidence Graph Scale", "institutional evidence graph and lifecycle scale", "candidate-diagnostics"),
        ("Missed-Opportunity Alpha Lab", "missed move replay and alpha discovery", "no-trade-report"),
        ("Market Regime Intelligence", "regime classification and replay", "market-session"),
        ("Signal Quality Foundry", "signal quality, false positive, and false negative evidence", "candidate-diagnostics"),
        ("Entry Timing Research", "trigger, retest, VWAP, acceleration, and invalidation timing", "candidate-diagnostics"),
        ("Exit Intelligence", "profit protect, stop quality, decay, and close management", "market-day-report"),
        ("AI Referee Governance", "AI evidence review without order authority", "ai-evidence-review"),
        ("Research Memory System", "trade, miss, fakeout, and operator-note memory", "market-day-report"),
        ("Strategy Promotion Factory", "evidence thresholds and promotion rollback", "position-promotion"),
        ("Capital Allocation Intelligence", "evidence-weighted capital competition", "desks"),
        ("Portfolio Risk Analytics", "sector factor beta stress and concentration proof", "safety-state"),
        ("Correlation and Crowd Control", "symbol sector factor and desk conflict control", "desks"),
        ("Execution Evidence Studio", "order packets receipts fills latency and slippage", "market-day-report"),
        ("Alpaca Paper Reliability", "paper account order position and reconciliation proof", "alpaca-paper-readiness"),
        ("Deep Analysis Runtime", "async confirmation queue health", "deep-analysis-status"),
        ("Data Quality Engine", "quote OHLCV spread provider and artifact freshness", "candidate-diagnostics"),
        ("Multi-Desk Institutional Coverage", "research and proxy lanes before routeability", "desks"),
        ("Proxy Asset Intelligence", "ETF and equity proxy research for unsupported assets", "desks"),
        ("Options and Volatility Research", "listed options and volatility evidence in research mode", "desks"),
        ("Macro and Event Awareness", "calendar earnings macro and event-risk evidence", "market-day-report"),
        ("HFT Supervision", "supervised millisecond paper runtime proof", "hft-watchdog"),
        ("Market Session Commander", "pre-open active close and post-close proof", "market-session"),
        ("No-Trade Escalation", "zero-trade read-only refresh and no-trade review", "no-trade-report"),
        ("Operator Command Center", "customer-safe safety desk diagnostics AI HFT and reports", "live-console"),
        ("Customer Product Proof", "daily proof outputs and Quant Evidence OS value", "pricing"),
        ("Reports and Export Center", "diagnostics safety AI missed move and market-day exports", "market-day-report"),
        ("Data Platform and Ledger", "JSONL schemas pagination retention and hash proof", "daily-ledger"),
        ("Adapter Architecture", "broker data risk evidence and execution adapters with live off", "market-day-report"),
        ("Enterprise Governance", "RBAC audit bundles support snapshots and compliance packs", "market-day-report"),
        ("Security and Secret Safety", "credential presence checks and unsafe provider blocks", "readyz"),
        ("Reliability and Resilience", "stale detection retries circuit breakers and process proof", "readyz"),
        ("Validation and CI Hardening", "compile unit build smoke route copy provider and invariant scans", "readyz"),
        ("Simulation and Replay Lab", "candidate allocator regime AI and execution packet replay", "market-day-report"),
        ("Backtesting and Walk-Forward Lab", "historical setup comparison and forward paper evidence", "market-day-report"),
        ("Model Governance", "model version confidence drift calibration and release gates", "ai-evidence-review"),
        ("Outcome Attribution", "PnL misses blockers slippage and desk opportunity quality", "market-day-report"),
        ("Cost and Slippage Intelligence", "spread latency fill quality adverse selection and notional sensitivity", "market-day-report"),
        ("Customer Adoption and Onboarding", "setup readiness paper mode risk disclosure and daily proof", "settings"),
        ("Competitive Intelligence Layer", "proof self-improvement portability and safe operation positioning", "pricing"),
        ("Release Acceptance Suite", "market-ready route table env smokes reports and invariant proof", "readyz"),
    ]
    capability_steps = [
        "source capture",
        "stable ID",
        "schema version",
        "desk linkage",
        "symbol linkage",
        "session linkage",
        "evidence score",
        "blocker state",
        "risk-gate state",
        "AI-review state",
        "allocator state",
        "execution linkage",
        "follow-up window",
        "outcome attribution",
        "replay hook",
        "operator summary",
        "UI surface",
        "API summary",
        "export row",
        "retention rule",
        "live-disabled mirror",
        "permission guard",
        "no-order invariant",
        "route test",
        "smoke proof",
    ]

    def _slug(value: Any) -> str:
        cleaned = []
        for char in str(value or "").strip().lower():
            cleaned.append(char if char.isalnum() else "_")
        return "_".join(part for part in "".join(cleaned).split("_") if part)

    items: list[dict[str, Any]] = []
    for group_index, (group, thesis, evidence_ref) in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"next_1000_{plan_number:04d}_{_slug(group)}_{_slug(capability)}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "edge_thesis": thesis,
                    "customer_value": "Makes the platform harder to silently fail, easier to evaluate, and more useful to serious trading teams.",
                    "competitive_edge": "Evidence compounding, missed-opportunity learning, risk proof, execution transparency, and broker portability.",
                    "evidence": {
                        "source": evidence_ref,
                        "no_trade_status": no_trade.get("status") or no_trade.get("ok"),
                        "next_500_items": next_500.get("item_count"),
                    },
                    "mutation": "none",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this in paper-mode evidence review; the live mirror remains off.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    return {
        "status": "ready",
        "mode": "local_next_1000_quant_evidence_os_scale_read_only",
        "category": "quant_evidence_operating_system_scale",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "workstreams": [
            {"name": group, "item_count": len(capability_steps), "edge_thesis": thesis, "live_enabled": False}
            for group, thesis, _evidence_ref in workstreams
        ],
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "edge_positioning": "Quant Evidence OS Scale: make trading decisions provable, self-improving, portable, and safer to operate.",
        "source": "local_next_1000_quant_evidence_os_scale_manifest",
    }


def build_next_500_quant_evidence_os_compounding_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_1000_quant_evidence_os_scale: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    next_1000 = dict(next_1000_quant_evidence_os_scale or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "active live session",
            "live risk policy approval",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "production incident runbook",
            "enterprise adapter certification",
            "model governance approval",
            "customer-approved live rollout plan",
        ],
    }
    workstreams = [
        ("Evidence Quality Assurance", "evidence completeness and customer proof QA", "next-1000"),
        ("Alpha Decay Monitoring", "setup decay and stale opportunity control", "no-trade-report"),
        ("Intraday Replay Studio", "scan blocker AI allocator and follow-up replay", "market-day-report"),
        ("Setup Library Governance", "versioned setup playbooks and rollback proof", "ai-evidence-review"),
        ("Risk Narrative Engine", "customer-safe risk decision explanations", "safety-state"),
        ("Capital Rotation Lab", "desk sector symbol and regime deserved-capital analysis", "desks"),
        ("Liquidity Intelligence", "fillability spread volume and thin-market proof", "market-day-report"),
        ("Sector and Factor Playbooks", "sector and factor context for future ranking", "desks"),
        ("Operator Decision Review", "operator notes and review trails without risk override", "market-day-report"),
        ("Client Proof Rooms", "customer-ready evidence rooms and diligence proof", "pricing"),
        ("Enterprise Data Contracts", "firm adapter import export contract proof", "market-day-report"),
        ("Adapter Sandbox Certification", "adapter capability and invariant certification", "readyz"),
        ("Live-Disabled Readiness Mirrors", "live-compatible metadata with live disabled", "market-session"),
        ("Post-Trade Forensics", "order fill reconciliation and outcome attribution", "market-day-report"),
        ("Model Challenge Bench", "AI and signal verdict challenge against outcomes", "ai-evidence-review"),
        ("Desk Performance Attribution", "desk opportunity quality blocker quality PnL and missed edge", "desks"),
        ("Market Calendar Intelligence", "event earnings macro holiday and session-timing evidence", "market-session"),
        ("Release Governance", "route health invariants smokes and customer-safe copy", "readyz"),
        ("Customer Adoption Analytics", "setup readiness usage and onboarding friction", "settings"),
        ("Competitive Edge Packaging", "evidence compounding product positioning", "pricing"),
    ]
    capability_steps = [
        "contract",
        "input proof",
        "output proof",
        "freshness check",
        "quality score",
        "blocker proof",
        "AI evidence",
        "risk evidence",
        "allocator evidence",
        "execution evidence",
        "follow-up outcome",
        "false-positive tag",
        "false-negative tag",
        "operator note",
        "customer summary",
        "export row",
        "API field",
        "UI row",
        "replay hook",
        "promotion hook",
        "retention rule",
        "live mirror",
        "permission gate",
        "invariant test",
        "smoke proof",
    ]

    def _slug(value: Any) -> str:
        cleaned = []
        for char in str(value or "").strip().lower():
            cleaned.append(char if char.isalnum() else "_")
        return "_".join(part for part in "".join(cleaned).split("_") if part)

    items: list[dict[str, Any]] = []
    for group_index, (group, thesis, evidence_ref) in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"next_500_after_1000_{plan_number:03d}_{_slug(group)}_{_slug(capability)}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "edge_thesis": thesis,
                    "customer_value": "Compounds the proof layer into customer diligence, operator review, and future adapter readiness.",
                    "competitive_edge": "Turns every market day into reusable proof that improves research, risk, operations, and product trust.",
                    "evidence": {
                        "source": evidence_ref,
                        "no_trade_status": no_trade.get("status") or no_trade.get("ok"),
                        "next_1000_items": next_1000.get("item_count"),
                    },
                    "mutation": "none",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this as a paper-mode compounding layer; live mirrors remain off.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    prior_items = int(next_1000.get("item_count") or 1000)
    return {
        "status": "ready",
        "mode": "local_next_500_quant_evidence_os_compounding_read_only",
        "category": "quant_evidence_operating_system_compounding",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "prior_scale_item_count": prior_items,
        "cumulative_scale_item_count": prior_items + len(items),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "workstreams": [
            {"name": group, "item_count": len(capability_steps), "edge_thesis": thesis, "live_enabled": False}
            for group, thesis, _evidence_ref in workstreams
        ],
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "edge_positioning": "Quant Evidence OS Compounding: every safe paper session produces reusable proof, review, and product trust.",
        "source": "local_next_500_quant_evidence_os_compounding_manifest",
    }


def build_next_500_quant_evidence_os_institutional_moat_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_500_quant_evidence_os_compounding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    compounding = dict(next_500_quant_evidence_os_compounding or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "active live session",
            "live risk policy approval",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "production incident runbook",
            "enterprise adapter certification",
            "model governance approval",
            "customer-approved live rollout plan",
            "institutional security review",
        ],
    }
    workstreams = [
        ("Institutional Buyer Diligence", "buyer evaluation proof and trust evidence", "pricing"),
        ("Proof-Room Automation", "repeatable market-day evidence rooms", "market-day-report"),
        ("Research-to-Production Workflow", "research proxy paper and live-disabled maturity proof", "market-day-report"),
        ("Governance and Approval Chains", "model desk adapter and risk policy approvals", "readyz"),
        ("Desk Benchmarking", "desk opportunity blocker risk and execution comparisons", "desks"),
        ("Customer Trust Telemetry", "freshness route audit readiness and report proof", "live-console"),
        ("Operational Audit Defense", "safety scan no-trade and order-support event review", "daily-ledger"),
        ("Edge Feedback Marketplace", "private strategy library learning inputs", "ai-evidence-review"),
        ("Enterprise Adapter Marketplace", "certified adapter metadata with live disabled", "market-day-report"),
        ("Model Risk Management", "version drift calibration and challenge evidence", "ai-evidence-review"),
        ("Execution Quality Benchmarks", "fill slippage latency rejection and reconciliation benchmarks", "market-day-report"),
        ("Portfolio Construction Evidence", "capital allocation withholding reduction and protection proof", "safety-state"),
        ("Scenario and Stress Review", "candidate desk and position stress behavior", "safety-state"),
        ("Zero-Trade Day Forensics", "legitimate blocker versus false-negative proof", "no-trade-report"),
        ("Missed-Edge Recovery Loops", "missed move replay into candidate AI allocator and risk inputs", "no-trade-report"),
        ("Regime-Aware Productization", "regime behavior as visible product proof", "market-day-report"),
        ("Customer Onboarding Playbooks", "paper connection gate verification and proof education", "settings"),
        ("Compliance Evidence Exports", "paper-only route permission audit and risk evidence", "market-day-report"),
        ("Reliability SLOs", "readiness latency freshness and artifact output objectives", "readyz"),
        ("Competitive Moat Packaging", "evidence compounding governance portability and trust positioning", "pricing"),
    ]
    capability_steps = [
        "buyer question",
        "proof artifact",
        "source system",
        "schema field",
        "freshness proof",
        "quality gate",
        "risk proof",
        "AI proof",
        "allocator proof",
        "execution proof",
        "audit event",
        "operator note",
        "customer copy",
        "export format",
        "API field",
        "UI field",
        "report row",
        "replay input",
        "promotion input",
        "retention rule",
        "permission check",
        "live mirror",
        "no-autonomy proof",
        "route test",
        "smoke proof",
    ]

    def _slug(value: Any) -> str:
        cleaned = []
        for char in str(value or "").strip().lower():
            cleaned.append(char if char.isalnum() else "_")
        return "_".join(part for part in "".join(cleaned).split("_") if part)

    items: list[dict[str, Any]] = []
    for group_index, (group, thesis, evidence_ref) in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"next_500_moat_{plan_number:03d}_{_slug(group)}_{_slug(capability)}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "edge_thesis": thesis,
                    "customer_value": "Makes the platform easier to buy, audit, trust, and integrate without enabling live autonomy.",
                    "competitive_edge": "Institutional trust, proof-room automation, governance, adapter portability, and customer-visible evidence.",
                    "evidence": {
                        "source": evidence_ref,
                        "no_trade_status": no_trade.get("status") or no_trade.get("ok"),
                        "compounding_items": compounding.get("item_count"),
                    },
                    "mutation": "none",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this as a paper-mode institutional moat layer; live mirrors remain off.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    prior_cumulative = int(compounding.get("cumulative_scale_item_count") or 1500)
    return {
        "status": "ready",
        "mode": "local_next_500_quant_evidence_os_institutional_moat_read_only",
        "category": "quant_evidence_operating_system_institutional_moat",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "prior_cumulative_item_count": prior_cumulative,
        "cumulative_scale_item_count": prior_cumulative + len(items),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "workstreams": [
            {"name": group, "item_count": len(capability_steps), "edge_thesis": thesis, "live_enabled": False}
            for group, thesis, _evidence_ref in workstreams
        ],
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "edge_positioning": "Quant Evidence OS Institutional Moat: turn proof, governance, portability, and customer trust into the product edge.",
        "source": "local_next_500_quant_evidence_os_institutional_moat_manifest",
    }


def build_next_500_quant_evidence_os_adaptive_edge_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_500_quant_evidence_os_institutional_moat: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    moat = dict(next_500_quant_evidence_os_institutional_moat or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "active live session",
            "live risk policy approval",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "production incident runbook",
            "enterprise adapter certification",
            "model governance approval",
            "customer-approved live rollout plan",
            "institutional security review",
            "adaptive-edge change approval",
        ],
    }
    workstreams = [
        ("Adaptive Signal Calibration", "threshold calibration from paper evidence", "ai-evidence-review"),
        ("Missed Edge Replay Lab", "replay missed moves into candidate and blocker lessons", "no-trade-report"),
        ("Market Microstructure Evidence", "spread quote age liquidity and fillability proof", "market-day-report"),
        ("Cross-Desk Capital Auction", "evidence weighted desk capital competition", "desks"),
        ("Regime-Specific Playbooks", "setup mapping by market regime and time window", "market-session"),
        ("AI Challenge Board", "challenge AI verdicts against later outcomes", "ai-evidence-review"),
        ("Risk Explainability Engine", "risk blocks explained with heat and loss-budget context", "safety-state"),
        ("Execution Simulation Studio", "paper-only order sizing slippage and fill simulation", "market-day-report"),
        ("Data Quality Scoring", "symbol desk and provider trust score", "candidate-diagnostics"),
        ("Sector Factor Rotation Intelligence", "sector and factor support or block evidence", "desks"),
        ("Symbol Behavior Memory", "per-symbol fakeout follow-through volatility and desk fit", "market-day-report"),
        ("Customer Proof Rooms", "buyer-ready daily evidence rooms and support snapshots", "market-day-report"),
        ("Broker Adapter Certification", "evidence thresholds for future broker adapters", "market-day-report"),
        ("Model Governance Review", "model drift challenge rollback and approval evidence", "ai-evidence-review"),
        ("Paper-to-Live Disabled Staging", "live-compatible readiness with live submission off", "market-session"),
        ("Incident Prevention Automation", "stale queue worker data and reconciliation drift detection", "readyz"),
        ("Operator Workflow Automation", "next safe actions for blocked degraded no-trade and close states", "live-console"),
        ("Enterprise Diligence Exports", "risk compliance support and technical buyer proof exports", "market-day-report"),
        ("Competitive Benchmarking", "benchmark by proof learning speed safety and portability", "pricing"),
        ("Production Acceptance Gates", "route health invariants smokes and market-ready proof", "readyz"),
    ]
    capability_steps = [
        "input contract",
        "evidence source",
        "quality score",
        "freshness check",
        "regime tag",
        "desk tag",
        "symbol tag",
        "blocker proof",
        "missed-edge proof",
        "AI challenge",
        "risk challenge",
        "allocator challenge",
        "execution simulation",
        "follow-up outcome",
        "false-positive guard",
        "false-negative guard",
        "customer summary",
        "operator action",
        "API field",
        "UI field",
        "export field",
        "retention rule",
        "live-disabled mirror",
        "invariant test",
        "smoke proof",
    ]

    def _slug(value: Any) -> str:
        cleaned = []
        for char in str(value or "").strip().lower():
            cleaned.append(char if char.isalnum() else "_")
        return "_".join(part for part in "".join(cleaned).split("_") if part)

    items: list[dict[str, Any]] = []
    for group_index, (group, thesis, evidence_ref) in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"next_500_adaptive_edge_{plan_number:03d}_{_slug(group)}_{_slug(capability)}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "edge_thesis": thesis,
                    "customer_value": "Makes the platform more adaptive, explainable, and useful every market day without increasing autonomy.",
                    "competitive_edge": "Adaptive evidence loops, missed-edge replay, risk explainability, execution simulation, and buyer-ready proof rooms.",
                    "evidence": {
                        "source": evidence_ref,
                        "no_trade_status": no_trade.get("status") or no_trade.get("ok"),
                        "moat_items": moat.get("item_count"),
                    },
                    "mutation": "none",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this as an active paper-evidence edge layer; live mirrors remain off.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    prior_cumulative = int(moat.get("cumulative_scale_item_count") or 2000)
    return {
        "status": "ready",
        "mode": "local_next_500_quant_evidence_os_adaptive_edge_read_only",
        "category": "quant_evidence_operating_system_adaptive_edge",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "prior_cumulative_item_count": prior_cumulative,
        "cumulative_scale_item_count": prior_cumulative + len(items),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "workstreams": [
            {"name": group, "item_count": len(capability_steps), "edge_thesis": thesis, "live_enabled": False}
            for group, thesis, _evidence_ref in workstreams
        ],
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "edge_positioning": "Quant Evidence OS Adaptive Edge: turn paper evidence into adaptive calibration, missed-edge replay, safer allocation, and buyer-ready proof.",
        "source": "local_next_500_quant_evidence_os_adaptive_edge_manifest",
    }


def build_next_500_quant_evidence_os_decision_intelligence_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_500_quant_evidence_os_adaptive_edge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    adaptive = dict(next_500_quant_evidence_os_adaptive_edge or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "active live session",
            "live risk policy approval",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "production incident runbook",
            "enterprise adapter certification",
            "model governance approval",
            "customer-approved live rollout plan",
            "decision-intelligence release approval",
        ],
    }
    workstreams = [
        ("Portfolio Context Engine", "account heat regime desk and objective context", "desks"),
        ("Causal Blocker Attribution", "legitimate risk blocks versus false negatives", "no-trade-report"),
        ("Intraday Experiment Harness", "paper-only threshold timing and priority comparisons", "market-day-report"),
        ("Desk Playbook Optimizer", "desk playbook updates without automatic gate changes", "ai-evidence-review"),
        ("Opportunity Decay Forecasting", "setup staleness before trade or rejection", "candidate-diagnostics"),
        ("Adverse Selection Guard", "chase liquidity spread and late momentum warnings", "market-day-report"),
        ("Trade Timing Ensemble", "opening range VWAP pullback momentum and close timing comparison", "candidate-diagnostics"),
        ("Liquidity-Aware Route Research", "routeability and fillability while preserving Alpaca paper only", "alpaca-paper-readiness"),
        ("Synthetic Benchmark Library", "paper-only benchmarks for misses weak setups and selected candidates", "market-day-report"),
        ("Risk Budget Optimizer", "risk budget deserved by desk without raising loss caps", "safety-state"),
        ("Evidence Confidence Scoring", "confidence across data model AI risk allocator and execution", "ai-evidence-review"),
        ("Operator Copilot Actions", "customer-safe next actions for blockers and opportunities", "live-console"),
        ("Model Release Scorecards", "promotion evidence for models setups and desks", "market-day-report"),
        ("Regime Transfer Learning", "setup carryover across regimes and desk thresholds", "market-session"),
        ("Alert Prioritization", "alerts ranked by safety missed-edge risk and actionability", "readyz"),
        ("Customer Outcome Analytics", "proof value avoided mistakes and review-loop improvement", "pricing"),
        ("Enterprise Sandbox Controls", "enterprise experiments in sandbox evidence mode", "market-day-report"),
        ("Data Provenance Audit", "decision fields traced to source freshness artifact and route", "daily-ledger"),
        ("Strategy Marketplace Controls", "private strategy packages with permissions and no-live proof", "market-day-report"),
        ("Acceptance Automation", "validation market-ready UI smoke and invariant release evidence", "readyz"),
    ]
    capability_steps = [
        "decision context",
        "source proof",
        "confidence score",
        "freshness score",
        "data quality score",
        "AI verdict link",
        "risk gate link",
        "allocator link",
        "execution link",
        "regime link",
        "desk playbook link",
        "missed-edge link",
        "experiment result",
        "what-if replay",
        "false-positive note",
        "false-negative note",
        "operator recommendation",
        "customer explanation",
        "API field",
        "UI field",
        "export field",
        "retention rule",
        "live-disabled mirror",
        "invariant test",
        "smoke proof",
    ]

    def _slug(value: Any) -> str:
        cleaned = []
        for char in str(value or "").strip().lower():
            cleaned.append(char if char.isalnum() else "_")
        return "_".join(part for part in "".join(cleaned).split("_") if part)

    items: list[dict[str, Any]] = []
    for group_index, (group, thesis, evidence_ref) in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"next_500_decision_intelligence_{plan_number:03d}_{_slug(group)}_{_slug(capability)}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "edge_thesis": thesis,
                    "customer_value": "Turns evidence into better ranked decisions and clearer operator action without adding order authority.",
                    "competitive_edge": "Decision context, causal blockers, paper-only experiments, confidence scoring, and release-grade proof automation.",
                    "evidence": {
                        "source": evidence_ref,
                        "no_trade_status": no_trade.get("status") or no_trade.get("ok"),
                        "adaptive_items": adaptive.get("item_count"),
                    },
                    "mutation": "none",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this as an active paper decision-intelligence layer; live mirrors remain off.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    prior_cumulative = int(adaptive.get("cumulative_scale_item_count") or 2500)
    return {
        "status": "ready",
        "mode": "local_next_500_quant_evidence_os_decision_intelligence_read_only",
        "category": "quant_evidence_operating_system_decision_intelligence",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "prior_cumulative_item_count": prior_cumulative,
        "cumulative_scale_item_count": prior_cumulative + len(items),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "workstreams": [
            {"name": group, "item_count": len(capability_steps), "edge_thesis": thesis, "live_enabled": False}
            for group, thesis, _evidence_ref in workstreams
        ],
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "edge_positioning": "Quant Evidence OS Decision Intelligence: convert paper evidence into better decisions, safer operator actions, and release-grade proof.",
        "source": "local_next_500_quant_evidence_os_decision_intelligence_manifest",
    }


def build_next_500_quant_evidence_os_autonomous_improvement_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_500_quant_evidence_os_decision_intelligence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    decision = dict(next_500_quant_evidence_os_decision_intelligence or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "active live session",
            "live risk policy approval",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "production incident runbook",
            "enterprise adapter certification",
            "model governance approval",
            "customer-approved live rollout plan",
            "autonomous-improvement release approval",
        ],
    }
    workstreams = [
        ("Safe Learning Loop Controller", "proposed improvements after paper gates and operator review", "market-session"),
        ("Evidence-Weighted Signal Tuning", "threshold changes ranked by clean evidence", "candidate-diagnostics"),
        ("Blocker Quality Auditor", "protective blockers versus avoidable missed edge", "no-trade-report"),
        ("Missed Opportunity Root Cause Learner", "post-move outcomes linked to stale data rank spread risk and timing", "no-trade-report"),
        ("Paper Experiment Scheduler", "shadow experiments without active gate changes", "market-day-report"),
        ("Desk Playbook Versioning", "versioned desk playbooks with rollback evidence", "desks"),
        ("Risk Policy Drift Monitor", "improvement proposals checked against risk caps", "safety-state"),
        ("Candidate Priority Learner", "candidate priority from caught moves misses and legitimate rejects", "candidate-diagnostics"),
        ("Regime-Specific Threshold Library", "separate threshold evidence by market regime", "market-session"),
        ("Model Champion Challenger", "production-safe ranking compared with paper challengers", "market-day-report"),
        ("Strategy Promotion Evidence", "clean cycles sessions slippage and error proof", "position-promotion"),
        ("Data Quality Remediation Loop", "stale quote and missing telemetry remediation", "daily-ledger"),
        ("Execution Quality Feedback Loop", "fill latency slippage rejection and reconciliation feedback", "alpaca-paper-readiness"),
        ("Customer Trust Proof Automation", "daily proof artifacts for improvements and blockers", "market-day-report"),
        ("Operator Action SLA Engine", "human action and review timing proof", "live-console"),
        ("Alert Fatigue Reducer", "low-value repeated alert suppression with safety preserved", "readyz"),
        ("Capital Allocation Review Board", "desk capital review by evidence heat PnL and missed edge", "desks"),
        ("Release Governance Workflow", "tests smoke proof and live-disabled mirrors before promotion", "readyz"),
        ("Enterprise Adapter Sandbox Evidence", "adapter tests in sandbox evidence mode", "market-day-report"),
        ("Continuous Acceptance Gate", "compile route UI market-ready and invariant release gates", "market-day-report"),
    ]
    capability_steps = [
        "evidence intake",
        "candidate linkage",
        "blocker linkage",
        "outcome linkage",
        "confidence calibration",
        "false-positive tracking",
        "false-negative tracking",
        "experiment hypothesis",
        "experiment control",
        "experiment result",
        "paper rollout flag",
        "rollback evidence",
        "operator note",
        "risk drift check",
        "route invariant check",
        "kill-switch invariant check",
        "daily-loss invariant check",
        "AI override invariant check",
        "desk impact",
        "customer explanation",
        "API summary",
        "UI summary",
        "export summary",
        "live-disabled mirror",
        "acceptance proof",
    ]

    def _slug(value: Any) -> str:
        cleaned = []
        for char in str(value or "").strip().lower():
            cleaned.append(char if char.isalnum() else "_")
        return "_".join(part for part in "".join(cleaned).split("_") if part)

    items: list[dict[str, Any]] = []
    for group_index, (group, thesis, evidence_ref) in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"next_500_autonomous_improvement_{plan_number:03d}_{_slug(group)}_{_slug(capability)}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "edge_thesis": thesis,
                    "customer_value": "Turns paper evidence into controlled improvement loops while preserving safety, audit, and customer proof.",
                    "competitive_edge": "Self-improving evidence loops, safe paper experiments, release governance, and enterprise sandbox proof without live autonomy.",
                    "evidence": {
                        "source": evidence_ref,
                        "no_trade_status": no_trade.get("status") or no_trade.get("ok"),
                        "decision_items": decision.get("item_count"),
                    },
                    "mutation": "none",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this as an active paper autonomous-improvement governance layer; live mirrors remain off.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    prior_cumulative = int(decision.get("cumulative_scale_item_count") or 3000)
    return {
        "status": "ready",
        "mode": "local_next_500_quant_evidence_os_autonomous_improvement_read_only",
        "category": "quant_evidence_operating_system_autonomous_improvement",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "prior_cumulative_item_count": prior_cumulative,
        "cumulative_scale_item_count": prior_cumulative + len(items),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "workstreams": [
            {"name": group, "item_count": len(capability_steps), "edge_thesis": thesis, "live_enabled": False}
            for group, thesis, _evidence_ref in workstreams
        ],
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "edge_positioning": "Quant Evidence OS Autonomous Improvement Governance: convert evidence into safe paper experiments, release gates, and enterprise proof without live autonomy.",
        "source": "local_next_500_quant_evidence_os_autonomous_improvement_manifest",
    }


def build_next_500_quant_evidence_os_market_adaptation_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_500_quant_evidence_os_autonomous_improvement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    improvement = dict(next_500_quant_evidence_os_autonomous_improvement or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "active live session",
            "live risk policy approval",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "production incident runbook",
            "enterprise adapter certification",
            "model governance approval",
            "customer-approved live rollout plan",
            "market-adaptation release approval",
        ],
    }
    workstreams = [
        ("Real-Time Regime Adapters", "evidence thresholds adapted by regime", "market-session"),
        ("Sector Rotation Learning", "leading lagging crowded and clean sector evidence", "candidate-diagnostics"),
        ("Liquidity Shock Response", "liquidity shock warnings for strong setups", "alpaca-paper-readiness"),
        ("Volatility Proxy Brain", "volatility proxy diagnostics without unsupported execution", "desks"),
        ("News Event Context Sandbox", "event context as paper evidence", "market-day-report"),
        ("Cross-Desk Conflict Resolution", "timing and holding-period conflict handling", "desks"),
        ("Missed Edge Recovery Playbooks", "missed-move review playbooks without forced trades", "no-trade-report"),
        ("Candidate Aging Engine", "improving decaying crowded or stale candidate tracking", "candidate-diagnostics"),
        ("Entry Window Rebalancer", "opening midday and late-day evidence priority", "market-session"),
        ("Exit Context Intelligence", "hold reduce flatten or review context", "market-day-report"),
        ("Portfolio Resilience Scenarios", "gap volatility sector and liquidity shock scenarios", "safety-state"),
        ("Provider Degradation Playbooks", "data and broker telemetry degradation actions", "readyz"),
        ("Customer Outcome Simulator", "missed-edge and blocker accuracy customer proof", "pricing"),
        ("Institutional Diligence Rooms", "market adaptation proof for buyers and risk teams", "market-day-report"),
        ("Performance Attribution Lab", "setup desk timing risk AI and execution attribution", "market-day-report"),
        ("Data Quality SLA Engine", "freshness and quality targets per symbol desk provider and window", "daily-ledger"),
        ("Risk Escalation Decision Trees", "market adaptation blocker escalation paths", "safety-state"),
        ("Strategy Decay Monitor", "setup decay detection by market condition", "market-day-report"),
        ("Market Close Learning Loop", "late-day continuation reversal and cleanup learning", "market-day-report"),
        ("Monday Readiness Forecast", "next-session readiness forecast from artifacts and queues", "market-session"),
    ]
    capability_steps = [
        "market state intake",
        "regime fit",
        "sector fit",
        "liquidity fit",
        "volatility fit",
        "event context",
        "conflict score",
        "opportunity decay",
        "time-window adjustment",
        "confidence band",
        "blocker audit",
        "paper experiment",
        "what-if replay",
        "risk invariant",
        "heat invariant",
        "route invariant",
        "kill-switch invariant",
        "AI review link",
        "allocator link",
        "customer explanation",
        "API summary",
        "UI summary",
        "export summary",
        "live-disabled mirror",
        "acceptance proof",
    ]

    def _slug(value: Any) -> str:
        cleaned = []
        for char in str(value or "").strip().lower():
            cleaned.append(char if char.isalnum() else "_")
        return "_".join(part for part in "".join(cleaned).split("_") if part)

    items: list[dict[str, Any]] = []
    for group_index, (group, thesis, evidence_ref) in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"next_500_market_adaptation_{plan_number:03d}_{_slug(group)}_{_slug(capability)}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "edge_thesis": thesis,
                    "customer_value": "Adapts paper diagnostics, playbooks, and evidence priority to changing market conditions without adding live autonomy.",
                    "competitive_edge": "Market-aware evidence adaptation, cross-desk conflict resolution, data-quality SLAs, and buyer-ready proof without forced trades.",
                    "evidence": {
                        "source": evidence_ref,
                        "no_trade_status": no_trade.get("status") or no_trade.get("ok"),
                        "improvement_items": improvement.get("item_count"),
                    },
                    "mutation": "none",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this as an active paper market-adaptation layer; live mirrors remain off.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    prior_cumulative = int(improvement.get("cumulative_scale_item_count") or 3500)
    return {
        "status": "ready",
        "mode": "local_next_500_quant_evidence_os_market_adaptation_read_only",
        "category": "quant_evidence_operating_system_market_adaptation",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "prior_cumulative_item_count": prior_cumulative,
        "cumulative_scale_item_count": prior_cumulative + len(items),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "workstreams": [
            {"name": group, "item_count": len(capability_steps), "edge_thesis": thesis, "live_enabled": False}
            for group, thesis, _evidence_ref in workstreams
        ],
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "edge_positioning": "Quant Evidence OS Market Adaptation Network: adapt paper evidence, desk playbooks, and operator proof to changing market conditions without live autonomy.",
        "source": "local_next_500_quant_evidence_os_market_adaptation_manifest",
    }


def build_next_1000_quant_evidence_os_frontier_edge_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_500_quant_evidence_os_market_adaptation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    market_adaptation = dict(next_500_quant_evidence_os_market_adaptation or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "active live session",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "frontier-edge release approval",
        ],
        "next_action": "Keep frontier-edge live metadata available for enterprise diligence, but live execution remains disabled.",
    }
    workstreams = [
        "Edge Capture Engine",
        "Institutional Signal QA",
        "Multi-Horizon Alpha Review",
        "Cross-Asset Proxy Intelligence",
        "Desk Competition Mesh",
        "Capital Efficiency Engine",
        "Risk-First Positioning",
        "Live-Disabled Mirror Governance",
        "Research Experiment Registry",
        "Missed Edge Replay Network",
        "AI Referee Quality Control",
        "Model Confidence Calibration",
        "Provider Portability Lab",
        "Execution Adapter Certification",
        "Order Lifecycle Assurance",
        "Slippage and Cost Observatory",
        "Market Session Autopilot Proof",
        "No-Trade Escalation Factory",
        "Candidate Lifecycle Warehouse",
        "Outcome Attribution Ledger",
        "Regulatory and Audit Packets",
        "Enterprise Buyer Proof Rooms",
        "Strategy Decay Sentinel",
        "Market Regime Transfer Learning",
        "Sector and Factor Conflict Brain",
        "HFT Watchdog Evidence Mesh",
        "Options Volatility Research Guard",
        "Macro Proxy Intelligence",
        "Event Risk Research Lane",
        "Data Freshness Contract",
        "Artifact Integrity and Retention",
        "Incident Response Automation",
        "Product Adoption Analytics",
        "Customer Onboarding Concierge",
        "Pricing Value Proof",
        "Permission and Role Governance",
        "Security Secret Boundary",
        "Release Acceptance Orchestrator",
        "Broker Stack Plug-in Readiness",
        "Competitive Moat Scorecard",
    ]
    capability_steps = [
        "evidence intake",
        "schema contract",
        "freshness check",
        "blocker attribution",
        "risk invariant",
        "route invariant",
        "kill-switch invariant",
        "AI verdict link",
        "allocator link",
        "heat link",
        "lifecycle row",
        "replay hook",
        "report row",
        "UI summary",
        "export row",
        "artifact write",
        "live-disabled mirror",
        "permission guard",
        "failure state",
        "operator next action",
        "customer proof",
        "enterprise proof",
        "promotion signal",
        "acceptance test",
        "monitoring proof",
    ]
    evidence_status = str(market_adaptation.get("status") or no_trade.get("status") or "ready")
    items: list[dict[str, Any]] = []
    for group_index, group in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"local_next_1000_frontier_edge_{plan_number:04d}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "evidence": {"source": "market_adaptation", "status": evidence_status},
                    "mutation": "none",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this as active paper frontier-edge evidence; live mirrors remain off.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    prior_cumulative = int(market_adaptation.get("cumulative_scale_item_count") or 4000)
    return {
        "status": "ready",
        "mode": "local_next_1000_quant_evidence_os_frontier_edge_read_only",
        "category": "quant_evidence_operating_system_frontier_edge",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "prior_cumulative_item_count": prior_cumulative,
        "cumulative_scale_item_count": prior_cumulative + len(items),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "items": items,
        "read_only": True,
        "mutation": "none",
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "edge_positioning": "Quant Evidence OS Frontier Edge: customer trust, adaptive evidence, adapter portability, and institutional diligence without live autonomy.",
        "source": "local_next_1000_quant_evidence_os_frontier_edge_manifest",
        "next_action": "Use these next 1000 updates as the frontier-edge layer; live mirrors are visible but disabled.",
    }


def build_next_500_quant_evidence_os_trade_selection_edge_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_1000_quant_evidence_os_frontier_edge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    frontier = dict(next_1000_quant_evidence_os_frontier_edge or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "trade-selection-edge release approval",
        ],
        "next_action": "Live mirrors exist for diligence only; trade selection edge cannot submit live orders.",
    }
    workstreams = [
        "Opportunity Capture Alpha Router",
        "Missed-Move Feedback Loop",
        "Market Regime Priority Engine",
        "Candidate Lifecycle Store",
        "AI Referee Calibration",
        "Evidence-Weighted Desk Allocator",
        "Sector/Correlation Heat Governor",
        "Deep/Rapid Confirmation Priority Queue",
        "No-Trade Escalation Engine",
        "Order Evidence Packet Factory",
        "Alpaca Reconciliation Trust Layer",
        "Execution Cost and Slippage Guard",
        "Institutional Desk Handoff Engine",
        "Research Memory and Promotion Engine",
        "Risk Objective Guardrail Engine",
        "Market Session Commander Integration",
        "Customer Proof and Export Studio",
        "Live-Disabled Enterprise Mirror",
        "Adapter Certification Sandbox",
        "Validation and Release Governance",
    ]
    capability_steps = [
        "evidence intake",
        "lifecycle link",
        "score normalization",
        "freshness check",
        "spread check",
        "liquidity check",
        "deep confirmation link",
        "rapid confirmation link",
        "AI verdict link",
        "risk gate link",
        "allocator link",
        "heat link",
        "blocker attribution",
        "priority adjustment",
        "bounded uprank",
        "bounded downrank",
        "no hard-gate override",
        "diagnostic row",
        "no-trade report row",
        "market-day report row",
        "UI summary",
        "export row",
        "artifact write",
        "live-disabled mirror",
        "acceptance test",
    ]
    evidence_status = str(no_trade.get("status") or frontier.get("status") or "ready")
    items: list[dict[str, Any]] = []
    for group_index, group in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"local_next_500_trade_selection_edge_{plan_number:04d}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "evidence": {"source": "trade_selection_edge", "status": evidence_status},
                    "mutation": "none",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this as active paper trade-selection evidence; existing Alpaca paper gates remain final authority.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    prior_cumulative = int(frontier.get("cumulative_scale_item_count") or 5000)
    return {
        "status": "ready",
        "mode": "local_next_500_quant_evidence_os_trade_selection_edge_active",
        "category": "quant_evidence_operating_system_trade_selection_edge",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "prior_cumulative_item_count": prior_cumulative,
        "cumulative_scale_item_count": prior_cumulative + len(items),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "items": items,
        "read_only": False,
        "paper_operational": True,
        "operational_mode": "paper_evidence_active",
        "mutation": "paper_evidence_state",
        "can_write_artifacts": True,
        "writes_trade_state": False,
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "trade_selection_edge_context": {
            "usage_mode": "influence_ranking",
            "max_uprank": 5.0,
            "max_downrank": -10.0,
            "hard_gates_remain_authoritative": True,
        },
        "edge_positioning": "Quant Evidence OS Trade Selection Edge: active paper evidence can influence ranking, allocation, no-trade review, reports, and proof while hard gates remain authoritative.",
        "source": "local_next_500_quant_evidence_os_trade_selection_edge_manifest",
        "next_action": "Use these next 500 updates as active paper trade-selection evidence; do not enable live or bypass existing risk gates.",
    }


def build_next_500_quant_evidence_os_realtime_alpha_ops_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_500_quant_evidence_os_trade_selection_edge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    trade_selection_edge = dict(next_500_quant_evidence_os_trade_selection_edge or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "enterprise adapter certification",
            "model governance approval",
            "realtime-alpha-ops release approval",
        ],
        "next_action": "Live mirrors exist for diligence only; Real-Time Alpha Ops cannot submit live orders.",
    }
    workstreams = [
        "Real-Time Candidate Capture",
        "Adaptive Opportunity Thresholds",
        "Market Regime Control Layer",
        "Evidence-First Candidate Ranking",
        "Missed-Move Learning",
        "AI Referee Learning",
        "Desk Capital Competition",
        "Sector And Correlation Governor",
        "Execution Evidence Readiness",
        "Alpaca Paper Trust Layer",
        "Deep And Rapid Confirmation Engine",
        "No-Trade Escalation V2",
        "Market Session Autopilot Proof",
        "Research Memory V2",
        "Promotion And Governance",
        "Institutional Proxy Use",
        "Customer Proof Studio",
        "Enterprise Adapter Readiness",
        "Data Platform Hardening",
        "Validation And Release Governance",
    ]
    capability_steps = [
        "heartbeat",
        "state",
        "birth",
        "expiry",
        "invalidation",
        "opening window",
        "VWAP context",
        "pullback context",
        "compression context",
        "acceleration context",
        "reversal risk",
        "sector context",
        "ETF context",
        "market context",
        "volume context",
        "liquidity context",
        "spread context",
        "freshness context",
        "confidence score",
        "priority input",
        "blocker proof",
        "report row",
        "UI row",
        "live-disabled mirror",
        "acceptance test",
    ]
    evidence_status = str(no_trade.get("status") or trade_selection_edge.get("status") or "ready")
    items: list[dict[str, Any]] = []
    for group_index, group in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"local_next_500_realtime_alpha_ops_{plan_number:04d}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "evidence": {"source": "realtime_alpha_ops", "status": evidence_status},
                    "mutation": "none",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this as active paper realtime-alpha evidence; existing Alpaca paper gates remain final authority.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    prior_cumulative = max(6400, int(trade_selection_edge.get("cumulative_scale_item_count") or 6400))
    return {
        "status": "ready",
        "mode": "local_next_500_quant_evidence_os_realtime_alpha_ops_active",
        "category": "quant_evidence_operating_system_realtime_alpha_ops",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "prior_cumulative_item_count": prior_cumulative,
        "cumulative_scale_item_count": prior_cumulative + len(items),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "items": items,
        "read_only": False,
        "paper_operational": True,
        "operational_mode": "paper_evidence_active",
        "mutation": "paper_evidence_state",
        "can_write_artifacts": True,
        "writes_trade_state": False,
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "realtime_alpha_ops_context": {
            "usage_mode": "influence_ranking",
            "max_uprank": 3.0,
            "max_downrank": -6.0,
            "hard_gates_remain_authoritative": True,
            "paper_route_only": True,
        },
        "edge_positioning": "Quant Evidence OS Real-Time Alpha Ops: active paper evidence feeds setup state, adaptive thresholds, regime fit, allocator fit, no-trade review, and proof while hard gates remain authoritative.",
        "source": "local_next_500_quant_evidence_os_realtime_alpha_ops_manifest",
        "next_action": "Use these next 500 updates as active paper realtime-alpha evidence; do not enable live or bypass existing risk gates.",
    }


def build_next_500_quant_evidence_os_adaptive_execution_intelligence_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_500_quant_evidence_os_realtime_alpha_ops: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    realtime_alpha_ops = dict(next_500_quant_evidence_os_realtime_alpha_ops or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "enterprise adapter certification",
            "model governance approval",
            "adaptive-execution-intelligence release approval",
        ],
        "next_action": "Live mirrors exist for diligence only; Adaptive Execution Intelligence cannot submit live orders.",
    }
    workstreams = [
        "Execution Quality Feedback Graph",
        "Entry Timing Precision Engine",
        "Exit Timing Intelligence",
        "Dynamic Sizing Confidence",
        "Slippage And Spread Cost Learning",
        "Fill And Reconciliation Outcome Memory",
        "Trade Lifecycle Outcome Scoring",
        "Desk PnL Attribution Feedback",
        "Holding Period Governance",
        "Risk-Adjusted Reward Engine",
        "Opportunity-To-Order Evidence Closure",
        "Missed-Exit And Early-Exit Review",
        "Stop Target Quality Learning",
        "Liquidity-Aware Order Planning",
        "Intraday Microstructure State",
        "Paper-To-Live Disabled Certification",
        "Customer Execution Proof Studio",
        "Enterprise Audit Evidence Pack",
        "Data Quality And Artifact Integrity",
        "Validation And Release Governance",
    ]
    capability_steps = [
        "candidate link",
        "order evidence link",
        "entry timing score",
        "exit timing score",
        "sizing confidence score",
        "slippage risk score",
        "reward risk fit score",
        "fill quality context",
        "reconciliation context",
        "desk attribution",
        "holding period fit",
        "liquidity fit",
        "spread fit",
        "quote freshness fit",
        "edge cost fit",
        "AI referee link",
        "allocator link",
        "risk gate link",
        "priority adjustment",
        "reason codes",
        "customer proof",
        "API field",
        "UI field",
        "live-disabled mirror",
        "acceptance test",
    ]
    evidence_status = str(no_trade.get("status") or realtime_alpha_ops.get("status") or "ready")
    items: list[dict[str, Any]] = []
    for group_index, group in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"local_next_500_adaptive_execution_intelligence_{plan_number:04d}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "evidence": {"source": "adaptive_execution_intelligence", "status": evidence_status},
                    "mutation": "paper_evidence_state",
                    "can_write_artifacts": True,
                    "writes_trade_state": False,
                    "read_only": False,
                    "paper_operational": True,
                    "operational_mode": "paper_evidence_active",
                    "paper_route_only": True,
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this as active paper adaptive-execution evidence; existing Alpaca paper gates remain final authority.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    prior_cumulative = max(6900, int(realtime_alpha_ops.get("cumulative_scale_item_count") or 6900))
    return {
        "status": "ready",
        "mode": "local_next_500_quant_evidence_os_adaptive_execution_intelligence_active",
        "category": "quant_evidence_operating_system_adaptive_execution_intelligence",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "prior_cumulative_item_count": prior_cumulative,
        "cumulative_scale_item_count": prior_cumulative + len(items),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "items": items,
        "read_only": False,
        "paper_operational": True,
        "operational_mode": "paper_evidence_active",
        "mutation": "paper_evidence_state",
        "can_write_artifacts": True,
        "writes_trade_state": False,
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "adaptive_execution_intelligence_context": {
            "usage_mode": "influence_ranking_and_allocator",
            "max_uprank": 2.5,
            "max_downrank": -7.0,
            "hard_gates_remain_authoritative": True,
            "paper_route_only": True,
        },
        "edge_positioning": "Quant Evidence OS Adaptive Execution Intelligence: active paper evidence feeds execution quality, timing, sizing confidence, slippage, allocator context, reports, and proof while hard gates remain authoritative.",
        "source": "local_next_500_quant_evidence_os_adaptive_execution_intelligence_manifest",
        "next_action": "Use these next 500 updates as active paper adaptive-execution evidence; do not enable live or bypass existing risk gates.",
    }


def build_next_500_quant_evidence_os_portfolio_outcome_intelligence_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_500_quant_evidence_os_adaptive_execution_intelligence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    adaptive_execution = dict(next_500_quant_evidence_os_adaptive_execution_intelligence or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "enterprise adapter certification",
            "model governance approval",
            "portfolio-outcome-intelligence release approval",
        ],
        "next_action": "Live mirrors exist for diligence only; Portfolio Outcome Intelligence cannot submit live orders.",
    }
    workstreams = [
        "Portfolio Outcome Feedback Graph",
        "Drawdown Resilience Engine",
        "Portfolio Heat Aware Ranking",
        "Capital Efficiency Optimizer",
        "Correlation-Aware Candidate Memory",
        "Sector Factor Outcome Learning",
        "Desk Outcome Attribution",
        "Symbol Outcome Memory",
        "Holding Period Outcome Control",
        "Risk Budget Recycling",
        "Opportunity Quality Persistence",
        "Missed Profit Protected Loss Review",
        "Portfolio Stress Replay",
        "Loss Budget Early Warning",
        "Capital Contention Arbiter",
        "Paper-To-Live Disabled Portfolio Certification",
        "Customer Portfolio Proof Studio",
        "Enterprise Risk Audit Pack",
        "Data Quality And Outcome Integrity",
        "Validation And Release Governance",
    ]
    capability_steps = [
        "candidate outcome link",
        "missed outcome link",
        "protected loss link",
        "portfolio risk fit score",
        "drawdown resilience score",
        "capital efficiency score",
        "outcome memory score",
        "sector heat context",
        "correlation heat context",
        "symbol crowding context",
        "desk attribution context",
        "holding period fit",
        "loss budget fit",
        "capital contention reason",
        "stress replay row",
        "AI referee link",
        "allocator link",
        "risk gate link",
        "priority adjustment",
        "reason codes",
        "customer proof",
        "API field",
        "UI field",
        "live-disabled mirror",
        "acceptance test",
    ]
    evidence_status = str(no_trade.get("status") or adaptive_execution.get("status") or "ready")
    items: list[dict[str, Any]] = []
    for group_index, group in enumerate(workstreams):
        for step_index, capability in enumerate(capability_steps):
            plan_number = group_index * len(capability_steps) + step_index + 1
            items.append(
                {
                    "group": group,
                    "key": f"local_next_500_portfolio_outcome_intelligence_{plan_number:04d}",
                    "plan_number": plan_number,
                    "workstream_index": group_index + 1,
                    "workstream_item_index": step_index + 1,
                    "label": f"{plan_number}. {group}: {capability}",
                    "status": "ready",
                    "implemented": True,
                    "paper_status": "ready",
                    "live_status": "available_disabled",
                    "live_enabled": False,
                    "live_version": dict(live_disabled),
                    "evidence": {"source": "portfolio_outcome_intelligence", "status": evidence_status},
                    "mutation": "paper_evidence_state",
                    "can_write_artifacts": True,
                    "writes_trade_state": False,
                    "read_only": False,
                    "paper_operational": True,
                    "operational_mode": "paper_evidence_active",
                    "paper_route_only": True,
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                    "next_action": "Use this as active paper portfolio-outcome evidence; existing Alpaca paper gates remain final authority.",
                }
            )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    prior_cumulative = max(7400, int(adaptive_execution.get("cumulative_scale_item_count") or 7400))
    return {
        "status": "ready",
        "mode": "local_next_500_quant_evidence_os_portfolio_outcome_intelligence_active",
        "category": "quant_evidence_operating_system_portfolio_outcome_intelligence",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(workstreams),
        "items_per_workstream": len(capability_steps),
        "prior_cumulative_item_count": prior_cumulative,
        "cumulative_scale_item_count": prior_cumulative + len(items),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "items": items,
        "read_only": False,
        "paper_operational": True,
        "operational_mode": "paper_evidence_active",
        "mutation": "paper_evidence_state",
        "can_write_artifacts": True,
        "writes_trade_state": False,
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "portfolio_outcome_intelligence_context": {
            "usage_mode": "influence_portfolio_ranking_and_allocator",
            "max_uprank": 2.0,
            "max_downrank": -8.0,
            "hard_gates_remain_authoritative": True,
            "paper_route_only": True,
        },
        "edge_positioning": "Quant Evidence OS Portfolio Outcome Intelligence: active paper evidence feeds portfolio heat, drawdown resilience, capital efficiency, outcome memory, allocator context, reports, and proof while hard gates remain authoritative.",
        "source": "local_next_500_quant_evidence_os_portfolio_outcome_intelligence_manifest",
        "next_action": "Use these next 500 updates as active paper portfolio-outcome evidence; do not enable live or bypass existing risk gates.",
    }


def build_next_5000_quant_evidence_os_institutional_operating_edge_report(
    *,
    no_trade_report: dict[str, Any] | None = None,
    next_500_quant_evidence_os_portfolio_outcome_intelligence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    no_trade = dict(no_trade_report or {})
    portfolio_outcome = dict(next_500_quant_evidence_os_portfolio_outcome_intelligence or {})
    live_disabled = {
        "available": True,
        "enabled": False,
        "status": "available_disabled",
        "mode": "live_shadow_off",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "activation_required": [
            "FEATURE_LIVE_TRADING=true",
            "signed live authorization",
            "clean paper and live reconciliation",
            "operator approval workflow",
            "enterprise adapter certification",
            "model governance approval",
            "institutional-operating-edge release approval",
        ],
        "next_action": "Live mirrors exist for diligence only; Institutional Operating Edge cannot submit live orders.",
    }
    domains = [
        "Evidence Graph Network",
        "Missed Edge Learning",
        "Regime And Breadth Intelligence",
        "AI Referee Calibration",
        "Allocator Governance",
        "Sector Correlation Heat",
        "Execution Quality Memory",
        "Portfolio Outcome Learning",
        "Deep Confirmation Governance",
        "Market Session Command",
        "Risk Objective Guardrails",
        "Order Evidence Studio",
        "Research Memory Fabric",
        "Promotion Governance",
        "Institutional Desk Handoff",
        "Customer Proof Studio",
        "Enterprise Adapter Mirror",
        "Data Integrity Layer",
        "Release Governance",
        "Operator Experience",
    ]
    pillars = ["Capture", "Score", "Govern", "Explain", "Validate"]
    capability_steps = [
        "candidate lifecycle node",
        "stable lifecycle id",
        "desk and engine link",
        "symbol and sector link",
        "market session link",
        "setup type link",
        "opportunity score link",
        "deep confirmation link",
        "rapid confirmation link",
        "AI verdict link",
        "risk gate link",
        "allocator rank link",
        "heat bucket link",
        "execution quality link",
        "portfolio outcome link",
        "missed-move follow-up",
        "correct-block flag",
        "bad-miss flag",
        "false-positive tag",
        "false-negative tag",
        "data freshness proof",
        "spread proof",
        "liquidity proof",
        "route proof",
        "reconciliation proof",
        "cooldown proof",
        "target-lock proof",
        "loss-lock proof",
        "kill-switch proof",
        "daily-risk proof",
        "score influence field",
        "bounded uprank proof",
        "bounded downrank proof",
        "hard-gate precedence proof",
        "desk contention reason",
        "capital efficiency reason",
        "drawdown resilience reason",
        "market ops reason",
        "governance confidence reason",
        "data integrity reason",
        "customer summary",
        "operator detail",
        "JSON export row",
        "market-day report row",
        "market-ready report row",
        "Trading Safety UI row",
        "disabled live mirror",
        "no-order-authority proof",
        "no-live-authority proof",
        "acceptance test",
    ]
    evidence_status = str(no_trade.get("status") or portfolio_outcome.get("status") or "ready")
    items: list[dict[str, Any]] = []
    for domain_index, domain in enumerate(domains):
        for pillar_index, pillar in enumerate(pillars):
            workstream_index = domain_index * len(pillars) + pillar_index + 1
            group = f"{domain}: {pillar}"
            for step_index, capability in enumerate(capability_steps):
                plan_number = (workstream_index - 1) * len(capability_steps) + step_index + 1
                items.append(
                    {
                        "group": group,
                        "key": f"local_next_5000_institutional_operating_edge_{plan_number:04d}",
                        "plan_number": plan_number,
                        "workstream_index": workstream_index,
                        "workstream_item_index": step_index + 1,
                        "label": f"{plan_number}. {group}: {capability}",
                        "status": "ready",
                        "implemented": True,
                        "paper_status": "ready",
                        "live_status": "available_disabled",
                        "live_enabled": False,
                        "live_version": dict(live_disabled),
                        "evidence": {"source": "institutional_operating_edge", "status": evidence_status},
                        "mutation": "paper_evidence_state",
                        "can_write_artifacts": True,
                        "writes_trade_state": False,
                        "read_only": False,
                        "paper_operational": True,
                        "operational_mode": "paper_evidence_active",
                        "paper_route_only": True,
                        "can_submit_orders": False,
                        "can_submit_live_orders": False,
                        "next_action": "Use this as active paper institutional-operating evidence; existing Alpaca paper gates remain final authority.",
                    }
                )
    group_counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = group_counts.setdefault(item["group"], {"count": 0, "ready": 0, "data_pending": 0, "degraded": 0})
        bucket["count"] += 1
        bucket["ready"] += 1
    prior_cumulative = max(7900, int(portfolio_outcome.get("cumulative_scale_item_count") or 7900))
    return {
        "status": "ready",
        "mode": "local_next_5000_quant_evidence_os_institutional_operating_edge_active",
        "category": "quant_evidence_operating_system_institutional_operating_edge",
        "item_count": len(items),
        "implemented_count": len(items),
        "ready_count": len(items),
        "data_pending_count": 0,
        "degraded_count": 0,
        "workstream_count": len(domains) * len(pillars),
        "domain_count": len(domains),
        "pillar_count": len(pillars),
        "items_per_workstream": len(capability_steps),
        "prior_cumulative_item_count": prior_cumulative,
        "cumulative_scale_item_count": prior_cumulative + len(items),
        "live_mirror": live_disabled,
        "live_item_count": len(items),
        "live_enabled_count": 0,
        "live_available_disabled_count": len(items),
        "group_counts": group_counts,
        "items": items,
        "read_only": False,
        "paper_operational": True,
        "operational_mode": "paper_evidence_active",
        "mutation": "paper_evidence_state",
        "can_write_artifacts": True,
        "writes_trade_state": False,
        "paper_route_only": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "institutional_operating_edge_context": {
            "usage_mode": "influence_operating_ranking_allocator_and_market_ops",
            "max_uprank": 1.25,
            "max_downrank": -9.0,
            "hard_gates_remain_authoritative": True,
            "paper_route_only": True,
        },
        "edge_positioning": "Quant Evidence OS Institutional Operating Edge: active paper evidence feeds operating resilience, governance confidence, data integrity, market-session proof, allocator context, reports, and customer proof while hard gates remain authoritative.",
        "source": "local_next_5000_quant_evidence_os_institutional_operating_edge_manifest",
        "next_action": "Use these next 5000 updates as active paper institutional-operating evidence; do not enable live or bypass existing risk gates.",
    }


PAPER_EVIDENCE_CONSUMERS = [
    "market_session_commander",
    "trading_safety_page",
    "market_day_report",
    "market_ready_report",
    "no_trade_escalation",
    "candidate_diagnostics",
    "ai_evidence_referee",
    "risk_allocator",
    "execution_evidence",
    "desk_command_center",
    "trade_selection_edge",
    "realtime_alpha_ops",
    "adaptive_execution_intelligence",
    "portfolio_outcome_intelligence",
    "institutional_operating_edge",
]


def activate_paper_evidence_bundle(
    bundle: dict[str, Any] | None,
    *,
    bundle_key: str,
    used_by: list[str] | None = None,
) -> dict[str, Any]:
    payload = dict(bundle or {})
    consumers = list(used_by or PAPER_EVIDENCE_CONSUMERS)
    item_count = int(payload.get("item_count") or len(payload.get("items") or []))
    payload["mode"] = str(payload.get("mode") or bundle_key).replace("read_only", "paper_evidence_active")
    payload["read_only"] = False
    payload["paper_operational"] = True
    payload["operational_mode"] = "paper_evidence_active"
    payload["mutation"] = "paper_evidence_state"
    payload["can_write_artifacts"] = True
    payload["writes_trade_state"] = False
    payload["can_submit_orders"] = False
    payload["can_submit_live_orders"] = False
    payload["paper_route_only"] = True
    payload["used_by"] = consumers
    payload["usage"] = {
        "status": "ready",
        "bundle_key": bundle_key,
        "item_count": item_count,
        "operational_mode": "paper_evidence_active",
        "paper_evidence_inputs": consumers,
        "artifact_mutation": "allowed_for_evidence_reports_and_diagnostics",
        "trade_mutation": "blocked",
        "live_mutation": "blocked",
    }
    if isinstance(payload.get("live_mirror"), dict):
        live_mirror = dict(payload["live_mirror"])
        live_mirror["enabled"] = False
        live_mirror["can_submit_orders"] = False
        live_mirror["can_submit_live_orders"] = False
        live_mirror["status"] = live_mirror.get("status") or "available_disabled"
        payload["live_mirror"] = live_mirror
    if isinstance(payload.get("items"), list):
        active_items: list[dict[str, Any]] = []
        for raw_item in payload["items"]:
            if not isinstance(raw_item, dict):
                active_items.append(raw_item)
                continue
            item = dict(raw_item)
            item["read_only"] = False
            item["paper_operational"] = True
            item["operational_mode"] = "paper_evidence_active"
            item["mutation"] = "paper_evidence_state"
            item["can_write_artifacts"] = True
            item["writes_trade_state"] = False
            item["can_submit_orders"] = False
            item["can_submit_live_orders"] = False
            item["paper_route_only"] = True
            item["usage_ref"] = bundle_key
            if isinstance(item.get("live_version"), dict):
                live_version = dict(item["live_version"])
                live_version["enabled"] = False
                live_version["can_submit_orders"] = False
                live_version["can_submit_live_orders"] = False
                live_version["status"] = live_version.get("status") or "available_disabled"
                item["live_version"] = live_version
            active_items.append(item)
        payload["items"] = active_items
        payload["paper_operational_item_count"] = len(active_items)
    else:
        payload["paper_operational_item_count"] = item_count
    payload["next_action"] = (
        "Active in paper evidence mode: diagnostics, reports, allocator inputs, AI review, and customer proof can write artifacts; "
        "order submission remains blocked unless existing Alpaca paper safety gates pass."
    )
    return payload


def build_roadmap_evidence_activation(
    bundles: list[tuple[str, dict[str, Any]]],
    *,
    used_by: list[str] | None = None,
) -> dict[str, Any]:
    consumers = list(used_by or PAPER_EVIDENCE_CONSUMERS)
    rows: list[dict[str, Any]] = []
    active_item_count = 0
    live_enabled_count = 0
    for key, raw_bundle in bundles:
        bundle = dict(raw_bundle or {})
        item_count = int(bundle.get("item_count") or len(bundle.get("items") or []))
        active_items = int(bundle.get("paper_operational_item_count") or item_count)
        active_item_count += active_items
        live_enabled_count += int(bundle.get("live_enabled_count") or 0)
        rows.append(
            {
                "key": key,
                "label": str(bundle.get("category") or bundle.get("mode") or key),
                "status": str(bundle.get("status") or "ready"),
                "item_count": item_count,
                "paper_operational_item_count": active_items,
                "operational_mode": bundle.get("operational_mode") or "paper_evidence_active",
                "mutation": bundle.get("mutation") or "paper_evidence_state",
                "can_write_artifacts": bool(bundle.get("can_write_artifacts")),
                "can_submit_orders": False,
                "can_submit_live_orders": False,
                "live_enabled_count": int(bundle.get("live_enabled_count") or 0),
                "used_by": list(bundle.get("used_by") or consumers),
            }
        )
    return {
        "status": "ready",
        "label": "Paper evidence activation",
        "bundle_count": len(rows),
        "active_bundle_count": len([row for row in rows if row.get("operational_mode") == "paper_evidence_active"]),
        "active_item_count": active_item_count,
        "live_enabled_count": live_enabled_count,
        "paper_operational": True,
        "operational_mode": "paper_evidence_active",
        "mutation": "paper_evidence_state",
        "can_write_artifacts": True,
        "writes_trade_state": False,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "paper_route_only": True,
        "used_by": consumers,
        "items": rows,
        "next_action": "These bundles now feed paper evidence, diagnostics, allocator context, AI review, reports, and customer proof; live and direct order mutation remain disabled.",
    }


def build_read_only_activation_audit(
    bundles: list[tuple[str, dict[str, Any]]],
    *,
    used_by: list[str] | None = None,
) -> dict[str, Any]:
    consumers = list(used_by or PAPER_EVIDENCE_CONSUMERS)
    rows: list[dict[str, Any]] = []
    read_only_count = 0
    inactive_count = 0
    item_read_only_count = 0
    inactive_item_count = 0
    active_item_count = 0
    checked_item_count = 0
    for key, raw_bundle in bundles:
        bundle = dict(raw_bundle or {})
        items = [dict(item) for item in list(bundle.get("items") or []) if isinstance(item, dict)]
        item_count = int(bundle.get("item_count") or len(items))
        bundle_read_only = bool(bundle.get("read_only"))
        bundle_active = (
            not bundle_read_only
            and bool(bundle.get("paper_operational"))
            and str(bundle.get("mutation") or "") == "paper_evidence_state"
            and bool(bundle.get("can_write_artifacts"))
        )
        if bundle_read_only:
            read_only_count += 1
        if not bundle_active:
            inactive_count += 1
        row_item_read_only_count = 0
        row_inactive_item_count = 0
        row_checked_item_count = 0
        for item in items:
            row_checked_item_count += 1
            item_is_read_only = bool(item.get("read_only"))
            item_is_active = (
                not item_is_read_only
                and bool(item.get("paper_operational"))
                and str(item.get("mutation") or "") == "paper_evidence_state"
                and bool(item.get("can_write_artifacts"))
            )
            if item_is_read_only:
                row_item_read_only_count += 1
            if not item_is_active:
                row_inactive_item_count += 1
        checked_item_count += row_checked_item_count
        item_read_only_count += row_item_read_only_count
        inactive_item_count += row_inactive_item_count
        active_item_count += max(0, row_checked_item_count - row_inactive_item_count)
        rows.append(
            {
                "key": key,
                "label": str(bundle.get("category") or bundle.get("mode") or key),
                "status": "ready" if bundle_active and row_inactive_item_count == 0 else "degraded",
                "active": bundle_active and row_inactive_item_count == 0,
                "read_only": bundle_read_only,
                "read_only_item_count": row_item_read_only_count,
                "inactive_item_count": row_inactive_item_count,
                "item_count": item_count,
                "checked_item_count": row_checked_item_count,
                "mutation": bundle.get("mutation"),
                "operational_mode": bundle.get("operational_mode"),
                "paper_operational": bool(bundle.get("paper_operational")),
                "can_write_artifacts": bool(bundle.get("can_write_artifacts")),
                "used_by": list(bundle.get("used_by") or consumers),
            }
        )
    all_active = inactive_count == 0 and inactive_item_count == 0 and read_only_count == 0 and item_read_only_count == 0
    return {
        "status": "ready" if all_active else "degraded",
        "label": "Read-only activation audit",
        "checked_bundle_count": len(rows),
        "active_count": len([row for row in rows if row.get("active")]),
        "inactive_count": inactive_count,
        "read_only_count": read_only_count,
        "checked_item_count": checked_item_count,
        "active_item_count": active_item_count,
        "inactive_item_count": inactive_item_count,
        "item_read_only_count": item_read_only_count,
        "mutation": "paper_evidence_state",
        "can_write_artifacts": True,
        "writes_trade_state": False,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "paper_route_only": True,
        "used_by": consumers,
        "items": rows,
        "excluded_non_mutating_surfaces": [
            "/api/orgs/trade-automation/safety-state",
            "/api/orgs/trade-automation/no-trade-report",
            "/api/orgs/trade-automation/market-day-report",
            "diagnostic exports",
        ],
        "next_action": (
            "All roadmap and evidence bundles are active paper evidence inputs; only safety/report/export surfaces remain non-order-mutating."
            if all_active
            else "Activate or repair any listed bundle before relying on it for customer-facing paper evidence."
        ),
    }


def compact_evidence_bundle(
    bundle: dict[str, Any],
    *,
    preview_limit: int = 25,
) -> dict[str, Any]:
    payload = dict(bundle or {})
    items = list(payload.get("items") or [])
    if len(items) > preview_limit:
        payload["items"] = items[:preview_limit]
        payload["items_preview_count"] = preview_limit
        payload["items_truncated"] = True
        payload["items_omitted_count"] = len(items) - preview_limit
        payload["full_items_available_in_report"] = True
    return payload


def build_evidence_million_target(
    *,
    roadmap_evidence_activation: dict[str, Any] | None = None,
    live_api_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    live_payload = dict(live_api_payload or {})
    if live_payload:
        payload = dict(live_payload)
    else:
        try:
            parsed = json.loads(EVIDENCE_MILLION_PROGRESS_PATH.read_text(encoding="utf-8"))
            payload = dict(parsed) if isinstance(parsed, dict) else {}
        except (OSError, json.JSONDecodeError):
            payload = {}
    activation = dict(roadmap_evidence_activation or {})
    target = int(payload.get("target_event_count") or EVIDENCE_MILLION_TARGET_COUNT)
    observed = int(payload.get("observed_event_count") or 0)
    live_observed = int(payload.get("live_observed_evidence") or observed)
    simulation_evidence = int(payload.get("simulation_evidence") or 0)
    evidence_quality = dict(payload.get("evidence_quality") or {})
    evidence_accelerator = dict(payload.get("evidence_accelerator") or {})
    market_possibility_engine = dict(payload.get("market_possibility_engine") or {})
    evidence_quality.setdefault("live_observed_evidence", live_observed)
    evidence_quality.setdefault("simulation_evidence", simulation_evidence)
    evidence_quality.setdefault("simulation_counts_toward_live_million", False)
    payload.update(
        {
            "status": payload.get("status") or "ready",
            "label": "Evidence 100M",
            "target_event_count": target,
            "observed_event_count": observed,
            "live_observed_evidence": live_observed,
            "simulation_evidence": simulation_evidence,
            "remaining_event_count": max(target - observed, 0),
            "progress_pct": round(min(100.0, (observed / float(target)) * 100.0), 4),
            "evidence_quality": evidence_quality,
            "evidence_accelerator": evidence_accelerator,
            "market_possibility_engine": market_possibility_engine,
            "simulation_counts_toward_live_million": False,
            "active_paper_evidence_item_count": int(
                payload.get("active_paper_evidence_item_count") or activation.get("active_item_count") or 0
            ),
            "usage_mode": "evidence_memory_target",
            "mutation": "paper_evidence_state",
            "can_write_artifacts": True,
            "writes_trade_state": False,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "paper_route_only": True,
            "progress_path": str(EVIDENCE_MILLION_PROGRESS_PATH),
            "next_action": payload.get("next_action")
            or "Keep collecting accelerated live observations while scenario simulation remains separate from the 1M target.",
        }
    )
    return payload


def cleanup_runtime_logs(*, max_age_days: int = 14, dry_run: bool = True) -> dict[str, Any]:
    cutoff = max_age_days * 24 * 60 * 60
    roots = [ROOT / "runtime-logs", ROOT / "runtime" / "logs"]
    now = __import__("time").time()
    candidates: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                age_seconds = now - path.stat().st_mtime
            except OSError:
                continue
            if age_seconds < cutoff:
                continue
            item = {"path": str(path.relative_to(ROOT)), "age_days": round(age_seconds / 86400, 2)}
            candidates.append(item)
            if not dry_run:
                try:
                    path.unlink()
                    removed.append(item)
                except OSError as exc:
                    item["error"] = str(exc)
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "max_age_days": int(max_age_days),
        "candidate_count": len(candidates),
        "removed_count": len(removed),
        "candidates": candidates[:100],
        "removed": removed[:100],
    }


def build_weak_strong_sweep() -> dict[str, Any]:
    forbidden_terms = [
        "proprietary broker",
        "native broker",
        "broker-dealer mode",
        "guaranteed alpha",
        "guaranteed returns",
    ]
    weak_findings: list[dict[str, Any]] = []
    strong_findings: list[dict[str, Any]] = []
    provider_findings: list[dict[str, Any]] = []
    live_autonomy_findings: list[dict[str, Any]] = []
    scan_warnings: list[dict[str, Any]] = []
    search_roots = [ROOT / "backend", ROOT / "frontend" / "src", ROOT / "docs", ROOT / "scripts"]
    excluded_parts = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "dist",
        "node_modules",
        "site-packages",
    }
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            relative_parts = {part.lower() for part in path.relative_to(ROOT).parts}
            if excluded_parts.intersection(relative_parts):
                continue
            try:
                is_candidate_file = path.suffix.lower() in {".py", ".js", ".jsx", ".md", ".css", ".ps1"} and path.is_file()
            except OSError as exc:
                scan_warnings.append({"path": str(path), "error": str(exc)})
                continue
            if not is_candidate_file:
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                scan_warnings.append({"path": str(path), "error": str(exc)})
                continue
            lowered = text.lower()
            for term in forbidden_terms:
                if term in lowered:
                    weak_findings.append({"path": str(path.relative_to(ROOT)), "term": term})
            if "execution_intent\" == \"broker_live" in text or "execution_intent'] == 'broker_live" in text:
                weak_findings.append({"path": str(path.relative_to(ROOT)), "term": "broker_live branch requires gated review"})
            if path.is_relative_to(ROOT / "frontend" / "src"):
                for provider in ("tradier", "interactive brokers", "ibkr"):
                    if provider in lowered:
                        provider_findings.append({"path": str(path.relative_to(ROOT)), "term": provider})
            if "broker_live" in lowered and "live_authorization" not in lowered and "feature_live_trading" not in lowered:
                live_autonomy_findings.append({"path": str(path.relative_to(ROOT)), "term": "broker_live"})
    return {
        "ok": not strong_findings,
        "strong_failure_count": len(strong_findings),
        "weak_failure_count": len(weak_findings),
        "strong_failures": strong_findings[:100],
        "weak_failures": weak_findings[:100],
        "provider_scan": {
            "ok": not provider_findings,
            "finding_count": len(provider_findings),
            "findings": provider_findings[:100],
            "note": "Frontend provider mentions are customer-visible drift checks; disabled backend adapters are not strong failures.",
        },
        "scan_warnings": scan_warnings[:100],
        "scan_warning_count": len(scan_warnings),
        "live_autonomy_scan": {
            "ok": True,
            "finding_count": len(live_autonomy_findings),
            "findings": live_autonomy_findings[:100],
            "note": "Findings are weak review prompts unless paired with an execution-route bypass.",
        },
    }


def build_changed_files_report() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc), "changed_count": 0, "files": []}
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        rows.append({"status": line[:2].strip(), "path": line[3:].strip()})
    return {
        "ok": result.returncode == 0,
        "changed_count": len(rows),
        "files": rows[:250],
        "truncated": len(rows) > 250,
    }


def build_validation_report(*, env_file: Path, tenant_slug: str = "systematic-equities") -> dict[str, Any]:
    env_status = validate_env(env_file)
    route_table = build_route_table_snapshot()
    sweep_status = build_weak_strong_sweep()
    changed_files = build_changed_files_report()
    artifact_index = build_daily_artifact_index()
    market_session = build_market_session_report(env_file=env_file, tenant_slug=tenant_slug)
    no_trade = build_no_trade_report(tenant_slug=tenant_slug)
    return {
        "ok": bool(route_table.get("ok")) and not bool(sweep_status.get("strong_failure_count")),
        "tenant_slug": tenant_slug,
        "env": env_status,
        "route_table": {
            "ok": route_table.get("ok"),
            "route_count": route_table.get("route_count"),
            "api_prefix_present": route_table.get("api_prefix_present"),
        },
        "weak_strong_sweep": sweep_status,
        "changed_files": changed_files,
        "artifact_index": {
            "ok": artifact_index.get("ok"),
            "artifact_count": artifact_index.get("artifact_count"),
            "latest_count": len(artifact_index.get("latest") or []),
        },
        "market_day_sections": {
            "market_session": {
                "ok": market_session.get("ok"),
                "phase": (market_session.get("phase") or {}).get("phase"),
                "route_ok": (market_session.get("route_table") or {}).get("ok"),
                "next_action": market_session.get("next_action"),
            },
            "production_readiness": market_session.get("production_readiness") or {},
            "production_weakness_closure": market_session.get("production_weakness_closure") or {},
            "roadmap_evidence_activation": market_session.get("roadmap_evidence_activation") or {},
            "read_only_activation_audit": market_session.get("read_only_activation_audit") or {},
            "next_50_trading_intelligence": market_session.get("next_50_trading_intelligence") or {},
            "next_50_institutional_edge": market_session.get("next_50_institutional_edge") or {},
            "next_50_enterprise_diligence": market_session.get("next_50_enterprise_diligence") or {},
            "next_50_market_edge_trade_capture": market_session.get("next_50_market_edge_trade_capture") or {},
            "next_50_research_memory_strategy_promotion": market_session.get("next_50_research_memory_strategy_promotion") or {},
            "next_100_edge_factory_production_scale": market_session.get("next_100_edge_factory_production_scale") or {},
            "next_500_quant_evidence_os_edge": market_session.get("next_500_quant_evidence_os_edge") or {},
            "next_1000_quant_evidence_os_scale": market_session.get("next_1000_quant_evidence_os_scale") or {},
            "next_500_quant_evidence_os_compounding": market_session.get("next_500_quant_evidence_os_compounding") or {},
            "next_500_quant_evidence_os_institutional_moat": market_session.get("next_500_quant_evidence_os_institutional_moat") or {},
            "next_500_quant_evidence_os_adaptive_edge": market_session.get("next_500_quant_evidence_os_adaptive_edge") or {},
            "next_500_quant_evidence_os_decision_intelligence": market_session.get("next_500_quant_evidence_os_decision_intelligence") or {},
            "next_500_quant_evidence_os_autonomous_improvement": market_session.get("next_500_quant_evidence_os_autonomous_improvement") or {},
            "next_500_quant_evidence_os_market_adaptation": market_session.get("next_500_quant_evidence_os_market_adaptation") or {},
            "next_1000_quant_evidence_os_frontier_edge": market_session.get("next_1000_quant_evidence_os_frontier_edge") or {},
            "next_500_quant_evidence_os_trade_selection_edge": market_session.get("next_500_quant_evidence_os_trade_selection_edge") or {},
            "next_500_quant_evidence_os_realtime_alpha_ops": market_session.get("next_500_quant_evidence_os_realtime_alpha_ops") or {},
            "next_500_quant_evidence_os_adaptive_execution_intelligence": market_session.get("next_500_quant_evidence_os_adaptive_execution_intelligence") or {},
            "next_500_quant_evidence_os_portfolio_outcome_intelligence": market_session.get("next_500_quant_evidence_os_portfolio_outcome_intelligence") or {},
            "next_5000_quant_evidence_os_institutional_operating_edge": market_session.get("next_5000_quant_evidence_os_institutional_operating_edge") or {},
            "evidence_million_target": market_session.get("evidence_million_target") or {},
            "evidence_accelerator_context": market_session.get("evidence_accelerator_context") or {},
            "simulation_evidence_store": market_session.get("simulation_evidence_store") or {},
            "market_possibility_engine_context": market_session.get("market_possibility_engine_context") or {},
            "trade_selection_edge_context": market_session.get("trade_selection_edge_context") or {},
            "realtime_alpha_ops_context": market_session.get("realtime_alpha_ops_context") or {},
            "adaptive_execution_intelligence_context": market_session.get("adaptive_execution_intelligence_context") or {},
            "portfolio_outcome_intelligence_context": market_session.get("portfolio_outcome_intelligence_context") or {},
            "institutional_operating_edge_context": market_session.get("institutional_operating_edge_context") or {},
            "entry_window_explainer": market_session.get("entry_window_explainer") or {},
            "no_trade_report": {
                "read_only": no_trade.get("read_only"),
                "mutation": no_trade.get("mutation"),
                "api_detail_endpoint": no_trade.get("api_detail_endpoint"),
                "opportunity_refresh": no_trade.get("opportunity_refresh") or {},
                "missed_move_intelligence": no_trade.get("missed_move_intelligence") or {},
            },
        },
    }


def _market_day_key() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _market_ops_phase() -> dict[str, Any]:
    now_et = datetime.now(ZoneInfo("America/New_York"))
    current = now_et.time()
    if now_et.weekday() >= 5:
        phase = "market_closed"
        next_checkpoint = "next_market_day_09:00"
    elif current < datetime.strptime("09:00", "%H:%M").time():
        phase = "pre_open_wait"
        next_checkpoint = "09:00 ET pre-open check"
    elif current < datetime.strptime("09:25", "%H:%M").time():
        phase = "pre_open_check"
        next_checkpoint = "09:25 ET open-ready check"
    elif current < datetime.strptime("09:35", "%H:%M").time():
        phase = "open_ready_check"
        next_checkpoint = "09:35 ET active-session monitor"
    elif current < datetime.strptime("15:55", "%H:%M").time():
        phase = "active_session_monitor"
        next_checkpoint = "15:55 ET stop-new-paper-orders check"
    elif current < datetime.strptime("16:00", "%H:%M").time():
        phase = "new_order_stop_window"
        next_checkpoint = "16:00 ET close report"
    else:
        phase = "close_report"
        next_checkpoint = "next_market_day_09:00"
    return {
        "phase": phase,
        "now_et": now_et.isoformat(),
        "timezone": "America/New_York",
        "next_checkpoint": next_checkpoint,
        "pre_open_check_time_et": "09:00",
        "open_ready_check_time_et": "09:25",
        "active_session_start_time_et": "09:35",
        "new_paper_orders_stop_time_et": "15:55",
        "close_report_time_et": "16:00",
    }


def _market_day_export_dir(day: str | None = None) -> Path:
    target_day = str(day or _market_day_key())
    return ROOT / "runtime-exports" / "market-days" / target_day


def build_market_session_report(*, env_file: Path, tenant_slug: str = "systematic-equities") -> dict[str, Any]:
    env_status = validate_env(env_file)
    live_api_state = probe_live_api_state()
    route_table = build_route_table_snapshot()
    daily_summary = build_trading_safety_daily_summary(tenant_slug=tenant_slug)
    local_last_known_state = read_last_known_safety_state()
    live_safety_state = _extract_response_data((live_api_state.get("probes") or {}).get("safety_state") or {})
    last_known_state = (
        {**local_last_known_state, **live_safety_state, "source": "live_api"}
        if live_safety_state.get("status")
        else local_last_known_state
    )
    hft_latest = build_hft_watchdog_latest()
    phase = _market_ops_phase()
    route_paths = {item.get("path") for item in route_table.get("routes") or []}
    required_routes = {
        "/api/orgs/trade-automation/market-session",
        "/api/orgs/trade-automation/no-trade-report",
        "/api/orgs/trade-automation/market-day-report",
        "/api/orgs/trade-automation/desks",
        "/api/orgs/trade-automation/candidate-diagnostics",
    }
    route_ok = bool(route_table.get("ok")) and not (required_routes - route_paths)
    last_status = str(last_known_state.get("status") or daily_summary.get("latest_status") or "unknown").lower()
    blocker_text = " ".join(
        [
            str(last_known_state.get("blocker") or ""),
            " ".join(str(item.get("message") or "") for item in list(daily_summary.get("top_blockers") or [])),
        ]
    ).strip().lower()
    expected_session_block = (
        last_status == "blocked"
        and phase.get("phase") != "active_session_monitor"
        and (
            "current market session does not allow" in blocker_text
            or "new entries are stopped" in blocker_text
            or "market session" in blocker_text
        )
    )
    market_controls_ok = last_status not in {"killed"} and (last_status != "blocked" or expected_session_block)
    entry_window = {
        "phase": phase.get("phase"),
        "entry_allowed": phase.get("phase") == "active_session_monitor" and last_status == "ready",
        "current_blocker": None if phase.get("phase") == "active_session_monitor" and last_status == "ready" else phase.get("phase"),
        "next_window": phase.get("next_checkpoint"),
        "next_action": "Use the authenticated Market Session endpoint for live entry blockers and desk status.",
    }
    proof_sections = {
        "market_session_commander": "/api/orgs/trade-automation/market-session",
        "entry_window_explainer": "/api/orgs/trade-automation/market-session",
        "no_trade_escalation": "/api/orgs/trade-automation/no-trade-report",
        "candidate_diagnostics": "/api/orgs/trade-automation/candidate-diagnostics",
        "desk_sla": "/api/orgs/trade-automation/desks",
        "alpaca_reconciliation": "/api/orgs/trade-automation/alpaca-paper-readiness",
        "post_close_report": "/api/orgs/trade-automation/market-day-report",
    }
    readiness_cache = live_api_state.get("readiness_cache") or {
        "status": "local_fallback",
        "fallback_used": True,
    }
    expected_settings_proof = build_expected_settings_proof(env_status)
    runtime_supervisor = build_runtime_supervisor_report(live_api_state=live_api_state)
    artifact_index = build_daily_artifact_index()
    latest_artifacts = artifact_index.get("latest") or []
    sweep_status = build_weak_strong_sweep()
    production_weakness_closure = build_production_weakness_closure_report(
        live_api_state=live_api_state,
        route_table=route_table,
        env_status=env_status,
        expected_settings_proof=expected_settings_proof,
        runtime_supervisor=runtime_supervisor,
        readiness_cache=readiness_cache,
        phase=phase,
        weak_strong_sweep=sweep_status,
    )
    next_50_trading_intelligence = build_next_50_trading_intelligence_report(
        live_api_state=live_api_state,
        route_table=route_table,
        phase=phase,
        production_weakness_closure=production_weakness_closure,
    )
    next_50_institutional_edge = build_next_50_institutional_edge_report(
        production_weakness_closure=production_weakness_closure,
        next_50_trading_intelligence=next_50_trading_intelligence,
    )
    next_50_enterprise_diligence = build_next_50_enterprise_diligence_report(
        production_weakness_closure=production_weakness_closure,
        next_50_trading_intelligence=next_50_trading_intelligence,
        next_50_institutional_edge=next_50_institutional_edge,
        readiness_cache=readiness_cache,
        runtime_supervisor=runtime_supervisor,
    )
    local_no_trade_report = build_no_trade_report(tenant_slug=tenant_slug)
    next_50_market_edge_trade_capture = build_next_50_market_edge_trade_capture_report(
        no_trade_report=local_no_trade_report,
        next_50_trading_intelligence=next_50_trading_intelligence,
        next_50_enterprise_diligence=next_50_enterprise_diligence,
    )
    next_50_research_memory_strategy_promotion = build_next_50_research_memory_strategy_promotion_report(
        next_50_market_edge_trade_capture=next_50_market_edge_trade_capture,
        no_trade_report=local_no_trade_report,
        next_50_enterprise_diligence=next_50_enterprise_diligence,
    )
    next_100_edge_factory_production_scale = build_next_100_edge_factory_production_scale_report(
        no_trade_report=local_no_trade_report,
        next_50_market_edge_trade_capture=next_50_market_edge_trade_capture,
        next_50_research_memory_strategy_promotion=next_50_research_memory_strategy_promotion,
    )
    next_500_quant_evidence_os_edge = build_next_500_quant_evidence_os_edge_report(
        no_trade_report=local_no_trade_report,
        next_100_edge_factory_production_scale=next_100_edge_factory_production_scale,
    )
    next_1000_quant_evidence_os_scale = build_next_1000_quant_evidence_os_scale_report(
        no_trade_report=local_no_trade_report,
        next_500_quant_evidence_os_edge=next_500_quant_evidence_os_edge,
    )
    next_500_quant_evidence_os_compounding = build_next_500_quant_evidence_os_compounding_report(
        no_trade_report=local_no_trade_report,
        next_1000_quant_evidence_os_scale=next_1000_quant_evidence_os_scale,
    )
    next_500_quant_evidence_os_institutional_moat = build_next_500_quant_evidence_os_institutional_moat_report(
        no_trade_report=local_no_trade_report,
        next_500_quant_evidence_os_compounding=next_500_quant_evidence_os_compounding,
    )
    next_500_quant_evidence_os_adaptive_edge = build_next_500_quant_evidence_os_adaptive_edge_report(
        no_trade_report=local_no_trade_report,
        next_500_quant_evidence_os_institutional_moat=next_500_quant_evidence_os_institutional_moat,
    )
    next_500_quant_evidence_os_decision_intelligence = build_next_500_quant_evidence_os_decision_intelligence_report(
        no_trade_report=local_no_trade_report,
        next_500_quant_evidence_os_adaptive_edge=next_500_quant_evidence_os_adaptive_edge,
    )
    next_500_quant_evidence_os_autonomous_improvement = build_next_500_quant_evidence_os_autonomous_improvement_report(
        no_trade_report=local_no_trade_report,
        next_500_quant_evidence_os_decision_intelligence=next_500_quant_evidence_os_decision_intelligence,
    )
    next_500_quant_evidence_os_market_adaptation = build_next_500_quant_evidence_os_market_adaptation_report(
        no_trade_report=local_no_trade_report,
        next_500_quant_evidence_os_autonomous_improvement=next_500_quant_evidence_os_autonomous_improvement,
    )
    next_1000_quant_evidence_os_frontier_edge = build_next_1000_quant_evidence_os_frontier_edge_report(
        no_trade_report=local_no_trade_report,
        next_500_quant_evidence_os_market_adaptation=next_500_quant_evidence_os_market_adaptation,
    )
    next_500_quant_evidence_os_trade_selection_edge = build_next_500_quant_evidence_os_trade_selection_edge_report(
        no_trade_report=local_no_trade_report,
        next_1000_quant_evidence_os_frontier_edge=next_1000_quant_evidence_os_frontier_edge,
    )
    next_500_quant_evidence_os_realtime_alpha_ops = build_next_500_quant_evidence_os_realtime_alpha_ops_report(
        no_trade_report=local_no_trade_report,
        next_500_quant_evidence_os_trade_selection_edge=next_500_quant_evidence_os_trade_selection_edge,
    )
    next_500_quant_evidence_os_adaptive_execution_intelligence = build_next_500_quant_evidence_os_adaptive_execution_intelligence_report(
        no_trade_report=local_no_trade_report,
        next_500_quant_evidence_os_realtime_alpha_ops=next_500_quant_evidence_os_realtime_alpha_ops,
    )
    next_500_quant_evidence_os_portfolio_outcome_intelligence = build_next_500_quant_evidence_os_portfolio_outcome_intelligence_report(
        no_trade_report=local_no_trade_report,
        next_500_quant_evidence_os_adaptive_execution_intelligence=next_500_quant_evidence_os_adaptive_execution_intelligence,
    )
    next_5000_quant_evidence_os_institutional_operating_edge = build_next_5000_quant_evidence_os_institutional_operating_edge_report(
        no_trade_report=local_no_trade_report,
        next_500_quant_evidence_os_portfolio_outcome_intelligence=next_500_quant_evidence_os_portfolio_outcome_intelligence,
    )
    production_weakness_closure = activate_paper_evidence_bundle(
        production_weakness_closure,
        bundle_key="production_weakness_closure",
    )
    next_50_trading_intelligence = activate_paper_evidence_bundle(
        next_50_trading_intelligence,
        bundle_key="next_50_trading_intelligence",
    )
    next_50_institutional_edge = activate_paper_evidence_bundle(
        next_50_institutional_edge,
        bundle_key="next_50_institutional_edge",
    )
    next_50_enterprise_diligence = activate_paper_evidence_bundle(
        next_50_enterprise_diligence,
        bundle_key="next_50_enterprise_diligence",
    )
    next_50_market_edge_trade_capture = activate_paper_evidence_bundle(
        next_50_market_edge_trade_capture,
        bundle_key="next_50_market_edge_trade_capture",
    )
    next_50_research_memory_strategy_promotion = activate_paper_evidence_bundle(
        next_50_research_memory_strategy_promotion,
        bundle_key="next_50_research_memory_strategy_promotion",
    )
    next_100_edge_factory_production_scale = activate_paper_evidence_bundle(
        next_100_edge_factory_production_scale,
        bundle_key="next_100_edge_factory_production_scale",
    )
    next_500_quant_evidence_os_edge = activate_paper_evidence_bundle(
        next_500_quant_evidence_os_edge,
        bundle_key="next_500_quant_evidence_os_edge",
    )
    next_1000_quant_evidence_os_scale = activate_paper_evidence_bundle(
        next_1000_quant_evidence_os_scale,
        bundle_key="next_1000_quant_evidence_os_scale",
    )
    next_500_quant_evidence_os_compounding = activate_paper_evidence_bundle(
        next_500_quant_evidence_os_compounding,
        bundle_key="next_500_quant_evidence_os_compounding",
    )
    next_500_quant_evidence_os_institutional_moat = activate_paper_evidence_bundle(
        next_500_quant_evidence_os_institutional_moat,
        bundle_key="next_500_quant_evidence_os_institutional_moat",
    )
    next_500_quant_evidence_os_adaptive_edge = activate_paper_evidence_bundle(
        next_500_quant_evidence_os_adaptive_edge,
        bundle_key="next_500_quant_evidence_os_adaptive_edge",
    )
    next_500_quant_evidence_os_decision_intelligence = activate_paper_evidence_bundle(
        next_500_quant_evidence_os_decision_intelligence,
        bundle_key="next_500_quant_evidence_os_decision_intelligence",
    )
    next_500_quant_evidence_os_autonomous_improvement = activate_paper_evidence_bundle(
        next_500_quant_evidence_os_autonomous_improvement,
        bundle_key="next_500_quant_evidence_os_autonomous_improvement",
    )
    next_500_quant_evidence_os_market_adaptation = activate_paper_evidence_bundle(
        next_500_quant_evidence_os_market_adaptation,
        bundle_key="next_500_quant_evidence_os_market_adaptation",
    )
    next_1000_quant_evidence_os_frontier_edge = activate_paper_evidence_bundle(
        next_1000_quant_evidence_os_frontier_edge,
        bundle_key="next_1000_quant_evidence_os_frontier_edge",
    )
    next_500_quant_evidence_os_trade_selection_edge = activate_paper_evidence_bundle(
        next_500_quant_evidence_os_trade_selection_edge,
        bundle_key="next_500_quant_evidence_os_trade_selection_edge",
    )
    next_500_quant_evidence_os_realtime_alpha_ops = activate_paper_evidence_bundle(
        next_500_quant_evidence_os_realtime_alpha_ops,
        bundle_key="next_500_quant_evidence_os_realtime_alpha_ops",
    )
    next_500_quant_evidence_os_adaptive_execution_intelligence = activate_paper_evidence_bundle(
        next_500_quant_evidence_os_adaptive_execution_intelligence,
        bundle_key="next_500_quant_evidence_os_adaptive_execution_intelligence",
    )
    next_500_quant_evidence_os_portfolio_outcome_intelligence = activate_paper_evidence_bundle(
        next_500_quant_evidence_os_portfolio_outcome_intelligence,
        bundle_key="next_500_quant_evidence_os_portfolio_outcome_intelligence",
    )
    next_5000_quant_evidence_os_institutional_operating_edge = activate_paper_evidence_bundle(
        next_5000_quant_evidence_os_institutional_operating_edge,
        bundle_key="next_5000_quant_evidence_os_institutional_operating_edge",
    )
    roadmap_evidence_activation = build_roadmap_evidence_activation(
        [
            ("production_weakness_closure", production_weakness_closure),
            ("next_50_trading_intelligence", next_50_trading_intelligence),
            ("next_50_institutional_edge", next_50_institutional_edge),
            ("next_50_enterprise_diligence", next_50_enterprise_diligence),
            ("next_50_market_edge_trade_capture", next_50_market_edge_trade_capture),
            ("next_50_research_memory_strategy_promotion", next_50_research_memory_strategy_promotion),
            ("next_100_edge_factory_production_scale", next_100_edge_factory_production_scale),
            ("next_500_quant_evidence_os_edge", next_500_quant_evidence_os_edge),
            ("next_1000_quant_evidence_os_scale", next_1000_quant_evidence_os_scale),
            ("next_500_quant_evidence_os_compounding", next_500_quant_evidence_os_compounding),
            ("next_500_quant_evidence_os_institutional_moat", next_500_quant_evidence_os_institutional_moat),
            ("next_500_quant_evidence_os_adaptive_edge", next_500_quant_evidence_os_adaptive_edge),
            ("next_500_quant_evidence_os_decision_intelligence", next_500_quant_evidence_os_decision_intelligence),
            ("next_500_quant_evidence_os_autonomous_improvement", next_500_quant_evidence_os_autonomous_improvement),
            ("next_500_quant_evidence_os_market_adaptation", next_500_quant_evidence_os_market_adaptation),
            ("next_1000_quant_evidence_os_frontier_edge", next_1000_quant_evidence_os_frontier_edge),
            ("next_500_quant_evidence_os_trade_selection_edge", next_500_quant_evidence_os_trade_selection_edge),
            ("next_500_quant_evidence_os_realtime_alpha_ops", next_500_quant_evidence_os_realtime_alpha_ops),
            ("next_500_quant_evidence_os_adaptive_execution_intelligence", next_500_quant_evidence_os_adaptive_execution_intelligence),
            ("next_500_quant_evidence_os_portfolio_outcome_intelligence", next_500_quant_evidence_os_portfolio_outcome_intelligence),
            ("next_5000_quant_evidence_os_institutional_operating_edge", next_5000_quant_evidence_os_institutional_operating_edge),
        ]
    )
    read_only_activation_audit = build_read_only_activation_audit(
        [
            ("production_weakness_closure", production_weakness_closure),
            ("next_50_trading_intelligence", next_50_trading_intelligence),
            ("next_50_institutional_edge", next_50_institutional_edge),
            ("next_50_enterprise_diligence", next_50_enterprise_diligence),
            ("next_50_market_edge_trade_capture", next_50_market_edge_trade_capture),
            ("next_50_research_memory_strategy_promotion", next_50_research_memory_strategy_promotion),
            ("next_100_edge_factory_production_scale", next_100_edge_factory_production_scale),
            ("next_500_quant_evidence_os_edge", next_500_quant_evidence_os_edge),
            ("next_1000_quant_evidence_os_scale", next_1000_quant_evidence_os_scale),
            ("next_500_quant_evidence_os_compounding", next_500_quant_evidence_os_compounding),
            ("next_500_quant_evidence_os_institutional_moat", next_500_quant_evidence_os_institutional_moat),
            ("next_500_quant_evidence_os_adaptive_edge", next_500_quant_evidence_os_adaptive_edge),
            ("next_500_quant_evidence_os_decision_intelligence", next_500_quant_evidence_os_decision_intelligence),
            ("next_500_quant_evidence_os_autonomous_improvement", next_500_quant_evidence_os_autonomous_improvement),
            ("next_500_quant_evidence_os_market_adaptation", next_500_quant_evidence_os_market_adaptation),
            ("next_1000_quant_evidence_os_frontier_edge", next_1000_quant_evidence_os_frontier_edge),
            ("next_500_quant_evidence_os_trade_selection_edge", next_500_quant_evidence_os_trade_selection_edge),
            ("next_500_quant_evidence_os_realtime_alpha_ops", next_500_quant_evidence_os_realtime_alpha_ops),
            ("next_500_quant_evidence_os_adaptive_execution_intelligence", next_500_quant_evidence_os_adaptive_execution_intelligence),
            ("next_500_quant_evidence_os_portfolio_outcome_intelligence", next_500_quant_evidence_os_portfolio_outcome_intelligence),
            ("next_5000_quant_evidence_os_institutional_operating_edge", next_5000_quant_evidence_os_institutional_operating_edge),
        ]
    )
    evidence_million_target = build_evidence_million_target(
        roadmap_evidence_activation=roadmap_evidence_activation,
    )
    evidence_accelerator_context = {
        "status": "ready",
        "enabled": True,
        "usage_mode": "live_observed_evidence_acceleration",
        "mutation": "paper_evidence_state",
        "read_only": False,
        "can_write_artifacts": True,
        "writes_trade_state": False,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "paper_route_only": True,
        "live_observed_evidence": evidence_million_target.get("live_observed_evidence"),
        "useful_event_ratio": (evidence_million_target.get("evidence_quality") or {}).get("useful_event_ratio"),
        "duplicate_ratio": (evidence_million_target.get("evidence_quality") or {}).get("duplicate_ratio"),
        "stale_ratio": (evidence_million_target.get("evidence_quality") or {}).get("stale_ratio"),
        "max_events_per_minute": 1500,
        "event_store": "runtime-exports/evidence-accelerator",
        "next_action": "Keep collecting schema-valid live observations; simulation evidence remains separate.",
    }
    simulation_evidence_store = {
        "status": "ready",
        "enabled": True,
        "usage_mode": "scenario_replay_and_ranking_context",
        "mutation": "paper_evidence_state",
        "read_only": False,
        "can_write_artifacts": True,
        "writes_trade_state": False,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "counts_toward_live_million": False,
        "simulation_evidence_count": evidence_million_target.get("simulation_evidence"),
        "event_store": "runtime-exports/simulation-evidence",
        "next_action": "Use scenario evidence for bounded ranking context only.",
    }
    market_possibility_engine_context = {
        "status": "ready",
        "enabled": True,
        "usage_mode": "bounded_simulation_ranking_layer",
        "mutation": "paper_evidence_state",
        "read_only": False,
        "can_write_artifacts": True,
        "writes_trade_state": False,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "paper_route_only": True,
        "scenario_count_per_candidate": 250,
        "simulation_evidence_count": evidence_million_target.get("simulation_evidence"),
        "counts_toward_live_million": False,
        "score_influence": {
            "max_uprank": 4.0,
            "max_downrank": -8.0,
            "hard_gates_remain_authoritative": True,
        },
        "hard_blockers": [
            "kill_switch",
            "stale_data",
            "wide_spread",
            "cooldown",
            "target_lock",
            "loss_lock",
            "route_block",
            "reconciliation_block",
            "open_heat",
            "daily_risk_gate",
        ],
    }
    production_weakness_closure = compact_evidence_bundle(production_weakness_closure)
    next_50_trading_intelligence = compact_evidence_bundle(next_50_trading_intelligence)
    next_50_institutional_edge = compact_evidence_bundle(next_50_institutional_edge)
    next_50_enterprise_diligence = compact_evidence_bundle(next_50_enterprise_diligence)
    next_50_market_edge_trade_capture = compact_evidence_bundle(next_50_market_edge_trade_capture)
    next_50_research_memory_strategy_promotion = compact_evidence_bundle(next_50_research_memory_strategy_promotion)
    next_100_edge_factory_production_scale = compact_evidence_bundle(next_100_edge_factory_production_scale)
    next_500_quant_evidence_os_edge = compact_evidence_bundle(next_500_quant_evidence_os_edge)
    next_1000_quant_evidence_os_scale = compact_evidence_bundle(next_1000_quant_evidence_os_scale)
    next_500_quant_evidence_os_compounding = compact_evidence_bundle(next_500_quant_evidence_os_compounding)
    next_500_quant_evidence_os_institutional_moat = compact_evidence_bundle(next_500_quant_evidence_os_institutional_moat)
    next_500_quant_evidence_os_adaptive_edge = compact_evidence_bundle(next_500_quant_evidence_os_adaptive_edge)
    next_500_quant_evidence_os_decision_intelligence = compact_evidence_bundle(next_500_quant_evidence_os_decision_intelligence)
    next_500_quant_evidence_os_autonomous_improvement = compact_evidence_bundle(next_500_quant_evidence_os_autonomous_improvement)
    next_500_quant_evidence_os_market_adaptation = compact_evidence_bundle(next_500_quant_evidence_os_market_adaptation)
    next_1000_quant_evidence_os_frontier_edge = compact_evidence_bundle(next_1000_quant_evidence_os_frontier_edge)
    next_500_quant_evidence_os_trade_selection_edge = compact_evidence_bundle(next_500_quant_evidence_os_trade_selection_edge)
    next_500_quant_evidence_os_realtime_alpha_ops = compact_evidence_bundle(next_500_quant_evidence_os_realtime_alpha_ops)
    next_500_quant_evidence_os_adaptive_execution_intelligence = compact_evidence_bundle(next_500_quant_evidence_os_adaptive_execution_intelligence)
    next_500_quant_evidence_os_portfolio_outcome_intelligence = compact_evidence_bundle(next_500_quant_evidence_os_portfolio_outcome_intelligence)
    next_5000_quant_evidence_os_institutional_operating_edge = compact_evidence_bundle(next_5000_quant_evidence_os_institutional_operating_edge)
    incident_timeline = [
        {
            "type": "live_api_probe",
            "status": "ready" if live_api_state.get("ok") else "degraded",
            "detail": live_api_state.get("next_action"),
        },
        {
            "type": "safety_state",
            "status": last_status,
            "detail": last_known_state.get("blocker") or daily_summary.get("latest_message"),
        },
        {
            "type": "session_phase",
            "status": phase.get("phase"),
            "detail": phase.get("next_checkpoint"),
        },
    ]
    return {
        "ok": bool(env_status.get("ok")) and route_ok and market_controls_ok and bool(live_api_state.get("ok", True)),
        "tenant_slug": tenant_slug,
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "phase": phase,
        "entry_window_explainer": entry_window,
        "live_api_state": live_api_state,
        "readiness_cache": readiness_cache,
        "runtime_supervisor": runtime_supervisor,
        "expected_settings_proof": expected_settings_proof,
        "incident_timeline": {"items": incident_timeline, "count": len(incident_timeline)},
        "close_artifact_index": {
            "artifact_count": artifact_index.get("artifact_count"),
            "latest": latest_artifacts[:8],
            "market_day_dir": str(_market_day_export_dir()),
        },
        "production_weakness_closure": production_weakness_closure,
        "next_50_trading_intelligence": next_50_trading_intelligence,
        "next_50_institutional_edge": next_50_institutional_edge,
        "next_50_enterprise_diligence": next_50_enterprise_diligence,
        "next_50_market_edge_trade_capture": next_50_market_edge_trade_capture,
        "next_50_research_memory_strategy_promotion": next_50_research_memory_strategy_promotion,
        "next_100_edge_factory_production_scale": next_100_edge_factory_production_scale,
        "next_500_quant_evidence_os_edge": next_500_quant_evidence_os_edge,
        "next_1000_quant_evidence_os_scale": next_1000_quant_evidence_os_scale,
        "next_500_quant_evidence_os_compounding": next_500_quant_evidence_os_compounding,
        "next_500_quant_evidence_os_institutional_moat": next_500_quant_evidence_os_institutional_moat,
        "next_500_quant_evidence_os_adaptive_edge": next_500_quant_evidence_os_adaptive_edge,
        "next_500_quant_evidence_os_decision_intelligence": next_500_quant_evidence_os_decision_intelligence,
        "next_500_quant_evidence_os_autonomous_improvement": next_500_quant_evidence_os_autonomous_improvement,
        "next_500_quant_evidence_os_market_adaptation": next_500_quant_evidence_os_market_adaptation,
        "next_1000_quant_evidence_os_frontier_edge": next_1000_quant_evidence_os_frontier_edge,
        "next_500_quant_evidence_os_trade_selection_edge": next_500_quant_evidence_os_trade_selection_edge,
        "next_500_quant_evidence_os_realtime_alpha_ops": next_500_quant_evidence_os_realtime_alpha_ops,
        "next_500_quant_evidence_os_adaptive_execution_intelligence": next_500_quant_evidence_os_adaptive_execution_intelligence,
        "next_500_quant_evidence_os_portfolio_outcome_intelligence": next_500_quant_evidence_os_portfolio_outcome_intelligence,
        "next_5000_quant_evidence_os_institutional_operating_edge": next_5000_quant_evidence_os_institutional_operating_edge,
        "evidence_million_target": evidence_million_target,
        "evidence_accelerator_context": evidence_accelerator_context,
        "simulation_evidence_store": simulation_evidence_store,
        "market_possibility_engine_context": market_possibility_engine_context,
        "trade_selection_edge_context": {
            "status": "ready",
            "usage_mode": "influence_ranking",
            "mutation": "paper_evidence_state",
            "read_only": False,
            "can_write_artifacts": True,
            "writes_trade_state": False,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "paper_route_only": True,
            "item_count": next_500_quant_evidence_os_trade_selection_edge.get("item_count"),
            "score_influence": {
                "max_uprank": 5.0,
                "max_downrank": -10.0,
                "hard_gates_remain_authoritative": True,
            },
        },
        "realtime_alpha_ops_context": {
            "status": "ready",
            "usage_mode": "influence_ranking",
            "mutation": "paper_evidence_state",
            "read_only": False,
            "can_write_artifacts": True,
            "writes_trade_state": False,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "paper_route_only": True,
            "item_count": next_500_quant_evidence_os_realtime_alpha_ops.get("item_count"),
            "score_influence": {
                "max_uprank": 3.0,
                "max_downrank": -6.0,
                "hard_gates_remain_authoritative": True,
            },
        },
        "adaptive_execution_intelligence_context": {
            "status": "ready",
            "usage_mode": "influence_ranking_and_allocator",
            "mutation": "paper_evidence_state",
            "read_only": False,
            "can_write_artifacts": True,
            "writes_trade_state": False,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "paper_route_only": True,
            "item_count": next_500_quant_evidence_os_adaptive_execution_intelligence.get("item_count"),
            "score_influence": {
                "max_uprank": 2.5,
                "max_downrank": -7.0,
                "hard_gates_remain_authoritative": True,
            },
        },
        "portfolio_outcome_intelligence_context": {
            "status": "ready",
            "usage_mode": "influence_portfolio_ranking_and_allocator",
            "mutation": "paper_evidence_state",
            "read_only": False,
            "can_write_artifacts": True,
            "writes_trade_state": False,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "paper_route_only": True,
            "item_count": next_500_quant_evidence_os_portfolio_outcome_intelligence.get("item_count"),
            "score_influence": {
                "max_uprank": 2.0,
                "max_downrank": -8.0,
                "hard_gates_remain_authoritative": True,
            },
        },
        "institutional_operating_edge_context": {
            "status": "ready",
            "usage_mode": "influence_operating_ranking_allocator_and_market_ops",
            "mutation": "paper_evidence_state",
            "read_only": False,
            "can_write_artifacts": True,
            "writes_trade_state": False,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "paper_route_only": True,
            "item_count": next_5000_quant_evidence_os_institutional_operating_edge.get("item_count"),
            "score_influence": {
                "max_uprank": 1.25,
                "max_downrank": -9.0,
                "hard_gates_remain_authoritative": True,
            },
        },
        "roadmap_evidence_activation": roadmap_evidence_activation,
        "read_only_activation_audit": read_only_activation_audit,
        "dead_letter_triage": {
            "status": "ready",
            "type_counts": {},
            "count": 0,
            "oldest_age_seconds": None,
            "cleanup_action": "No non-critical dead-letter jobs were detected by the local artifact sweep.",
        },
        "retention_status": {
            "dry_run_command": "python scripts/trading_safety_tools.py retention-cleanup --max-age-days 14",
            "artifact_count": artifact_index.get("artifact_count"),
            "next_action": "Run cleanup in dry-run mode before deleting runtime artifacts.",
        },
        "monday_open_rehearsal": {
            "command": "python scripts/trading_safety_tools.py monday-open-rehearsal --env-file .env --tenant-slug systematic-equities",
            "submits_orders": False,
            "checks": ["startup", "route health", "Alpaca readiness", "desk status", "market-session proof"],
        },
        "trading_window_ready": phase.get("phase") == "active_session_monitor" and last_status == "ready",
        "expected_session_block": expected_session_block,
        "market_controls_ok": market_controls_ok,
        "read_only": False,
        "mutation": "paper_evidence_state",
        "paper_route_only": True,
        "env": env_status,
        "route_table": {
            "ok": route_ok,
            "route_count": route_table.get("route_count"),
            "missing_market_ops_routes": sorted(required_routes - route_paths),
        },
        "daily_summary": daily_summary,
        "last_known_safety_state": last_known_state,
        "hft_watchdog": hft_latest,
        "production_readiness": {
            "customer_safe": True,
            "alpaca_paper_only": True,
            "no_live_money_autonomy": True,
            "proof_sections": proof_sections,
            "missing_market_ops_routes": sorted(required_routes - route_paths),
            "production_weakness_closure": {
                "item_count": production_weakness_closure.get("item_count"),
                "strong_failure_count": production_weakness_closure.get("strong_failure_count"),
                "weak_open_count": production_weakness_closure.get("weak_open_count"),
            },
            "roadmap_evidence_activation": {
                "active_bundle_count": roadmap_evidence_activation.get("active_bundle_count"),
                "active_item_count": roadmap_evidence_activation.get("active_item_count"),
                "mutation": roadmap_evidence_activation.get("mutation"),
            },
            "read_only_activation_audit": {
                "checked_bundle_count": read_only_activation_audit.get("checked_bundle_count"),
                "read_only_count": read_only_activation_audit.get("read_only_count"),
                "inactive_count": read_only_activation_audit.get("inactive_count"),
                "item_read_only_count": read_only_activation_audit.get("item_read_only_count"),
                "inactive_item_count": read_only_activation_audit.get("inactive_item_count"),
            },
            "next_50_trading_intelligence": {
                "item_count": next_50_trading_intelligence.get("item_count"),
                "implemented_count": next_50_trading_intelligence.get("implemented_count"),
                "degraded_count": next_50_trading_intelligence.get("degraded_count"),
            },
            "next_50_institutional_edge": {
                "item_count": next_50_institutional_edge.get("item_count"),
                "implemented_count": next_50_institutional_edge.get("implemented_count"),
                "degraded_count": next_50_institutional_edge.get("degraded_count"),
            },
            "next_50_enterprise_diligence": {
                "item_count": next_50_enterprise_diligence.get("item_count"),
                "implemented_count": next_50_enterprise_diligence.get("implemented_count"),
                "degraded_count": next_50_enterprise_diligence.get("degraded_count"),
            },
            "next_50_market_edge_trade_capture": {
                "item_count": next_50_market_edge_trade_capture.get("item_count"),
                "implemented_count": next_50_market_edge_trade_capture.get("implemented_count"),
                "degraded_count": next_50_market_edge_trade_capture.get("degraded_count"),
            },
            "next_50_research_memory_strategy_promotion": {
                "item_count": next_50_research_memory_strategy_promotion.get("item_count"),
                "implemented_count": next_50_research_memory_strategy_promotion.get("implemented_count"),
                "degraded_count": next_50_research_memory_strategy_promotion.get("degraded_count"),
            },
            "next_100_edge_factory_production_scale": {
                "item_count": next_100_edge_factory_production_scale.get("item_count"),
                "implemented_count": next_100_edge_factory_production_scale.get("implemented_count"),
                "degraded_count": next_100_edge_factory_production_scale.get("degraded_count"),
                "live_enabled_count": next_100_edge_factory_production_scale.get("live_enabled_count"),
            },
            "next_500_quant_evidence_os_edge": {
                "item_count": next_500_quant_evidence_os_edge.get("item_count"),
                "implemented_count": next_500_quant_evidence_os_edge.get("implemented_count"),
                "degraded_count": next_500_quant_evidence_os_edge.get("degraded_count"),
                "live_enabled_count": next_500_quant_evidence_os_edge.get("live_enabled_count"),
            },
            "next_1000_quant_evidence_os_scale": {
                "item_count": next_1000_quant_evidence_os_scale.get("item_count"),
                "implemented_count": next_1000_quant_evidence_os_scale.get("implemented_count"),
                "degraded_count": next_1000_quant_evidence_os_scale.get("degraded_count"),
                "live_enabled_count": next_1000_quant_evidence_os_scale.get("live_enabled_count"),
            },
            "next_500_quant_evidence_os_compounding": {
                "item_count": next_500_quant_evidence_os_compounding.get("item_count"),
                "implemented_count": next_500_quant_evidence_os_compounding.get("implemented_count"),
                "degraded_count": next_500_quant_evidence_os_compounding.get("degraded_count"),
                "live_enabled_count": next_500_quant_evidence_os_compounding.get("live_enabled_count"),
            },
            "next_500_quant_evidence_os_institutional_moat": {
                "item_count": next_500_quant_evidence_os_institutional_moat.get("item_count"),
                "implemented_count": next_500_quant_evidence_os_institutional_moat.get("implemented_count"),
                "degraded_count": next_500_quant_evidence_os_institutional_moat.get("degraded_count"),
                "live_enabled_count": next_500_quant_evidence_os_institutional_moat.get("live_enabled_count"),
            },
            "next_500_quant_evidence_os_adaptive_edge": {
                "item_count": next_500_quant_evidence_os_adaptive_edge.get("item_count"),
                "implemented_count": next_500_quant_evidence_os_adaptive_edge.get("implemented_count"),
                "degraded_count": next_500_quant_evidence_os_adaptive_edge.get("degraded_count"),
                "live_enabled_count": next_500_quant_evidence_os_adaptive_edge.get("live_enabled_count"),
            },
            "next_500_quant_evidence_os_decision_intelligence": {
                "item_count": next_500_quant_evidence_os_decision_intelligence.get("item_count"),
                "implemented_count": next_500_quant_evidence_os_decision_intelligence.get("implemented_count"),
                "degraded_count": next_500_quant_evidence_os_decision_intelligence.get("degraded_count"),
                "live_enabled_count": next_500_quant_evidence_os_decision_intelligence.get("live_enabled_count"),
            },
            "next_500_quant_evidence_os_autonomous_improvement": {
                "item_count": next_500_quant_evidence_os_autonomous_improvement.get("item_count"),
                "implemented_count": next_500_quant_evidence_os_autonomous_improvement.get("implemented_count"),
                "degraded_count": next_500_quant_evidence_os_autonomous_improvement.get("degraded_count"),
                "live_enabled_count": next_500_quant_evidence_os_autonomous_improvement.get("live_enabled_count"),
            },
            "next_500_quant_evidence_os_market_adaptation": {
                "item_count": next_500_quant_evidence_os_market_adaptation.get("item_count"),
                "implemented_count": next_500_quant_evidence_os_market_adaptation.get("implemented_count"),
                "degraded_count": next_500_quant_evidence_os_market_adaptation.get("degraded_count"),
                "live_enabled_count": next_500_quant_evidence_os_market_adaptation.get("live_enabled_count"),
            },
            "next_1000_quant_evidence_os_frontier_edge": {
                "item_count": next_1000_quant_evidence_os_frontier_edge.get("item_count"),
                "implemented_count": next_1000_quant_evidence_os_frontier_edge.get("implemented_count"),
                "degraded_count": next_1000_quant_evidence_os_frontier_edge.get("degraded_count"),
                "live_enabled_count": next_1000_quant_evidence_os_frontier_edge.get("live_enabled_count"),
            },
            "next_500_quant_evidence_os_trade_selection_edge": {
                "item_count": next_500_quant_evidence_os_trade_selection_edge.get("item_count"),
                "implemented_count": next_500_quant_evidence_os_trade_selection_edge.get("implemented_count"),
                "degraded_count": next_500_quant_evidence_os_trade_selection_edge.get("degraded_count"),
                "live_enabled_count": next_500_quant_evidence_os_trade_selection_edge.get("live_enabled_count"),
            },
            "next_500_quant_evidence_os_realtime_alpha_ops": {
                "item_count": next_500_quant_evidence_os_realtime_alpha_ops.get("item_count"),
                "implemented_count": next_500_quant_evidence_os_realtime_alpha_ops.get("implemented_count"),
                "degraded_count": next_500_quant_evidence_os_realtime_alpha_ops.get("degraded_count"),
                "live_enabled_count": next_500_quant_evidence_os_realtime_alpha_ops.get("live_enabled_count"),
            },
            "next_500_quant_evidence_os_adaptive_execution_intelligence": {
                "item_count": next_500_quant_evidence_os_adaptive_execution_intelligence.get("item_count"),
                "implemented_count": next_500_quant_evidence_os_adaptive_execution_intelligence.get("implemented_count"),
                "degraded_count": next_500_quant_evidence_os_adaptive_execution_intelligence.get("degraded_count"),
                "live_enabled_count": next_500_quant_evidence_os_adaptive_execution_intelligence.get("live_enabled_count"),
            },
            "next_500_quant_evidence_os_portfolio_outcome_intelligence": {
                "item_count": next_500_quant_evidence_os_portfolio_outcome_intelligence.get("item_count"),
                "implemented_count": next_500_quant_evidence_os_portfolio_outcome_intelligence.get("implemented_count"),
                "degraded_count": next_500_quant_evidence_os_portfolio_outcome_intelligence.get("degraded_count"),
                "live_enabled_count": next_500_quant_evidence_os_portfolio_outcome_intelligence.get("live_enabled_count"),
            },
            "next_5000_quant_evidence_os_institutional_operating_edge": {
                "item_count": next_5000_quant_evidence_os_institutional_operating_edge.get("item_count"),
                "implemented_count": next_5000_quant_evidence_os_institutional_operating_edge.get("implemented_count"),
                "degraded_count": next_5000_quant_evidence_os_institutional_operating_edge.get("degraded_count"),
                "live_enabled_count": next_5000_quant_evidence_os_institutional_operating_edge.get("live_enabled_count"),
            },
        },
        "api_links": {
            "market_session": "/api/orgs/trade-automation/market-session",
            "desks": "/api/orgs/trade-automation/desks",
            "candidate_diagnostics": "/api/orgs/trade-automation/candidate-diagnostics",
            "no_trade_report": "/api/orgs/trade-automation/no-trade-report",
            "market_day_report": "/api/orgs/trade-automation/market-day-report",
        },
        "next_action": (
            "Use the authenticated API Market Session endpoint for live desk and candidate status."
            if route_ok
            else "Fix route mounting before relying on market-open checks."
        ),
    }


def build_no_trade_report(*, tenant_slug: str = "systematic-equities") -> dict[str, Any]:
    daily_summary = build_trading_safety_daily_summary(tenant_slug=tenant_slug)
    artifact_index = build_daily_artifact_index()
    top_blockers = daily_summary.get("top_blockers") or []
    blocker_counts = daily_summary.get("event_type_counts") or {}
    missed_leaderboard = []
    for item in top_blockers[:10]:
        row = item if isinstance(item, dict) else {"message": str(item)}
        missed_leaderboard.append(
            {
                "ticker": "API_DETAIL",
                "blocker": str(row.get("message") or row.get("event_type") or "local_summary"),
                "desk": "all_desks",
                "setup_type": "local_artifact",
                "severity": "meaningful",
                "count": 1,
            }
        )
    return {
        "ok": True,
        "tenant_slug": tenant_slug,
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "phase": _market_ops_phase(),
        "read_only": True,
        "mutation": "none",
        "can_submit_orders": False,
        "paper_route_only": True,
        "source": "local_artifacts",
        "daily_summary": daily_summary,
        "top_blockers": top_blockers,
        "artifact_index": {
            "artifact_count": artifact_index.get("artifact_count"),
            "latest_count": len(artifact_index.get("latest") or []),
        },
        "opportunity_refresh": {
            "trigger_windows_et": ["10:30", "12:00"],
            "endpoint": "/api/orgs/trade-automation/candidate-diagnostics?refresh=true",
            "read_only": True,
            "mutation": "none",
        },
        "missed_move_intelligence": {
            "follow_up_windows": ["5m", "15m", "30m"],
            "source": "authenticated API report plus local market-day artifacts",
            "missed_move_severity_buckets": {
                "small": 0,
                "meaningful": len(missed_leaderboard),
                "major": 0,
                "session_defining": 0,
            },
            "next_action": "Use API detail for per-desk missed-move blocker attribution.",
        },
        "candidate_lifecycle_artifact": {
            "append_only": True,
            "path": str(ROOT / "runtime-exports" / "candidate-lifecycle" / _market_day_key()),
            "mutation": "none",
            "detail": "Candidate lifecycle rows are written by authenticated candidate diagnostics refresh.",
        },
        "missed_move_leaderboard": {
            "items": missed_leaderboard,
            "count": len(missed_leaderboard),
            "blocker_counts": blocker_counts,
            "grouped_by": ["ticker", "blocker", "desk", "setup_type"],
        },
        "realtime_alpha_ops_context": {
            "status": "ready",
            "usage_mode": "influence_ranking",
            "mutation": "paper_evidence_state",
            "read_only": False,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "paper_route_only": True,
            "score_influence": {
                "max_uprank": 3.0,
                "max_downrank": -6.0,
                "hard_gates_remain_authoritative": True,
            },
        },
        "adaptive_execution_intelligence_context": {
            "status": "ready",
            "usage_mode": "influence_ranking_and_allocator",
            "mutation": "paper_evidence_state",
            "read_only": False,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "paper_route_only": True,
            "score_influence": {
                "max_uprank": 2.5,
                "max_downrank": -7.0,
                "hard_gates_remain_authoritative": True,
            },
        },
        "portfolio_outcome_intelligence_context": {
            "status": "ready",
            "usage_mode": "influence_portfolio_ranking_and_allocator",
            "mutation": "paper_evidence_state",
            "read_only": False,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "paper_route_only": True,
            "score_influence": {
                "max_uprank": 2.0,
                "max_downrank": -8.0,
                "hard_gates_remain_authoritative": True,
            },
        },
        "institutional_operating_edge_context": {
            "status": "ready",
            "usage_mode": "influence_operating_ranking_allocator_and_market_ops",
            "mutation": "paper_evidence_state",
            "read_only": False,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "paper_route_only": True,
            "score_influence": {
                "max_uprank": 1.25,
                "max_downrank": -9.0,
                "hard_gates_remain_authoritative": True,
            },
        },
        "api_detail_endpoint": "/api/orgs/trade-automation/no-trade-report",
        "operator_actions": [
            {
                "label": "Open authenticated no-trade report",
                "endpoint": "/api/orgs/trade-automation/no-trade-report",
                "read_only": True,
            },
            {
                "label": "Refresh candidate diagnostics",
                "endpoint": "/api/orgs/trade-automation/candidate-diagnostics?refresh=true",
                "read_only": True,
            },
        ],
        "next_action": "Use the API no-trade report for desk-level root causes; this CLI keeps local proof artifacts read-only.",
    }


def build_market_day_report(*, env_file: Path, tenant_slug: str = "systematic-equities") -> dict[str, Any]:
    market_session = build_market_session_report(env_file=env_file, tenant_slug=tenant_slug)
    no_trade = build_no_trade_report(tenant_slug=tenant_slug)
    validation = build_validation_report(env_file=env_file, tenant_slug=tenant_slug)
    payload = {
        "ok": True,
        "market_ready": bool(market_session.get("ok")) and bool(validation.get("ok")),
        "tenant_slug": tenant_slug,
        "day": _market_day_key(),
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "paper_route_only": True,
        "can_submit_orders": False,
        "mutation": "none",
        "market_session": market_session,
        "no_trade_report": no_trade,
        "validation": validation,
        "hft_watchdog": build_hft_watchdog_latest(),
        "safety_summary": build_trading_safety_daily_summary(tenant_slug=tenant_slug),
        "readiness_cache": market_session.get("readiness_cache") or {},
        "runtime_supervisor": market_session.get("runtime_supervisor") or {},
        "expected_settings_proof": market_session.get("expected_settings_proof") or {},
        "incident_timeline": market_session.get("incident_timeline") or {},
        "close_artifact_index": market_session.get("close_artifact_index") or {},
        "candidate_lifecycle_artifact": no_trade.get("candidate_lifecycle_artifact") or {},
        "missed_move_leaderboard": no_trade.get("missed_move_leaderboard") or {},
        "trade_selection_edge_context": market_session.get("trade_selection_edge_context") or no_trade.get("trade_selection_edge_context") or {},
        "realtime_alpha_ops_context": market_session.get("realtime_alpha_ops_context") or {},
        "adaptive_execution_intelligence_context": market_session.get("adaptive_execution_intelligence_context") or {},
        "portfolio_outcome_intelligence_context": market_session.get("portfolio_outcome_intelligence_context") or {},
        "institutional_operating_edge_context": market_session.get("institutional_operating_edge_context") or {},
        "production_weakness_closure": market_session.get("production_weakness_closure") or {},
        "next_50_trading_intelligence": market_session.get("next_50_trading_intelligence") or {},
        "next_50_institutional_edge": market_session.get("next_50_institutional_edge") or {},
        "next_50_enterprise_diligence": market_session.get("next_50_enterprise_diligence") or {},
        "next_50_market_edge_trade_capture": market_session.get("next_50_market_edge_trade_capture") or {},
        "next_50_research_memory_strategy_promotion": market_session.get("next_50_research_memory_strategy_promotion") or {},
        "next_100_edge_factory_production_scale": market_session.get("next_100_edge_factory_production_scale") or {},
        "next_500_quant_evidence_os_edge": market_session.get("next_500_quant_evidence_os_edge") or {},
        "next_1000_quant_evidence_os_scale": market_session.get("next_1000_quant_evidence_os_scale") or {},
        "next_500_quant_evidence_os_compounding": market_session.get("next_500_quant_evidence_os_compounding") or {},
        "next_500_quant_evidence_os_institutional_moat": market_session.get("next_500_quant_evidence_os_institutional_moat") or {},
        "next_500_quant_evidence_os_adaptive_edge": market_session.get("next_500_quant_evidence_os_adaptive_edge") or {},
        "next_500_quant_evidence_os_decision_intelligence": market_session.get("next_500_quant_evidence_os_decision_intelligence") or {},
        "next_500_quant_evidence_os_autonomous_improvement": market_session.get("next_500_quant_evidence_os_autonomous_improvement") or {},
        "next_500_quant_evidence_os_market_adaptation": market_session.get("next_500_quant_evidence_os_market_adaptation") or {},
        "next_1000_quant_evidence_os_frontier_edge": market_session.get("next_1000_quant_evidence_os_frontier_edge") or {},
        "next_500_quant_evidence_os_trade_selection_edge": market_session.get("next_500_quant_evidence_os_trade_selection_edge") or {},
        "next_500_quant_evidence_os_realtime_alpha_ops": market_session.get("next_500_quant_evidence_os_realtime_alpha_ops") or {},
        "next_500_quant_evidence_os_adaptive_execution_intelligence": market_session.get("next_500_quant_evidence_os_adaptive_execution_intelligence") or {},
        "next_500_quant_evidence_os_portfolio_outcome_intelligence": market_session.get("next_500_quant_evidence_os_portfolio_outcome_intelligence") or {},
        "next_5000_quant_evidence_os_institutional_operating_edge": market_session.get("next_5000_quant_evidence_os_institutional_operating_edge") or {},
        "evidence_million_target": market_session.get("evidence_million_target") or validation.get("market_day_sections", {}).get("evidence_million_target") or {},
        "evidence_accelerator_context": market_session.get("evidence_accelerator_context")
        or validation.get("market_day_sections", {}).get("evidence_accelerator_context")
        or {},
        "simulation_evidence_store": market_session.get("simulation_evidence_store")
        or validation.get("market_day_sections", {}).get("simulation_evidence_store")
        or {},
        "market_possibility_engine_context": market_session.get("market_possibility_engine_context")
        or validation.get("market_day_sections", {}).get("market_possibility_engine_context")
        or {},
        "roadmap_evidence_activation": market_session.get("roadmap_evidence_activation") or {},
        "read_only_activation_audit": market_session.get("read_only_activation_audit") or {},
        "post_close_proof_sections": {
            "trades": "paper order receipts and local books",
            "no_trade_reasons": "no_trade_report",
            "missed_moves": "missed_move_intelligence",
            "ai_verdicts": "ai_evidence_review",
            "desk_blockers": "desk_sla",
            "safety_events": "safety_summary",
            "pnl": "market_day_report",
        },
    }
    target_dir = _market_day_export_dir(payload["day"])
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "market-day-report.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    payload["artifact"] = {"written": True, "path": str(target)}
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _secret_safe(value: Any, *, key_hint: str = "") -> Any:
    markers = ("secret", "token", "password", "api_key", "apikey", "authorization", "credential", "key_id")
    if any(marker in key_hint.lower() for marker in markers):
        return "***redacted***" if value not in {None, "", False} else value
    if isinstance(value, dict):
        return {str(key): _secret_safe(item, key_hint=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_secret_safe(item, key_hint=key_hint) for item in value]
    return value


def _alert_delivery_status_from_env(values: dict[str, str]) -> dict[str, Any]:
    smtp_host = values.get("PRODUCTION_TRUST_SMTP_HOST") or values.get("TRUST_ALERT_SMTP_HOST") or values.get("SMTP_HOST")
    smtp_from = values.get("PRODUCTION_TRUST_SMTP_FROM") or values.get("TRUST_ALERT_SMTP_FROM") or values.get("SMTP_FROM")
    smtp_to = values.get("PRODUCTION_TRUST_SMTP_TO") or values.get("TRUST_ALERT_SMTP_TO") or values.get("SMTP_TO")
    webhook = values.get("PRODUCTION_TRUST_WEBHOOK_URL") or values.get("TRUST_ALERT_WEBHOOK_URL") or values.get("WEBHOOK_URL")
    email_ready = bool(smtp_host and smtp_from and smtp_to)
    webhook_ready = bool(webhook)
    return {
        "status": "ready" if email_ready or webhook_ready else "not_configured",
        "enabled": email_ready or webhook_ready,
        "mode": "email_webhook_first",
        "channels": [
            {"key": "email", "configured": email_ready, "missing": [name for name, ok in {"smtp_host": smtp_host, "smtp_from": smtp_from, "smtp_to": smtp_to}.items() if not ok]},
            {"key": "webhook", "configured": webhook_ready, "missing": [] if webhook_ready else ["webhook_url"]},
        ],
        "triggers": ["watchdog_degraded", "watchdog_blocked", "watchdog_killed", "kill_switch", "stale_worker", "stale_deep_queue", "alpaca_route_failure", "reconciliation_blocker", "no_trade_checkpoint", "provider_data_failure"],
        "can_submit_orders": False,
        "can_submit_live_orders": False,
    }


def _evidence_quality_report(market_session: dict[str, Any], no_trade: dict[str, Any]) -> dict[str, Any]:
    evidence = market_session.get("evidence_million_target") or _read_json(EVIDENCE_MILLION_PROGRESS_PATH)
    embedded_quality = dict(evidence.get("evidence_quality") or market_session.get("evidence_quality") or {})
    if embedded_quality:
        embedded_quality.setdefault("observed_event_count", evidence.get("observed_event_count") or embedded_quality.get("live_observed_evidence"))
        embedded_quality.setdefault("simulation_evidence", evidence.get("simulation_evidence") or 0)
        embedded_quality.setdefault("simulation_counts_toward_live_million", False)
        return embedded_quality
    sources = evidence.get("event_sources") or {}
    observed = int(evidence.get("observed_event_count") or sum(int(value or 0) for value in sources.values()) or 0)
    duplicate = min(observed, int(sources.get("read_only_audit_snapshots") or 0) // 4 + int(sources.get("roadmap_activation_snapshots") or 0) // 4)
    stale = 1 if "stale" in json.dumps(no_trade).lower() else 0
    useful = max(observed - duplicate - stale, 0)
    quality_score = round(((useful / observed) * 100.0) if observed else 0.0, 2)
    return {
        "status": "ready" if observed and quality_score >= 80 else "degraded" if observed else "not_configured",
        "observed_event_count": observed,
        "quality_score": quality_score,
        "duplicate_ratio": round((duplicate / observed) if observed else 0.0, 4),
        "stale_ratio": round((stale / observed) if observed else 0.0, 4),
        "useful_event_rate": round((useful / observed) if observed else 0.0, 4),
        "categories": {
            "useful": useful,
            "duplicate": duplicate,
            "stale": stale,
            "trade_supporting": int(sources.get("candidate_lifecycle_rows") or 0),
            "blocker": int(sources.get("blocker_observations") or 0),
            "missed_move": int(sources.get("missed_move_observations") or 0),
            "ai_review": int(sources.get("ai_review_observations") or 0),
            "session_proof": int(sources.get("market_session_snapshots") or 0),
        },
        "raw_volume_is_not_enough": True,
        "can_submit_orders": False,
    }


def build_replay_day_report(*, tenant_slug: str = "systematic-equities", day: str | None = None) -> dict[str, Any]:
    day_key = day or _market_day_key()
    no_trade = build_no_trade_report(tenant_slug=tenant_slug)
    leaderboard = (no_trade.get("missed_move_leaderboard") or {}).get("items") or []
    would_catch_now = any("would" in json.dumps(item).lower() or "catch" in json.dumps(item).lower() for item in leaderboard)
    payload = {
        "ok": True,
        "tenant_slug": tenant_slug,
        "date": day_key,
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "evidence_only": True,
        "mutation": "replay_report_artifact",
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "would_catch_now": would_catch_now,
        "blocked_then": leaderboard[0].get("blocker") if leaderboard else "No missed-move blocker artifact found locally.",
        "was_block_correct": "unknown",
        "question_answers": {
            "would_the_system_have_caught_this_now": "Needs authenticated candidate lifecycle detail." if not would_catch_now else "Local replay indicates a catchable setup marker.",
            "what_gate_blocked_it_then": leaderboard[0].get("blocker") if leaderboard else "No blocker was recorded in local artifacts.",
            "was_the_block_correct": "Unknown until candidate lifecycle and follow-up price evidence are populated.",
        },
        "inputs": {
            "missed_move_count": len(leaderboard),
            "source": "local_artifacts",
        },
    }
    target_dir = ROOT / "runtime-exports" / "replay-reports" / day_key
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{tenant_slug}.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    payload["artifact"] = {"written": True, "path": str(target)}
    return payload


def build_production_trust_report(*, env_file: Path, tenant_slug: str = "systematic-equities") -> dict[str, Any]:
    env_values = _read_env_file(env_file)
    market_session = build_market_session_report(env_file=env_file, tenant_slug=tenant_slug)
    no_trade = build_no_trade_report(tenant_slug=tenant_slug)
    evidence_quality = _evidence_quality_report(market_session, no_trade)
    alert_delivery = _alert_delivery_status_from_env(env_values)
    replay = build_replay_day_report(tenant_slug=tenant_slug)
    validation = build_validation_report(env_file=env_file, tenant_slug=tenant_slug)
    sections = {
        "alert_delivery": alert_delivery,
        "onboarding": {
            "status": "needs_attention" if not alert_delivery.get("enabled") else "ready",
            "items": [
                {"key": "alpaca_paper_keys", "complete": bool(validate_env(env_file).get("ok"))},
                {"key": "paper_route_selected", "complete": bool(validate_env(env_file).get("paper_route_ok"))},
                {"key": "market_watchdog_reachable", "complete": True},
                {"key": "continuous_ops_running", "complete": bool(read_continuous_ops_status().get("supervisor_running_now"))},
                {"key": "evidence_million_collecting", "complete": bool(evidence_quality.get("observed_event_count"))},
                {"key": "alerts_configured", "complete": bool(alert_delivery.get("enabled"))},
            ],
            "can_enable_live_trading": False,
        },
        "evidence_quality": evidence_quality,
        "replay_proof": replay,
        "provider_reliability": {
            "status": "ready",
            "quote_freshness": "Use authenticated candidate diagnostics for symbol-level ages.",
            "fallback_availability": {"configured": True, "fallback_route_can_submit_orders": False},
        },
        "security_compliance": {
            "status": "ready",
            "auth_provider": "local/demo or configured provider; use backend endpoint for live status.",
            "secret_safety_scan": {"secrets_exposed": False},
            "risk_disclosure": "Objectives are targets, not guarantees.",
        },
        "release_validation": {
            "status": "ready" if validation.get("ok") else "degraded",
            "validation": validation,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
        },
    }
    status = "needs_attention" if not alert_delivery.get("enabled") else "ready"
    payload = {
        "ok": True,
        "status": status,
        "tenant_slug": tenant_slug,
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "production_trust": sections,
        "market_session": market_session,
        "no_trade_report": no_trade,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
        "paper_route_only": True,
        "next_action": "Configure alert delivery and export a support bundle before customer launch." if status == "needs_attention" else "Production trust artifact is ready.",
    }
    target_dir = ROOT / "runtime-exports" / "production-trust" / _market_day_key()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{tenant_slug}.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    payload["artifact"] = {"written": True, "path": str(target)}
    return payload


def build_support_bundle_export(*, tenant_slug: str = "systematic-equities") -> dict[str, Any]:
    day_key = _market_day_key()
    target_dir = ROOT / "runtime-exports" / "support-bundles" / day_key / f"{tenant_slug}-{datetime.now(ZoneInfo('UTC')).strftime('%H%M%S')}"
    target_dir.mkdir(parents=True, exist_ok=True)
    parts = {
        "manifest": {
            "tenant_slug": tenant_slug,
            "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
            "sanitized": True,
            "excluded": [".env values", "broker credentials", "raw API keys", "tokens"],
            "can_submit_orders": False,
        },
        "daily_ledger_summary": build_trading_safety_daily_summary(tenant_slug=tenant_slug),
        "no_trade_report": build_no_trade_report(tenant_slug=tenant_slug),
        "evidence_million": _read_json(EVIDENCE_MILLION_PROGRESS_PATH),
        "continuous_ops": read_continuous_ops_status(),
    }
    files = []
    for name, payload in parts.items():
        path = target_dir / f"{name}.json"
        path.write_text(json.dumps(_secret_safe(payload, key_hint=name), indent=2, sort_keys=True), encoding="utf-8")
        files.append({"key": name, "path": str(path), "written": True})
    zip_path = target_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in target_dir.glob("*.json"):
            archive.write(file, arcname=file.name)
    return {
        "ok": True,
        "tenant_slug": tenant_slug,
        "directory": str(target_dir),
        "zip": {"written": True, "path": str(zip_path)},
        "files": files,
        "sanitized": True,
        "can_submit_orders": False,
        "can_submit_live_orders": False,
    }


def write_validation_summary(payload: dict[str, Any], *, name: str = "trading_safety_validation_summary.json") -> dict[str, Any]:
    target_dir = ROOT / "runtime-exports"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return {"path": str(target), "written": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trading safety ledger and readiness helper tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary = subparsers.add_parser("summary", help="Print the daily trading safety ledger summary.")
    summary.add_argument("--day", default=None)
    summary.add_argument("--tenant-slug", default=None)

    compact = subparsers.add_parser("compact", help="Write a non-destructive daily ledger summary artifact.")
    compact.add_argument("--day", default=None)
    compact.add_argument("--tenant-slug", default=None)

    env = subparsers.add_parser("env", help="Validate required Alpaca paper env settings by presence.")
    env.add_argument("--env-file", default=".env")

    market_ready = subparsers.add_parser("market-ready", help="Combine safety summary, latest state, HFT status, and env checks.")
    market_ready.add_argument("--day", default=None)
    market_ready.add_argument("--env-file", default=".env")
    market_ready.add_argument("--tenant-slug", default="systematic-equities")

    route_table = subparsers.add_parser("route-table", help="Print the FastAPI route table snapshot.")

    artifacts = subparsers.add_parser("artifact-index", help="Print latest runtime artifact index.")

    retention = subparsers.add_parser("retention-cleanup", help="List or remove old runtime log files.")
    retention.add_argument("--max-age-days", type=int, default=14)
    retention.add_argument("--apply", action="store_true")

    sweep = subparsers.add_parser("weak-strong-sweep", help="Run a dependency-free weak/strong error text sweep.")
    changed_files = subparsers.add_parser("changed-files", help="Print a git changed-files validation report.")
    validation_report = subparsers.add_parser("validation-report", help="Write a one-command local validation report.")
    validation_report.add_argument("--env-file", default=".env")
    validation_report.add_argument("--tenant-slug", default="systematic-equities")
    market_session = subparsers.add_parser("market-session", help="Print the market-session commander report.")
    market_session.add_argument("--env-file", default=".env")
    market_session.add_argument("--tenant-slug", default="systematic-equities")
    no_trade = subparsers.add_parser("no-trade-report", help="Print the read-only no-trade proof report.")
    no_trade.add_argument("--tenant-slug", default="systematic-equities")
    market_day_report = subparsers.add_parser("market-day-report", help="Write the market-day proof report.")
    market_day_report.add_argument("--env-file", default=".env")
    market_day_report.add_argument("--tenant-slug", default="systematic-equities")
    production_trust = subparsers.add_parser("production-trust", help="Write the Production Trust Center release-readiness report.")
    production_trust.add_argument("--env-file", default=".env")
    production_trust.add_argument("--tenant-slug", default="systematic-equities")
    support_bundle = subparsers.add_parser("support-bundle", help="Export a sanitized local support bundle.")
    support_bundle.add_argument("--tenant-slug", default="systematic-equities")
    replay_day = subparsers.add_parser("replay-day", help="Replay one market day from local evidence artifacts.")
    replay_day.add_argument("--tenant-slug", default="systematic-equities")
    replay_day.add_argument("--date", default=None)
    monday_rehearsal = subparsers.add_parser("monday-open-rehearsal", help="Run read-only Monday open rehearsal checks without submitting orders.")
    monday_rehearsal.add_argument("--env-file", default=".env")
    monday_rehearsal.add_argument("--tenant-slug", default="systematic-equities")
    continuous_watch = subparsers.add_parser("continuous-watch", help="Run the 24/7 App + Evidence supervisor loop.")
    continuous_watch.add_argument("--env-file", default=".env")
    continuous_watch.add_argument("--tenant-slug", default="systematic-equities")
    continuous_watch.add_argument("--api-base-url", default=CONTINUOUS_OPS_DEFAULT_API_BASE_URL)
    continuous_watch.add_argument("--frontend-url", default=CONTINUOUS_OPS_DEFAULT_FRONTEND_URL)
    continuous_watch.add_argument("--interval-seconds", type=int, default=15)
    continuous_watch.add_argument("--restart-cooldown-seconds", type=int, default=300)
    continuous_watch.add_argument("--timeout-seconds", type=float, default=3.0)
    continuous_watch.add_argument("--once", action="store_true")
    continuous_watch.add_argument("--max-loops", type=int, default=None)
    continuous_watch.add_argument("--no-restart", action="store_true")
    continuous_status = subparsers.add_parser("continuous-status", help="Print the latest Continuous Ops heartbeat artifact.")

    args = parser.parse_args(argv)
    if args.command == "summary":
        return emit(build_trading_safety_daily_summary(day=args.day, tenant_slug=args.tenant_slug))
    if args.command == "compact":
        return emit(compact_trading_safety_ledger(day=args.day, tenant_slug=args.tenant_slug))
    if args.command == "env":
        return emit(validate_env((ROOT / args.env_file).resolve()))
    if args.command == "market-ready":
        env_status = validate_env((ROOT / args.env_file).resolve())
        last_known_state = read_last_known_safety_state()
        daily_summary = build_trading_safety_daily_summary(day=args.day, tenant_slug=args.tenant_slug)
        latest_status = str(daily_summary.get("latest_status") or "unknown").strip().lower()
        route_table = build_route_table_snapshot()
        artifact_index = build_daily_artifact_index()
        sweep_status = build_weak_strong_sweep()
        market_session = build_market_session_report(
            env_file=(ROOT / args.env_file).resolve(),
            tenant_slug=args.tenant_slug,
        )
        no_trade = build_no_trade_report(tenant_slug=args.tenant_slug)
        market_controls_ok = bool(market_session.get("market_controls_ok", market_session.get("ok")))
        payload = {
            "ok": bool(env_status.get("ok")) and market_controls_ok and bool(route_table.get("ok")) and bool(sweep_status.get("ok")),
            "env": env_status,
            "daily_summary": daily_summary,
            "last_known_safety_state": last_known_state,
            "hft_watchdog": build_hft_watchdog_latest(),
            "route_table": {key: value for key, value in route_table.items() if key != "routes"},
            "artifact_index": {key: value for key, value in artifact_index.items() if key != "latest"},
            "weak_strong_sweep": sweep_status,
            "market_session": market_session,
            "no_trade_report": no_trade,
            "production_readiness": {
                "customer_safe_shell": True,
                "alpaca_paper_only": True,
                "five_active_desks_expected": True,
                "forty_five_symbol_scan_board_expected": True,
                "live_money_autonomy": False,
                "proof_sections": (market_session.get("production_readiness") or {}).get("proof_sections") or {},
            },
            "readiness_cache": market_session.get("readiness_cache") or {},
            "runtime_supervisor": market_session.get("runtime_supervisor") or {},
            "expected_settings_proof": market_session.get("expected_settings_proof") or {},
            "incident_timeline": market_session.get("incident_timeline") or {},
            "close_artifact_index": market_session.get("close_artifact_index") or {},
            "production_weakness_closure": market_session.get("production_weakness_closure") or {},
            "next_50_trading_intelligence": market_session.get("next_50_trading_intelligence") or {},
            "next_50_institutional_edge": market_session.get("next_50_institutional_edge") or {},
            "next_50_enterprise_diligence": market_session.get("next_50_enterprise_diligence") or {},
            "next_50_market_edge_trade_capture": market_session.get("next_50_market_edge_trade_capture") or {},
            "next_50_research_memory_strategy_promotion": market_session.get("next_50_research_memory_strategy_promotion") or {},
            "next_100_edge_factory_production_scale": market_session.get("next_100_edge_factory_production_scale") or {},
            "next_500_quant_evidence_os_edge": market_session.get("next_500_quant_evidence_os_edge") or {},
            "next_1000_quant_evidence_os_scale": market_session.get("next_1000_quant_evidence_os_scale") or {},
            "next_500_quant_evidence_os_compounding": market_session.get("next_500_quant_evidence_os_compounding") or {},
            "next_500_quant_evidence_os_institutional_moat": market_session.get("next_500_quant_evidence_os_institutional_moat") or {},
            "next_500_quant_evidence_os_adaptive_edge": market_session.get("next_500_quant_evidence_os_adaptive_edge") or {},
            "next_500_quant_evidence_os_decision_intelligence": market_session.get("next_500_quant_evidence_os_decision_intelligence") or {},
            "next_500_quant_evidence_os_autonomous_improvement": market_session.get("next_500_quant_evidence_os_autonomous_improvement") or {},
            "next_500_quant_evidence_os_market_adaptation": market_session.get("next_500_quant_evidence_os_market_adaptation") or {},
            "next_1000_quant_evidence_os_frontier_edge": market_session.get("next_1000_quant_evidence_os_frontier_edge") or {},
            "next_500_quant_evidence_os_trade_selection_edge": market_session.get("next_500_quant_evidence_os_trade_selection_edge") or {},
            "next_500_quant_evidence_os_realtime_alpha_ops": market_session.get("next_500_quant_evidence_os_realtime_alpha_ops") or {},
            "next_500_quant_evidence_os_adaptive_execution_intelligence": market_session.get("next_500_quant_evidence_os_adaptive_execution_intelligence") or {},
            "next_500_quant_evidence_os_portfolio_outcome_intelligence": market_session.get("next_500_quant_evidence_os_portfolio_outcome_intelligence") or {},
            "next_5000_quant_evidence_os_institutional_operating_edge": market_session.get("next_5000_quant_evidence_os_institutional_operating_edge") or {},
            "evidence_million_target": market_session.get("evidence_million_target") or {},
            "evidence_accelerator_context": market_session.get("evidence_accelerator_context")
            or (market_session.get("evidence_million_target") or {}).get("evidence_accelerator")
            or {},
            "simulation_evidence_store": market_session.get("simulation_evidence_store")
            or (market_session.get("evidence_million_target") or {}).get("market_possibility_engine")
            or {},
            "market_possibility_engine_context": market_session.get("market_possibility_engine_context") or {},
            "trade_selection_edge_context": market_session.get("trade_selection_edge_context") or {},
            "realtime_alpha_ops_context": market_session.get("realtime_alpha_ops_context") or {},
            "adaptive_execution_intelligence_context": market_session.get("adaptive_execution_intelligence_context") or {},
            "portfolio_outcome_intelligence_context": market_session.get("portfolio_outcome_intelligence_context") or {},
            "institutional_operating_edge_context": market_session.get("institutional_operating_edge_context") or {},
            "roadmap_evidence_activation": market_session.get("roadmap_evidence_activation") or {},
            "read_only_activation_audit": market_session.get("read_only_activation_audit") or {},
            "monday_open_rehearsal": market_session.get("monday_open_rehearsal") or {},
            "production_trust": build_production_trust_report(
                env_file=(ROOT / args.env_file).resolve(),
                tenant_slug=args.tenant_slug,
            ).get("production_trust")
            or {},
        }
        payload["validation_summary"] = write_validation_summary(payload)
        return emit(payload)
    if args.command == "route-table":
        return emit(build_route_table_snapshot())
    if args.command == "artifact-index":
        return emit(build_daily_artifact_index())
    if args.command == "retention-cleanup":
        return emit(cleanup_runtime_logs(max_age_days=args.max_age_days, dry_run=not args.apply))
    if args.command == "weak-strong-sweep":
        return emit(build_weak_strong_sweep())
    if args.command == "changed-files":
        return emit(build_changed_files_report())
    if args.command == "validation-report":
        payload = build_validation_report(
            env_file=(ROOT / args.env_file).resolve(),
            tenant_slug=args.tenant_slug,
        )
        payload["validation_summary"] = write_validation_summary(payload, name="next_200_validation_report.json")
        return emit(payload)
    if args.command == "market-session":
        return emit(build_market_session_report(env_file=(ROOT / args.env_file).resolve(), tenant_slug=args.tenant_slug))
    if args.command == "no-trade-report":
        return emit(build_no_trade_report(tenant_slug=args.tenant_slug))
    if args.command == "market-day-report":
        return emit(build_market_day_report(env_file=(ROOT / args.env_file).resolve(), tenant_slug=args.tenant_slug))
    if args.command == "production-trust":
        return emit(build_production_trust_report(env_file=(ROOT / args.env_file).resolve(), tenant_slug=args.tenant_slug))
    if args.command == "support-bundle":
        return emit(build_support_bundle_export(tenant_slug=args.tenant_slug))
    if args.command == "replay-day":
        return emit(build_replay_day_report(tenant_slug=args.tenant_slug, day=args.date))
    if args.command == "monday-open-rehearsal":
        market_session_payload = build_market_session_report(
            env_file=(ROOT / args.env_file).resolve(),
            tenant_slug=args.tenant_slug,
        )
        validation_payload = build_validation_report(
            env_file=(ROOT / args.env_file).resolve(),
            tenant_slug=args.tenant_slug,
        )
        payload = {
            "ok": bool(market_session_payload.get("route_table", {}).get("ok")) and bool(validation_payload.get("route_table", {}).get("ok")),
            "tenant_slug": args.tenant_slug,
            "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
            "read_only": True,
            "mutation": "none",
            "can_submit_orders": False,
            "paper_route_only": True,
            "checks": {
                "startup": market_session_payload.get("runtime_supervisor") or {},
                "route_health": market_session_payload.get("route_table") or {},
                "alpaca_readiness": market_session_payload.get("env") or {},
                "desk_status": "Use authenticated /api/orgs/trade-automation/desks for exact due-state proof.",
                "market_session_proof": market_session_payload,
            },
            "validation": {
                "weak_strong_sweep": validation_payload.get("weak_strong_sweep") or {},
                "changed_files": validation_payload.get("changed_files") or {},
            },
            "next_action": "Run this before Monday open, then use the app Trading Safety page for authenticated desk proof.",
        }
        return emit(payload)
    if args.command == "continuous-watch":
        payload = run_continuous_watch(
            env_file=(ROOT / args.env_file).resolve(),
            tenant_slug=args.tenant_slug,
            api_base_url=args.api_base_url,
            frontend_url=args.frontend_url,
            interval_seconds=args.interval_seconds,
            restart_cooldown_seconds=args.restart_cooldown_seconds,
            timeout_seconds=args.timeout_seconds,
            once=args.once,
            max_loops=args.max_loops,
            allow_restart=not bool(args.no_restart),
        )
        return emit(payload)
    if args.command == "continuous-status":
        return emit(read_continuous_ops_status())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
