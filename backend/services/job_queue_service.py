from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import SessionLocal
from backend.models.saas import AsyncJob, Tenant

logger = logging.getLogger("stock_signals.jobs")

_WORKER_LOCK = Lock()
_WORKER_STOP = Event()
_WORKER_THREAD: Thread | None = None
_WORKER_LAST_LOOP_AT: datetime | None = None
_WORKER_LAST_SUCCESS_AT: datetime | None = None
_WORKER_LAST_ERROR_AT: datetime | None = None
_WORKER_LAST_ERROR_MESSAGE: str | None = None

_JOB_TYPE_LABELS = {
    "ai_desk_autonomous_cycle": "AI desk autonomous cycle",
    "partner_webhook_delivery": "Partner webhook delivery",
    "billing_reconciliation": "Billing reconciliation",
}
_RUNNING_JOB_STALE_MINUTES = 10
_SQLITE_LOCK_RETRY_ATTEMPTS = 3
_SQLITE_LOCK_RETRY_DELAY_SECONDS = 0.15


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _normalize_job_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError("Job type is required.")
    return normalized


def _compute_retry_delay_seconds(attempt_count: int) -> int:
    exponent = max(int(attempt_count or 1) - 1, 0)
    delay = settings.job_retry_base_seconds * (2**exponent)
    return max(1, min(int(delay), settings.job_retry_max_seconds))


def _classify_retryable_status(status_code: int | None) -> bool:
    if status_code in {408, 409, 425, 429}:
        return True
    if status_code is None:
        return True
    if int(status_code) <= 0:
        return True
    return int(status_code) >= 500


