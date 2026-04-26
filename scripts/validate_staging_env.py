from __future__ import annotations

import argparse
import sys
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


def _is_placeholder(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    markers = (
        "replace-with-",
        "replace_me",
        "your-",
        "user:password@host",
        "price_replace_me",
        "pk_test_replace_me",
        "sk_test_replace_me",
        "whsec_replace_me",
    )
    return any(marker in normalized for marker in markers)


def _expect(env: dict[str, str], key: str, *, exact: str | None = None, disallow: set[str] | None = None) -> list[str]:
    value = env.get(key, "")
    issues: list[str] = []
    if not value:
        issues.append(f"{key} is missing.")
        return issues
    if exact is not None and value != exact:
        issues.append(f"{key} must be set to {exact!r}.")
    if disallow and value in disallow:
        issues.append(f"{key} must not be one of: {', '.join(sorted(disallow))}.")
    return issues


def validate_env(path: Path) -> tuple[list[str], list[str]]:
    env = _parse_env_file(path)
    blockers: list[str] = []
    warnings: list[str] = []

    blockers.extend(_expect(env, "APP_ENV", exact="staging"))
    blockers.extend(_expect(env, "API_PORT", exact="8001"))
    blockers.extend(_expect(env, "API_RELOAD", exact="false"))
    blockers.extend(_expect(env, "AUTH_ENABLED", exact="true"))
    blockers.extend(_expect(env, "ALLOW_DEMO_AUTH", exact="false"))
    blockers.extend(_expect(env, "AUTH_PROVIDER", disallow={"local-demo"}))

    database_url = env.get("DATABASE_URL", "")
    if not database_url:
        blockers.append("DATABASE_URL is missing.")
    elif database_url.startswith("sqlite:///") or database_url.startswith("sqlite+pysqlite:///"):
        blockers.append("DATABASE_URL still points to local SQLite.")
    elif _is_placeholder(database_url):
        blockers.append("DATABASE_URL still uses a placeholder value.")

    for key in ("AUTH_SESSION_SECRET", "AUTH_STATE_SECRET", "API_TOKEN_SALT"):
        value = env.get(key, "")
        if not value:
            blockers.append(f"{key} is missing.")
        elif _is_placeholder(value):
            blockers.append(f"{key} still uses a placeholder value.")

    market_provider = env.get("MARKET_DATA_PROVIDER", "").strip().lower()
    if market_provider == "alpaca":
        for key in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
            value = env.get(key, "")
            if not value:
                blockers.append(f"{key} is missing for Alpaca market data.")
            elif _is_placeholder(value):
                blockers.append(f"{key} still uses a placeholder value.")
        blockers.extend(_expect(env, "ALPACA_USE_SANDBOX", exact="false"))
        blockers.extend(_expect(env, "ALPACA_OPTIONS_FEED", exact="opra"))

    stripe_keys = {
        "STRIPE_PUBLISHABLE_KEY": env.get("STRIPE_PUBLISHABLE_KEY", ""),
        "STRIPE_SECRET_KEY": env.get("STRIPE_SECRET_KEY", ""),
        "STRIPE_WEBHOOK_SECRET": env.get("STRIPE_WEBHOOK_SECRET", ""),
    }
    staging_billing_mode = env.get("STAGING_BILLING_MODE", "").strip().lower()
    present_count = sum(bool(v) for v in stripe_keys.values())
    if present_count and present_count < len(stripe_keys):
        blockers.append("Stripe is only partially configured.")
    elif present_count == len(stripe_keys):
        for key, value in stripe_keys.items():
            if _is_placeholder(value):
                blockers.append(f"{key} still uses a placeholder value.")
    elif staging_billing_mode == "disabled":
        pass
    else:
        warnings.append("Stripe keys are not configured yet. Billing will remain non-live.")

    public_api_base_url = env.get("PUBLIC_API_BASE_URL", "")
    if not public_api_base_url:
        blockers.append("PUBLIC_API_BASE_URL is missing.")
    elif _is_placeholder(public_api_base_url):
        blockers.append("PUBLIC_API_BASE_URL still uses a placeholder value.")
    elif ":8001/api" not in public_api_base_url:
        blockers.append("PUBLIC_API_BASE_URL must target the local staging API on port 8001.")

    runtime_api_base_url = env.get("RUNTIME_API_BASE_URL", "")
    if not runtime_api_base_url:
        blockers.append("RUNTIME_API_BASE_URL is missing.")
    elif _is_placeholder(runtime_api_base_url):
        blockers.append("RUNTIME_API_BASE_URL still uses a placeholder value.")
    elif runtime_api_base_url != "http://127.0.0.1:8001/api":
        blockers.append("RUNTIME_API_BASE_URL must be set to 'http://127.0.0.1:8001/api'.")

    allow_origins = env.get("ALLOW_ORIGINS", "")
    staging_access_mode = env.get("STAGING_ACCESS_MODE", "").strip().lower()
    if not allow_origins:
        blockers.append("ALLOW_ORIGINS is missing.")
    elif ("localhost" in allow_origins or "127.0.0.1" in allow_origins) and staging_access_mode != "local":
        warnings.append("ALLOW_ORIGINS still includes localhost values.")

    return blockers, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a staging-style env file against current readiness blockers.")
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the env file to validate.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
      print(f"ERROR: env file not found: {env_path}")
      return 1

    blockers, warnings = validate_env(env_path)
    print(f"Validated: {env_path}")
    if blockers:
        print("BLOCKERS:")
        for item in blockers:
            print(f"- {item}")
    if warnings:
        print("WARNINGS:")
        for item in warnings:
            print(f"- {item}")
    if not blockers and not warnings:
        print("No blockers or warnings found.")

    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
