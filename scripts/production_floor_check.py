from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.auth_service import get_auth_config
from backend.services.job_queue_service import get_job_worker_status, start_job_worker, stop_job_worker
from backend.services.market_service import get_health
from backend.services.readiness_service import get_production_readiness_snapshot


def _print_section(title: str, payload: dict[str, Any]) -> None:
    print(title)
    print(json.dumps(payload, indent=2, sort_keys=True))
    print("---")


def _build_report() -> dict[str, Any]:
    auth_config = get_auth_config()
    health = get_health()
    readiness = get_production_readiness_snapshot()
    worker_status = get_job_worker_status()
    return {
        "auth_config": auth_config,
        "health": health,
        "readiness_summary": readiness.get("summary") or {},
        "worker_status": worker_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect the current backend production-floor posture without involving frontend/chart code.",
    )
    parser.add_argument(
        "--probe-worker",
        action="store_true",
        help="Temporarily start the background worker, capture readiness again, then stop it.",
    )
    args = parser.parse_args()

    _print_section("PRODUCTION_FLOOR_BASELINE", _build_report())

    if args.probe_worker:
        start_job_worker()
        try:
            _print_section("PRODUCTION_FLOOR_WITH_WORKER", _build_report())
        finally:
            stop_job_worker()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
