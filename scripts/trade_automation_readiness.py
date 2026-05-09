#!/usr/bin/env python
"""Runtime helpers for Trade Automation readiness smoke checks."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_STATUS_PATH = PROJECT_ROOT / "runtime-logs" / "backup-status.json"
TRADE_AUTOMATION_STATUS_PATH = PROJECT_ROOT / "runtime-logs" / "trade-automation-readiness.json"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "runtime-logs" / "backups"
DEFAULT_MAX_INLINE_BACKUP_BYTES = 512 * 1024 * 1024
LARGE_DB_BACKUP_PROOF_MAX_AGE_DAYS = 14


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_env_file(path: Path) -> dict[str, str]:
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


def resolve_sqlite_database(env: dict[str, str]) -> Path:
    database_url = env.get("DATABASE_URL")
    if not database_url:
        for candidate in (
            PROJECT_ROOT / "backend" / "storage" / "app.db",
            PROJECT_ROOT / "backend-storage" / "app.db",
        ):
            if candidate.exists():
                return candidate
        return PROJECT_ROOT / "backend" / "storage" / "app.db"
    for prefix in ("sqlite:///", "sqlite+pysqlite:///"):
        if database_url.startswith(prefix):
            path_value = database_url[len(prefix) :]
            db_path = Path(path_value)
            if not db_path.is_absolute():
                db_path = PROJECT_ROOT / db_path
            if not db_path.exists() and str(db_path).endswith(str(Path("backend-storage") / "app.db")):
                fallback = PROJECT_ROOT / "backend" / "storage" / "app.db"
                if fallback.exists():
                    return fallback
            return db_path
    return PROJECT_ROOT / "backend" / "storage" / "app.db"


def _positive_int(value: str | None, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def resolve_max_inline_backup_bytes(env: dict[str, str]) -> int:
    return _positive_int(
        os.environ.get("TRADE_AUTOMATION_BACKUP_MAX_INLINE_BYTES")
        or env.get("TRADE_AUTOMATION_BACKUP_MAX_INLINE_BYTES"),
        DEFAULT_MAX_INLINE_BACKUP_BYTES,
    )


def latest_backup_file(backup_dir: Path = DEFAULT_BACKUP_DIR) -> Path | None:
    if not backup_dir.exists():
        return None
    candidates = [
        path
        for path in backup_dir.glob("app-*.db")
        if path.is_file() and path.stat().st_size > 0
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def run_backup_readiness(env_file: Path) -> dict[str, Any]:
    env = load_env_file(env_file)
    source_db = resolve_sqlite_database(env)
    now = utc_now_iso()
    blockers: list[str] = []
    warnings: list[str] = []
    backup_file: Path | None = None
    restore_tested_at: str | None = None
    source_size_bytes = 0
    skipped_full_backup = False
    backup_mode = "full_copy"
    latest_backup_age_days: float | None = None
    max_inline_backup_bytes = resolve_max_inline_backup_bytes(env)

    DEFAULT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not source_db.exists():
        blockers.append(f"SQLite database was not found at {source_db}.")
    else:
        source_size_bytes = source_db.stat().st_size
        if source_size_bytes > max_inline_backup_bytes:
            skipped_full_backup = True
            backup_mode = "large_database_source_proof"
            backup_file = latest_backup_file()
            if backup_file is not None:
                latest_backup_age_days = max(
                    0.0,
                    (datetime.now(timezone.utc).timestamp() - backup_file.stat().st_mtime) / 86400.0,
                )
                if latest_backup_age_days > LARGE_DB_BACKUP_PROOF_MAX_AGE_DAYS:
                    warnings.append(
                        "SQLite database is larger than the inline readiness backup limit and the latest full "
                        f"backup is older than {LARGE_DB_BACKUP_PROOF_MAX_AGE_DAYS} days."
                    )
            else:
                warnings.append(
                    "SQLite database is larger than the inline readiness backup limit and no full backup proof "
                    "was found in runtime-logs/backups."
                )
            try:
                with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True, timeout=5.0) as source:
                    source.execute("PRAGMA busy_timeout=5000")
                    schema_version = source.execute("PRAGMA schema_version").fetchone()
                    page_count = source.execute("PRAGMA page_count").fetchone()
                    source.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
                if schema_version is not None and page_count is not None:
                    restore_tested_at = now
                else:
                    blockers.append("SQLite source metadata proof did not return schema and page data.")
            except sqlite3.Error as exc:
                blockers.append(f"SQLite source integrity proof failed: {exc}")
        else:
            backup_file = DEFAULT_BACKUP_DIR / f"app-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.db"
            try:
                with sqlite3.connect(source_db, timeout=5.0) as source, sqlite3.connect(backup_file) as destination:
                    source.execute("PRAGMA busy_timeout=5000")
                    source.backup(destination)
                with sqlite3.connect(backup_file) as conn:
                    result = conn.execute("PRAGMA integrity_check").fetchone()
                if result and str(result[0]).lower() == "ok":
                    restore_tested_at = now
                else:
                    blockers.append("SQLite restore integrity check did not return ok.")
            except sqlite3.Error as exc:
                blockers.append(f"SQLite restore drill failed: {exc}")

    manifest = {
        "status": "ready" if not blockers and not warnings else "warning" if not blockers else "error",
        "provider": "local-sqlite-copy",
        "schedule": "Manual readiness smoke plus before deployment",
        "last_success_at": now if not blockers else None,
        "last_attempt_at": now,
        "restore_tested_at": restore_tested_at,
        "retention_days": 14,
        "location": str(DEFAULT_BACKUP_DIR.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "latest_backup_file": str(backup_file.relative_to(PROJECT_ROOT)).replace("\\", "/") if backup_file else None,
        "source_size_bytes": source_size_bytes,
        "max_inline_backup_bytes": max_inline_backup_bytes,
        "skipped_full_backup": skipped_full_backup,
        "backup_mode": backup_mode,
        "latest_backup_age_days": latest_backup_age_days,
        "notes": "Created by scripts/trade_automation_readiness.py backup.",
        "blockers": blockers,
        "warnings": warnings,
    }
    write_json(BACKUP_STATUS_PATH, manifest)
    return manifest


def probe_url(url: str, *, timeout: float) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        request = Request(url, headers={"Accept": "application/json,text/plain,*/*"})
        with urlopen(request, timeout=timeout) as response:
            body = response.read(1024)
            status_code = int(response.status)
    except HTTPError as exc:
        status_code = int(exc.code)
        body = exc.read(1024)
    except (OSError, URLError) as exc:
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": round((time.perf_counter() - start) * 1000.0, 3),
            "error": str(exc),
            "body_preview": None,
        }
    return {
        "ok": status_code < 400,
        "status_code": status_code,
        "latency_ms": round((time.perf_counter() - start) * 1000.0, 3),
        "error": None,
        "body_preview": body.decode("utf-8", errors="replace")[:400],
    }


def run_runtime_readiness(backend_url: str, frontend_url: str, timeout: float, latency_target_ms: float) -> dict[str, Any]:
    backend_url = backend_url.rstrip("/")
    frontend_url = frontend_url.rstrip("/")
    health = probe_url(f"{backend_url}/healthz", timeout=timeout)
    ready = probe_url(f"{backend_url}/readyz", timeout=timeout)
    trade_automation = probe_url(
        f"{backend_url}/orgs/trade-automation?profile_key=personal_paper",
        timeout=timeout,
    )
    frontend = probe_url(frontend_url, timeout=timeout)

    blockers: list[str] = []
    warnings: list[str] = []
    if not health["ok"]:
        blockers.append("Backend /api/healthz is not OK.")
    if not trade_automation["ok"]:
        blockers.append("Trade Automation API did not load successfully.")
    if trade_automation["latency_ms"] > latency_target_ms:
        blockers.append(
            f"Trade Automation API latency {trade_automation['latency_ms']:.0f}ms exceeds target {latency_target_ms:.0f}ms."
        )
    if not frontend["ok"]:
        warnings.append("Frontend URL is not reachable.")
    payload = {
        "status": "ready" if not blockers and not warnings else "warning" if not blockers else "blocked",
        "checked_at": utc_now_iso(),
        "backend_health_ok": bool(health["ok"]),
        "backend_health_status_code": health["status_code"],
        "backend_ready_ok": bool(ready["ok"]),
        "backend_ready_status_code": ready["status_code"],
        "frontend_health_ok": bool(frontend["ok"]),
        "frontend_status_code": frontend["status_code"],
        "trade_automation_ready": bool(trade_automation["ok"]),
        "trade_automation_status_code": trade_automation["status_code"],
        "trade_automation_latency_ms": trade_automation["latency_ms"],
        "latency_target_ms": latency_target_ms,
        "blockers": blockers,
        "warnings": warnings,
        "probes": {
            "health": health,
            "ready": ready,
            "trade_automation": trade_automation,
            "frontend": frontend,
        },
    }
    write_json(TRADE_AUTOMATION_STATUS_PATH, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Trade Automation readiness helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="Create and validate the local backup manifest.")
    backup.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))

    runtime = subparsers.add_parser("runtime", help="Probe backend/frontend and record Trade Automation route readiness.")
    runtime.add_argument("--backend-url", default="http://127.0.0.1:8000/api")
    runtime.add_argument("--frontend-url", default="http://localhost:5173")
    runtime.add_argument("--timeout", type=float, default=30.0)
    runtime.add_argument("--latency-target-ms", type=float, default=5000.0)

    args = parser.parse_args()
    if args.command == "backup":
        result = run_backup_readiness(Path(args.env_file))
    else:
        result = run_runtime_readiness(
            args.backend_url,
            args.frontend_url,
            timeout=float(args.timeout),
            latency_target_ms=float(args.latency_target_ms),
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if str(result.get("status")) in {"ready", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
