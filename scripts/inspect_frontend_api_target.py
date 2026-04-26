from __future__ import annotations

import json
from pathlib import Path


DEFAULT_ENV_FILE = Path("frontend/.env.local")


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


def main() -> int:
    env_path = DEFAULT_ENV_FILE
    values = parse_env(env_path)
    api_base_url = values.get("VITE_API_BASE_URL")
    ws_base_url = values.get("VITE_WS_BASE_URL")
    status = "ready" if api_base_url else "blocked"

    payload = {
        "status": status,
        "env_file": str(env_path.as_posix()),
        "api_base_url": api_base_url,
        "ws_base_url": ws_base_url,
        "next_action": (
            "Frontend local target is configured."
            if status == "ready"
            else "Set frontend local API target before running UI acceptance."
        ),
    }
    print(json.dumps(payload, indent=2))
    return 0 if status == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
