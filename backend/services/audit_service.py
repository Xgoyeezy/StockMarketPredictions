from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.saas import AuditEvent, Tenant, User


def record_audit_event(
    db: Session,
    *,
    event_type: str,
    tenant: Tenant | None = None,
    user: User | None = None,
    payload: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            tenant=tenant,
            user=user,
            event_type=event_type,
            actor_email=user.email if user else None,
            payload_json=payload or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )


def list_audit_events_for_tenant(
    db: Session,
    *,
    tenant_id: str,
    limit: int = 20,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    statement = (
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(max(1, min(int(limit or 20), 100)))
    )
    if event_type:
        statement = statement.where(AuditEvent.event_type == str(event_type).strip())

    rows = db.execute(statement).scalars()
    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "actor_email": row.actor_email,
            "payload": row.payload_json or {},
            "ip_address": row.ip_address,
            "user_agent": row.user_agent,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
