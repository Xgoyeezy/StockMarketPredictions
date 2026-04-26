from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validate_staging_env import validate_env


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _mask_username(username: str | None) -> str | None:
    if not username:
        return None
    if len(username) <= 2:
        return "*" * len(username)
    return f"{username[0]}***{username[-1]}"


def _check_socket(host: str | None, port: int | None) -> tuple[str, str]:
    if not host or not port:
        return "blocked", "Database host or port could not be parsed from DATABASE_URL."
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.5)
        if sock.connect_ex((host, port)) == 0:
            return "ready", f"Database target is reachable on {host}:{port}."
    return "blocked", f"Database target is not reachable on {host}:{port}."


def _build_remediation_steps(*, host: str | None, port: int | None, access_mode: str, config_blocked: bool) -> list[str]:
    if config_blocked:
        return [
            r".\scripts\staging_ops.ps1 -Action status",
            r".\scripts\staging_ops.ps1 -Action env-check",
        ]

    if access_mode == "local":
        return [
            r".\scripts\staging_ops.ps1 -Action preflight",
            r".\scripts\staging_ops.ps1 -Action docker-diagnose",
            r".\scripts\staging_ops.ps1 -Action db-up",
            r".\scripts\staging_ops.ps1 -Action db-check",
        ]

    if host and port:
        return [
            r'.\scripts\staging_ops.ps1 -Action show-db-url',
            r'.\scripts\staging_ops.ps1 -Action db-check',
            r'.\scripts\staging_ops.ps1 -Action api',
        ]

    return [
        r'.\scripts\staging_ops.ps1 -Action status',
        r'.\scripts\staging_ops.ps1 -Action runtime-gate',
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize the runtime gate between clean staging config and a live staging boot.")
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(json.dumps({"status": "blocked", "message": f"env file not found: {env_path}"}, indent=2))
        return 1

    env = _parse_env_file(env_path)
    blockers, warnings = validate_env(env_path)
    database_url = env.get("DATABASE_URL", "").strip()
    access_mode = env.get("STAGING_ACCESS_MODE", "").strip().lower() or "unspecified"
    parsed = urlparse(database_url) if database_url else None
    db_host = parsed.hostname if parsed else None
    db_port = parsed.port if parsed else None
    db_name = parsed.path.lstrip("/") if parsed and parsed.path else None

    socket_status, socket_message = _check_socket(db_host, db_port)
    runtime_checks = [
        {
            "key": "staging_config",
            "status": "ready" if not blockers else "blocked",
            "message": "Staging config is clean." if not blockers else blockers[0],
        },
        {
            "key": "database_target",
            "status": socket_status,
            "message": socket_message,
        },
    ]

    report = {
        "status": "blocked" if blockers or socket_status == "blocked" else "ready",
        "env_file": str(env_path),
        "access_mode": access_mode,
        "database": {
            "host": db_host,
            "port": db_port,
            "database": db_name or None,
            "username": _mask_username(parsed.username) if parsed else None,
        },
        "config_warnings": warnings,
        "runtime_checks": runtime_checks,
        "remediation_steps": _build_remediation_steps(
            host=db_host,
            port=db_port,
            access_mode=access_mode,
            config_blocked=bool(blockers),
        ),
        "next_action": blockers[0] if blockers else socket_message if socket_status == "blocked" else "Start the staging API and run the production floor check.",
    }
    print(json.dumps(report, indent=2))
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
