from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import settings
from backend.core.database import init_database


def _database_driver(url: str) -> str:
    lowered = str(url or "").lower()
    if lowered.startswith("postgresql+psycopg://"):
        return "postgresql+psycopg"
    if lowered.startswith("postgresql://"):
        return "postgresql"
    if lowered.startswith("sqlite:///"):
        return "sqlite"
    return "unknown"


def _build_connect_args(url: str) -> dict[str, object]:
    driver = _database_driver(url)
    if driver in {"postgresql", "postgresql+psycopg"}:
        return {"connect_timeout": 5}
    if driver == "sqlite":
        return {"check_same_thread": False}
    return {}


def main() -> int:
    report: dict[str, object] = {
        "environment": settings.environment,
        "database_url": settings.database_url,
        "database_driver": _database_driver(settings.database_url),
        "checks": [],
    }

    try:
        init_database()
        report["checks"].append(
            {
                "key": "schema_init",
                "status": "ready",
                "message": "Database metadata initialization succeeded.",
            }
        )
    except Exception as exc:
        report["checks"].append(
            {
                "key": "schema_init",
                "status": "blocked",
                "message": f"Database metadata initialization failed: {exc}",
            }
        )
        print(json.dumps(report, indent=2))
        return 1

    try:
        engine = create_engine(
            settings.database_url,
            future=True,
            connect_args=_build_connect_args(settings.database_url),
        )
        with engine.connect() as connection:
            db_now = connection.execute(text("SELECT current_database(), now()")).one()
            report["checks"].append(
                {
                    "key": "query_probe",
                    "status": "ready",
                    "message": "Database connectivity probe succeeded.",
                    "details": {
                        "current_database": db_now[0],
                        "server_time": db_now[1].isoformat() if hasattr(db_now[1], "isoformat") else str(db_now[1]),
                    },
                }
            )
        engine.dispose()
    except Exception as exc:
        report["checks"].append(
            {
                "key": "query_probe",
                "status": "blocked",
                "message": f"Database connectivity probe failed: {exc}",
            }
        )
        print(json.dumps(report, indent=2))
        return 1

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
