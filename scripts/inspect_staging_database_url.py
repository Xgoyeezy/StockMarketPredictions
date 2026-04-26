from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect the DATABASE_URL in a staging env file without printing credentials.",
    )
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(f"ERROR: env file not found: {env_path}")
        return 1

    env = _parse_env_file(env_path)
    database_url = env.get("DATABASE_URL", "").strip()
    if not database_url:
        print(json.dumps({"status": "blocked", "message": "DATABASE_URL is missing."}, indent=2))
        return 1

    parsed = urlparse(database_url)
    report = {
        "status": "ready",
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "port": parsed.port,
        "database": parsed.path.lstrip("/") or None,
        "username": _mask_username(parsed.username),
        "has_password": parsed.password is not None,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
