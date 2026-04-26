from __future__ import annotations

import argparse
import json
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


def _inspect_database_url(database_url: str) -> dict[str, object]:
    parsed = urlparse(database_url)
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "port": parsed.port,
        "database": parsed.path.lstrip("/") or None,
        "username": _mask_username(parsed.username),
        "has_password": parsed.password is not None,
    }


def _inspect_public_urls(env: dict[str, str]) -> dict[str, object]:
    return {
        "access_mode": env.get("STAGING_ACCESS_MODE", "").strip() or "unspecified",
        "allow_origins": [item.strip() for item in env.get("ALLOW_ORIGINS", "").split(",") if item.strip()],
        "frontend_url": env.get("FRONTEND_DEV_URL", "").strip() or None,
        "public_api_base_url": env.get("PUBLIC_API_BASE_URL", "").strip() or None,
    }


def _inspect_billing(env: dict[str, str]) -> dict[str, object]:
    mode = env.get("STAGING_BILLING_MODE", "").strip() or "unspecified"
    has_publishable_key = bool(env.get("STRIPE_PUBLISHABLE_KEY", "").strip())
    has_secret_key = bool(env.get("STRIPE_SECRET_KEY", "").strip())
    has_webhook_secret = bool(env.get("STRIPE_WEBHOOK_SECRET", "").strip())
    effective_mode = (
        "disabled"
        if mode == "disabled"
        else "test_stripe"
        if mode == "test_stripe"
        else "configured"
        if has_secret_key
        else "non-live-warning"
    )
    return {
        "mode": mode,
        "effective_mode": effective_mode,
        "has_publishable_key": has_publishable_key,
        "has_secret_key": has_secret_key,
        "has_webhook_secret": has_webhook_secret,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize the current staging environment posture without exposing secrets.",
    )
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(json.dumps({"status": "blocked", "message": f"env file not found: {env_path}"}, indent=2))
        return 1

    env = _parse_env_file(env_path)
    blockers, warnings = validate_env(env_path)
    database_url = env.get("DATABASE_URL", "").strip()

    status = "blocked" if blockers else "warning" if warnings else "ready"
    report = {
        "status": status,
        "env_file": str(env_path),
        "environment": env.get("APP_ENV"),
        "auth": {
            "enabled": env.get("AUTH_ENABLED"),
            "allow_demo_auth": env.get("ALLOW_DEMO_AUTH"),
            "provider": env.get("AUTH_PROVIDER"),
            "session_secure": env.get("AUTH_SESSION_SECURE", "").strip() or "default",
        },
        "database": _inspect_database_url(database_url) if database_url else {"status": "blocked", "message": "DATABASE_URL missing"},
        "public_urls": _inspect_public_urls(env),
        "billing": _inspect_billing(env),
        "blockers": blockers,
        "warnings": warnings,
        "next_action": blockers[0] if blockers else warnings[0] if warnings else "Run the staging database and floor checks.",
    }
    print(json.dumps(report, indent=2))
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
