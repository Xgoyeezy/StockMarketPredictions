from __future__ import annotations

import argparse
from pathlib import Path


def _replace_database_url(contents: str, database_url: str) -> str:
    lines = contents.splitlines()
    updated_lines: list[str] = []
    replaced = False

    for line in lines:
        if line.startswith("DATABASE_URL="):
            updated_lines.append(f"DATABASE_URL={database_url}")
            replaced = True
        else:
            updated_lines.append(line)

    if not replaced:
        updated_lines.append(f"DATABASE_URL={database_url}")

    updated = "\n".join(updated_lines)
    if contents.endswith("\n"):
        updated += "\n"
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set DATABASE_URL in a staging env file.",
    )
    parser.add_argument("database_url", help="Full database URL to write into the env file.")
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(f"ERROR: env file not found: {env_path}")
        return 1

    original = env_path.read_text(encoding="utf-8")
    updated = _replace_database_url(original, args.database_url.strip())
    env_path.write_text(updated, encoding="utf-8")

    print(f"Updated DATABASE_URL in {env_path}")
    print("DATABASE_URL updated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
