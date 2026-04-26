from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
from pathlib import Path


def _check_docker_cli() -> tuple[str, str]:
    if shutil.which("docker") is None:
        return "blocked", "Docker CLI is not installed or not on PATH."

    completed = subprocess.run(
        ["docker", "version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "Docker daemon is unavailable.").strip()
        return "blocked", message
    return "ready", "Docker CLI and daemon are available."


def _check_port(host: str, port: int) -> tuple[str, str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.5)
        if sock.connect_ex((host, port)) == 0:
            return "ready", f"Local staging Postgres is reachable on {host}:{port}."
    return "blocked", f"Nothing is listening on {host}:{port}."


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check local prerequisites for the repo-backed staging Postgres path.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to probe for local Postgres.")
    parser.add_argument("--port", default=54329, type=int, help="Port to probe for local Postgres.")
    args = parser.parse_args()

    checks = []
    docker_status, docker_message = _check_docker_cli()
    checks.append(("docker", docker_status, docker_message))

    port_status, port_message = _check_port(args.host, args.port)
    checks.append(("postgres_port", port_status, port_message))

    print("LOCAL_STAGING_PREFLIGHT")
    for key, status, message in checks:
        print(f"- {key}: {status} - {message}")

    blockers = [message for _, status, message in checks if status == "blocked"]
    if blockers:
        print("NEXT_ACTION:")
        print(f"- {blockers[0]}")
        return 1

    print("NEXT_ACTION:")
    print("- Local staging prerequisites are clear. Start the backend with .env.staging.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
