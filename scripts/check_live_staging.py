from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _fetch_json(url: str) -> tuple[str, dict[str, object] | None]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = response.read().decode("utf-8")
            return "ready", json.loads(payload)
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read().decode("utf-8")
            return "warning", json.loads(payload)
        except Exception:
            return "warning", {"error": f"HTTP {exc.code}", "message": str(exc)}
    except Exception as exc:
        return "blocked", {"error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the live staging server via health and readiness endpoints.")
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(json.dumps({"status": "blocked", "message": f"env file not found: {env_path}"}, indent=2))
        return 1

    env = _parse_env_file(env_path)
    base_url = (env.get("PUBLIC_API_BASE_URL", "").strip() or "").rstrip("/")
    if not base_url:
        print(json.dumps({"status": "blocked", "message": "PUBLIC_API_BASE_URL is missing."}, indent=2))
        return 1

    health_status, health_payload = _fetch_json(f"{base_url.removesuffix('/api')}/api/health")
    ready_status, ready_payload = _fetch_json(f"{base_url.removesuffix('/api')}/api/readyz")

    if ready_status == "ready":
        final_status = "warning" if health_status == "blocked" else "ready"
    elif ready_status == "warning":
        final_status = "warning"
    else:
        final_status = "blocked"
    report = {
        "status": final_status,
        "base_url": base_url,
        "health": {
            "status": health_status,
            "payload": health_payload,
        },
        "readyz": {
            "status": ready_status,
            "payload": ready_payload,
        },
        "next_action": (
            "Resolve live staging connectivity first."
            if final_status == "blocked"
            else "Resolve remaining readiness warnings."
            if final_status == "warning"
            else "Live staging health and readiness checks are clean."
        ),
    }
    print(json.dumps(report, indent=2))
    return 1 if final_status == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
