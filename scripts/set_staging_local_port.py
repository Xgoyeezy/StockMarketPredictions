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
    parser = argparse.ArgumentParser(description="Set a local staging API port and localhost API base URL.")
    parser.add_argument("--port", required=True, type=int, help="Local API port for staging.")
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(f"ERROR: env file not found: {env_path}")
        return 1

    port = int(args.port)
    replacements = {
        "API_PORT": str(port),
        "PUBLIC_API_BASE_URL": f"http://localhost:{port}/api",
    }

    original = env_path.read_text(encoding="utf-8")
    updated = _replace_keys(original, replacements)
    env_path.write_text(updated, encoding="utf-8")

    print(f"Updated local staging port in {env_path}")
    print(f"API_PORT={port}")
    print(f"PUBLIC_API_BASE_URL=http://localhost:{port}/api")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
