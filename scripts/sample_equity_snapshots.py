from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.database import SessionLocal
from backend.models.saas import Tenant
from backend.services import equity_snapshot_service as ess
from backend.services.trade_automation_service import _read_trade_automation_state, _run_trade_automation_cycle


def main() -> int:
    parser = argparse.ArgumentParser(description="Force multiple automation cycles to collect mark-to-market equity snapshots.")
    parser.add_argument("--tenant-slug", default="alpha-desk", help="Tenant slug that owns the automation state.")
    parser.add_argument("--count", type=int, default=5, help="Number of forced cycles to run.")
    parser.add_argument("--sleep-seconds", type=float, default=2.0, help="Pause between forced cycles.")
    args = parser.parse_args()

    count = max(1, int(args.count))
    sleep_seconds = max(0.0, float(args.sleep_seconds))
    cycle_results: list[dict[str, object]] = []

    for index in range(count):
        with SessionLocal() as db:
            tenant = db.execute(select(Tenant).where(Tenant.slug == args.tenant_slug).limit(1)).scalar_one_or_none()
            if tenant is None:
                raise SystemExit(f"Tenant '{args.tenant_slug}' was not found.")
            state = _read_trade_automation_state(tenant)
            snapshot = _run_trade_automation_cycle(db, tenant=tenant, state=state, forced=True, actor=None)
            equity_snapshot = dict(snapshot.get("equity_snapshot") or {})
            cycle_results.append(
                {
                    "index": index + 1,
                    "status": dict(snapshot.get("status") or {}),
                    "decision": dict((snapshot.get("runtime") or {}).get("last_decision") or {}),
                    "equity_snapshot": equity_snapshot,
                }
            )
        if index < count - 1 and sleep_seconds > 0:
            time.sleep(sleep_seconds)

    snapshots = ess.read_equity_snapshots()
    snapshots = snapshots[snapshots.get("tenant_slug", "").astype(str).str.strip() == args.tenant_slug] if not snapshots.empty else snapshots
    print(
        json.dumps(
            {
                "tenant_slug": args.tenant_slug,
                "requested_cycles": count,
                "completed_cycles": len(cycle_results),
                "latest_snapshot_count": int(len(snapshots.index)) if not snapshots.empty else 0,
                "cycles": cycle_results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
