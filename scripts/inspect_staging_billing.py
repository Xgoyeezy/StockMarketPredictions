from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect staging billing posture.")
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(json.dumps({"status": "blocked", "message": f"env file not found: {env_path}"}, indent=2))
        return 1

    env = _parse_env_file(env_path)
    report = {
        "billing_mode": env.get("STAGING_BILLING_MODE", "").strip() or "unspecified",
        "has_publishable_key": bool(env.get("STRIPE_PUBLISHABLE_KEY", "").strip()),
        "has_secret_key": bool(env.get("STRIPE_SECRET_KEY", "").strip()),
        "has_webhook_secret": bool(env.get("STRIPE_WEBHOOK_SECRET", "").strip()),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
