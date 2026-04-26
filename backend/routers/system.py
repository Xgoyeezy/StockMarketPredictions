from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock, Thread
from time import monotonic

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.config import settings
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import (
    ApiEnvelope,
    NoteCreateRequest,
    NotesBulkActionRequest,
    NotesImportRequest,
    NoteSnoozeRequest,
    NoteUpdateRequest,
    SaveWorkspaceRequest,
    TickerSymbolRequest,
    WorkspaceImportRequest,
    WorkspaceUpdateRequest,
)
from backend.services.alerts_service import get_alerts_snapshot
from backend.services.audit_service import record_audit_event
from backend.services.billing_service import enforce_entitlement_limit
from backend.services.frontend_service import get_frontend_activity, get_frontend_bootstrap, get_frontend_filters, get_frontend_workspace_snapshot, get_operations_status, get_release_info, get_release_notes, get_support_diagnostics_export
from backend.services.market_service import get_defaults, get_health
from backend.services.notes_service import advance_note, bulk_update_notes, create_note, delete_note, duplicate_note, export_notes, get_notes_agenda, get_notes_board, get_notes_calendar, get_notes_summary, get_recent_notes, import_notes, list_notes, snooze_note, update_note
from backend.services.readiness_service import get_production_readiness_snapshot
from backend.services.tenant_service import _dispatch_partner_webhook_event, _resolve_tenant_for_current_user, _resolve_user_for_current_user
from backend.services.ticker_hub_service import clear_recent_tickers, get_ticker_hub, record_recent_ticker, toggle_favorite_ticker
from backend.services.workspace_service import delete_workspace, duplicate_workspace, export_workspaces, import_workspaces, list_workspaces, save_workspace, update_workspace

router = APIRouter(tags=["system"])
_READINESS_PROBE_CACHE_TTL_SECONDS = 10.0
_readiness_probe_cache: dict[str, object] = {
    "expires_at": 0.0,
    "provider_id": None,
    "snapshot": None,
    "refreshing": False,
}
_readiness_probe_cache_lock = Lock()


def _clear_readiness_probe_cache() -> None:
    with _readiness_probe_cache_lock:
        _readiness_probe_cache.update({"expires_at": 0.0, "provider_id": None, "snapshot": None, "refreshing": False})


def _store_readiness_probe_snapshot(snapshot: dict, provider_id: int) -> None:
    _readiness_probe_cache.update(
        {
            "expires_at": monotonic() + _READINESS_PROBE_CACHE_TTL_SECONDS,
            "provider_id": provider_id,
            "snapshot": deepcopy(snapshot),
            "refreshing": False,
        }
    )


def _refresh_readiness_probe_cache(provider_id: int) -> None:
    try:
        snapshot = get_production_readiness_snapshot()
    except Exception:
        with _readiness_probe_cache_lock:
            _readiness_probe_cache["refreshing"] = False
        return
    with _readiness_probe_cache_lock:
        if id(get_production_readiness_snapshot) == provider_id:
            _store_readiness_probe_snapshot(snapshot, provider_id)
        else:
            _readiness_probe_cache["refreshing"] = False


def _get_cached_readiness_snapshot() -> dict:
    now = monotonic()
    provider_id = id(get_production_readiness_snapshot)
    start_refresh = False
    with _readiness_probe_cache_lock:
        cached_snapshot = _readiness_probe_cache.get("snapshot")
        provider_matches = _readiness_probe_cache.get("provider_id") == provider_id
        if isinstance(cached_snapshot, dict) and provider_matches:
            if float(_readiness_probe_cache.get("expires_at") or 0.0) > now:
                return deepcopy(cached_snapshot)
            if not bool(_readiness_probe_cache.get("refreshing")):
                _readiness_probe_cache["refreshing"] = True
                start_refresh = True
            stale_snapshot = deepcopy(cached_snapshot)
        else:
            stale_snapshot = None
    if stale_snapshot is not None:
        if start_refresh:
            Thread(target=_refresh_readiness_probe_cache, args=(provider_id,), name="readiness-probe-refresh", daemon=True).start()
        return stale_snapshot
    snapshot = get_production_readiness_snapshot()
    with _readiness_probe_cache_lock:
        _store_readiness_probe_snapshot(snapshot, provider_id)
    return snapshot


