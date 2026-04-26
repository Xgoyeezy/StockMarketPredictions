from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import SessionLocal
from backend.models.saas import Tenant
from backend.services.billing_service import (
    _build_billing_event_snapshot,
    _build_billing_recovery_snapshot,
    _build_billing_sync_snapshot,
)
from backend.services.deployment_service import get_deployment_readiness_snapshot
from backend.services.job_queue_service import get_job_metrics_snapshot, get_job_worker_status
from backend.services.tenant_service import _build_tenant_launch_ops_snapshot

_PENDING_JOB_WARNING_COUNT = 10
_PENDING_JOB_STALE_MINUTES = 15


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _make_check(
    key: str,
    label: str,
    *,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_status = str(status or "warning").strip().lower() or "warning"
    return {
        "key": key,
        "label": label,
        "status": normalized_status,
        "ready": normalized_status == "ready",
        "message": str(message or "").strip(),
        "details": details or {},
    }


def _make_gate(
    key: str,
    label: str,
    *,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_status = str(status or "warning").strip().lower() or "warning"
    return {
        "key": key,
        "label": label,
        "status": normalized_status,
        "passed": normalized_status == "ready",
        "blocking": normalized_status == "blocked",
        "message": str(message or "").strip(),
        "details": details or {},
    }


def _check_database(session: Session) -> dict[str, Any]:
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - defensive guard
        return _make_check(
            "database",
            "Database connectivity",
            status="blocked",
            message="Database connectivity probe failed.",
            details={"error": str(exc)},
        )
    return _make_check(
        "database",
        "Database connectivity",
        status="ready",
        message="Database probe succeeded.",
    )


def _check_job_worker() -> dict[str, Any]:
    snapshot = get_job_worker_status()
    if not snapshot.get("enabled"):
        return _make_check(
            "job_worker",
            "Background worker",
            status="blocked",
            message="Background worker is disabled.",
            details=snapshot,
        )
    if not snapshot.get("running"):
        return _make_check(
            "job_worker",
            "Background worker",
            status="blocked",
            message="Background worker is enabled but not running.",
            details=snapshot,
        )
    if snapshot.get("last_error_at"):
        return _make_check(
            "job_worker",
            "Background worker",
            status="warning",
            message="Background worker is running but recently reported an error.",
            details=snapshot,
        )
    return _make_check(
        "job_worker",
        "Background worker",
        status="ready",
        message="Background worker is running.",
        details=snapshot,
    )


def _check_job_backlog(session: Session) -> dict[str, Any]:
    snapshot = get_job_metrics_snapshot(session)
    summary = dict(snapshot.get("summary") or {})
    dead_letter_count = int(summary.get("dead_letter", 0) or 0)
    pending_count = int(summary.get("pending", 0) or 0)
    oldest_pending_at = _parse_iso_datetime(summary.get("oldest_pending_at"))
    stale_backlog = bool(
        oldest_pending_at
        and (_utc_now() - oldest_pending_at) > timedelta(minutes=_PENDING_JOB_STALE_MINUTES)
    )

    if dead_letter_count > 0:
        return _make_check(
            "job_backlog",
            "Async job backlog",
            status="blocked",
            message="Dead-letter jobs are present and should be investigated before pilot launch.",
            details=snapshot,
        )
    if pending_count >= _PENDING_JOB_WARNING_COUNT or stale_backlog:
        return _make_check(
            "job_backlog",
            "Async job backlog",
            status="warning",
            message="Async job backlog is growing or stale.",
            details=snapshot,
        )
    return _make_check(
        "job_backlog",
        "Async job backlog",
        status="ready",
        message="Async job backlog is healthy.",
        details=snapshot,
    )


def _check_deployment_readiness() -> dict[str, Any]:
    snapshot = get_deployment_readiness_snapshot()
    summary = dict(snapshot.get("summary") or {})
    normalized_status = str(summary.get("status") or "").strip().lower()
    if normalized_status == "ready":
        return _make_check(
            "deployment",
            "Deployment readiness",
            status="ready",
            message="Deployment artifacts, backups, and runbooks are ready.",
            details=snapshot,
        )
    if normalized_status == "warning":
        return _make_check(
            "deployment",
            "Deployment readiness",
            status="warning",
            message=str(summary.get("next_action") or "Deployment readiness has warnings."),
            details=snapshot,
        )
    return _make_check(
        "deployment",
        "Deployment readiness",
        status="blocked",
        message=str(summary.get("next_action") or "Deployment readiness has blockers."),
        details=snapshot,
    )


def _check_tenant_billing(session: Session, tenant: Tenant | None) -> dict[str, Any]:
    if tenant is None:
        return _make_check(
            "tenant_billing",
            "Tenant billing sync",
            status="skipped",
            message="Tenant context not provided for billing readiness.",
        )

    subscription = next(iter(tenant.subscriptions), None)
    event_snapshot = _build_billing_event_snapshot(session, tenant)
    recovery_snapshot = _build_billing_recovery_snapshot(session, tenant, subscription, event_snapshot)
    sync_snapshot = _build_billing_sync_snapshot(subscription, event_snapshot, recovery_snapshot)
    status = str(sync_snapshot.get("status") or "attention").strip().lower()
    if status == "healthy":
        return _make_check(
            "tenant_billing",
            "Tenant billing sync",
            status="ready",
            message=str(sync_snapshot.get("message") or "Tenant billing is healthy."),
            details=sync_snapshot,
        )
    return _make_check(
        "tenant_billing",
        "Tenant billing sync",
        status="warning",
        message=str(sync_snapshot.get("message") or "Tenant billing needs attention."),
        details=sync_snapshot,
    )


def _check_tenant_launch(tenant: Tenant | None) -> dict[str, Any]:
    if tenant is None:
        return _make_check(
            "tenant_launch",
            "Tenant launch path",
            status="skipped",
            message="Tenant context not provided for launch readiness.",
        )

    launch_ops = _build_tenant_launch_ops_snapshot(tenant)
    if not launch_ops.get("enabled"):
        return _make_check(
            "tenant_launch",
            "Tenant launch path",
            status="ready",
            message="Tenant is on the standard launch path.",
            details=launch_ops,
        )
    if launch_ops.get("launch_ready"):
        return _make_check(
            "tenant_launch",
            "Tenant launch path",
            status="ready",
            message="Tenant white-label launch path is ready.",
            details=launch_ops,
        )
    return _make_check(
        "tenant_launch",
        "Tenant launch path",
        status="blocked",
        message=str(launch_ops.get("next_action") or "Tenant launch path is blocked."),
        details=launch_ops,
    )


def _resolve_release_gate_billing(session: Session, tenant: Tenant | None) -> dict[str, Any]:
    if tenant is None:
        return _make_gate(
            "billing_sync",
            "Billing sync",
            status="skipped",
            message="Tenant context not provided for billing sync gating.",
        )

    subscription = next(iter(tenant.subscriptions), None)
    event_snapshot = _build_billing_event_snapshot(session, tenant)
    recovery_snapshot = _build_billing_recovery_snapshot(session, tenant, subscription, event_snapshot)
    sync_snapshot = _build_billing_sync_snapshot(subscription, event_snapshot, recovery_snapshot)
    sync_status = str(sync_snapshot.get("status") or "attention").strip().lower()

    if sync_status == "healthy":
        gate_status = "ready"
    elif sync_status in {"attention", "stale"}:
        gate_status = "blocked"
    else:
        gate_status = "warning"

    return _make_gate(
        "billing_sync",
        "Billing sync",
        status=gate_status,
        message=str(sync_snapshot.get("message") or "Billing sync status needs review."),
        details=sync_snapshot,
    )


def _resolve_release_gate_job_backlog(job_snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = dict(job_snapshot.get("summary") or {})
    pending_count = int(summary.get("pending", 0) or 0)
    stuck_running_count = int(summary.get("stuck_running_count", 0) or 0)
    oldest_pending_at = _parse_iso_datetime(summary.get("oldest_pending_at"))
    stale_backlog = bool(
        oldest_pending_at
        and (_utc_now() - oldest_pending_at) > timedelta(minutes=_PENDING_JOB_STALE_MINUTES)
    )

    if stuck_running_count > 0 or stale_backlog:
        status = "blocked"
        message = "Async job backlog is stale or has stuck running jobs."
    elif pending_count >= _PENDING_JOB_WARNING_COUNT:
        status = "warning"
        message = "Async job backlog is above the pilot warning threshold."
    else:
        status = "ready"
        message = "Async job backlog is within the pilot launch threshold."

    return _make_gate(
        "job_backlog",
        "Job backlog",
        status=status,
        message=message,
        details=job_snapshot,
    )


def _resolve_release_gate_dead_letters(job_snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = dict(job_snapshot.get("summary") or {})
    dead_letter_count = int(summary.get("dead_letter", 0) or 0)
    if dead_letter_count > 0:
        return _make_gate(
            "dead_letters",
            "Dead letters",
            status="blocked",
            message="Dead-letter jobs are present and should be cleared before pilot launch.",
            details=job_snapshot,
        )
    return _make_gate(
        "dead_letters",
        "Dead letters",
        status="ready",
        message="No dead-letter jobs are blocking launch.",
        details=job_snapshot,
    )


def get_release_gate_snapshot(
    *,
    db: Session | None = None,
    tenant_slug: str | None = None,
) -> dict[str, Any]:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        tenant = None
        if tenant_slug:
            tenant = (
                session.query(Tenant)
                .filter(Tenant.slug == str(tenant_slug or "").strip().lower())
                .one_or_none()
            )
        job_snapshot = get_job_metrics_snapshot(session)
        gates = [
            _resolve_release_gate_billing(session, tenant),
            _resolve_release_gate_job_backlog(job_snapshot),
            _resolve_release_gate_dead_letters(job_snapshot),
        ]
    finally:
        if owns_session:
            session.close()

    actionable_gates = [item for item in gates if item["status"] != "skipped"]
    blocked = [item for item in actionable_gates if item["status"] == "blocked"]
    warnings = [item for item in actionable_gates if item["status"] == "warning"]
    ready = [item for item in actionable_gates if item["status"] == "ready"]

    if blocked:
        status = "blocked"
    elif warnings:
        status = "warning"
    else:
        status = "ready"

    next_action = (
        blocked[0]["message"]
        if blocked
        else warnings[0]["message"]
        if warnings
        else "Release gates are clear for pilot launch."
    )

    return {
        "summary": {
            "status": status,
            "ready": status == "ready",
            "checked_at": _utc_now_iso(),
            "ready_gates": len(ready),
            "warning_gates": len(warnings),
            "blocked_gates": len(blocked),
            "total_gates": len(actionable_gates),
            "blockers": [item["message"] for item in blocked],
            "warnings": [item["message"] for item in warnings],
            "next_action": next_action,
        },
        "gates": gates,
        "tenant": {
            "slug": tenant.slug if tenant is not None else None,
            "name": tenant.name if tenant is not None else None,
            "status": tenant.status if tenant is not None else None,
            "plan_key": tenant.plan_key if tenant is not None else None,
        },
    }


def get_production_readiness_snapshot(
    *,
    db: Session | None = None,
    tenant_slug: str | None = None,
) -> dict[str, Any]:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        tenant = None
        if tenant_slug:
            tenant = (
                session.query(Tenant)
                .filter(Tenant.slug == str(tenant_slug or "").strip().lower())
                .one_or_none()
            )

        checks = [
            _check_database(session),
            _check_job_worker(),
            _check_job_backlog(session),
            _check_deployment_readiness(),
            _check_tenant_billing(session, tenant),
            _check_tenant_launch(tenant),
        ]
    finally:
        if owns_session:
            session.close()

    actionable_checks = [item for item in checks if item["status"] != "skipped"]
    blocked_checks = [item for item in actionable_checks if item["status"] == "blocked"]
    warning_checks = [item for item in actionable_checks if item["status"] == "warning"]
    ready_checks = [item for item in actionable_checks if item["status"] == "ready"]

    if blocked_checks:
        status = "blocked"
    elif warning_checks:
        status = "warning"
    else:
        status = "ready"

    blockers = [item["message"] for item in blocked_checks]
    warnings = [item["message"] for item in warning_checks]
    ready = status == "ready"
    total_checks = len(actionable_checks)
    readiness_percent = round((len(ready_checks) / max(total_checks, 1)) * 100, 1)
    next_action = blockers[0] if blockers else warnings[0] if warnings else "Pilot production checks are ready."

    return {
        "summary": {
            "status": status,
            "ready": ready,
            "checked_at": _utc_now_iso(),
            "ready_checks": len(ready_checks),
            "warning_checks": len(warning_checks),
            "blocked_checks": len(blocked_checks),
            "total_checks": total_checks,
            "readiness_percent": readiness_percent,
            "blockers": blockers,
            "warnings": warnings,
            "next_action": next_action,
        },
        "checks": checks,
        "tenant": {
            "slug": tenant.slug if tenant is not None else None,
            "name": tenant.name if tenant is not None else None,
            "status": tenant.status if tenant is not None else None,
            "plan_key": tenant.plan_key if tenant is not None else None,
        },
    }
