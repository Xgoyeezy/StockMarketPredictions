from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import select

from backend.core.database import SessionLocal
from backend.models.saas import Tenant
from backend.services.tenant_service import create_tenant, ensure_user


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _runtime_logs_dir() -> Path:
    configured = str(os.getenv("RUNTIME_LOGS_DIR", "")).strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else (_project_root() / path)
    return _project_root() / "runtime-logs"


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    cleaned = str(value).strip() if value is not None else ""
    return cleaned or default


def seed_first_run() -> dict[str, object]:
    tenant_slug = _env("SEED_TENANT_SLUG", "systematic-equities").lower()
    tenant_name = _env("SEED_TENANT_NAME", "Systematic Equities Desk")
    tenant_plan_key = _env("SEED_TENANT_PLAN_KEY", "personal").lower()
    admin_email = _env("SEED_ADMIN_EMAIL", "admin@local").lower()
    admin_name = _env("SEED_ADMIN_NAME", "Local Admin")
    login_secret = str(os.getenv("LOCAL_AUTH_LOGIN_SECRET", "")).strip()

    runtime_logs = _runtime_logs_dir()
    runtime_logs.mkdir(parents=True, exist_ok=True)
    credentials_path = runtime_logs / "first-run-credentials.json"

    with SessionLocal() as db:
        has_tenant = db.execute(select(Tenant.id).limit(1)).scalar_one_or_none() is not None
        if has_tenant:
            return {
                "seeded": False,
                "reason": "db_not_empty",
                "credentials_path": str(credentials_path),
            }

        user = ensure_user(
            db,
            auth_subject=f"local:{admin_email}",
            email=admin_email,
            name=admin_name,
            provider="local-session",
            platform_role="admin",
        )
        payload = create_tenant(
            db,
            owner=user,
            name=tenant_name,
            slug=tenant_slug,
            plan_key=tenant_plan_key,
            billing_email=admin_email,
        )

    credentials_payload = {
        "tenant_slug": tenant_slug,
        "tenant_name": tenant_name,
        "plan_key": tenant_plan_key,
        "admin": {
            "email": admin_email,
            "name": admin_name,
        },
        "local_session": {
            "login_secret_required": bool(login_secret),
            "login_secret": login_secret or None,
        },
        "login_hint": {
            "path": "/login",
            "use_email": admin_email,
            "use_name": admin_name,
        },
    }
    credentials_path.write_text(json.dumps(credentials_payload, indent=2, sort_keys=True), encoding="utf-8")

    banner = {
        "seeded": True,
        "tenant": payload,
        "credentials_path": str(credentials_path),
        "login_secret_required": bool(login_secret),
    }
    if login_secret:
        banner["login_secret"] = login_secret
    return banner


def main() -> int:
    result = seed_first_run()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