def _is_sqlite_lock_error(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


def enqueue_job(
    db: Session,
    *,
    job_type: str,
    payload: dict[str, Any],
    tenant_id: str | None = None,
    max_attempts: int | None = None,
    available_at: datetime | None = None,
) -> AsyncJob:
    normalized_job_type = _normalize_job_type(job_type)
    normalized_payload = dict(payload or {})
    normalized_max_attempts = max(1, int(max_attempts or settings.job_max_attempts))
    normalized_available_at = available_at or _utc_now()
    last_error: OperationalError | None = None
    for attempt in range(1, _SQLITE_LOCK_RETRY_ATTEMPTS + 1):
        job = AsyncJob(
            tenant_id=tenant_id,
            job_type=normalized_job_type,
            status="queued",
            payload_json=dict(normalized_payload),
            result_json={},
            attempt_count=0,
            max_attempts=normalized_max_attempts,
            available_at=normalized_available_at,
        )
        db.add(job)
        try:
            db.flush()
            return job
        except OperationalError as exc:
            db.rollback()
            if not _is_sqlite_lock_error(exc) or attempt >= _SQLITE_LOCK_RETRY_ATTEMPTS:
                raise
            last_error = exc
            logger.warning(
                "Retrying async job enqueue after SQLite lock (attempt %s/%s).",
                attempt,
                _SQLITE_LOCK_RETRY_ATTEMPTS,
            )
            time.sleep(_SQLITE_LOCK_RETRY_DELAY_SECONDS * attempt)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to enqueue async job.")


def enqueue_partner_webhook_delivery(
    db: Session,
    *,
    tenant: Tenant,
    webhook_id: str,
    event_key: str,
    payload: dict[str, Any],
    max_attempts: int | None = None,
) -> AsyncJob:
    job_payload = {
        "tenant_id": tenant.id,
        "tenant_slug": tenant.slug,
        "webhook_id": str(webhook_id or "").strip(),
        "event_key": str(event_key or "").strip().lower(),
        "payload": dict(payload or {}),
    }
    return enqueue_job(
        db,
        tenant_id=tenant.id,
        job_type="partner_webhook_delivery",
        payload=job_payload,
        max_attempts=max_attempts or settings.partner_webhook_max_attempts,
    )


def enqueue_billing_reconciliation(
    db: Session,
    *,
    tenant: Tenant,
    action: str,
    failed_event_id: str | None = None,
    max_attempts: int | None = None,
) -> AsyncJob:
    job_payload = {
        "tenant_id": tenant.id,
        "tenant_slug": tenant.slug,
        "action": str(action or "reconcile").strip().lower(),
        "failed_event_id": str(failed_event_id or "").strip() or None,
    }
    return enqueue_job(
        db,
        tenant_id=tenant.id,
        job_type="billing_reconciliation",
        payload=job_payload,
        max_attempts=max_attempts or settings.billing_recovery_job_max_attempts,
    )


def _claim_due_jobs(db: Session, *, limit: int) -> list[str]:
    now = _utc_now()
    rows = list(
        db.execute(
            select(AsyncJob)
            .where(AsyncJob.status.in_(("queued", "retrying")))
            .where(AsyncJob.available_at <= now)
            .order_by(AsyncJob.available_at.asc(), AsyncJob.created_at.asc())
            .limit(max(1, int(limit))),
        ).scalars()
    )
    claimed_ids: list[str] = []
    for row in rows:
        row.status = "running"
        row.started_at = now
        row.finished_at = None
        row.attempt_count = int(row.attempt_count or 0) + 1
        claimed_ids.append(row.id)
    if claimed_ids:
        db.flush()
        db.commit()
    return claimed_ids


def _mark_job_success(job: AsyncJob, *, result: dict[str, Any] | None = None, http_status: int | None = None) -> None:
    now = _utc_now()
    job.status = "succeeded"
    job.finished_at = now
    job.error_message = None
    job.available_at = now
    job.result_json = dict(result or {})
    job.last_http_status = int(http_status) if http_status is not None else None


def _mark_job_retry(
    job: AsyncJob,
    *,
    error_message: str | None = None,
    result: dict[str, Any] | None = None,
    http_status: int | None = None,
) -> None:
    now = _utc_now()
    delay_seconds = _compute_retry_delay_seconds(job.attempt_count)
    job.status = "retrying"
    job.finished_at = None
    job.error_message = str(error_message or "").strip() or None
    job.result_json = dict(result or {})
    job.last_http_status = int(http_status) if http_status is not None else None
    job.available_at = now + timedelta(seconds=delay_seconds)


def _mark_job_dead_letter(
    job: AsyncJob,
    *,
    error_message: str | None = None,
    result: dict[str, Any] | None = None,
    http_status: int | None = None,
) -> None:
    now = _utc_now()
    job.status = "dead_letter"
    job.finished_at = now
    job.error_message = str(error_message or "").strip() or None
    job.result_json = dict(result or {})
    job.last_http_status = int(http_status) if http_status is not None else None
    job.available_at = now


def _mark_job_handler_missing(job: AsyncJob) -> None:
    _mark_job_dead_letter(job, error_message=f"No job handler registered for {job.job_type}.")


def recover_stale_running_jobs(
    db: Session,
    *,
    stale_after_minutes: int = _RUNNING_JOB_STALE_MINUTES,
) -> dict[str, int]:
    now = _utc_now()
    stale_cutoff = now - timedelta(minutes=max(1, int(stale_after_minutes)))
    stale_jobs = list(
        db.execute(
            select(AsyncJob)
            .where(AsyncJob.status == "running")
            .where(AsyncJob.started_at.is_not(None))
            .where(AsyncJob.started_at <= stale_cutoff)
            .order_by(AsyncJob.started_at.asc(), AsyncJob.created_at.asc()),
        ).scalars()
    )
    recovered = 0
    dead_lettered = 0
    for job in stale_jobs:
        status_note = f"Recovered stale running job after {stale_after_minutes}m without completion."
        if int(job.attempt_count or 0) < int(job.max_attempts or settings.job_max_attempts):
            job.status = "retrying"
            job.started_at = None
            job.finished_at = now
            job.available_at = now
            job.error_message = status_note
            recovered += 1
        else:
            _mark_job_dead_letter(job, error_message=status_note, result=dict(job.result_json or {}), http_status=job.last_http_status)
            dead_lettered += 1
    if stale_jobs:
        db.commit()
    return {
        "recovered": recovered,
        "dead_lettered": dead_lettered,
    }


def _process_partner_webhook_delivery_job(db: Session, job: AsyncJob) -> dict[str, Any]:
    from backend.services import tenant_service

    payload = dict(job.payload_json or {})
    tenant_id = str(payload.get("tenant_id") or job.tenant_id or "").strip()
    webhook_id = str(payload.get("webhook_id") or "").strip()
    event_key = str(payload.get("event_key") or "").strip().lower()
    event_payload = dict(payload.get("payload") or {})
    tenant = db.get(Tenant, tenant_id) if tenant_id else None
    if tenant is None:
        return {
            "ok": False,
            "retryable": False,
            "error": "Tenant for queued webhook job no longer exists.",
            "result": {"event_key": event_key, "webhook_id": webhook_id, "status": "dropped"},
        }

    rows = tenant_service._read_partner_webhooks_state(tenant)
    target = next((row for row in rows if row.get("id") == webhook_id), None)
    if target is None:
        return {
            "ok": False,
            "retryable": False,
            "error": "Webhook endpoint no longer exists for this tenant.",
            "result": {"event_key": event_key, "webhook_id": webhook_id, "status": "dropped"},
        }
    if str(target.get("status") or "active").strip().lower() != "active":
        return {
            "ok": True,
            "result": {
                "event_key": event_key,
                "webhook_id": webhook_id,
                "status": "skipped",
                "reason": "Webhook is paused.",
            },
        }

    deliveries = tenant_service._read_webhook_delivery_log(tenant)
    delivery_entry = tenant_service._deliver_partner_webhook_request(
        tenant=tenant,
        target=target,
        event_key=event_key,
        payload=event_payload,
        deliveries=deliveries,
        timeout_seconds=settings.partner_webhook_timeout_seconds,
    )
    tenant_service._write_partner_webhooks_state(tenant, rows)
    tenant_service._write_webhook_delivery_log(tenant, deliveries)
    db.flush()
    status_code = delivery_entry.get("status_code")
    if delivery_entry.get("status") == "success":
        return {
            "ok": True,
            "http_status": status_code,
            "result": delivery_entry,
        }
    return {
        "ok": False,
        "retryable": _classify_retryable_status(status_code),
        "error": delivery_entry.get("error"),
        "http_status": status_code,
        "result": delivery_entry,
    }


def _process_billing_reconciliation_job(db: Session, job: AsyncJob) -> dict[str, Any]:
    from backend.services import billing_service
    from backend.services.exceptions import NotFoundError, ValidationError

    payload = dict(job.payload_json or {})
    try:
        result = billing_service.process_billing_recovery_job(
            db,
            tenant_id=str(payload.get("tenant_id") or job.tenant_id or "").strip(),
            action=str(payload.get("action") or "reconcile").strip().lower(),
            failed_event_id=str(payload.get("failed_event_id") or "").strip() or None,
            job_id=job.id,
        )
        return {
            "ok": True,
            "result": result,
        }
    except (NotFoundError, ValidationError) as exc:
        return {
            "ok": False,
            "retryable": False,
            "error": str(exc),
            "result": {
                "action": str(payload.get("action") or "reconcile").strip().lower(),
                "failed_event_id": str(payload.get("failed_event_id") or "").strip() or None,
            },
        }


def _process_ai_desk_autonomous_cycle_job(db: Session, job: AsyncJob) -> dict[str, Any]:
    from backend.services.ai_desk_manager_service import process_ai_autonomous_cycle_job

    return process_ai_autonomous_cycle_job(db, job)


def _run_job_handler(db: Session, job: AsyncJob) -> dict[str, Any]:
    if job.job_type == "ai_desk_autonomous_cycle":
        return _process_ai_desk_autonomous_cycle_job(db, job)
    if job.job_type == "partner_webhook_delivery":
        return _process_partner_webhook_delivery_job(db, job)
    if job.job_type == "billing_reconciliation":
        return _process_billing_reconciliation_job(db, job)
    _mark_job_handler_missing(job)
    return {
        "ok": False,
        "retryable": False,
        "error": f"No job handler registered for {job.job_type}.",
        "result": {},
    }


def run_due_jobs(db: Session, *, limit: int | None = None) -> dict[str, Any]:
    recovery = recover_stale_running_jobs(db)
    claimed_ids = _claim_due_jobs(db, limit=max(1, int(limit or settings.job_worker_batch_size)))
    summary = {
        "claimed": len(claimed_ids),
        "succeeded": 0,
        "retried": 0,
        "dead_letter": 0,
        "recovered_stale": int(recovery.get("recovered", 0) or 0),
        "dead_lettered_stale": int(recovery.get("dead_lettered", 0) or 0),
    }
    for job_id in claimed_ids:
        job = db.get(AsyncJob, job_id)
        if job is None:
            continue
        try:
            outcome = _run_job_handler(db, job)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Async job %s failed with an unexpected error.", job.id)
            outcome = {"ok": False, "retryable": True, "error": str(exc), "result": {}}

        if outcome.get("ok"):
            _mark_job_success(job, result=outcome.get("result"), http_status=outcome.get("http_status"))
            summary["succeeded"] += 1
        else:
            retryable = bool(outcome.get("retryable", True))
            if retryable and int(job.attempt_count or 0) < int(job.max_attempts or settings.job_max_attempts):
                _mark_job_retry(
                    job,
                    error_message=outcome.get("error"),
                    result=outcome.get("result"),
                    http_status=outcome.get("http_status"),
                )
                summary["retried"] += 1
            else:
                _mark_job_dead_letter(
                    job,
                    error_message=outcome.get("error"),
                    result=outcome.get("result"),
                    http_status=outcome.get("http_status"),
                )
                summary["dead_letter"] += 1
        db.commit()
    return summary


def drain_due_jobs(*, limit: int | None = None) -> dict[str, Any]:
    with SessionLocal() as db:
        return run_due_jobs(db, limit=limit)


def get_job_metrics_snapshot(
    db: Session | None = None,
    *,
    tenant_id: str | None = None,
    job_type: str | None = None,
) -> dict[str, Any]:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        statement = select(AsyncJob).order_by(AsyncJob.updated_at.desc(), AsyncJob.created_at.desc())
        if tenant_id:
            statement = statement.where(AsyncJob.tenant_id == tenant_id)
        if job_type:
            statement = statement.where(AsyncJob.job_type == _normalize_job_type(job_type))
        jobs = list(session.execute(statement).scalars())
    finally:
        if owns_session:
            session.close()

    status_counts = Counter(str(item.status or "queued") for item in jobs)
    type_counts = Counter(str(item.job_type or "unknown") for item in jobs)
    pending_jobs = [item for item in jobs if item.status in {"queued", "retrying", "running"}]
    running_jobs = [item for item in jobs if item.status == "running"]
    dead_letters = [item for item in jobs if item.status == "dead_letter"]
    recent_jobs = jobs[:12]
    recent_failures = [item for item in jobs if item.status in {"retrying", "dead_letter"}][:8]
    stale_cutoff = _utc_now() - timedelta(minutes=_RUNNING_JOB_STALE_MINUTES)
    stuck_running_jobs = [
        item
        for item in running_jobs
        if _normalize_datetime(item.started_at) is not None
        and _normalize_datetime(item.started_at) <= stale_cutoff
    ]

    return {
        "summary": {
            "count": len(jobs),
            "queued": status_counts.get("queued", 0),
            "retrying": status_counts.get("retrying", 0),
            "running": status_counts.get("running", 0),
            "succeeded": status_counts.get("succeeded", 0),
            "dead_letter": status_counts.get("dead_letter", 0),
            "pending": len(pending_jobs),
            "stuck_running_count": len(stuck_running_jobs),
            "oldest_pending_at": min(
                (
                    item.available_at.isoformat()
                    for item in pending_jobs
                    if item.available_at is not None
                ),
                default=None,
            ),
            "oldest_running_at": min(
                (
                    item.started_at.isoformat()
                    for item in running_jobs
                    if item.started_at is not None
                ),
                default=None,
            ),
            "running_stale_after_minutes": _RUNNING_JOB_STALE_MINUTES,
            "recent_failure_count": len(recent_failures),
            "last_finished_at": max(
                (
                    item.finished_at.isoformat()
                    for item in jobs
                    if item.finished_at is not None
                ),
                default=None,
            ),
        },
        "job_types": [
            {
                "key": key,
                "label": _JOB_TYPE_LABELS.get(key, key.replace("_", " ").title()),
                "count": count,
            }
            for key, count in type_counts.most_common()
        ],
        "recent_jobs": [
            {
                "id": item.id,
                "job_type": item.job_type,
                "job_label": _JOB_TYPE_LABELS.get(item.job_type, item.job_type.replace("_", " ").title()),
                "status": item.status,
                "attempt_count": item.attempt_count,
                "max_attempts": item.max_attempts,
                "available_at": item.available_at.isoformat() if item.available_at else None,
                "started_at": item.started_at.isoformat() if item.started_at else None,
                "finished_at": item.finished_at.isoformat() if item.finished_at else None,
                "error_message": item.error_message,
                "last_http_status": item.last_http_status,
                "tenant_id": item.tenant_id,
            }
            for item in recent_jobs
        ],
        "recent_failures": [
            {
                "id": item.id,
                "job_type": item.job_type,
                "status": item.status,
                "attempt_count": item.attempt_count,
                "max_attempts": item.max_attempts,
                "available_at": item.available_at.isoformat() if item.available_at else None,
                "finished_at": item.finished_at.isoformat() if item.finished_at else None,
                "error_message": item.error_message,
                "last_http_status": item.last_http_status,
            }
            for item in recent_failures
        ],
        "stuck_running": [
            {
                "id": item.id,
                "job_type": item.job_type,
                "attempt_count": item.attempt_count,
                "max_attempts": item.max_attempts,
                "started_at": item.started_at.isoformat() if item.started_at else None,
                "available_at": item.available_at.isoformat() if item.available_at else None,
                "tenant_id": item.tenant_id,
            }
            for item in stuck_running_jobs[:8]
        ],
        "dead_letters": [
            {
                "id": item.id,
                "job_type": item.job_type,
                "attempt_count": item.attempt_count,
                "max_attempts": item.max_attempts,
                "finished_at": item.finished_at.isoformat() if item.finished_at else None,
                "error_message": item.error_message,
                "last_http_status": item.last_http_status,
            }
            for item in dead_letters[:8]
        ],
    }


def get_job_worker_status() -> dict[str, Any]:
    with _WORKER_LOCK:
        thread = _WORKER_THREAD
        running = bool(thread and thread.is_alive())
        return {
            "enabled": bool(settings.job_worker_enabled),
            "running": running,
            "thread_name": thread.name if thread else None,
            "stop_requested": bool(_WORKER_STOP.is_set()),
            "poll_seconds": int(settings.job_worker_poll_seconds),
            "batch_size": int(settings.job_worker_batch_size),
            "last_loop_at": _WORKER_LAST_LOOP_AT.isoformat() if _WORKER_LAST_LOOP_AT else None,
            "last_success_at": _WORKER_LAST_SUCCESS_AT.isoformat() if _WORKER_LAST_SUCCESS_AT else None,
            "last_error_at": _WORKER_LAST_ERROR_AT.isoformat() if _WORKER_LAST_ERROR_AT else None,
            "last_error_message": _WORKER_LAST_ERROR_MESSAGE,
        }


def _worker_loop() -> None:
    global _WORKER_LAST_LOOP_AT, _WORKER_LAST_SUCCESS_AT, _WORKER_LAST_ERROR_AT, _WORKER_LAST_ERROR_MESSAGE
    while not _WORKER_STOP.is_set():
        _WORKER_LAST_LOOP_AT = _utc_now()
        try:
            with SessionLocal() as db:
                run_due_jobs(db, limit=settings.job_worker_batch_size)
            with SessionLocal() as db:
                from backend.services.trade_automation_service import (
                    run_enabled_trade_automation_cycles,
                    run_trade_automation_limited_live_cap_expansion_canary_reviews,
                    run_trade_automation_limited_live_cap_expansion_reports,
                    run_trade_automation_limited_live_broker_reconciliations,
                    run_trade_automation_limited_live_daily_closeouts,
                    run_trade_automation_limited_live_higher_cap_reports,
                    run_trade_automation_limited_live_next_tier_cap_canary_reviews,
                    run_trade_automation_limited_live_next_tier_cap_reports,
                    run_trade_automation_limited_live_rollout_canary_reviews,
                    run_trade_automation_live_pilot_expansion_canary_reviews,
                    run_trade_automation_live_pilot_canary_reviews,
                    run_trade_automation_live_pilot_promotion_reports,
                    run_trade_automation_live_pilot_window_canary_reviews,
                    run_trade_automation_paper_broker_reconciliations,
                    run_trade_automation_paper_canary_reviews,
                    run_trade_automation_paper_order_lifecycle_canary_reviews,
                    run_trade_automation_daily_ai_reviews,
                )

                run_enabled_trade_automation_cycles(db, limit=settings.job_worker_batch_size)
                run_trade_automation_daily_ai_reviews(db, limit=settings.job_worker_batch_size)
                run_trade_automation_paper_broker_reconciliations(db, limit=settings.job_worker_batch_size)
                run_trade_automation_paper_order_lifecycle_canary_reviews(db, limit=settings.job_worker_batch_size)
                run_trade_automation_paper_canary_reviews(db, limit=settings.job_worker_batch_size)
                run_trade_automation_live_pilot_canary_reviews(db, limit=settings.job_worker_batch_size)
                run_trade_automation_live_pilot_expansion_canary_reviews(db, limit=settings.job_worker_batch_size)
                run_trade_automation_live_pilot_window_canary_reviews(db, limit=settings.job_worker_batch_size)
                run_trade_automation_live_pilot_promotion_reports(db, limit=settings.job_worker_batch_size)
                run_trade_automation_limited_live_rollout_canary_reviews(db, limit=settings.job_worker_batch_size)
                run_trade_automation_limited_live_cap_expansion_reports(db, limit=settings.job_worker_batch_size)
                run_trade_automation_limited_live_cap_expansion_canary_reviews(db, limit=settings.job_worker_batch_size)
                run_trade_automation_limited_live_next_tier_cap_reports(db, limit=settings.job_worker_batch_size)
                run_trade_automation_limited_live_broker_reconciliations(db, limit=settings.job_worker_batch_size)
                run_trade_automation_limited_live_daily_closeouts(db, limit=settings.job_worker_batch_size)
                run_trade_automation_limited_live_next_tier_cap_canary_reviews(db, limit=settings.job_worker_batch_size)
                run_trade_automation_limited_live_higher_cap_reports(db, limit=settings.job_worker_batch_size)
            _WORKER_LAST_SUCCESS_AT = _utc_now()
            _WORKER_LAST_ERROR_AT = None
            _WORKER_LAST_ERROR_MESSAGE = None
        except Exception:  # pragma: no cover - defensive worker logging
            logger.exception("Async job worker loop failed.")
            _WORKER_LAST_ERROR_AT = _utc_now()
            _WORKER_LAST_ERROR_MESSAGE = "Async job worker loop failed."
        _WORKER_STOP.wait(max(1, settings.job_worker_poll_seconds))


def start_job_worker() -> None:
    global _WORKER_THREAD
    if not settings.job_worker_enabled:
        return
    with _WORKER_LOCK:
        if _WORKER_THREAD and _WORKER_THREAD.is_alive():
            return
        _WORKER_STOP.clear()
        _WORKER_THREAD = Thread(target=_worker_loop, name="stock-signals-job-worker", daemon=True)
        _WORKER_THREAD.start()


def stop_job_worker() -> None:
    global _WORKER_THREAD
    with _WORKER_LOCK:
        if _WORKER_THREAD is None:
            return
        _WORKER_STOP.set()
        _WORKER_THREAD.join(timeout=max(1, settings.job_worker_poll_seconds) + 1)
        _WORKER_THREAD = None