def _probe_response(payload: dict[str, object], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers={"Cache-Control": "no-store"})


def _support_diagnostics_filename(tenant_slug: str | None) -> str:
    slug = str(tenant_slug or "system").strip().lower() or "system"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"pilot-diagnostics-{slug}-{timestamp}.json"


def _assert_workspace_capacity(db: Session, current_user: CurrentUser, requested_total: int) -> None:
    enforce_entitlement_limit(db, current_user, "workspace_count", requested_total=requested_total, resource_label="workspaces")
    enforce_entitlement_limit(db, current_user, "saved_layouts", requested_total=requested_total, resource_label="saved layouts")


def _emit_workspace_saved_event(
    db: Session,
    current_user: CurrentUser,
    *,
    action: str,
    workspaces: list[dict[str, object]],
) -> None:
    if not workspaces:
        return

    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    payload = {
        "event": "workspace.saved",
        "action": action,
        "tenant": {
            "slug": tenant.slug,
            "name": tenant.name,
            "plan_key": tenant.plan_key,
            "status": tenant.status,
        },
        "workspace_count": len(workspaces),
        "workspaces": [
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "page": str(item.get("page") or "dashboard"),
                "updated_at": item.get("updated_at"),
                "pinned": bool(item.get("pinned", False)),
                "tags": list(item.get("tags") or []),
            }
            for item in workspaces[:10]
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    dispatch_result = _dispatch_partner_webhook_event(
        db,
        tenant=tenant,
        event_key="workspace.saved",
        payload=payload,
    )
    record_audit_event(
        db,
        event_type="workspace.saved",
        tenant=tenant,
        user=actor,
        payload={
            **payload,
            "webhook_attempts": dispatch_result["attempted"],
            "webhook_jobs_queued": dispatch_result.get("queued", 0),
            "webhook_deliveries": dispatch_result["delivered"],
        },
    )
    db.commit()


@router.get("", response_model=ApiEnvelope)
def api_root() -> ApiEnvelope:
    health_snapshot = get_health()
    readiness_snapshot = _get_cached_readiness_snapshot()
    readiness_summary = dict(readiness_snapshot.get("summary") or {})
    return envelope(
        {
            "service": health_snapshot.get("service"),
            "version": health_snapshot.get("version"),
            "status": health_snapshot.get("status"),
            "environment": settings.environment,
            "ready": bool(readiness_summary.get("ready", False)),
            "warning_count": int(readiness_summary.get("warning_checks", 0) or 0),
            "blocked_count": int(readiness_summary.get("blocked_checks", 0) or 0),
            "next_action": readiness_summary.get("next_action"),
            "links": {
                "health": "/api/health",
                "healthz": "/api/healthz",
                "readyz": "/api/readyz",
                "defaults": "/api/defaults",
                "release": "/api/release",
            },
        }
    )


@router.get("/health", response_model=ApiEnvelope)
def health() -> ApiEnvelope:
    health_snapshot = get_health()
    readiness_snapshot = _get_cached_readiness_snapshot()
    return envelope(
        {
            **health_snapshot,
            "ready": bool(readiness_snapshot.get("summary", {}).get("ready", False)),
            "readiness": readiness_snapshot,
        }
    )


@router.get("/healthz", include_in_schema=False)
def healthz() -> JSONResponse:
    try:
        health_snapshot = get_health()
    except Exception as exc:  # pragma: no cover - defensive guard for deployment probes
        return _probe_response(
            {
                "probe": "liveness",
                "status": "error",
                "service": "Stock Options Signal Dashboard",
                "version": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
            },
            status_code=503,
        )
    return _probe_response(
        {
            "probe": "liveness",
            "status": health_snapshot.get("status", "unknown"),
            "service": health_snapshot.get("service"),
            "version": health_snapshot.get("version"),
            "timestamp": health_snapshot.get("timestamp"),
        }
    )


@router.get("/readyz", include_in_schema=False)
def readyz() -> JSONResponse:
    try:
        readiness_snapshot = _get_cached_readiness_snapshot()
    except Exception as exc:  # pragma: no cover - defensive guard for deployment probes
        return _probe_response(
            {
                "probe": "readiness",
                "ready": False,
                "status": "error",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "blocked_checks": 1,
                "warning_checks": 0,
                "next_action": "Restore readiness telemetry before accepting traffic.",
                "blockers": [str(exc)],
                "warnings": [],
            },
            status_code=503,
        )
    summary = dict(readiness_snapshot.get("summary") or {})
    blocked_checks = int(summary.get("blocked_checks", 0) or 0)
    ready_for_traffic = blocked_checks == 0
    return _probe_response(
        {
            "probe": "readiness",
            "ready": ready_for_traffic,
            "status": summary.get("status", "unknown"),
            "checked_at": summary.get("checked_at"),
            "blocked_checks": blocked_checks,
            "warning_checks": int(summary.get("warning_checks", 0) or 0),
            "next_action": summary.get("next_action"),
            "blockers": list(summary.get("blockers") or []),
            "warnings": list(summary.get("warnings") or []),
        },
        status_code=200 if ready_for_traffic else 503,
    )


@router.get("/defaults", response_model=ApiEnvelope)
def defaults() -> ApiEnvelope:
    return envelope(get_defaults())


@router.get("/frontend/bootstrap", response_model=ApiEnvelope)
def frontend_bootstrap(
    consumer: str = Query(default="full"),
    current_user: CurrentUser = Depends(get_current_user),
) -> ApiEnvelope:
    return envelope(
        get_frontend_bootstrap(
            current_user.user_id,
            current_user.tenant_slug,
            current_user.tenant_name,
            current_user.tenant_brand_settings or {},
            current_user.tenant_logo_url,
            current_user.tenant_delivery_settings or {},
            consumer=consumer,
            current_user=current_user,
        )
    )


@router.get("/frontend/workspace", response_model=ApiEnvelope)
def frontend_workspace() -> ApiEnvelope:
    return envelope(get_frontend_workspace_snapshot())


@router.get('/frontend/filters', response_model=ApiEnvelope)
def frontend_filters() -> ApiEnvelope:
    return envelope(get_frontend_filters())


@router.get('/frontend/alerts', response_model=ApiEnvelope)
def frontend_alerts(
    limit: int = 12,
    min_severity: str = 'all',
    search: str = '',
    source: str = 'all',
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        get_alerts_snapshot(
            limit=limit,
            min_severity=min_severity,
            search=search,
            source=source,
            current_user=current_user,
            db=db,
        )
    )


@router.get('/release', response_model=ApiEnvelope)
def release() -> ApiEnvelope:
    return envelope(get_release_info())


@router.get('/release/notes', response_model=ApiEnvelope)
def release_notes() -> ApiEnvelope:
    return envelope(get_release_notes())


@router.get('/ops/status', response_model=ApiEnvelope)
def operations_status(current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(get_operations_status(current_user.user_id, current_user.tenant_slug, current_user=current_user))


@router.get("/ops/diagnostics", include_in_schema=False)
def operations_diagnostics(current_user: CurrentUser = Depends(get_current_user)) -> JSONResponse:
    payload = get_support_diagnostics_export(current_user.user_id, current_user.tenant_slug)
    filename = _support_diagnostics_filename(current_user.tenant_slug)
    return JSONResponse(
        payload,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get('/frontend/workspaces', response_model=ApiEnvelope)
def frontend_workspaces(
    search: str = '',
    page: str = 'all',
    pinned_only: bool = False,
    tag: str = '',
    sort_by: str = 'updated_desc',
    current_user: CurrentUser = Depends(get_current_user),
) -> ApiEnvelope:
    return envelope(list_workspaces(current_user.user_id, search=search, page=page, pinned_only=pinned_only, tag=tag, sort_by=sort_by, tenant_slug=current_user.tenant_slug))


@router.post('/frontend/workspaces', response_model=ApiEnvelope)
def frontend_save_workspace(
    payload: SaveWorkspaceRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    existing = list_workspaces(current_user.user_id, tenant_slug=current_user.tenant_slug)
    requested_total = existing["count"]
    if not any(str(item.get("name", "")).strip().lower() == payload.name.strip().lower() for item in existing["items"]):
        requested_total += 1
    _assert_workspace_capacity(db, current_user, requested_total)
    workspace = save_workspace(current_user.user_id, payload.name, payload.page, payload.payload, payload.notes, payload.pinned, payload.tags, current_user.tenant_slug)
    _emit_workspace_saved_event(db, current_user, action="saved", workspaces=[workspace])
    return envelope(workspace)


@router.put('/frontend/workspaces/{workspace_id}', response_model=ApiEnvelope)
def frontend_update_workspace(
    workspace_id: str,
    payload: WorkspaceUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    workspace = update_workspace(current_user.user_id, workspace_id, payload.model_dump(exclude_none=True), current_user.tenant_slug)
    _emit_workspace_saved_event(db, current_user, action="updated", workspaces=[workspace])
    return envelope(workspace)


@router.post('/frontend/workspaces/{workspace_id}/duplicate', response_model=ApiEnvelope)
def frontend_duplicate_workspace(
    workspace_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    existing = list_workspaces(current_user.user_id, tenant_slug=current_user.tenant_slug)
    _assert_workspace_capacity(db, current_user, existing["count"] + 1)
    workspace = duplicate_workspace(current_user.user_id, workspace_id, current_user.tenant_slug)
    _emit_workspace_saved_event(db, current_user, action="duplicated", workspaces=[workspace])
    return envelope(workspace)


@router.get('/frontend/workspaces/export', response_model=ApiEnvelope)
def frontend_export_workspaces(current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(export_workspaces(current_user.user_id, current_user.tenant_slug))


@router.post('/frontend/workspaces/import', response_model=ApiEnvelope)
def frontend_import_workspaces(
    payload: WorkspaceImportRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    existing = list_workspaces(current_user.user_id, tenant_slug=current_user.tenant_slug)
    imported_names = {
        str(item.get("name", "")).strip().lower()
        for item in payload.items
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    }
    if payload.mode == 'replace':
        requested_total = len(imported_names)
    else:
        existing_names = {str(item.get("name", "")).strip().lower() for item in existing["items"] if str(item.get("name", "")).strip()}
        requested_total = len(existing_names | imported_names)
    _assert_workspace_capacity(db, current_user, requested_total)
    result = import_workspaces(current_user.user_id, payload.items, payload.mode, current_user.tenant_slug)
    _emit_workspace_saved_event(db, current_user, action="imported", workspaces=list(result.get("items") or []))
    return envelope(result)


@router.delete('/frontend/workspaces/{workspace_id}', response_model=ApiEnvelope)
def frontend_delete_workspace(workspace_id: str, current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(delete_workspace(current_user.user_id, workspace_id, current_user.tenant_slug))


@router.get('/frontend/activity', response_model=ApiEnvelope)
def frontend_activity(
    search: str = '',
    severity: str = 'all',
    activity_type: str = Query(default='all', alias='type'),
    limit: int = 12,
    current_user: CurrentUser = Depends(get_current_user),
) -> ApiEnvelope:
    return envelope(
        get_frontend_activity(
            current_user.user_id,
            current_user.tenant_slug,
            search=search,
            severity=severity,
            activity_type=activity_type,
            limit=limit,
            current_user=current_user,
        )
    )


@router.get('/frontend/ticker-hub', response_model=ApiEnvelope)
def frontend_ticker_hub(limit_recent: int = 8) -> ApiEnvelope:
    return envelope(get_ticker_hub(limit_recent=limit_recent))


@router.post('/frontend/ticker-hub/recent', response_model=ApiEnvelope)
def frontend_record_recent_ticker(payload: TickerSymbolRequest) -> ApiEnvelope:
    return envelope(record_recent_ticker(payload.ticker))


@router.post('/frontend/ticker-hub/favorites/toggle', response_model=ApiEnvelope)
def frontend_toggle_favorite_ticker(payload: TickerSymbolRequest) -> ApiEnvelope:
    return envelope(toggle_favorite_ticker(payload.ticker))


@router.delete('/frontend/ticker-hub/recent', response_model=ApiEnvelope)
def frontend_clear_recent_ticker_hub() -> ApiEnvelope:
    return envelope(clear_recent_tickers())


@router.get('/frontend/notes', response_model=ApiEnvelope)
def frontend_notes(
    search: str = '',
    ticker: str = '',
    status: str = 'active',
    tag: str = '',
    limit: int = 100,
    priority: str = 'all',
    pinned_only: bool = False,
    sort_by: str = 'updated_desc',
    note_type: str = 'all',
    due_state: str = 'all',
    completed: str = 'all',
    owner: str = '',
    has_link: str = 'all',
    checklist_state: str = 'all',
    reminder_state: str = 'all',
    recurrence: str = 'all',
    blocked_state: str = 'all',
    progress_state: str = 'all',
) -> ApiEnvelope:
    return envelope(list_notes(search=search, ticker=ticker, status=status, tag=tag, limit=limit, priority=priority, pinned_only=pinned_only, sort_by=sort_by, note_type=note_type, due_state=due_state, completed=completed, owner=owner, has_link=has_link, checklist_state=checklist_state, reminder_state=reminder_state, recurrence=recurrence, blocked_state=blocked_state, progress_state=progress_state))




@router.get('/frontend/notes/recent', response_model=ApiEnvelope)
def frontend_recent_notes(limit: int = 8, include_archived: bool = False) -> ApiEnvelope:
    return envelope(get_recent_notes(limit=limit, include_archived=include_archived))

@router.get('/frontend/notes/summary', response_model=ApiEnvelope)
def frontend_notes_summary() -> ApiEnvelope:
    return envelope(get_notes_summary())


@router.get('/frontend/notes/board', response_model=ApiEnvelope)
def frontend_notes_board(status: str = 'active') -> ApiEnvelope:
    return envelope(get_notes_board(status=status))


@router.get('/frontend/notes/calendar', response_model=ApiEnvelope)
def frontend_notes_calendar(days: int = 14, status: str = 'active') -> ApiEnvelope:
    return envelope(get_notes_calendar(days=days, status=status))


@router.get('/frontend/notes/agenda', response_model=ApiEnvelope)
def frontend_notes_agenda(days: int = 7, status: str = 'active') -> ApiEnvelope:
    return envelope(get_notes_agenda(days=days, status=status))


@router.get('/frontend/notes/export', response_model=ApiEnvelope)
def frontend_export_notes() -> ApiEnvelope:
    return envelope(export_notes())


@router.post('/frontend/notes/import', response_model=ApiEnvelope)
def frontend_import_notes(payload: NotesImportRequest) -> ApiEnvelope:
    return envelope(import_notes(payload.items, payload.mode))


@router.post('/frontend/notes', response_model=ApiEnvelope)
def frontend_create_note(payload: NoteCreateRequest) -> ApiEnvelope:
    return envelope(create_note(payload.title, payload.body, payload.ticker, payload.tags, payload.owner, payload.source_url or '', payload.checklist, payload.related_note_ids, payload.blocked_by_ids, payload.pinned, payload.priority, payload.note_type, payload.due_at, payload.reminder_at, payload.recurrence, payload.recurrence_end_at, payload.completed, payload.estimate_minutes, payload.spent_minutes))


@router.post('/frontend/notes/{note_id}/duplicate', response_model=ApiEnvelope)
def frontend_duplicate_note(note_id: str) -> ApiEnvelope:
    return envelope(duplicate_note(note_id))


@router.post('/frontend/notes/{note_id}/advance', response_model=ApiEnvelope)
def frontend_advance_note(note_id: str) -> ApiEnvelope:
    return envelope(advance_note(note_id))


@router.put('/frontend/notes/{note_id}', response_model=ApiEnvelope)
def frontend_update_note(note_id: str, payload: NoteUpdateRequest) -> ApiEnvelope:
    return envelope(update_note(note_id, payload.model_dump(exclude_none=True)))


@router.delete('/frontend/notes/{note_id}', response_model=ApiEnvelope)
def frontend_delete_note(note_id: str) -> ApiEnvelope:
    return envelope(delete_note(note_id))



@router.post('/frontend/notes/bulk', response_model=ApiEnvelope)
def frontend_bulk_notes(payload: NotesBulkActionRequest) -> ApiEnvelope:
    return envelope(bulk_update_notes(payload.note_ids, payload.action))


@router.post('/frontend/notes/{note_id}/snooze', response_model=ApiEnvelope)
def frontend_snooze_note(note_id: str, payload: NoteSnoozeRequest) -> ApiEnvelope:
    return envelope(snooze_note(note_id, payload.minutes))
