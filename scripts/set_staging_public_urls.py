from __future__ import annotations

import argparse
from pathlib import Path


def _replace_keys(contents: str, replacements: dict[str, str]) -> str:
    lines = contents.splitlines()
    seen: set[str] = set()
    updated_lines: list[str] = []

    for line in lines:
        replaced = False
        for key, value in replacements.items():
            if line.startswith(f"{key}="):
                updated_lines.append(f"{key}={value}")
                seen.add(key)
                replaced = True
                break
        if not replaced:
            updated_lines.append(line)

    for key, value in replacements.items():
        if key not in seen:
            updated_lines.append(f"{key}={value}")

    updated = "\n".join(updated_lines)
    if contents.endswith("\n"):
        updated += "\n"
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set the staging frontend and API URLs in a staging env file.",
    )
    parser.add_argument("--frontend-url", required=True, help="Frontend origin, e.g. https://staging.example.com")
    parser.add_argument("--api-base-url", required=True, help="Public API base URL, e.g. https://api-staging.example.com/api")
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(f"ERROR: env file not found: {env_path}")
        return 1

    frontend_url = args.frontend_url.strip().rstrip("/")
    api_base_url = args.api_base_url.strip().rstrip("/")
    replacements = {
        "ALLOW_ORIGINS": frontend_url,
        "FRONTEND_DEV_URL": frontend_url,
        "PUBLIC_API_BASE_URL": api_base_url,
    }

    original = env_path.read_text(encoding="utf-8")
    updated = _replace_keys(original, replacements)
    env_path.write_text(updated, encoding="utf-8")

    print(f"Updated staging public URLs in {env_path}")
    print(f"ALLOW_ORIGINS={frontend_url}")
    print(f"FRONTEND_DEV_URL={frontend_url}")
    print(f"PUBLIC_API_BASE_URL={api_base_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
