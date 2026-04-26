from __future__ import annotations

import argparse
from pathlib import Path


def _replace_keys(contents: str, replacements: dict[str, str]) -> str:
    lines = contents.splitlines()
    updated_lines: list[str] = []
    seen: set[str] = set()

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
    parser = argparse.ArgumentParser(description="Set the intended staging access mode.")
    parser.add_argument("--mode", choices=("local", "remote"), required=True, help="Whether staging is currently local-only or remote-ready.")
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(f"ERROR: env file not found: {env_path}")
        return 1

    original = env_path.read_text(encoding="utf-8")
    secure_cookie = "false" if args.mode == "local" else "true"
    updated = _replace_keys(
        original,
        {
            "STAGING_ACCESS_MODE": args.mode,
            "AUTH_SESSION_SECURE": secure_cookie,
        },
    )
    env_path.write_text(updated, encoding="utf-8")

    print(f"Updated access mode in {env_path}")
    print(f"STAGING_ACCESS_MODE={args.mode}")
    print(f"AUTH_SESSION_SECURE={secure_cookie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
