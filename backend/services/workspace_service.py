from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.config import settings
from backend.services.desk_service import normalize_desk_slug
from backend.services.exceptions import NotFoundError, ValidationServiceError
from backend.services.storage_utils import read_json_file, write_json_file

_LEGACY_WORKSPACE_FILE = Path(__file__).resolve().parent.parent / 'data' / 'saved_workspaces.json'
_WORKSPACE_FILE = Path(settings.storage_dir) / 'saved_workspaces.json'
_DEFAULTS: dict[str, Any] = {'workspaces': []}
_ALLOWED_PAGES = {'dashboard', 'watchlist', 'trades', 'journal', 'portfolio', 'alerts', 'settings', 'compare', 'activity', 'workspaces'}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_store() -> None:
    _WORKSPACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _WORKSPACE_FILE.exists():
        write_json_file(_WORKSPACE_FILE, _DEFAULTS)


def _normalize_tags(tags: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in tags or []:
        cleaned = str(value or '').strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized[:12]


def _normalize_user_id(value: Any) -> str:
    cleaned = str(value or settings.demo_user_id).strip()
    return cleaned or settings.demo_user_id


def _normalize_tenant_slug(value: Any) -> str:
    return normalize_desk_slug(value or settings.demo_tenant_slug)


def _normalize_workspace(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row or {})
    payload['id'] = str(payload.get('id') or str(uuid.uuid4()))
    payload['user_id'] = _normalize_user_id(payload.get('user_id'))
    payload['tenant_slug'] = _normalize_tenant_slug(payload.get('tenant_slug'))
    payload['name'] = str(payload.get('name') or '').strip()
    payload['page'] = str(payload.get('page') or 'dashboard').strip().lower()
    if payload['page'] not in _ALLOWED_PAGES:
        payload['page'] = 'dashboard'
    payload['payload'] = payload.get('payload') or {}
    if not isinstance(payload['payload'], dict):
        payload['payload'] = {}
    payload['notes'] = str(payload.get('notes') or '').strip()
    payload['created_at'] = str(payload.get('created_at') or _utc_now())
    payload['updated_at'] = str(payload.get('updated_at') or payload['created_at'])
    payload['pinned'] = bool(payload.get('pinned', False))
    payload['tags'] = _normalize_tags(payload.get('tags'))
    return payload


def _read_store() -> dict[str, Any]:
    source = _WORKSPACE_FILE if _WORKSPACE_FILE.exists() or not _LEGACY_WORKSPACE_FILE.exists() else _LEGACY_WORKSPACE_FILE
    data = read_json_file(source, dict(_DEFAULTS))
    if not isinstance(data, dict):
        data = dict(_DEFAULTS)
    rows = data.get('workspaces', [])
    if not isinstance(rows, list):
        rows = []
    data['workspaces'] = [_normalize_workspace(row) for row in rows if isinstance(row, dict)]
    return data


def _write_store(payload: dict[str, Any]) -> None:
    _ensure_store()
    write_json_file(_WORKSPACE_FILE, payload)


def list_workspaces(
    user_id: str,
    search: str = '',
    page: str = 'all',
    pinned_only: bool = False,
    tag: str = '',
    sort_by: str = 'updated_desc',
    tenant_slug: str | None = None,
) -> dict[str, Any]:
    store = _read_store()
    scoped_user_id = _normalize_user_id(user_id)
    scoped_tenant_slug = _normalize_tenant_slug(tenant_slug)
    rows = [
        row for row in store['workspaces']
        if _normalize_user_id(row.get('user_id')) == scoped_user_id
        and _normalize_tenant_slug(row.get('tenant_slug')) == scoped_tenant_slug
    ]
    needle = str(search or '').strip().lower()
    page_filter = str(page or 'all').strip().lower()
    tag_filter = str(tag or '').strip().lower()
    if needle:
        rows = [row for row in rows if needle in str(row.get('name', '')).lower() or needle in str(row.get('notes', '')).lower() or any(needle in t for t in row.get('tags', []))]
    if page_filter and page_filter != 'all':
        rows = [row for row in rows if str(row.get('page', '')).lower() == page_filter]
    if pinned_only:
        rows = [row for row in rows if bool(row.get('pinned'))]
    if tag_filter:
        rows = [row for row in rows if tag_filter in row.get('tags', [])]

    if sort_by == 'name_asc':
        rows.sort(key=lambda row: (str(row.get('name', '')).lower(), str(row.get('updated_at', ''))))
    elif sort_by == 'page_asc':
        rows.sort(key=lambda row: (str(row.get('page', '')).lower(), str(row.get('name', '')).lower()))
    else:
        rows.sort(key=lambda row: (not bool(row.get('pinned')), str(row.get('updated_at', '')), str(row.get('name', '')).lower()), reverse=True)

    tag_counts: dict[str, int] = {}
    for row in rows:
        for item in row.get('tags', []):
            tag_counts[item] = tag_counts.get(item, 0) + 1
    return {'items': rows, 'count': len(rows), 'tag_counts': tag_counts}


def save_workspace(
    user_id: str,
    name: str,
    page: str,
    payload: dict[str, Any] | None = None,
    notes: str = '',
    pinned: bool = False,
    tags: list[str] | None = None,
    tenant_slug: str | None = None,
) -> dict[str, Any]:
    scoped_user_id = _normalize_user_id(user_id)
    scoped_tenant_slug = _normalize_tenant_slug(tenant_slug)
    clean_name = str(name or '').strip()
    clean_page = str(page or 'dashboard').strip().lower()
    if not clean_name:
        raise ValidationServiceError('Workspace name is required.')
    if len(clean_name) > 80:
        raise ValidationServiceError('Workspace name must be 80 characters or fewer.')
    if clean_page not in _ALLOWED_PAGES:
        raise ValidationServiceError('Unsupported workspace page.')

    store = _read_store()
    timestamp = _utc_now()
    existing = next((
        row for row in store['workspaces']
        if _normalize_user_id(row.get('user_id')) == scoped_user_id
        and _normalize_tenant_slug(row.get('tenant_slug')) == scoped_tenant_slug
        and str(row.get('name', '')).lower() == clean_name.lower()
    ), None)
    if existing is None:
        existing = {
            'id': str(uuid.uuid4()),
            'user_id': scoped_user_id,
            'tenant_slug': scoped_tenant_slug,
            'name': clean_name,
            'created_at': timestamp,
        }
        store['workspaces'].append(existing)
    existing.update({
        'user_id': scoped_user_id,
        'tenant_slug': scoped_tenant_slug,
        'name': clean_name,
        'page': clean_page,
        'payload': payload or {},
        'notes': str(notes or '').strip(),
        'updated_at': timestamp,
        'pinned': bool(pinned),
        'tags': _normalize_tags(tags),
    })
    normalized = _normalize_workspace(existing)
    for idx, row in enumerate(store['workspaces']):
        if str(row.get('id')) == normalized['id']:
            store['workspaces'][idx] = normalized
            break
    _write_store(store)
    return normalized


def update_workspace(user_id: str, workspace_id: str, updates: dict[str, Any], tenant_slug: str | None = None) -> dict[str, Any]:
    store = _read_store()
    scoped_user_id = _normalize_user_id(user_id)
    scoped_tenant_slug = _normalize_tenant_slug(tenant_slug)
    existing = next((
        row for row in store['workspaces']
        if _normalize_user_id(row.get('user_id')) == scoped_user_id
        and _normalize_tenant_slug(row.get('tenant_slug')) == scoped_tenant_slug
        and str(row.get('id')) == str(workspace_id)
    ), None)
    if existing is None:
        raise NotFoundError('Workspace not found.')
    if 'name' in updates:
        clean_name = str(updates.get('name') or '').strip()
        if not clean_name:
            raise ValidationServiceError('Workspace name is required.')
        existing['name'] = clean_name
    if 'page' in updates:
        clean_page = str(updates.get('page') or '').strip().lower()
        if clean_page not in _ALLOWED_PAGES:
            raise ValidationServiceError('Unsupported workspace page.')
        existing['page'] = clean_page
    if 'payload' in updates and isinstance(updates.get('payload'), dict):
        existing['payload'] = updates['payload']
    if 'notes' in updates:
        existing['notes'] = str(updates.get('notes') or '').strip()
    if 'pinned' in updates:
        existing['pinned'] = bool(updates.get('pinned'))
    if 'tags' in updates:
        existing['tags'] = _normalize_tags(updates.get('tags'))
    existing['updated_at'] = _utc_now()
    normalized = _normalize_workspace(existing)
    for idx, row in enumerate(store['workspaces']):
        if str(row.get('id')) == normalized['id']:
            store['workspaces'][idx] = normalized
            break
    _write_store(store)
    return normalized


def duplicate_workspace(user_id: str, workspace_id: str, tenant_slug: str | None = None) -> dict[str, Any]:
    store = _read_store()
    scoped_user_id = _normalize_user_id(user_id)
    scoped_tenant_slug = _normalize_tenant_slug(tenant_slug)
    existing = next((
        row for row in store['workspaces']
        if _normalize_user_id(row.get('user_id')) == scoped_user_id
        and _normalize_tenant_slug(row.get('tenant_slug')) == scoped_tenant_slug
        and str(row.get('id')) == str(workspace_id)
    ), None)
    if existing is None:
        raise NotFoundError('Workspace not found.')
    copy_name = f"{existing.get('name', 'Workspace')} Copy"
    counter = 2
    existing_names = {
        str(row.get('name', '')).lower()
        for row in store['workspaces']
        if _normalize_user_id(row.get('user_id')) == scoped_user_id
        and _normalize_tenant_slug(row.get('tenant_slug')) == scoped_tenant_slug
    }
    while copy_name.lower() in existing_names:
        copy_name = f"{existing.get('name', 'Workspace')} Copy {counter}"
        counter += 1
    duplicate = _normalize_workspace({
        **existing,
        'id': str(uuid.uuid4()),
        'user_id': scoped_user_id,
        'tenant_slug': scoped_tenant_slug,
        'name': copy_name,
        'created_at': _utc_now(),
        'updated_at': _utc_now(),
    })
    store['workspaces'].append(duplicate)
    _write_store(store)
    return duplicate


def export_workspaces(user_id: str, tenant_slug: str | None = None) -> dict[str, Any]:
    scoped_rows = list_workspaces(user_id, tenant_slug=tenant_slug)['items']
    return {'exported_at': _utc_now(), 'count': len(scoped_rows), 'items': scoped_rows}


def import_workspaces(user_id: str, items: list[dict[str, Any]], mode: str = 'merge', tenant_slug: str | None = None) -> dict[str, Any]:
    store = _read_store()
    scoped_user_id = _normalize_user_id(user_id)
    scoped_tenant_slug = _normalize_tenant_slug(tenant_slug)
    imported: list[dict[str, Any]] = []
    if str(mode).lower() == 'replace':
        store['workspaces'] = [
            row for row in store['workspaces']
            if not (
                _normalize_user_id(row.get('user_id')) == scoped_user_id
                and _normalize_tenant_slug(row.get('tenant_slug')) == scoped_tenant_slug
            )
        ]
    existing_by_name = {
        str(row.get('name', '')).lower(): row
        for row in store['workspaces']
        if _normalize_user_id(row.get('user_id')) == scoped_user_id
        and _normalize_tenant_slug(row.get('tenant_slug')) == scoped_tenant_slug
    }
    for item in items:
        normalized = _normalize_workspace({**item, 'user_id': scoped_user_id, 'tenant_slug': scoped_tenant_slug})
        if not normalized.get('name'):
            continue
        key = str(normalized['name']).lower()
        if key in existing_by_name:
            existing_by_name[key].update(normalized)
            existing_by_name[key]['updated_at'] = _utc_now()
            imported.append(_normalize_workspace(existing_by_name[key]))
        else:
            store['workspaces'].append(normalized)
            existing_by_name[key] = normalized
            imported.append(normalized)
    deduped = []
    seen_ids = set()
    for row in store['workspaces']:
        normalized = _normalize_workspace(row)
        if normalized['id'] not in seen_ids:
            seen_ids.add(normalized['id'])
            deduped.append(normalized)
    store['workspaces'] = deduped
    _write_store(store)
    return {'imported_count': len(imported), 'count': len(store['workspaces']), 'items': imported}


def delete_workspace(user_id: str, workspace_id: str, tenant_slug: str | None = None) -> dict[str, Any]:
    store = _read_store()
    scoped_user_id = _normalize_user_id(user_id)
    scoped_tenant_slug = _normalize_tenant_slug(tenant_slug)
    rows = store['workspaces']
    next_rows = [
        row for row in rows
        if not (
            _normalize_user_id(row.get('user_id')) == scoped_user_id
            and _normalize_tenant_slug(row.get('tenant_slug')) == scoped_tenant_slug
            and str(row.get('id')) == str(workspace_id)
        )
    ]
    if len(next_rows) == len(rows):
        raise NotFoundError('Workspace not found.')
    store['workspaces'] = next_rows
    _write_store(store)
    return {'deleted': True, 'id': workspace_id}
