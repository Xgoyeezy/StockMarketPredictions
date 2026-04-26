from __future__ import annotations

import json
import socket
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


FRONTEND_ENV = Path("frontend/.env.local")
EXPECTED_API_BASE_URL = "http://localhost:8001/api"
EXPECTED_FRONTEND_URL = "http://localhost:5173"
FRONTEND_HOST_CANDIDATES = ("127.0.0.1", "localhost", "::1")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        results = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False

    for family, socktype, proto, _, sockaddr in results:
        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(timeout)
            if sock.connect_ex(sockaddr) == 0:
                return True
    return False


def fetch_frontend(url: str) -> dict[str, object]:
    try:
        with urlopen(url, timeout=3) as response:
            body = response.read(512).decode("utf-8", errors="ignore")
            return {
                "reachable": True,
                "status_code": getattr(response, "status", None),
                "body_preview": body[:120],
            }
    except URLError as exc:
        return {
            "reachable": False,
            "error": str(exc.reason or exc),
        }


def main() -> int:
    env_values = parse_env(FRONTEND_ENV)
    api_base_url = env_values.get("VITE_API_BASE_URL")
    ws_base_url = env_values.get("VITE_WS_BASE_URL")
    ready_hosts = [host for host in FRONTEND_HOST_CANDIDATES if port_open(host, 5173)]
    port_ready = bool(ready_hosts)
    frontend_probe = fetch_frontend(EXPECTED_FRONTEND_URL) if port_ready else {"reachable": False, "error": "Port 5173 is not listening."}

    blockers: list[str] = []
    warnings: list[str] = []

    if api_base_url != EXPECTED_API_BASE_URL:
        blockers.append(
            f"Frontend API target is {api_base_url or 'unset'}, expected {EXPECTED_API_BASE_URL}."
        )

    if not port_ready:
        warnings.append("Frontend dev server is not currently listening on localhost:5173.")

    if port_ready:
        warnings.append("If the frontend dev server was already running before the env change, restart it so the new API target is loaded.")

    status = "ready" if not blockers else "blocked"
    payload = {
        "status": status,
        "frontend_url": EXPECTED_FRONTEND_URL,
        "api_base_url": api_base_url,
        "ws_base_url": ws_base_url,
        "frontend_port_ready": port_ready,
        "frontend_ready_hosts": ready_hosts,
        "frontend_probe": frontend_probe,
        "blockers": blockers,
        "warnings": warnings,
        "next_action": (
            "Open the frontend and run the non-chart UI checklist."
            if status == "ready"
            else "Point the frontend at the staging API before running UI acceptance."
        ),
    }
    print(json.dumps(payload, indent=2))
    return 0 if status == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
