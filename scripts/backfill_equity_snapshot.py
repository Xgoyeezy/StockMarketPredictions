from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.database import SessionLocal
from backend.models.saas import Tenant
from backend.services.equity_snapshot_service import record_trade_automation_equity_snapshot
from backend.services.trade_automation_service import _read_trade_automation_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill the current automation state into the equity snapshot history.")
    parser.add_argument("--tenant-slug", default="alpha-desk", help="Tenant slug that owns the automation state.")
    args = parser.parse_args()

    with SessionLocal() as db:
        tenant = db.execute(select(Tenant).where(Tenant.slug == args.tenant_slug).limit(1)).scalar_one_or_none()
        if tenant is None:
            raise SystemExit(f"Tenant '{args.tenant_slug}' was not found.")
        state = _read_trade_automation_state(tenant)
        snapshot = record_trade_automation_equity_snapshot(tenant=tenant, state=state)

    print(
        json.dumps(
            {
                "tenant_slug": args.tenant_slug,
                "snapshot_recorded": snapshot is not None,
                "snapshot": snapshot,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
