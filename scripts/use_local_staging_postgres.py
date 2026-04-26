from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.set_staging_database_url import _replace_database_url

LOCAL_STAGING_DATABASE_URL = "postgresql+psycopg://stocksignals:stocksignals_staging@localhost:54329/stocksignals_staging"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Point a staging env file at the local Dockerized Postgres instance used for non-demo staging boots.",
    )
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print the local staging DATABASE_URL without modifying the env file.",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = (PROJECT_ROOT / env_path).resolve()
    if args.print_only:
        print(LOCAL_STAGING_DATABASE_URL)
        return 0

    if not env_path.exists():
        print(f"ERROR: env file not found: {env_path}")
        return 1

    original = env_path.read_text(encoding="utf-8")
    updated = _replace_database_url(original, LOCAL_STAGING_DATABASE_URL)
    env_path.write_text(updated, encoding="utf-8")

    print(f"Updated DATABASE_URL in {env_path}")
    print(f"DATABASE_URL={LOCAL_STAGING_DATABASE_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
