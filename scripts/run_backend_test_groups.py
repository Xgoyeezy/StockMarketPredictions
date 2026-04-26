from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = PROJECT_ROOT / "tests"
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backend_test_groups import GROUPS, grouped_test_names, unittest_names_for_group  # noqa: E402


def list_groups() -> int:
    buckets = grouped_test_names()
    for group in GROUPS:
        print(f"{group.slug}: {len(buckets[group.slug])} tests")
        print(f"  {group.title}")
        print(f"  {group.description}")
    return 0


def run_group(group_slug: str, verbosity: int, failfast: bool) -> int:
    names = unittest_names_for_group(group_slug)
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=verbosity, failfast=failfast).run(suite)
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run grouped backend unittest buckets for sale verification.")
    parser.add_argument("group", choices=["list", *[group.slug for group in GROUPS]], help="Group to list or execute.")
    parser.add_argument("--verbosity", type=int, default=2, help="unittest verbosity level")
    parser.add_argument("--failfast", action="store_true", help="Stop on first failure")
    args = parser.parse_args()

    if args.group == "list":
        return list_groups()
    return run_group(args.group, verbosity=args.verbosity, failfast=args.failfast)


if __name__ == "__main__":
    raise SystemExit(main())
