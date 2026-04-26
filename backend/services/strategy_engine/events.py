from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.models.saas import DomainEventLog


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def record_domain_event(
    db: Session,
    *,
    tenant_id: str | None,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str | None,
    payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    status: str = "recorded",
) -> DomainEventLog:
    event = DomainEventLog(
        tenant_id=tenant_id,
        event_type=str(event_type or "").strip() or "event.recorded",
        aggregate_type=str(aggregate_type or "").strip() or "system",
        aggregate_id=str(aggregate_id or "").strip() or None,
        status=status,
        payload_json=dict(payload or {}),
        metadata_json=dict(metadata or {}),
        processed_at=utc_now() if status == "processed" else None,
    )
    db.add(event)
    db.flush()
    return event


def serialize_domain_event(event: DomainEventLog) -> dict[str, Any]:
    return {
        "id": event.id,
        "tenant_id": event.tenant_id,
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "status": event.status,
        "payload": dict(event.payload_json or {}),
        "metadata": dict(event.metadata_json or {}),
        "processed_at": event.processed_at.isoformat() if event.processed_at else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
